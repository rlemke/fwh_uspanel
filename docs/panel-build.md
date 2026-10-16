# Panel Build — the tidy US state × year table

**Namespace:** `uspanel.data` (facet `BuildPanel`) · `uspanel.workflows` (workflow `BuildUSStatePanel`) ·
**FFL:** `src/uspanel/ffl/uspanel.ffl` ·
**Handler:** `src/uspanel/handlers/uspanel_handlers.py` → `handle_build_panel` ·
**Core:** `src/uspanel/_lib.py` → `build_panel()` ·
**Tests:** `tests/test_uspanel.py` → `test_build_panel_joins_and_covers`, `test_json_sidecar_shape`

## Overview

This is the **flagship** feature: `BuildPanel` fetches eight clean public sources,
**outer-joins** them on `(state, year)`, and caches one tidy wide table as **CSV +
JSON** with a per-column **coverage report**. It is the inspectable dataset you look
at *before* deciding an analysis — no charts or models (that is
[findings-analysis](findings-analysis.md)); the deliberate contract is
"reproducible spine first."

The panel is a **ragged** 50-states-plus-DC × years table (README: **1,326 rows**,
1999–2024): columns span different coverage windows, so every downstream consumer
must handle nulls. The analyzable overlap where economics + migration + health +
demographics coexist is **2010–2017** (README: 408 rows). One row = one state-year;
one column = one metric from one source.

## How it works

`build_panel(*, force=False)` (`_lib.py`) is a **fetch → outer-join → write**
pipeline, all in one process:

1. **Fetch every source** into a `{(fips, year): …}` dict — `fetch_acs`,
   `fetch_bls_unemployment`, `fetch_pep_migration`, `fetch_nchs_mortality`,
   `fetch_hiv_diagnoses`, `fetch_covid_deaths`, `fetch_partisan_lean`,
   `fetch_hq_migration`. Each is documented in [data-sources](data-sources.md).
2. **Population denominator** — `pop_by` is built from ACS population, and a
   `pop_lookup(fp, yr)` with a **nearest-year fallback** (offsets `0, 1, -1, 2, -2`)
   supplies a per-100k denominator even for years with no ACS 1-year release (so
   COVID-2020 still normalizes). `per100k(count, fp, yr)` divides HIV/COVID counts by
   it.
3. **Outer-join** — `keys = union of all eight source key-sets`. For each `(fips,
   year)` (sorted by `(postal, year)`), a record is seeded with `fips/state/name/year`
   plus every `COLUMNS` entry set to `None`, then each source's dict is merged in.
   HIV and COVID are inserted as **per-100k rates**; the rest as raw values. A missing
   source for a given state-year simply leaves those columns `None` — that is the
   raggedness.
4. **Coverage report** — `coverage = {col: count of non-null cells}` over all rows.
5. **Write** — a tidy wide CSV (`fips,state,name,year,<COLUMNS>`, empty string for
   nulls) and a JSON sidecar (`{columns, rows, coverage, year_min, year_max}`,
   compact separators) via `storage.open_write` (see [storage-cache](storage-cache.md)).

Data shape at each hop: `raw source JSON → {(fips, year): dict} × 8 → merged rows
(list[dict]) → us_state_panel.csv + us_state_panel.json`.

The workflow `uspanel.workflows.BuildUSStatePanel(force)` is a one-step wrapper: it
calls `BuildPanel` and yields `{status="completed", csv_path, n_rows}`.

## Fan-out

**Single-task — no fan-out.** There is no `foreach` in the FFL; `build_panel`
assembles all 51 states in one process. This is correct by construction: every source
is a small **national** table (one ACS/PEP/BLS/NCHS call returns all states at once),
and the outer-join needs every source co-resident to align them on `(state, year)`.
The only per-state loops are *inside* the single task (e.g. iterating `STATES` to seed
BLS series IDs and HQ counts). Fleet parallelism would add coordination cost with no
wall-clock win here.

## Data & fields

**Join key:** `(fips, year)` where `fips` is the 2-digit state FIPS from the `STATES`
table (50 states + DC = 51 units; `_NAME_TO_FIPS` / `_POSTAL_TO_FIPS` are the reverse
lookups sources key by).

**Key/identity columns:** `fips`, `state` (postal), `name`, `year`.

**Value columns** (`COLUMNS` in `_lib.py`), grouped as the panel presents them:

