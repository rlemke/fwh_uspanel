"""Offline tests for the US-panel build — the three source fetchers are mocked,
so no Census/BLS network is hit; the join, coverage report, and CSV/JSON output
are exercised end to end.
"""

from __future__ import annotations

import csv
import json

import pytest


@pytest.fixture()
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_STORAGE", "local")
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path))
    yield tmp_path


def _patch(monkeypatch):
    from uspanel import _lib
    monkeypatch.setattr(_lib, "fetch_acs", lambda: {
        ("06", 2015): {"population": 39144818.0, "median_hh_income": 64500.0, "foreign_born_pct": 27.3},
        ("48", 2015): {"population": 27469114.0, "median_hh_income": 55653.0, "foreign_born_pct": 17.0},
    })
    monkeypatch.setattr(_lib, "fetch_bls_unemployment", lambda: {
        ("06", 2015): 6.22, ("48", 2015): 4.47, ("06", 2024): 5.3,
    })
    monkeypatch.setattr(_lib, "fetch_pep_migration", lambda: {
        ("06", 2015): {"net_domestic_migration": -79938.0, "net_international_migration": 156870.0},
        ("48", 2015): {"net_domestic_migration": 172048.0, "net_international_migration": 117660.0},
    })


def test_build_panel_joins_and_covers(local_storage, monkeypatch):
    from uspanel import _lib
    _patch(monkeypatch)
    res = _lib.build_panel(force=True)

    # union of all (state, year) keys: CA/TX 2015 + CA 2024 = 3 rows
    assert res.n_rows == 3
    assert (res.year_min, res.year_max) == (2015, 2024)
    # coverage = non-null cells per column
    assert res.coverage["unemployment_rate"] == 3      # all three keys have it
    assert res.coverage["population"] == 2             # only the 2015 rows
    assert res.coverage["net_domestic_migration"] == 2

    rows = list(csv.DictReader(open(_lib.cstore.localize(res.csv_path))))
    ca15 = next(r for r in rows if r["state"] == "CA" and r["year"] == "2015")
    assert ca15["name"] == "California"
    assert float(ca15["foreign_born_pct"]) == 27.3
    assert float(ca15["net_domestic_migration"]) == -79938.0
    # the CA-2024 row has unemployment but blank population (ragged panel)
    ca24 = next(r for r in rows if r["state"] == "CA" and r["year"] == "2024")
    assert ca24["unemployment_rate"] == "5.3"
    assert ca24["population"] == ""


def test_json_sidecar_shape(local_storage, monkeypatch):
    from uspanel import _lib
    _patch(monkeypatch)
    res = _lib.build_panel(force=True)
    blob = json.load(open(_lib.cstore.localize(res.json_path)))
    assert blob["columns"][:4] == ["fips", "state", "name", "year"]
    assert set(_lib.COLUMNS).issubset(blob["columns"])
    assert blob["year_min"] == 2015 and blob["year_max"] == 2024


def test_num_handles_suppression_sentinels():
    from uspanel import _lib
    assert _lib._num("-666666666") is None      # ACS suppression sentinel
    assert _lib._num("") is None
    assert _lib._num("5.1") == 5.1
    assert _lib._num("-79938.0") == -79938.0    # legitimate negative (out-migration)
