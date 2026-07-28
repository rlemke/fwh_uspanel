# fwh_uspanel

A Facetwork domain that builds a **reproducible US state × year data panel** — the
tidy, inspectable spine for a "what moves with what across US states over time"
analysis (economics, migration, and — in v2 — health).

`BuildPanel` fetches three clean public sources, outer-joins them on `(state,
year)`, and caches one tidy table as **CSV + JSON** with a per-column coverage
report. No charts or models yet — this is the dataset you look at *before*
deciding the analysis.

## FFL at a glance

The domain is driven from [FFL](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md),
Facetwork's workflow language. A step is `name = Facet(args)`; ordering comes from
**references**, not line order — here a `when` gate runs the analysis only once the
panel reports enough rows:

```ffl
namespace my.uspanel {

    use uspanel.data

    /** Build the panel, then the findings page — only if the panel is complete. */
    workflow OrderedPanelRun(force: Boolean = false, min_rows: Int = 500) => (status: String, html_path: String) andThen {

        panel = uspanel.data.BuildPanel(force = $.force) andThen when {
            case $.n_rows >= $$.min_rows => {
                findings = uspanel.data.BuildFindings(force = $$.force)
                yield OrderedPanelRun(status = "completed", html_path = findings.html_path)
            }
            case _ => {
                yield OrderedPanelRun(status = "panel_too_thin", html_path = "")
            }
        }
    }
}
```

```bash
fw ffl run --primary my.ffl --library src/uspanel/ffl/uspanel.ffl \
  --workflow my.uspanel.OrderedPanelRun
```

📖 **[docs/ffl-examples.md](docs/ffl-examples.md)** — the full example gallery:
the two-stage ingest/analysis split, why line order is not run order, call-time
mixins (timeout/retry), `catch` for a missing `CENSUS_API_KEY`, wrapping the
shipped workflows, and publishing the findings page. Every snippet there is
compile-checked.

## Feature specifications

Per-feature specs live in [`docs/`](docs/README.md) — how each feature works, what
it fetches, its facets/workflows, and its cache/output:

| Spec | What it covers |
|------|----------------|
| [Panel Build](docs/panel-build.md) | **Flagship.** `BuildPanel`: eight sources -> outer-join on `(state, year)` -> tidy CSV/JSON + coverage report. |
| [Findings Analysis](docs/findings-analysis.md) | `BuildFindings`: within-state fixed-effects vs levels, state-clustered bootstrap, findings HTML page. |
| [Data Sources](docs/data-sources.md) | The eight public feeds (ACS, BLS, PEP, NCHS, AtlasPlus HIV, CDC COVID, county elections, SEC DERA) — endpoints, coverage, quirks. |
| [Storage & Cache](docs/storage-cache.md) | Backend-aware paths (local vs MinIO/S3), the BLS + DERA per-source caches. |
| [Domain & Handlers](docs/domain-handlers.md) | `facetwork.domains` registration, dispatch wiring, task-list routing. |

Index: [`docs/README.md`](docs/README.md).

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
