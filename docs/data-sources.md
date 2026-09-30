# Data Sources — the eight public feeds

**Namespace:** `uspanel.data` (consumed by `BuildPanel`) ·
**Core:** `src/uspanel/_lib.py` → `fetch_acs`, `fetch_bls_unemployment`,
`fetch_pep_migration`, `fetch_nchs_mortality`, `fetch_hiv_diagnoses`,
`fetch_covid_deaths`, `fetch_partisan_lean`, `fetch_hq_migration` ·
**Tests:** `tests/test_uspanel.py` (all eight are mocked)

## Overview

Every column in the panel comes from one of **eight** public feeds. Each fetcher
returns a `{(fips, year): value-or-dict}` map keyed on the same `(state FIPS, year)`
so [panel-build](panel-build.md)'s outer-join can align them. This spec is the
per-source contract — endpoint, exact fields/series IDs, coverage window, key
requirement, and the specific quirk that makes each one finicky. All eight are
grounded in `_lib.py`; the module constants (`CENSUS_BASE`, `BLS_V1`, `NCHS_SOCRATA`,
`ATLASPLUS`, `COVID_SOCRATA`, `ELECTION_BASE`, `DERA_BASE`, …) name the endpoints.

## How it works

All fetchers share a shape: HTTP GET/POST → parse → map to `{(fips, year): …}`,
normalizing numeric cells through `_num()` (which nulls empty/`"null"` values and the
ACS suppression sentinel `≤ -666666666`). State identity is resolved via the `STATES`
table and its reverses `_NAME_TO_FIPS` / `_POSTAL_TO_FIPS`, so a source keyed by state
*name* (NCHS, COVID) or *postal* (SEC) still lands on the right FIPS. Sources that hit
undocumented or fragile backends (HIV, COVID, partisan, HQ) **degrade to an empty
dict** on any exception rather than aborting the whole panel — an honest,
fail-soft-per-source design.

## Fan-out

