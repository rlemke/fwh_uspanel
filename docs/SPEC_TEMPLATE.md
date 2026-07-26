<!-- SPEC TEMPLATE — every docs/<feature>.md follows this shape so the set reads
consistently. Delete this comment in real specs. Keep sections in this order;
omit a section only if it genuinely does not apply (say so in one line rather
than dropping the heading silently). Ground every claim in the actual FFL
docstrings / handler code / _lib.py source — do not invent behaviour. This is a
tabular data-panel domain, so the OSM template's "Filtering & attributes" section
is renamed "Data & fields" (which sources, which columns, which coverage window). -->

# <Feature Name>

**Namespace(s):** `uspanel.<ns>` · **FFL:** `src/uspanel/ffl/uspanel.ffl` ·
**Handlers:** `src/uspanel/handlers/uspanel_handlers.py` ·
**Core:** `src/uspanel/{_lib,analysis,storage}.py`

## Overview
One or two paragraphs: what this feature is for, the request it answers, and where
it sits in the pipeline (fetch → align → cache → analyze → render).

## How it works
The algorithm / data flow, step by step. Name the concrete functions and the shape
of the data at each hop (raw JSON → `{(fips, year): dict}` → tidy rows → CSV/JSON →
findings HTML). If there is a split (fetch vs analyze), say so.

## Fan-out
Does it fan out across the fleet? This domain is deliberately **single-task** — one
`BuildPanel` / `BuildFindings` run assembles the whole 51-state panel in-process,
because the sources are small national tables and the outer-join needs every source
in one place. Say "single-task — no fan-out" and why, or note the state-loop that
happens *inside* a single task.

## Data & fields
Which public source(s) this feature reads, the exact endpoint / table / series IDs,
the coverage window (year range), the API-key requirement, and the panel **columns**
it populates (`population`, `unemployment_rate`, `net_domestic_migration`,
`cancer_death_rate`, `dem_pres_share`, `corp_hq_net`, …). Name the join key
(`(fips, year)`) and any per-100k normalization. If the feature reads no external
source (pure transform/analysis), say so.

## External libraries / binaries
Every non-stdlib dependency this feature relies on and what for — e.g. `requests`
(all HTTP fetches), `numpy` (the analysis math), `facetwork.runtime.storage`
(backend-aware cache). Distinguish a **binary** dependency from a **pip** one. This
domain is pure-Python with no binaries.

## Facets & workflows
The event facets and workflows, with signatures and a one-line purpose taken from
the FFL `/** … */` docstrings. Mark event facets (need a handler) vs pure, and note
the `Effect` / `Cost` / `Timeout` mixins present on each.

## Cache / output
The cache namespace under `$FW_DATA_ROOT/cache/uspanel/` (or the local
`uspanel-cache` / `uspanel-output` roots), the cache type (per-source JSON caches vs
the final artifact), and the output artifact(s) + format (CSV / JSON / HTML page).
Note whether outputs go to local disk or MinIO/S3 on the fleet.

## Gotchas & notes
Known pitfalls, rate limits, key requirements, ragged-coverage caveats, or
non-obvious constraints (worth capturing anything a future maintainer would trip on).

## Related specs
Links to the specs this feature composes with or depends on.
