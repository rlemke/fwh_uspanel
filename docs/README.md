# fwh_uspanel — Feature Specifications

This directory holds one **spec per feature** of the US state × year panel domain.
Each document follows a common shape ([`SPEC_TEMPLATE.md`](SPEC_TEMPLATE.md)) and
states, for that feature: how it works, whether it **fans out** (this domain is
deliberately single-task), which public **sources** and panel **columns** it touches,
the **external libraries** it relies on, its **facets & workflows**, and its
**cache/output**. Claims are grounded in the FFL `/** … */` docstrings and the
`_lib.py` / `analysis.py` / `storage.py` source — the source of truth for each facet
remains its FFL docstring; these specs are the feature-level narrative over them.

**Start here:** [**Panel Build**](panel-build.md) — the flagship feature. It fetches
eight public sources, outer-joins them on `(state, year)`, and caches the tidy
CSV/JSON panel with a coverage report; everything else builds on it.

## Core pipeline

| Spec | What it covers |
|------|----------------|
| [panel-build.md](panel-build.md) | **Flagship.** `BuildPanel`: fetch eight sources -> outer-join on `(state, year)` -> tidy CSV + JSON + per-column coverage report; the ragged 51-state panel and the `BuildUSStatePanel` workflow. |
| [findings-analysis.md](findings-analysis.md) | `BuildFindings`: within-state fixed-effects vs naive levels, first-differences, a state-clustered bootstrap of 15 hypotheses, rendered to a self-contained HTML page. |

## Data ingest

| Spec | What it covers |
|------|----------------|
| [data-sources.md](data-sources.md) | The eight public feeds (Census ACS, BLS LAUS, Census PEP, NCHS mortality, CDC AtlasPlus HIV, CDC COVID, county election returns, SEC DERA HQs) — endpoints, series IDs, coverage windows, key requirements, and per-source quirks. |

## Infrastructure

| Spec | What it covers |
|------|----------------|
| [storage-cache.md](storage-cache.md) | The backend-aware storage layer: `cache_root` / `output_root`, local vs MinIO/S3, stage-then-finalize remote writes, the BLS + DERA per-source caches. |
| [domain-handlers.md](domain-handlers.md) | Domain registration (`facetwork.domains` entry point, `DomainPackage`), the `_DISPATCH` map, RegistryRunner vs poller wiring, task-list routing. |

---

*See also the repo [`README.md`](../README.md) (columns, sources, honest scope) and
the FFL definitions in [`src/uspanel/ffl/uspanel.ffl`](../src/uspanel/ffl/uspanel.ffl).
The live/queryable interface is the MCP `fw_capabilities` / `fw_describe_handler`
tools. This domain has no `CLAUDE.md` or `tools/` dir — the FFL docstrings + these
specs are the contract.*
