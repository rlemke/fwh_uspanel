# Findings Analysis — within-state FE + clustered bootstrap to an HTML page

**Namespace:** `uspanel.data` (facet `BuildFindings`) · `uspanel.workflows` (workflow `BuildUSPanelFindings`) ·
**FFL:** `src/uspanel/ffl/uspanel.ffl` ·
**Handler:** `src/uspanel/handlers/uspanel_handlers.py` -> `handle_build_findings` ·
**Core:** `src/uspanel/analysis.py` -> `analyze()`, `render_findings_html()`, `build_findings()` ·
**Tests:** `tests/test_uspanel.py` -> `test_analyze_and_render_on_synthetic_panel`

## Overview

`BuildFindings` is the analysis layer on top of the panel: it answers **"what moves
people between US states, and does that survive a serious test?"** It contrasts naive
cross-state **level** correlations against **within-state** signal (two-way state+year
fixed effects and first differences), tests a fixed set of hypotheses with a
**state-clustered bootstrap** confidence interval, and renders a self-contained
results HTML page. Everything is computed live from the cached panel — **no
hardcoded numbers** — so the published page always reflects the current data.

The headline (2010-2017 overlap): interstate migration tracks the **economy**
(unemployment pushes people out a year later; income pulls them in) and shows **no
robust link** to disease burden or partisan lean; the flashy raw `income~cancer`
correlation collapses to about 0 within states — the spurious-trend lesson in one line.

## How it works

`build_findings(*, force=False)` (`analysis.py`) is **panel -> analyze -> render ->
write**:

1. **Ensure the panel** — calls `build_panel(force=force)` (see
   [panel-build](panel-build.md)) and reads back `us_state_panel.json`.
2. **`analyze(rows)`** — the statistics core:
   - Filters to `ANALYSIS_YEARS = 2010-2017` and keeps only **complete-case** rows
     (every one of the 12 `VARS` non-NaN). `_row_vars` derives the analysis variables
     (e.g. `dom_mig`/`intl_mig` per-1000 population, `log_inc = log(median income)`).
   - **Two-way demeaning** (`twoway_demean`) removes state and year means iteratively
     (3 passes) -> within-state, within-year residuals (fixed effects).
   - **Levels-vs-within demo** — for four illustrative pairs, `_corr` on raw `X` vs on
     demeaned `D` (this is the `income~cancer` collapse).
   - **FE correlation matrix** — `_corr(D[a], D[b])` over all 12x12 variable pairs.
   - **First differences** (`first_diff`) — consecutive-year delta within each state.
   - **State-clustered bootstrap** (`clustered_boot`, `B=3000`) — resamples the
     **states** (not the rows), so the panel structure is respected; returns `r` and a
     95% percentile CI. A deterministic `rng = default_rng(12345)` makes the page
     reproducible.
   - Runs the 15 `HYPOTHESES` (each `(x, y, lag, description)`); a test **holds** iff
     its 95% CI excludes 0.
3. **`render_findings_html(res)`** — builds a single-file HTML page: "what holds up"
   cards, a levels-vs-within table, the FE correlation heatmap (blue = positive, red =
   negative), the full hypothesis table, and honest caveats (ecological vs individual,
   short window, multiple-comparison, Alaska-missing).
4. **Write** — to `output_root()/findings/index.html`.

The workflow `uspanel.workflows.BuildUSPanelFindings(force)` wraps `BuildFindings`
and yields `{status="completed", html_path, n_rows}`.

## Fan-out

**Single-task — no fan-out.** All the math is in-process numpy over roughly 400 rows;
`clustered_boot` runs `B=3000` resamples locally. There is nothing to distribute.

## Data & fields