| Group | Columns | Source (spec) |
|---|---|---|
| Socioeconomic + migration (v1 spine) | `population`, `median_hh_income`, `foreign_born_pct`, `aid_pct`, `unemployment_rate`, `net_domestic_migration`, `net_international_migration` | ACS, BLS, PEP — [data-sources](data-sources.md) |
| Health (age-adjusted death rate /100k) | `mortality_all`, `cancer_death_rate`, `heart_death_rate`, `flu_pneumonia_death_rate`, `diabetes_death_rate`, `stroke_death_rate` | NCHS |
| Health (rate /100k) | `hiv_diagnosis_rate`, `covid_death_rate` | AIDSVu, CDC COVID |
| Political | `dem_pres_share` | county-election returns |
| Corporate | `corp_hq_count`, `corp_hq_net` | SEC DERA |

Per-source coverage windows differ (ACS 2005–2023 no-2020, BLS 2005–2024, PEP
2010–2019, NCHS 1999–2017, HIV 2008+, COVID 2020+, partisan 2005–2024, HQ 2010–2017)
— see [data-sources](data-sources.md) for the exact windows and why they differ.

## External libraries / binaries

- **`requests`** (pip) — every source fetch. `build_panel` calls `_require_requests()`
  transitively via each fetcher.
- **`facetwork.runtime.storage`** via `src/uspanel/storage.py` — backend-aware
  read/write so the same code caches to a local dir in tests and to MinIO/S3 on the
  fleet.
- **No binaries.** Pure-Python; no osmium/GDAL/Java. (`numpy` is a dependency but is
  used only by [findings-analysis](findings-analysis.md), not by `build_panel`.)

## Facets & workflows

| Facet / workflow | Kind | Effect / Cost / Timeout | Purpose (from FFL docstring) |
|---|---|---|---|
| `uspanel.data.BuildPanel(force=false) => (csv_path, json_path, n_rows, year_min, year_max)` | **event** | `Effect(external)` · `Cost(moderate)` · `Timeout(15min)` | "Fetch every source, outer-join on (state, year), cache the tidy panel as CSV + JSON with a per-column coverage report." |
| `uspanel.workflows.BuildUSStatePanel(force=false) => (status, csv_path, n_rows)` | workflow | — | "Build the US state × year data panel (Census + BLS)." One-step wrapper over `BuildPanel`. |

`handle_build_panel` is a thin adapter: it calls `build_panel(force=…)`, emits a
`_step_log` success line (`"BuildPanel: {n_rows} rows {year_min}-{year_max} -> {csv}"`),
and returns the five FFL return fields. On exception it logs an error line and
re-raises (no silent empty default).

## Cache / output

- **Output artifacts** (via `storage.output_root()`):
  - `us_state_panel.csv` — tidy wide, one row per state-year.
  - `us_state_panel.json` — `{columns, rows, coverage, year_min, year_max}`.
- **Roots:** local `uspanel-output/` under `FW_DATA_ROOT`; on the fleet
  `s3://afl-cache/cache/uspanel/output/` (MinIO). Overridable with
  `FW_USPANEL_OUTPUT_DIR`. See [storage-cache](storage-cache.md).
- **Per-source caches** (BLS series, DERA HQ snapshots) live under
  `storage.cache_root()`, separate from the output — see [data-sources](data-sources.md).
- The `force` flag is threaded through the FFL/handler but the current
  `build_panel` **re-fetches unconditionally** — the effective reuse is at the
  per-source cache layer (BLS empty-fetch fallback, immutable DERA quarter caches),
  not a whole-panel short-circuit. See Gotchas.

## Gotchas & notes

- **`force` is plumbed but `build_panel` does not branch on it.** The signature
  accepts `force` and the handler passes it, yet the function always re-fetches; only
  the sub-caches (BLS, DERA) implement reuse. A maintainer expecting a whole-panel
  cache short-circuit will not find one here.
- **Ragged by design.** Columns have different year spans; never assume a
  rectangular panel. The coverage report is the ground truth for what is populated.
- **`CENSUS_API_KEY` is mandatory** for the ACS and PEP portions — `_census_key()`
  raises if unset, which would abort those fetchers (and via `fetch_pep_migration`'s
  `raise_for_status`, the whole build). BLS/NCHS/COVID/HIV/election/SEC are keyless.
- **Row count varies with live coverage.** README quotes 1,326 rows; the actual count
  is `len(union of source keys)` and shifts as sources gain/lose years or fail
  (HIV/COVID/partisan/HQ degrade to `{}` on error, shrinking the union).
- **Sort order is `(postal, year)`**, not FIPS — rows read alphabetically by postal
  code.

## Related specs

- [data-sources](data-sources.md) — the eight fetchers this join consumes, with
  endpoints, coverage windows, and per-source quirks.
- [findings-analysis](findings-analysis.md) — the analysis + HTML page built *from*
  this panel (`BuildFindings` calls `build_panel` first).
- [storage-cache](storage-cache.md) — where the CSV/JSON and per-source caches land.
- [domain-handlers](domain-handlers.md) — how `BuildPanel` is registered and dispatched.
