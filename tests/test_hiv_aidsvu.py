"""HIV diagnoses come from AIDSVu after CDC withdrew the AtlasPlus backend.

Every path under gis.cdc.gov/grasp/AtlasPlus/ has returned 404 since
2026-08-25; the fetcher swallowed that into an empty dict, the HIV column went
NaN in every row, and BuildFindings died with a bare "list index out of range"
while rendering an empty correlation matrix.
"""
from __future__ import annotations

import io

import pytest

openpyxl = pytest.importorskip("openpyxl")


def _workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["AIDSVu state new diagnoses"])
    ws.append([])
    ws.append([])
    ws.append(["GEO ID", "State", "Year", "New Diagnoses State Cases", "New Diagnoses MSM Cases"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_reads_the_total_column_keyed_by_padded_fips():
    from uspanel import _lib

    blob = _workbook([
        [6, "California", 2015, 5069, 3500],
        [4, "Arizona", 2015, "1,012", 600],   # GEO ID is FIPS (4), not an index
        [48, "Texas", 2015, None, 100],       # suppressed -> absent, never 0
        [None, "", None, None, None],
    ])
    assert _lib.parse_aidsvu_workbook(blob, 2015) == {("06", 2015): 5069, ("04", 2015): 1012}


def test_parse_fails_loudly_when_the_total_column_is_renamed():
    from uspanel import _lib

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["GEO ID", "State", "Year", "Cases"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(RuntimeError, match="New Diagnoses State Cases"):
        _lib.parse_aidsvu_workbook(buf.getvalue(), 2015)


def test_urls_are_scraped_not_constructed(monkeypatch):
    from uspanel import _lib

    html = ('<a href="https://aidsvu.org/wp-content/uploads/2025/08/'
            'AIDSVu_State_NewDX_2023-20250726.xlsx">x</a>'
            '<a href="https://aidsvu.org/x/2012_AIDSVu_State_PrEP_6.27.19.xlsx">y</a>')
    monkeypatch.setattr(_lib.requests, "get",
                        lambda *a, **k: type("R", (), {"text": html})())
    assert _lib._aidsvu_state_newdx_urls() == {
        2023: "https://aidsvu.org/wp-content/uploads/2025/08/AIDSVu_State_NewDX_2023-20250726.xlsx"}


def test_findings_name_the_missing_variable_instead_of_crashing(monkeypatch):
    from uspanel import analysis

    rows = [{"state": s, "year": y, "unemployment_rate": 5.0} for s in ("06", "48") for y in range(2010, 2018)]
    monkeypatch.setattr(analysis, "build_panel", lambda force=False: type("P", (), {"json_path": "p"})())

    class _F(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import json
    monkeypatch.setattr(analysis.cstore, "open_read", lambda p: _F(json.dumps({"rows": rows})))
    with pytest.raises(RuntimeError, match=r"No data at all for: .*hiv"):
        analysis.build_findings()
