"""US state × year panel — fetch + align + cache a tidy multi-source dataset.

The reproducible spine for a "what moves with what across US states over time"
analysis. Each source is a clean public API; ``build_panel`` outer-joins them on
(state, year) into one tidy table and caches it as CSV + JSON, with a per-column
coverage report. NO charts/models yet — this is the inspectable dataset.

Sources (v1, all verified clean-API):

- **ACS 1-year** (Census, 2005-2023, no 2020 1-yr release) — population
  (``B01003_001E``), median household income (``B19013_001E``), foreign-born %
  (``B05002_013E`` / ``B05002_001E``). Needs ``CENSUS_API_KEY``.
- **BLS LAUS** (statewide unemployment rate, seasonally adjusted, annual mean of
  the 12 monthly values) — series ``LASST<fips>0000000000003``, keyless API.
- **Census PEP components** (vintage 2019) — net domestic + net international
  migration by state-year, **2010-2019** (the clean-API window; 2020+ is a
  bulk-CSV fast-follow, 2000-2009 an intercensal one).

Honest coverage: the columns span different years (see the coverage report), so
downstream analysis must handle the ragged panel. Health columns (cause-specific
mortality via CDC WONDER, HIV via AtlasPlus, COVID/flu/RSV via NHSN) are the
planned v2 — this v1 is the socioeconomic + migration spine.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from . import storage as cstore

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger("uspanel")

USER_AGENT = "facetwork-uspanel/1.0 (+https://github.com/rlemke/facetwork)"
CENSUS_BASE = "https://api.census.gov/data"
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
# NCHS Leading Causes of Death by state (Socrata, 1999-2017), age-adjusted death
# rate per 100k. cause_name → panel column.
NCHS_SOCRATA = "https://data.cdc.gov/resource/bi63-dtpu.json"
NCHS_CAUSES = {
    "All causes": "mortality_all",
    "Cancer": "cancer_death_rate",
    "Heart disease": "heart_death_rate",
    "Influenza and pneumonia": "flu_pneumonia_death_rate",
    "Diabetes": "diabetes_death_rate",
    "Stroke": "stroke_death_rate",
}

ACS_YEARS = [y for y in range(2005, 2024) if y != 2020]  # no 2020 1-yr release
BLS_YEARS = list(range(2005, 2025))
PEP_VINTAGE = 2019  # components API window = 2010-2019

# 50 states + DC: FIPS → (postal, name).
STATES: dict[str, tuple[str, str]] = {
    "01": ("AL", "Alabama"), "02": ("AK", "Alaska"), "04": ("AZ", "Arizona"),
    "05": ("AR", "Arkansas"), "06": ("CA", "California"), "08": ("CO", "Colorado"),
    "09": ("CT", "Connecticut"), "10": ("DE", "Delaware"), "11": ("DC", "District of Columbia"),
    "12": ("FL", "Florida"), "13": ("GA", "Georgia"), "15": ("HI", "Hawaii"),
    "16": ("ID", "Idaho"), "17": ("IL", "Illinois"), "18": ("IN", "Indiana"),
    "19": ("IA", "Iowa"), "20": ("KS", "Kansas"), "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"), "23": ("ME", "Maine"), "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"), "26": ("MI", "Michigan"), "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"), "29": ("MO", "Missouri"), "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"), "32": ("NV", "Nevada"), "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"), "35": ("NM", "New Mexico"), "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"), "38": ("ND", "North Dakota"), "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"), "41": ("OR", "Oregon"), "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"), "45": ("SC", "South Carolina"), "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"), "48": ("TX", "Texas"), "49": ("UT", "Utah"),
    "50": ("VT", "Vermont"), "51": ("VA", "Virginia"), "53": ("WA", "Washington"),
    "54": ("WV", "West Virginia"), "55": ("WI", "Wisconsin"), "56": ("WY", "Wyoming"),
}

# Panel columns (besides the fips/postal/name/year keys).
COLUMNS = [
    # socioeconomic + migration spine (v1)
    "population", "median_hh_income", "foreign_born_pct",
    "unemployment_rate", "net_domestic_migration", "net_international_migration",
    # health: NCHS age-adjusted death rates per 100k (v2)
    "mortality_all", "cancer_death_rate", "heart_death_rate",
    "flu_pneumonia_death_rate", "diabetes_death_rate", "stroke_death_rate",
]

# name → fips (reverse of STATES) for sources keyed by state name.
_NAME_TO_FIPS = {name: fips for fips, (_p, name) in STATES.items()}


@dataclass
class PanelResult:
    csv_path: str
    json_path: str
    n_rows: int
    year_min: int
    year_max: int
    coverage: dict[str, int]  # column → non-null cell count


def _require_requests() -> None:
    if requests is None:
        raise RuntimeError("requests is required to fetch the panel sources")


def _census_key() -> str:
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is not set — the Census API now requires a key for "
            "ACS/PEP query endpoints (source it from ~/.facetwork/fleet-secrets.env)."
        )
    return key


def _num(v) -> float | None:
    """Census/BLS numeric cell → float, or None for missing/suppressed sentinels."""
    if v in (None, "", "null"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # ACS suppression / annotation sentinels are large negatives.
    if f <= -666666666:
        return None
    return f


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def fetch_acs() -> dict[tuple[str, int], dict]:
    """{(fips, year): {population, median_hh_income, foreign_born_pct}} from ACS 1-yr."""
    _require_requests()
    key = _census_key()
    out: dict[tuple[str, int], dict] = {}
    for year in ACS_YEARS:
        url = f"{CENSUS_BASE}/{year}/acs/acs1"
        params = {
            "get": "B01003_001E,B19013_001E,B05002_001E,B05002_013E",
            "for": "state:*", "key": key,
        }
        try:
            r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(30, 90))
            if r.status_code != 200:
                logger.warning("ACS %s: HTTP %s — skipping", year, r.status_code)
                continue
            rows = r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ACS %s failed: %s", year, exc)
            continue
        header, *data = rows
        idx = {name: i for i, name in enumerate(header)}
        for row in data:
            fips = row[idx["state"]]
            if fips not in STATES:
                continue
            pop = _num(row[idx["B01003_001E"]])
            fb = _num(row[idx["B05002_013E"]])
            tot = _num(row[idx["B05002_001E"]])
            out[(fips, year)] = {
                "population": pop,
                "median_hh_income": _num(row[idx["B19013_001E"]]),
                "foreign_born_pct": (round(fb / tot * 100, 2) if fb is not None and tot else None),
            }
        logger.info("ACS %s: %d states", year, len(data))
    return out


def fetch_bls_unemployment() -> dict[tuple[str, int], float]:
    """{(fips, year): annual mean unemployment rate} from BLS LAUS (statewide, SA)."""
    _require_requests()
    series_to_fips = {f"LASST{fips}0000000000003": fips for fips in STATES}
    ids = list(series_to_fips)
    out: dict[tuple[str, int], float] = {}
    # BLS v1 keyless limits: <=25 series and <=10-year span per request.
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        for y0 in range(BLS_YEARS[0], BLS_YEARS[-1] + 1, 10):
            y1 = min(y0 + 9, BLS_YEARS[-1])
            body = {"seriesid": chunk, "startyear": str(y0), "endyear": str(y1)}
            try:
                r = requests.post(BLS_V1, json=body, headers={"User-Agent": USER_AGENT},
                                  timeout=(30, 90))
                payload = r.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("BLS %s-%s chunk failed: %s", y0, y1, exc)
                continue
            if payload.get("status") != "REQUEST_SUCCEEDED":
                logger.warning("BLS %s-%s: %s", y0, y1, payload.get("message"))
                continue
            for s in payload.get("Results", {}).get("series", []):
                fips = series_to_fips.get(s["seriesID"])
                if not fips:
                    continue
                by_year: dict[int, list[float]] = {}
                for d in s.get("data", []):
                    v = _num(d.get("value"))
                    if v is None or not d.get("period", "").startswith("M") or d["period"] == "M13":
                        # M13 is the annual average; if present use it directly below
                        if d.get("period") == "M13" and v is not None:
                            out[(fips, int(d["year"]))] = round(v, 2)
                        continue
                    by_year.setdefault(int(d["year"]), []).append(v)
                for yr, vals in by_year.items():
                    if (fips, yr) not in out and vals:  # prefer M13 if it was present
                        out[(fips, yr)] = round(sum(vals) / len(vals), 2)
    logger.info("BLS unemployment: %d state-years", len(out))
    return out


def fetch_pep_migration() -> dict[tuple[str, int], dict]:
    """{(fips, year): {net_domestic_migration, net_international_migration}} 2010-2019."""
    _require_requests()
    key = _census_key()
    url = f"{CENSUS_BASE}/{PEP_VINTAGE}/pep/components"
    params = {"get": "DOMESTICMIG,INTERNATIONALMIG,PERIOD_CODE", "for": "state:*", "key": key}
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(30, 90))
    r.raise_for_status()
    rows = r.json()
    header, *data = rows
    idx = {name: i for i, name in enumerate(header)}
    out: dict[tuple[str, int], dict] = {}
    for row in data:
        fips = row[idx["state"]]
        if fips not in STATES:
            continue
        code = row[idx["PERIOD_CODE"]]
        try:
            year = 2009 + int(code)  # vintage-2019 components: code 1→2010 … 10→2019
        except (TypeError, ValueError):
            continue
        if year < 2010 or year > 2019:
            continue
        out[(fips, year)] = {
            "net_domestic_migration": _num(row[idx["DOMESTICMIG"]]),
            "net_international_migration": _num(row[idx["INTERNATIONALMIG"]]),
        }
    logger.info("PEP migration: %d state-years (2010-2019)", len(out))
    return out


def fetch_nchs_mortality() -> dict[tuple[str, int], dict]:
    """{(fips, year): {cause_col: age-adjusted death rate}} from NCHS (1999-2017).

    One Socrata call for the causes in ``NCHS_CAUSES``; ``aadr`` (age-adjusted
    death rate per 100k) is the right cross-state/time metric — it removes the
    age-structure confound that raw counts carry.
    """
    _require_requests()
    causes = "','".join(NCHS_CAUSES)
    params = {"$limit": 60000, "$where": f"cause_name in ('{causes}')",
              "$select": "year,cause_name,state,aadr"}
    r = requests.get(NCHS_SOCRATA, params=params, headers={"User-Agent": USER_AGENT},
                     timeout=(30, 120))
    r.raise_for_status()
    out: dict[tuple[str, int], dict] = {}
    for row in r.json():
        fips = _NAME_TO_FIPS.get(row.get("state"))  # skips "United States" + territories
        if not fips:
            continue
        rate = _num(row.get("aadr"))
        col = NCHS_CAUSES.get(row.get("cause_name"))
        if rate is None or col is None:
            continue
        try:
            year = int(row["year"])
        except (KeyError, TypeError, ValueError):
            continue
        out.setdefault((fips, year), {})[col] = rate
    logger.info("NCHS mortality: %d state-years (1999-2017)", len(out))
    return out


# ---------------------------------------------------------------------------
# Assemble + cache
# ---------------------------------------------------------------------------


def build_panel(*, force: bool = False) -> PanelResult:
    """Fetch every source, outer-join on (state, year), cache CSV + JSON."""
    csv_path = cstore.join(cstore.output_root(), "us_state_panel.csv")
    json_path = cstore.join(cstore.output_root(), "us_state_panel.json")

    acs = fetch_acs()
    bls = fetch_bls_unemployment()
    pep = fetch_pep_migration()
    nchs = fetch_nchs_mortality()

    keys = set(acs) | set(bls) | set(pep) | set(nchs)
    rows: list[dict] = []
    for fips, year in sorted(keys, key=lambda k: (STATES[k[0]][0], k[1])):
        postal, name = STATES[fips]
        rec = {"fips": fips, "state": postal, "name": name, "year": year}
        rec.update({c: None for c in COLUMNS})
        rec.update(acs.get((fips, year), {}))
        if (fips, year) in bls:
            rec["unemployment_rate"] = bls[(fips, year)]
        rec.update(pep.get((fips, year), {}))
        rec.update(nchs.get((fips, year), {}))
        rows.append(rec)

    coverage = {c: sum(1 for r in rows if r.get(c) is not None) for c in COLUMNS}
    years = sorted({r["year"] for r in rows})

    # CSV (tidy wide: one row per state-year)
    head = ["fips", "state", "name", "year", *COLUMNS]
    lines = [",".join(head)]
    for r in rows:
        lines.append(",".join("" if r.get(c) is None else str(r.get(c)) for c in head))
    with cstore.open_write(csv_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    with cstore.open_write(json_path, "w") as f:
        json.dump({"columns": head, "rows": rows, "coverage": coverage,
                   "year_min": years[0], "year_max": years[-1]}, f, separators=(",", ":"))

    logger.info("panel: %d rows, %d-%d, coverage=%s", len(rows), years[0], years[-1], coverage)
    return PanelResult(csv_path, json_path, len(rows), years[0], years[-1], coverage)
