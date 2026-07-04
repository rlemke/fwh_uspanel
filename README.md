# fwh_uspanel

A Facetwork domain that builds a **reproducible US state × year data panel** — the
tidy, inspectable spine for a "what moves with what across US states over time"
analysis (economics, migration, and — in v2 — health).

`BuildPanel` fetches three clean public sources, outer-joins them on `(state,
year)`, and caches one tidy table as **CSV + JSON** with a per-column coverage
report. No charts or models yet — this is the dataset you look at *before*
deciding the analysis.

## v1 columns + sources (all verified clean-API)

| column | source | coverage |
|---|---|---|
| `population`, `median_hh_income`, `foreign_born_pct` | Census **ACS 1-year** (`B01003`/`B19013`/`B05002`) | 2005–2023 (no 2020 1-yr release) |
| `unemployment_rate` | **BLS LAUS** statewide, SA, annual mean (`LASST<fips>…03`) | 2005–2024 |
| `net_domestic_migration`, `net_international_migration` | Census **PEP components** (vintage 2019) | 2010–2019 |

→ **1,020 rows** (50 states + DC × 2005–2024), a deliberately **ragged panel**
(columns span different years — downstream analysis must handle the gaps).

The data checks out: 2019's biggest domestic-migration losers are CA/NY/IL/NJ/MA
(high-cost states), the gainers FL/TX/AZ/NC/SC (Sun Belt).

## Honest scope

- **Census now requires `CENSUS_API_KEY`** for ACS/PEP query endpoints. BLS is keyless.
- **PEP migration is clean-API only for 2010–2019.** 2020–2024 (bulk state-components
  CSV) and 2000–2009 (intercensal) are a documented fast-follow.
- **Health columns are v2** — cause-specific mortality (CDC WONDER, ~1999+), HIV
  (CDC AtlasPlus, 2008+), COVID/flu/RSV (CDC NHSN, 2020+) — the parts that make
  the disease×economics×migration question answerable, reusing fwh_health plumbing.
- Country-level → **state-level panel** is deliberate: analyzing the US as one
  50-year time series is a spurious-trend trap; 50 states × years with state/year
  fixed effects is where inference becomes real.

## Workflow

```
uspanel.workflows.BuildUSStatePanel
  └─ uspanel.data.BuildPanel   # fetch ACS + BLS + PEP → join → CSV/JSON + coverage
```

Output → `cache/uspanel/output/us_state_panel.{csv,json}` (MinIO on the fleet).

## Run

```bash
pip install -e .                       # requests only
export CENSUS_API_KEY=…                 # ACS/PEP; BLS needs no key
python -m pytest tests/ -q             # offline (sources mocked)
```
