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
# CDC AtlasPlus (NCHHSTP) JSON backend — total new HIV diagnoses by state, 2008+.
# tx id 801 = "All transmission categories". Undocumented endpoint (browser XHR).
ATLASPLUS = "https://gis.cdc.gov/grasp/AtlasPlus"
_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_ATLAS_HDRS = {**_BROWSER_UA, "Content-Type": "application/json; charset=UTF-8",
               "Referer": ATLASPLUS + "/", "X-Requested-With": "XMLHttpRequest"}
HIV_YEAR_FROM = 2008
# CDC Provisional COVID-19 Deaths by state (Socrata), group="By Year", 2020+.
COVID_SOCRATA = "https://data.cdc.gov/resource/r8kw-7aab.json"
# State partisan lean = 2-party Democratic presidential vote share, aggregated
# county->state from tonmcg's open results, forward-filled to each panel year.
ELECTION_BASE = ("https://raw.githubusercontent.com/tonmcg/"
                 "US_County_Level_Election_Results_08-24/master")
ELECTION_YEARS = [2008, 2012, 2016, 2020, 2024]
# SEC DERA Financial Statement Data Sets — quarterly sub.txt carries each filer's
# business-address state (stprba). Tracking it across years detects HQ relocations
# (e.g. Tesla CA->TX). One quarter/year sampled; the per-year maps are cached (the
# data for a past quarter is immutable).
DERA_BASE = "https://www.sec.gov/files/dera/data/financial-statement-data-sets"
DERA_QUARTER = "q2"  # Q2 catches most annual (10-K) filers
HQ_YEARS = list(range(2010, 2018))  # panel-overlap window; moves detectable 2011-2017
SEC_UA = "facetwork research ralph_lemke@hotmail.com"

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
    "population", "median_hh_income", "foreign_born_pct", "aid_pct",
    "unemployment_rate", "net_domestic_migration", "net_international_migration",
    # health: NCHS age-adjusted death rates per 100k (v2)
    "mortality_all", "cancer_death_rate", "heart_death_rate",
    "flu_pneumonia_death_rate", "diabetes_death_rate", "stroke_death_rate",
    # HIV new diagnoses per 100k (AtlasPlus, 2008+) + COVID deaths per 100k (2020+)
    "hiv_diagnosis_rate", "covid_death_rate",
    # political: 2-party Democratic presidential vote share % (higher = bluer)
    "dem_pres_share",
    # corporate: public-company HQs based in the state + net HQ relocations (SEC)
    "corp_hq_count", "corp_hq_net",
]

