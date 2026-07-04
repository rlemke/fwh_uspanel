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
    monkeypatch.setattr(_lib, "fetch_nchs_mortality", lambda: {
        ("06", 2015): {"mortality_all": 619.9, "cancer_death_rate": 143.6, "heart_death_rate": 165.4},
        ("48", 2015): {"mortality_all": 741.0, "cancer_death_rate": 152.0, "heart_death_rate": 190.0},
    })
    monkeypatch.setattr(_lib, "fetch_hiv_diagnoses", lambda: {("06", 2015): 5069, ("48", 2015): 4512})
    monkeypatch.setattr(_lib, "fetch_covid_deaths", lambda: {("06", 2024): 9000})


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
    # health columns join alongside econ/migration on the same state-year
    assert float(ca15["cancer_death_rate"]) == 143.6
    assert float(ca15["mortality_all"]) == 619.9
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


def test_analyze_and_render_on_synthetic_panel():
    """analyze() runs on a small synthetic state panel and render produces HTML."""
    import math
    from uspanel import analysis

    # 6 states x 8 years, with a built-in unemployment->out-migration relationship
    rows = []
    for si, st in enumerate(["CA", "TX", "NY", "FL", "OH", "AZ"]):
        for yr in range(2010, 2018):
            u = 5 + si + 0.5 * (yr - 2010)
            rows.append({
                "state": st, "year": yr,
                "population": 10_000_000, "median_hh_income": 55000 + 500 * si,
                "foreign_born_pct": 10 + si, "unemployment_rate": u,
                # domestic migration falls as unemployment rises (the signal)
                "net_domestic_migration": int(-1000 * u + 200 * (si - 2)),
                "net_international_migration": 5000,
                "mortality_all": 700 + 10 * si, "cancer_death_rate": 150 + si,
                "heart_death_rate": 160 + si, "hiv_diagnosis_rate": 8 + si + 0.3 * (yr - 2010),
            })
    res = analysis.analyze(rows)
    assert res["n_rows"] == 48 and res["n_states"] == 6
    assert len(res["matrix"]) == len(analysis.VARS)
    # the demo table + at least one hypothesis test are present
    assert res["demo"] and res["tests"]
    html = analysis.render_findings_html(res)
    assert "<!doctype html>" in html and "Within-state correlation matrix" in html
