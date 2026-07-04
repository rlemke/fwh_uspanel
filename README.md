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
| `mortality_all`, `cancer_death_rate`, `heart_death_rate`, `flu_pneumonia_death_rate`, `diabetes_death_rate`, `stroke_death_rate` | NCHS **Leading Causes of Death** by state (Socrata `bi63-dtpu`), age-adjusted per 100k | 1999–2017 |

→ **1,326 rows** (50 states + DC × 1999–2024), a deliberately **ragged panel**
(columns span different years — downstream analysis must handle the gaps). The
**analyzable overlap** where economics + migration + health + demographics all
coexist is **2010–2017 (408 rows)**.

The data checks out: 2019's biggest domestic-migration losers are CA/NY/IL/NJ/MA
(high-cost states), the gainers FL/TX/AZ/NC/SC (Sun Belt); and WV/MS (poorest
states) top the all-cause, cancer, and heart-disease death rates while HI/CA sit
lowest — the known health-disparity pattern.

## Honest scope

- **Census now requires `CENSUS_API_KEY`** for ACS/PEP query endpoints. BLS is keyless.
- **PEP migration is clean-API only for 2010–2019.** 2020–2024 (bulk state-components
  CSV) and 2000–2009 (intercensal) are a documented fast-follow.
- **Health columns (v2, added)** — NCHS age-adjusted death rates by state, 1999–2017
  (cancer, heart, influenza/pneumonia, diabetes, stroke, all-cause). Still open:
  **HIV** (CDC AtlasPlus, 2008+ — only a finicky undocumented backend, no clean
  Socrata) and **COVID** (CDC's by-state Socrata was retired; 2020+ is off the
  2010–2017 analyzable window anyway) — both targeted fast-follows.
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
