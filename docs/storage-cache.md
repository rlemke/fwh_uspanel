# Storage & Cache — backend-aware paths, local vs MinIO

**Core:** `src/uspanel/storage.py` (imported as `cstore`) ·
**Used by:** `src/uspanel/_lib.py`, `src/uspanel/analysis.py` ·
**Tests:** `tests/test_uspanel.py` (the `local_storage` fixture pins `FW_STORAGE=local`)

## Overview

Every read/write in the domain goes through `src/uspanel/storage.py` — a thin
backend-aware wrapper over `facetwork.runtime.storage` (the same shape census-us /
save-earth use). It gives the panel one cache/output layout that works identically in
tests (a local `tmp_path`) and on the fleet (shared MinIO/S3), so handler code never
branches on the backend. This is infrastructure, not a facet — it underpins
[panel-build](panel-build.md) and [findings-analysis](findings-analysis.md).

## How it works

`storage.py` resolves a **data root** and derives two namespaces under it:

- `_data_root()` = `$FW_DATA_ROOT` or `facetwork.config.get_output_base()`.
- `cache_root()` — `$FW_USPANEL_CACHE_DIR` override, else remote
  `<root>/cache/uspanel/cache`, else local `<root>/uspanel-cache`.
- `output_root()` — `$FW_USPANEL_OUTPUT_DIR` override, else remote
  `<root>/cache/uspanel/output`, else local `<root>/uspanel-output`.

`is_remote(path)` keys on `"://"` in the path. Reads go through `localize()` (a
no-op locally; `facetwork.runtime.storage.localize` pulls a remote object to a local
temp for reading). Writes use a context manager `open_write(path, mode)`:

- **Local** — `os.makedirs` the parent, then a plain file handle.
- **Remote** — write to a `tempfile.mkstemp` scratch file, then on close **finalize**
  by streaming the temp bytes into `get_storage_backend(path).open(path, "wb")`. This
  is the object-store "no partial writes" discipline: stage locally, commit whole.

`exists()` / `join()` round out the helpers (`join` normalizes slashes so
`join(root, "cache", "uspanel")` works for both `s3://…` and `/local/…`).

## Fan-out

Not applicable — this is a synchronous per-process I/O layer. It carries **portable
`s3://` URIs** in the panel result paths, which is what *lets* a multi-server fleet
share one cache without shared disk (any runner resolves the same URI), but storage
itself does no distribution.

## Data & fields

Not a data source. The concrete paths it produces:

| Artifact | Root | Local path | Fleet path |
|---|---|---|---|
| Panel CSV | `output_root()` | `uspanel-output/us_state_panel.csv` | `s3://afl-cache/cache/uspanel/output/us_state_panel.csv` |
| Panel JSON | `output_root()` | `uspanel-output/us_state_panel.json` | `.../output/us_state_panel.json` |
| Findings page | `output_root()` | `uspanel-output/findings/index.html` | `.../output/findings/index.html` |
| BLS series cache | `cache_root()` | `uspanel-cache/bls-unemployment.json` | `s3://afl-cache/cache/uspanel/cache/bls-unemployment.json` |
| SEC HQ snapshot | `cache_root()` | `uspanel-cache/hq-<year>.json` | `.../cache/hq-<year>.json` |

(Assumes `FW_DATA_ROOT=s3://afl-cache` on the fleet; the `cache/uspanel/` prefix is
literal in `storage.py`.)

## External libraries / binaries

- **`facetwork.runtime.storage`** — `get_storage_backend`, `localize`; the actual
  S3/MinIO/local backend selection.
- **`facetwork.config.get_output_base`** — default root when `FW_DATA_ROOT` is unset.
- **stdlib** — `os`, `tempfile`, `contextlib`. No third-party deps, no binaries.

## Cache / output

This spec *is* the cache/output layer. Two namespaces:

- **`cache/uspanel/cache`** (`cache_root`) — per-source reuse: `bls-unemployment.json`
  (reused when a BLS live fetch is empty) and `hq-<year>.json` (immutable past-quarter
  DERA snapshots). See [data-sources](data-sources.md).
- **`cache/uspanel/output`** (`output_root`) — the durable artifacts (CSV, JSON,
  findings page).

## Gotchas & notes

- **Stale module docstring.** `storage.py`'s docstring describes "the cached UCDP
  aggregate + world geometry + the rendered map HTML" — copy-pasted from the
  `fwh_conflict` domain. This domain caches **no** UCDP data or map HTML; the code is
  correct and domain-agnostic, only the prose is wrong. Do not trust the docstring's
  artifact list; trust `cache_root()` / `output_root()`.
- **Remote writes are stage-then-finalize.** A crash mid-write leaves the temp file,
  not a truncated object — but also means the object appears only on clean close. Keep
  scratch local (the temp dir) as the deployment docs require.
- **Env overrides are absolute wins.** `FW_USPANEL_CACHE_DIR` /
  `FW_USPANEL_OUTPUT_DIR` bypass the root/remote logic entirely — handy for pinning a
  test or a one-off local run.
- **`localize` on every remote read** means large objects are pulled to temp before
  reading; fine for these small JSON/CSV artifacts, worth remembering if the panel
  ever grows.

## Related specs

- [panel-build](panel-build.md) — writes the CSV/JSON via this layer.
- [data-sources](data-sources.md) — the BLS + DERA per-source caches live here.
- [findings-analysis](findings-analysis.md) — writes the HTML page via this layer.
