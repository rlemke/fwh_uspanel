"""Event facet handlers for the uspanel domain — thin layers over ``_lib``."""

from __future__ import annotations

import os
from typing import Any

from .._lib import build_panel

MAPS = "uspanel.data"


def handle_build_panel(params: dict[str, Any]) -> dict[str, Any]:
    step_log = params.get("_step_log")
    try:
        res = build_panel(force=bool(params.get("force")))
        if step_log:
            step_log(f"BuildPanel: {res.n_rows} rows {res.year_min}-{res.year_max} "
                     f"-> {res.csv_path}", level="success")
        return {"csv_path": res.csv_path, "json_path": res.json_path,
                "n_rows": res.n_rows, "year_min": res.year_min, "year_max": res.year_max}
    except Exception as exc:
        if step_log:
            step_log(f"BuildPanel: {exc}", level="error")
        raise


_DISPATCH: dict[str, Any] = {f"{MAPS}.BuildPanel": handle_build_panel}


def handle(payload: dict) -> dict:
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet}")
    return handler(payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        runner.register_handler(facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}", entrypoint="handle")


def register_poller(poller) -> None:
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
