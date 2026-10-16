"""BLS unemployment from the LAUS bulk file, and a partial API fetch never shrinks the cache.

2026-10-16: the keyless API quota ran out mid-fetch, only the first 25-series chunk
succeeded, and saving that OVERWROTE a 51-state cache with 25 states; the findings
page silently dropped from 50 states to 24.
"""
from __future__ import annotations

import pytest


def _line(series: str, year: int, period: str, value: str) -> str:
    return f"{series:<30}\t{year}\t{period}\t{value:>12}\t"


def test_bulk_parse_keeps_only_complete_years_of_wanted_series():
    from uspanel import _lib

    ca = "LASST060000000000003"
    lines = ["series_id\tyear\tperiod\tvalue\tfootnote_codes"]
    lines += [_line(ca, 2015, f"M{m:02d}", "6.0" if m <= 6 else "6.44") for m in range(1, 13)]
    lines.append(_line(ca, 2015, "M13", "99.0"))                     # annual row, ignored
    lines += [_line(ca, 2024, f"M{m:02d}", "5.0") for m in range(1, 7)]  # partial year
    lines += [_line("LASST060000000000004", 2015, f"M{m:02d}", "1") for m in range(1, 13)]
    assert _lib.parse_bls_la_bulk("\n".join(lines)) == {("06", 2015): 6.22}


def test_partial_api_fetch_merges_into_the_cache(monkeypatch):
    from uspanel import _lib

    full = {(fp, 2015): 5.0 for fp in _lib.STATES}
    saved = {}
    monkeypatch.setattr(_lib, "_load_bls_cache", lambda: dict(full))
    monkeypatch.setattr(_lib, "_save_bls_cache", lambda d: saved.update(d))
    monkeypatch.setattr(_lib, "_fetch_bls_bulk", dict)
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    first = min(_lib.STATES)
    calls = iter([{"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": f"LASST{first}0000000000003",
         "data": [{"year": "2015", "period": "M13", "value": "7.5"}]}]}}])

    def post(*a, **k):
        payload = next(calls, {"status": "REQUEST_NOT_PROCESSED", "message": ["quota"]})
        return type("R", (), {"json": lambda self: payload})()

    monkeypatch.setattr(_lib.requests, "post", post)
    out = _lib.fetch_bls_unemployment()
    assert len(out) == len(full) and out[(first, 2015)] == 7.5
    assert len(saved) == len(full), "the cache must not shrink to the chunk that succeeded"


def test_bulk_needs_a_contact_user_agent(monkeypatch):
    from uspanel import _lib

    monkeypatch.delenv("BLS_USER_AGENT", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(_lib.requests, "get", lambda *a, **k: pytest.fail("fetched without a UA"))
    assert _lib._fetch_bls_bulk() == {}
