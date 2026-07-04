"""Backend-aware paths for the uspanel cache + outputs.

On the fleet (``FW_STORAGE=s3`` / ``FW_DATA_ROOT=s3://afl-cache``) artifacts —
the cached UCDP aggregate + world geometry + the rendered map HTML — land in the
shared MinIO object store. A thin wrapper over ``facetwork.runtime.storage`` (the
same shape census-us / save-earth use), so terminal use and fleet runs share one
cache rooted at ``$FW_DATA_ROOT/cache/uspanel/``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from typing import IO

from facetwork.config import get_output_base
from facetwork.runtime import storage as _fws


def is_remote(path: str) -> bool:
    return "://" in (path or "")


def _data_root() -> str:
    return os.environ.get("FW_DATA_ROOT") or get_output_base()


def join(*parts: str) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    base = parts[0].rstrip("/")
    rest = [p.strip("/") for p in parts[1:]]
    return "/".join([base, *[p for p in rest if p]])


def cache_root() -> str:
    ov = os.environ.get("FW_USPANEL_CACHE_DIR")
    if ov:
        return ov
    r = _data_root()
    return join(r, "cache", "uspanel", "cache") if is_remote(r) else join(r, "uspanel-cache")


def output_root() -> str:
    ov = os.environ.get("FW_USPANEL_OUTPUT_DIR")
    if ov:
        return ov
    r = _data_root()
    return join(r, "cache", "uspanel", "output") if is_remote(r) else join(r, "uspanel-output")


def exists(path: str) -> bool:
    return _fws.get_storage_backend(path).exists(path)


def localize(path: str) -> str:
    if not is_remote(path):
        return path
    return _fws.localize(path)


def open_read(path: str, mode: str = "r", **kw) -> IO:
    return open(localize(path), mode, **kw)


@contextlib.contextmanager
def open_write(path: str, mode: str = "w", **kw) -> Iterator[IO]:
    if not is_remote(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        f = open(path, mode, **kw)
        try:
            yield f
        finally:
            f.close()
        return

    fd, tmp = tempfile.mkstemp(suffix="_" + os.path.basename(path))
    os.close(fd)
    f = open(tmp, mode, **kw)
    try:
        yield f
        f.close()
        with open(tmp, "rb") as src, _fws.get_storage_backend(path).open(path, "wb") as dst:
            dst.write(src.read())
    finally:
        if not f.closed:
            f.close()
        try:
            os.unlink(tmp)
        except OSError:
            pass
