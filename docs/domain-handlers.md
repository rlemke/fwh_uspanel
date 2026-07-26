# Domain Registration & Handler Wiring

**Package entry:** `src/uspanel/__init__.py` (`domain`) ·
**Handlers:** `src/uspanel/handlers/{__init__,uspanel_handlers}.py` ·
**FFL:** `src/uspanel/ffl/uspanel.ffl` ·
**Entry point:** `pyproject.toml` -> `[project.entry-points."facetwork.domains"] uspanel = "uspanel:domain"`

## Overview

This spec documents how the domain plugs into Facetwork — how the two FFL event
facets are discovered, registered, and dispatched to Python. It is the "wiring"
counterpart to the two capability specs ([panel-build](panel-build.md),
[findings-analysis](findings-analysis.md)); a maintainer debugging "why isn't my
runner claiming `uspanel.data.*`" starts here.

## How it works

1. **Discovery** — `pyproject.toml` declares the `facetwork.domains` entry point
   `uspanel = "uspanel:domain"`. `fw runner start --domain uspanel` and `fw ffl seed`
   import that object.
2. **`domain`** (`__init__.py`) is a `DomainPackage(name="uspanel",
   ffl_dir=<pkg>/ffl, register_handlers=register_all_registry_handlers)`. `ffl_dir`
   points at `src/uspanel/ffl/` (the single `uspanel.ffl` with both namespaces), so
   seeding loads the facet/workflow definitions.
3. **Registration** — `handlers/__init__.py` exposes `register_all_registry_handlers`
   (RegistryRunner) and `register_all_handlers` (AgentPoller). Both delegate to
   `uspanel_handlers.py`:
   - `register_handlers(runner)` calls `runner.register_handler(facet_name, module_uri=
     file://…/uspanel_handlers.py, entrypoint="handle")` for each dispatch key.
   - `register_poller(poller)` calls `poller.register(facet_name, handler)`.
4. **Dispatch** — `uspanel_handlers.handle(payload)` reads `payload["_facet_name"]`
   and looks it up in `_DISPATCH`:

   | Facet | Handler | Delegates to |
   |---|---|---|
   | `uspanel.data.BuildPanel` | `handle_build_panel` | `_lib.build_panel` |
   | `uspanel.data.BuildFindings` | `handle_build_findings` | `analysis.build_findings` |

   An unknown facet raises `ValueError(f"Unknown facet: {facet}")` — an explicit
   failure, not a silent default.

The handlers are **thin adapters**: unpack params, call the core function, emit a
`_step_log` success/error line, return the FFL return fields, and re-raise on
exception. All real work lives in `_lib.py` / `analysis.py`.

## Fan-out

Not applicable — registration/dispatch is per-runner plumbing. Both facets are
single-task (see their specs). Routing follows the namespace: tasks for
`uspanel.data.*` carry task-list `uspanel`, and a runner that loaded these handlers
polls that namespace (standard Facetwork task-list routing; nothing domain-specific).

## Data & fields

No data sources. The only "fields" here are the dispatch keys (the two qualified facet
names) and the params each handler reads: both read `force` and the injected
`_step_log`; `handle` reads `_facet_name`.

## External libraries / binaries

- **`facetwork.domains.DomainPackage`** — the plugin contract.
- Handlers import `.._lib.build_panel` (eager) and `..analysis.build_findings`
  (lazy, inside `handle_build_findings`, so importing the handler module does not pull
  numpy until findings actually run).
- No binaries.

## Facets & workflows

Both event facets are declared in `uspanel.data`; the two workflows in
`uspanel.workflows` wrap them. Full signatures live in the capability specs
([panel-build](panel-build.md), [findings-analysis](findings-analysis.md)). This
domain registers **exactly two** handlers — there are no stub/no-op facets and no
FFL-only facets left unserved (contrast fwh_osm's `boundaries`, where the handler
module registers nothing): every facet in the FFL has a live handler in `_DISPATCH`.

## Cache / output

None of its own. Registration writes nothing; the handlers' artifacts are covered by
[storage-cache](storage-cache.md).

## Gotchas & notes

- **`__init__.py`'s `register_all_registry_handlers` is the wired path.** `DomainPackage`
  is constructed with `register_handlers=register_all_registry_handlers` (the
  RegistryRunner entry). `register_all_handlers` (the poller variant) exists but is not
  referenced by `DomainPackage` — it is there for an AgentPoller deployment.
- **`MAPS = "uspanel.data"`** in `uspanel_handlers.py` is just the namespace prefix
  string used to build the two dispatch keys — the name is a carryover convention (map
  domains use it), not a claim that this domain renders maps.
- **No `runner_env`** is set on `DomainPackage`, so the domain inherits the default
  runner resilience timeouts. `BuildPanel` declares `Timeout(minutes=15)` in FFL; a
  slow all-source fetch should complete well within that.
- **No `CLAUDE.md` or `tools/` dir** in this repo — the FFL docstrings + `README.md` +
  these specs are the contract; there is no per-domain CLI shim (unlike osm/noaa).

## Related specs

- [panel-build](panel-build.md) — the `BuildPanel` facet this dispatches.
- [findings-analysis](findings-analysis.md) — the `BuildFindings` facet this dispatches.
- [storage-cache](storage-cache.md) — where the dispatched handlers write.