**Single-task** — the fetchers run sequentially inside one `BuildPanel` task. Each is
a small national query (all states in one or a few calls), so there is nothing to fan
out. BLS is chunked (≤25 series, ≤10-year span per request) but that is request
batching within the one task, not fleet fan-out. See
[panel-build](panel-build.md#fan-out).

## Data & fields

| # | Source (fn) | Endpoint / IDs | Coverage | Key? | Columns produced |
|---|---|---|---|---|---|
| 1 | **Census ACS 1-year** (`fetch_acs`) | `api.census.gov/data/{year}/acs/acs1`; tables `B01003_001E` (pop), `B19013_001E` (median hh income), `B05002_001E`/`_013E` (foreign-born %), `B19058_001E`/`_002E` (public-assistance/SNAP → `aid_pct`) | 2005–2023, **no 2020** 1-yr release | **CENSUS_API_KEY** | `population`, `median_hh_income`, `foreign_born_pct`, `aid_pct` |
| 2 | **BLS LAUS** (`fetch_bls_unemployment`) | series `LASST<fips>0000000000003` (statewide, seasonally adjusted); v1 keyless or v2 with `BLS_API_KEY` | 2005–2024 | optional `BLS_API_KEY` | `unemployment_rate` |
| 3 | **Census PEP components** (`fetch_pep_migration`) | `api.census.gov/data/2019/pep/components`; `DOMESTICMIG`, `INTERNATIONALMIG`, `PERIOD_CODE` | **2010–2019** (vintage-2019 window) | **CENSUS_API_KEY** | `net_domestic_migration`, `net_international_migration` |
| 4 | **NCHS Leading Causes of Death** (`fetch_nchs_mortality`) | Socrata `data.cdc.gov/resource/bi63-dtpu.json`; `aadr` (age-adjusted rate /100k) for the 6 causes in `NCHS_CAUSES` | 1999–2017 | keyless | `mortality_all`, `cancer_death_rate`, `heart_death_rate`, `flu_pneumonia_death_rate`, `diabetes_death_rate`, `stroke_death_rate` |
| 5 | **CDC AtlasPlus HIV** (`fetch_hiv_diagnoses`) | undocumented JSON backend `gis.cdc.gov/grasp/AtlasPlus` — `getInitData/00` + `qtOutputData` POST, transmission id `801` ("all categories") | 2008+ | keyless (browser-UA spoof) | `hiv_diagnosis_rate` (count → /100k) |
| 6 | **CDC Provisional COVID Deaths** (`fetch_covid_deaths`) | Socrata `data.cdc.gov/resource/r8kw-7aab.json`, `group='By Year'` | 2020+ | keyless | `covid_death_rate` (count → /100k) |
| 7 | **County election returns** (`fetch_partisan_lean`) | tonmcg GitHub CSVs (`US_County_Level_...Results`), 2008/2012/2016 wide + 2020/2024 per-year | forward-filled 2005–2024 | keyless | `dem_pres_share` (2-party Dem % ) |
| 8 | **SEC DERA financial statements** (`fetch_hq_migration`) | `sec.gov/files/dera/data/financial-statement-data-sets/{year}q2.zip` → `sub.txt` → `stprba` business-address state | 2010–2017 | keyless (`SEC_USER_AGENT` required) | `corp_hq_count`, `corp_hq_net` |

Per-source detail worth knowing:

- **ACS** — `foreign_born_pct = B05002_013E / B05002_001E × 100`; `aid_pct =
  B19058_002E / B19058_001E × 100` (households on public assistance **or** SNAP). Skips
  any year that returns non-200 (e.g. the missing 2020 release) rather than failing.
- **BLS** — prefers the `M13` annual-average period if present, else the mean of the
  12 monthly `M##` values. Chunks requests to respect the keyless v1 limits.
- **PEP** — decodes the vintage-2019 `PERIOD_CODE` as `year = 2009 + code` (1→2010 …
  10→2019) and discards anything outside 2010–2019. This is the **only** clean-API
  migration window; 2020+ (bulk CSV) and 2000–2009 (intercensal) are documented
  fast-follows, not implemented.
- **NCHS** — `aadr` (age-adjusted death rate) is deliberately chosen over raw counts
  to remove the age-structure confound; `_NAME_TO_FIPS` drops the "United States" row
  and territories.
- **HIV** — spoofs a browser UA + `X-Requested-With` header against the AtlasPlus XHR
  backend; parses `sourcedata` rows positionally (`r[9]` = value, `r[2]` = geo id,
  `r[1]` = year id). Fragile by nature — hence fail-soft.
- **Partisan lean** — sums county `dem`/`gop` votes to a state 2-party Democratic
  share per election, then assigns each panel year the **most recent election at or
  before it** (a slow-moving lean). **Alaska is absent** from the county source for
  2008–2016, so it drops out of those years.
- **SEC HQ** — `corp_hq_count` = public-company HQs based in the state that year (a
  real 0 for covered states); `corp_hq_net` = net relocations (moved-IN minus
  moved-OUT), computed only for years whose **prior** year is also cached (so it is
  left unset where it can't be measured). One quarter (`q2`) sampled per year.

## External libraries / binaries

- **`requests`** (pip) — all eight fetchers; `_require_requests()` raises a clear
  error if it is unimportable.
- **stdlib** — `csv` + `io` (election + DERA parsing), `zipfile` (DERA `sub.txt`
  extraction), `json` (AtlasPlus POST body + caches).
- **No binaries, no numpy** — this module is HTTP + dict assembly only.

## Facets & workflows

No facet of its own — these functions are the **implementation** behind
`uspanel.data.BuildPanel` (see [panel-build](panel-build.md)). There is no
`fetch`-level FFL facet; the panel is the single external-effect surface.

## Cache / output

- **BLS** — `bls-unemployment.json` under `storage.cache_root()`. Written on every
  good fetch and **reused when a live fetch comes back empty** (quota exhaustion), so a
  throttled rebuild keeps the last good series instead of dropping unemployment
  entirely (`_load_bls_cache` / `_save_bls_cache`).
- **SEC DERA** — `hq-{year}.json` per year under `cache_root()`. A past quarter's data
  is **immutable**, so the cache is authoritative and the ~96 MB zip is downloaded once
  per year.
- The other six sources are **not** cached at this layer — they are re-fetched each
  `BuildPanel` run and only persisted inside the final `us_state_panel.{csv,json}`.

## Gotchas & notes

- **Fail-soft is per-source, not uniform.** ACS/PEP raise on a missing key (aborting
  those columns / the build); HIV/COVID/partisan/HQ swallow exceptions and return
  `{}`. So a network blip silently *shrinks* the panel for the fragile sources but
  *breaks* it for Census — different failure modes to reason about.
- **The undocumented AtlasPlus + retired COVID Socrata are the brittle two.** The
  README flags HIV (AtlasPlus, "finicky undocumented backend") and COVID (CDC by-state
  Socrata was retired) as the least stable feeds; treat empty HIV/COVID columns as
  expected, not a regression.
- **HIV/COVID are counts converted to per-100k** using ACS population with a
  nearest-year fallback — a 2020 COVID rate borrows the closest ACS population year
  because there is no 2020 1-year release.
- **SEC needs a real UA with contact info** — set `SEC_USER_AGENT` (e.g.
  `"Your Org you@example.org"`, alongside `CENSUS_API_KEY` in
  `~/.facetwork/fleet-secrets.env`). SEC blocks generic user agents, and the
  contact is a person's address, so it is configuration, never committed. A
  year already in the cache needs no request and so no user agent.
- **Numeric hygiene lives in `_num`** — the ACS suppression sentinel
  (`≤ -666666666`) must be nulled, or it would poison every ACS-derived column; keep
  legitimate negatives (out-migration) intact (tested in
  `test_num_handles_suppression_sentinels`).

## Related specs

- [panel-build](panel-build.md) — the outer-join that consumes all eight maps.
- [storage-cache](storage-cache.md) — where the BLS + DERA per-source caches live.
- [findings-analysis](findings-analysis.md) — how these columns are turned into the
  within-state analysis.