Reads **no external source** — it consumes the cached panel JSON only. The 12 analysis
variables (`VARS`, with `VAR_LABEL` display names): `unemp`, `dom_mig`, `intl_mig`,
`foreign%`, `log_inc`, `aid`, `allcause`, `cancer`, `heart`, `hiv`, `dem_share`,
`hq_net` — each mapped from a panel column by `_row_vars`. The 15 `HYPOTHESES` include
the economic-push (`unemp->dom_mig`, lag 1yr), economic-pull (`log_inc->dom_mig`),
health-vs-migration nulls, the "fleeing blue states" check (`dem_share->dom_mig`), the
"red-state welfare" check (`aid->dem_share`), and "do people follow companies?"
(`hq_net->dom_mig`).

## External libraries / binaries

- **`numpy`** (pip, `>=1.24` in pyproject) — all correlation / demeaning / bootstrap
  math.
- **stdlib** — `math`, `json`, `html.escape` (HTML safety), `datetime` (page
  timestamp).
- **No plotting library** — the heatmap and cards are hand-rendered inline HTML/CSS
  (self-contained page, no external assets). No binaries.

## Facets & workflows

| Facet / workflow | Kind | Effect / Cost / Timeout | Purpose (from FFL docstring) |
|---|---|---|---|
| `uspanel.data.BuildFindings(force=false) => (html_path, n_rows, n_states)` | **event** | `Effect(io)` · `Cost(cheap)` · `Timeout(15min)` | "Analyze the cached panel (levels-vs-within, FE correlation matrix, state-clustered bootstrap of the key hypotheses) and render the self-contained findings HTML page." |
| `uspanel.workflows.BuildUSPanelFindings(force=false) => (status, html_path, n_rows)` | workflow | — | "Build the published findings page from the US state panel." |

Note the mixins: `BuildFindings` is marked `Effect(io)` / `Cost(cheap)` (it writes a
page from cached data), whereas `BuildPanel` is `Effect(external)` / `Cost(moderate)`
(it hits eight live APIs) — the analysis is the cheap, local half of the pipeline.
`handle_build_findings` imports `build_findings` lazily and returns `{html_path,
n_rows, n_states}`, logging a success/error `_step_log` line.

## Cache / output

- **Output:** `findings/index.html` under `storage.output_root()` — local
  `uspanel-output/findings/` or, on the fleet, `s3://afl-cache/cache/uspanel/output/
  findings/index.html` (MinIO). Fully self-contained (inline CSS, no external assets),
  so it can be published as-is.
- **Input cache:** none of its own — it depends on the panel JSON produced by
  [panel-build](panel-build.md).

## Gotchas & notes

- **Complete-case only.** `analyze` keeps a row only if **all 12** `VARS` are present,
  so the effective analysis window is the narrow overlap where econ + migration +
  health + politics + HQ coexist (README: 2010-2017, ~400 state-years). Sparse live
  coverage can shrink this below the `< len(VARS)+2` guard, which returns an
  empty-result page rather than crashing.
- **Reproducible by seed.** The bootstrap RNG is seeded (`12345`); re-running on the
  same panel yields the identical page — intentional, so the published numbers are
  stable.
- **State-clustered, not row-bootstrapped** — resampling rows would understate
  uncertainty by ignoring within-state correlation. The CI is deliberately
  conservative (resample the 51 units).
- **Ecological, lagged, short window.** The page's own caveat box is load-bearing:
  this is state-level (not individual), a lagged correlation is not proof of cause, the
  window is one short recovery period, and several pairs are tested (multiple
  comparisons). Do not quote a "holds" result as causal.
- **Alaska drops out** wherever `dem_share` is missing (no county election source
  2008-2016), so the effective unit count can be 50, not 51 — surfaced in the caveat.
- **COVID is in the panel but not this analysis** — it starts 2020, outside the
  2010-2017 window.

## Related specs

- [panel-build](panel-build.md) — produces the panel JSON this feature analyzes.
- [data-sources](data-sources.md) — where the analysis columns originate.
- [storage-cache](storage-cache.md) — where the results page is written.
- [domain-handlers](domain-handlers.md) — how `BuildFindings` is registered/dispatched.