# reverse lookups for sources keyed by state name / postal code.
_NAME_TO_FIPS = {name: fips for fips, (_p, name) in STATES.items()}
_POSTAL_TO_FIPS = {postal: fips for fips, (postal, _n) in STATES.items()}


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
    """{(fips, year): {population, median_hh_income, foreign_born_pct, aid_pct}} from ACS 1-yr.

    aid_pct = share of households receiving public assistance income OR Food
    Stamps/SNAP (table B19058) — a broad "government aid" measure.
    """
    _require_requests()
    key = _census_key()
    out: dict[tuple[str, int], dict] = {}
    for year in ACS_YEARS:
        url = f"{CENSUS_BASE}/{year}/acs/acs1"
        params = {
            "get": "B01003_001E,B19013_001E,B05002_001E,B05002_013E,B19058_001E,B19058_002E",
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
            aid = _num(row[idx["B19058_002E"]]) if "B19058_002E" in idx else None
            hh = _num(row[idx["B19058_001E"]]) if "B19058_001E" in idx else None
            out[(fips, year)] = {
                "population": pop,
                "median_hh_income": _num(row[idx["B19013_001E"]]),
                "foreign_born_pct": (round(fb / tot * 100, 2) if fb is not None and tot else None),
                "aid_pct": (round(aid / hh * 100, 2) if aid is not None and hh else None),
            }
        logger.info("ACS %s: %d states", year, len(data))
    return out


def _bls_cache_path() -> str:
    return cstore.join(cstore.cache_root(), "bls-unemployment.json")


def _load_bls_cache() -> dict[tuple[str, int], float]:
    p = _bls_cache_path()
    if not cstore.exists(p):
        return {}
    try:
        with cstore.open_read(p) as f:
            raw = json.load(f)  # {"fips|year": rate}
        out = {}
        for k, v in raw.items():
            fp, yr = k.split("|")
            out[(fp, int(yr))] = float(v)
        return out
    except Exception:  # noqa: BLE001
        return {}


def _save_bls_cache(data: dict[tuple[str, int], float]) -> None:
    raw = {f"{fp}|{yr}": v for (fp, yr), v in data.items()}
    with cstore.open_write(_bls_cache_path(), "w") as f:
        json.dump(raw, f)


def fetch_bls_unemployment() -> dict[tuple[str, int], float]:
    """{(fips, year): annual mean unemployment rate} from BLS LAUS (statewide, SA).

    BLS's keyless API has a small daily per-IP quota; a registered key (env
    ``BLS_API_KEY``, free, 500/day, v2) lifts it. Results are cached to
    ``bls-unemployment.json`` and REUSED when a live fetch comes back empty
    (quota exhausted) — so a throttled rebuild keeps the last good series instead
    of dropping unemployment entirely.
    """
    _require_requests()
    cached = _load_bls_cache()
    series_to_fips = {f"LASST{fips}0000000000003": fips for fips in STATES}
    ids = list(series_to_fips)
    key = os.environ.get("BLS_API_KEY", "").strip()
    endpoint = ("https://api.bls.gov/publicAPI/v2/timeseries/data/" if key else BLS_V1)
    out: dict[tuple[str, int], float] = {}
    # BLS v1 keyless limits: <=25 series and <=10-year span per request.
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        for y0 in range(BLS_YEARS[0], BLS_YEARS[-1] + 1, 10):
            y1 = min(y0 + 9, BLS_YEARS[-1])
            body = {"seriesid": chunk, "startyear": str(y0), "endyear": str(y1)}
            if key:
                body["registrationkey"] = key
            try:
                r = requests.post(endpoint, json=body, headers={"User-Agent": USER_AGENT},
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
    if out:
        _save_bls_cache(out)  # refresh the cache on a good fetch
        logger.info("BLS unemployment: %d state-years (fetched)", len(out))
        return out
    if cached:
        logger.warning("BLS live fetch empty (quota?) — using %d cached state-years", len(cached))
        return cached
    logger.warning("BLS unemployment: 0 state-years (no live data, no cache)")
    return {}


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


def fetch_hiv_diagnoses() -> dict[tuple[str, int], int]:
    """{(fips, year): total new HIV diagnoses} from CDC AtlasPlus (2008+).

    One getInitData catalog call + one qtOutputData POST for transmission id 801
    ("All transmission categories"). Undocumented JSON backend, so failures
    degrade to an empty dict rather than aborting the whole panel.
    """
    _require_requests()
    try:
        init = requests.get(f"{ATLASPLUS}/getInitData/00", headers=_BROWSER_UA, timeout=120).json()
        vv = init["varvals"]
        states = [v for v in vv if v.get("vtid") == 3 and v.get("geoLevel") == 1002 and v.get("fips")]
        gid_fips = {s["id"]: s["fips"] for s in states}
        years = {str(v["name"]): v["id"] for v in vv if v.get("vtid") == 2}
        yid_year = {v["id"]: str(v["name"]) for v in vv if v.get("vtid") == 2}
        ywanted = [y for y in (str(x) for x in range(HIV_YEAR_FROM, 2025)) if y in years]
        vids = ",".join(["203"] + [str(s["id"]) for s in states]
                        + [str(years[y]) for y in ywanted] + ["650", "551", "601", "801"])
        rows = requests.post(f"{ATLASPLUS}/qtOutputData", data=json.dumps({"VariableIDs": vids}),
                             headers=_ATLAS_HDRS, timeout=180).json().get("sourcedata") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("AtlasPlus HIV fetch failed: %s", exc)
        return {}
    out: dict[tuple[str, int], int] = {}
    for r in rows:
        if r[9] is None:
            continue
        fips, yr = gid_fips.get(r[2]), yid_year.get(r[1])
        if fips in STATES and yr and yr.isdigit():
            out[(fips, int(yr))] = int(r[9])
    logger.info("HIV diagnoses: %d state-years (2008+)", len(out))
    return out


def fetch_covid_deaths() -> dict[tuple[str, int], int]:
    """{(fips, year): COVID-19 deaths} by state, from CDC provisional (2020+)."""
    _require_requests()
    params = {"$where": "`group`='By Year'", "$select": "state,year,covid_19_deaths",
              "$limit": 20000}
    try:
        rows = requests.get(COVID_SOCRATA, params=params, headers={"User-Agent": USER_AGENT},
                            timeout=(30, 90)).json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("COVID fetch failed: %s", exc)
        return {}
    out: dict[tuple[str, int], int] = {}
    for row in rows:
        fips = _NAME_TO_FIPS.get(row.get("state"))
        yr = row.get("year")
        v = _num(row.get("covid_19_deaths"))
        if fips and yr and str(yr).isdigit() and v is not None:
            out[(fips, int(yr))] = int(v)
    logger.info("COVID deaths: %d state-years (2020+)", len(out))
    return out


def _agg_share(dem_by_state: dict, gop_by_state: dict) -> dict[str, float]:
    """{fips: 2-party Democratic vote share %} from summed dem/gop by state."""
    out = {}
    for fp in dem_by_state:
        d, g = dem_by_state[fp], gop_by_state.get(fp, 0)
        if d + g > 0 and fp in STATES:
            out[fp] = round(d / (d + g) * 100, 2)
    return out


def fetch_partisan_lean() -> dict[tuple[str, int], float]:
    """{(fips, year): Dem 2-party presidential vote share %} forward-filled.

    Aggregates county-level results (tonmcg, open CSVs) to a state 2-party
    Democratic share per election, then assigns each panel year the most recent
    election at or before it (slow-moving lean). Higher = bluer.
    """
    _require_requests()
    import csv as _csv
    import io as _io

    def _rows(url):
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(30, 90))
        r.raise_for_status()
        return list(_csv.DictReader(_io.StringIO(r.text)))

    share: dict[int, dict[str, float]] = {}
    try:
        # 2008 / 2012 / 2016 — one wide file keyed by county fips_code.
        wide = _rows(f"{ELECTION_BASE}/US_County_Level_Presidential_Results_08-16.csv")
        for e in (2008, 2012, 2016):
            dem, gop = {}, {}
            for row in wide:
                fp = str(row.get("fips_code", "")).split(".")[0].zfill(5)[:2]
                dem[fp] = dem.get(fp, 0) + int(_num(row.get(f"dem_{e}")) or 0)
                gop[fp] = gop.get(fp, 0) + int(_num(row.get(f"gop_{e}")) or 0)
            share[e] = _agg_share(dem, gop)
        # 2020 / 2024 — per-year long files (votes_dem / votes_gop / county_fips).
        for e in (2020, 2024):
            dem, gop = {}, {}
            for row in _rows(f"{ELECTION_BASE}/{e}_US_County_Level_Presidential_Results.csv"):
                fp = str(row.get("county_fips", "")).split(".")[0].zfill(5)[:2]
                dem[fp] = dem.get(fp, 0) + int(_num(row.get("votes_dem")) or 0)
                gop[fp] = gop.get(fp, 0) + int(_num(row.get("votes_gop")) or 0)
            share[e] = _agg_share(dem, gop)
    except Exception as exc:  # noqa: BLE001
        logger.warning("partisan-lean fetch failed: %s", exc)
        return {}

    elections = sorted(share)
    out: dict[tuple[str, int], float] = {}
    for fips in STATES:
        for year in range(2005, 2025):
            prior = [e for e in elections if e <= year and fips in share[e]]
            if prior:
                out[(fips, year)] = share[max(prior)][fips]
    logger.info("partisan lean: %d state-years (forward-filled from %s)", len(out), elections)
    return out


def _hq_states_for_year(year: int) -> dict[str, str]:
    """{cik: business-state postal} for filers in {year}Q2, cached per year.

    Downloads the DERA quarterly zip once (~96MB), extracts just sub.txt, keeps
    each filer's US business-address state, and caches the small map. Past-quarter
    data is immutable, so the cache is authoritative on re-runs.
    """
    import csv as _csv
    import io as _io
    import zipfile as _zip

    cache_key = cstore.join(cstore.cache_root(), f"hq-{year}.json")
    if cstore.exists(cache_key):
        try:
            with cstore.open_read(cache_key) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    url = f"{DERA_BASE}/{year}{DERA_QUARTER}.zip"
    try:
        content = requests.get(url, headers={"User-Agent": SEC_UA}, timeout=(30, 240)).content
        with _zip.ZipFile(_io.BytesIO(content)) as zf:
            text = zf.read("sub.txt").decode("latin-1")
    except Exception as exc:  # noqa: BLE001
        logger.warning("DERA %s fetch failed: %s", year, exc)
        return {}
    out: dict[str, str] = {}
    reader = _csv.DictReader(_io.StringIO(text), delimiter="\t")
    for row in reader:
        cik = row.get("cik")
        st = (row.get("stprba") or "").strip().upper()
        country = (row.get("countryba") or "").strip().upper()
        if cik and st in _POSTAL_TO_FIPS and country in ("US", ""):
            out[cik] = st  # last filing that quarter wins (fine for an annual snapshot)
    with cstore.open_write(cache_key, "w") as f:
        json.dump(out, f)
    logger.info("DERA HQ %s: %d public-company HQs", year, len(out))
    return out


def fetch_hq_migration() -> dict[tuple[str, int], dict]:
    """{(fips, year): {corp_hq_count, corp_hq_net}} from SEC business-address state.

    corp_hq_count = public-company HQs based in the state that year; corp_hq_net =
    net HQ relocations (companies that moved IN minus OUT), detected as a change in
    a company's business-address state between consecutive annual snapshots. Both
    default to 0 for a covered state-year (a real zero, not missing).
    """
    _require_requests()
    by_year = {y: _hq_states_for_year(y) for y in HQ_YEARS}
    covered = [y for y in HQ_YEARS if by_year.get(y)]
    if not covered:
        return {}
    out: dict[tuple[str, int], dict] = {}
    # counts (level) for every covered year
    for y in covered:
        counts: dict[str, int] = {}
        for st in by_year[y].values():
            counts[st] = counts.get(st, 0) + 1
        for postal, fips in _POSTAL_TO_FIPS.items():
            out[(fips, y)] = {"corp_hq_count": counts.get(postal, 0)}
    # net relocations — only for years whose prior year is covered (measurable);
    # a real 0 for states with no move, but left unset where it can't be computed.
    for y in covered:
        if y - 1 not in by_year or not by_year[y - 1]:
            continue
        for fips in STATES:
            out[(fips, y)]["corp_hq_net"] = 0
        prev, cur = by_year[y - 1], by_year[y]
        for cik, st_now in cur.items():
            st_before = prev.get(cik)
            if st_before and st_before != st_now and st_before in _POSTAL_TO_FIPS:
                out[(_POSTAL_TO_FIPS[st_now], y)]["corp_hq_net"] += 1     # moved IN
                out[(_POSTAL_TO_FIPS[st_before], y)]["corp_hq_net"] -= 1  # moved OUT
    logger.info("corp HQ migration: %d state-years (%s)", len(out), covered)
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
    hiv = fetch_hiv_diagnoses()
    covid = fetch_covid_deaths()
    partisan = fetch_partisan_lean()
    hq = fetch_hq_migration()

    # population lookup with a nearest-year fallback (so COVID 2020, which has no
    # ACS 1-year release, still gets a per-100k denominator).
    pop_by = {(fp, yr): d["population"] for (fp, yr), d in acs.items() if d.get("population")}

    def pop_lookup(fp: str, yr: int) -> float | None:
        for dy in (0, 1, -1, 2, -2):
            if (fp, yr + dy) in pop_by:
                return pop_by[(fp, yr + dy)]
        return None

    def per100k(count, fp, yr):
        p = pop_lookup(fp, yr)
        return round(count / p * 100000, 2) if p else None

    keys = (set(acs) | set(bls) | set(pep) | set(nchs) | set(hiv) | set(covid)
            | set(partisan) | set(hq))
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
        if (fips, year) in hiv:
            rec["hiv_diagnosis_rate"] = per100k(hiv[(fips, year)], fips, year)
        if (fips, year) in covid:
            rec["covid_death_rate"] = per100k(covid[(fips, year)], fips, year)
        if (fips, year) in partisan:
            rec["dem_pres_share"] = partisan[(fips, year)]
        if (fips, year) in hq:
            rec.update(hq[(fips, year)])
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
