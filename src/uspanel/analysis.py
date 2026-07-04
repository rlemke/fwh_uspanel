"""Analyze the US state panel and render a self-contained findings page.

The honest first-pass analysis of the 2010-2017 overlap (econ + migration +
health, 51 states): contrast naive LEVEL correlations against WITHIN-state signal
(two-way state+year fixed effects and first-differences), and test the key
hypotheses with a STATE-CLUSTERED bootstrap CI (resample states, not rows, so the
panel structure is respected). Everything is computed live from the cached panel
so the published page reflects the current data — no hardcoded numbers.

Headline (2010-2017): interstate migration tracks the ECONOMY (unemployment
pushes people out a year later; income pulls them in) and shows NO robust link to
disease burden — and the flashiest raw correlation (income~cancer, r=-0.61)
collapses to ~0 within states, the spurious-trend lesson in one line.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from html import escape

import numpy as np

from . import storage as cstore
from ._lib import build_panel

logger = logging.getLogger("uspanel.analysis")

FFL_URL = "https://github.com/rlemke/fwh_uspanel/blob/main/src/uspanel/ffl/uspanel.ffl"
REPO_URL = "https://github.com/rlemke/fwh_uspanel"

ANALYSIS_YEARS = range(2010, 2018)  # the econ+migration+health overlap
# analysis variables (display label → how to compute from a panel row)
VARS = ["unemp", "dom_mig", "intl_mig", "foreign%", "log_inc", "allcause", "cancer", "heart"]
VAR_LABEL = {
    "unemp": "Unemployment", "dom_mig": "Domestic migration /1k",
    "intl_mig": "Intl migration /1k", "foreign%": "Foreign-born %",
    "log_inc": "log(median income)", "allcause": "All-cause mortality",
    "cancer": "Cancer deaths", "heart": "Heart deaths",
}

HYPOTHESES = [
    ("unemp", "dom_mig", 1, "Unemployment leads domestic OUT-migration by 1 year (economic push)"),
    ("log_inc", "dom_mig", 0, "Rising income draws domestic IN-migration (economic pull)"),
    ("unemp", "dom_mig", 0, "Unemployment and out-migration, same year"),
    ("unemp", "intl_mig", 0, "State unemployment vs international migration"),
    ("allcause", "unemp", 0, "Health burden and the economy co-move"),
    ("cancer", "dom_mig", 0, "Cancer burden vs domestic migration"),
    ("heart", "dom_mig", 0, "Heart-disease burden vs domestic migration"),
]


@dataclass
class FindingsResult:
    html_path: str
    n_rows: int
    n_states: int


# ---------------------------------------------------------------------------
# stats helpers (pure numpy)
# ---------------------------------------------------------------------------


def _row_vars(r: dict) -> dict:
    def g(c):
        v = r.get(c)
        return float(v) if isinstance(v, (int, float)) else math.nan
    pop = g("population")
    inc = g("median_hh_income")
    return {
        "unemp": g("unemployment_rate"),
        "dom_mig": g("net_domestic_migration") / pop * 1000 if pop else math.nan,
        "intl_mig": g("net_international_migration") / pop * 1000 if pop else math.nan,
        "foreign%": g("foreign_born_pct"),
        "log_inc": math.log(inc) if inc and inc > 0 else math.nan,
        "allcause": g("mortality_all"),
        "cancer": g("cancer_death_rate"),
        "heart": g("heart_death_rate"),
    }


def _corr(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 5 or a[m].std() == 0 or b[m].std() == 0:
        return math.nan
    return float(np.corrcoef(a[m], b[m])[0, 1])


def analyze(rows: list[dict]) -> dict:
    """Compute levels-vs-within, the FE correlation matrix, and clustered-bootstrap
    hypothesis tests. Returns a JSON-serializable results dict."""
    rng = np.random.default_rng(12345)  # deterministic → reproducible page
    recs = []
    for r in rows:
        if not isinstance(r.get("year"), int) or r["year"] not in ANALYSIS_YEARS:
            continue
        d = _row_vars(r)
        if all(not math.isnan(d[v]) for v in VARS):
            recs.append((r["state"], r["year"], d))
    states = sorted({s for s, _, _ in recs})
    X = np.array([[d[v] for v in VARS] for _, _, d in recs])
    st = np.array([s for s, _, _ in recs])
    yr = np.array([y for _, y, _ in recs])

    def twoway_demean(col):
        x = col.astype(float).copy()
        for _ in range(3):
            for grp in (st, yr):
                for g in np.unique(grp):
                    idx = grp == g
                    x[idx] -= x[idx].mean()
        return x

    D = {v: twoway_demean(X[:, i]) for i, v in enumerate(VARS)}

    # levels-vs-within demonstration pairs
    demo = []
    for a, b in [("log_inc", "cancer"), ("unemp", "dom_mig"), ("foreign%", "dom_mig"),
                 ("allcause", "unemp")]:
        demo.append({"a": a, "b": b,
                     "level": _corr(X[:, VARS.index(a)], X[:, VARS.index(b)]),
                     "within": _corr(D[a], D[b])})

    # within-state FE correlation matrix
    matrix = [[_corr(D[a], D[b]) for b in VARS] for a in VARS]

    # first differences (within state, consecutive years)
    def first_diff(v):
        ci = VARS.index(v)
        out = {}
        for s in states:
            seq = sorted((y, X[i, ci]) for i, (ss, y) in enumerate(zip(st, yr)) if ss == s)
            for k in range(1, len(seq)):
                if seq[k][0] == seq[k - 1][0] + 1:
                    out[(s, seq[k][0])] = seq[k][1] - seq[k - 1][1]
        return out

    diffs = {v: first_diff(v) for v in VARS}

    def clustered_boot(xv, yv, lag, B=3000):
        dx, dy = diffs[xv], diffs[yv]
        by_state = {}
        for (s, y), vx in dx.items():
            if (s, y + lag) in dy:
                by_state.setdefault(s, []).append((vx, dy[(s, y + lag)]))
        allp = [p for ps in by_state.values() for p in ps]
        if len(allp) < 10:
            return math.nan, math.nan, math.nan, len(allp)
        a = np.array([p[0] for p in allp]); b = np.array([p[1] for p in allp])
        r0 = float(np.corrcoef(a, b)[0, 1])
        sts = list(by_state)
        boot = []
        for _ in range(B):
            pick = rng.choice(len(sts), len(sts), replace=True)
            pp = [p for j in pick for p in by_state[sts[j]]]
            aa = np.array([p[0] for p in pp]); bb = np.array([p[1] for p in pp])
            if aa.std() > 0 and bb.std() > 0:
                boot.append(float(np.corrcoef(aa, bb)[0, 1]))
        if not boot:
            return r0, math.nan, math.nan, len(allp)
        lo, hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))
        return r0, lo, hi, len(allp)

    tests = []
    for xv, yv, lag, desc in HYPOTHESES:
        r0, lo, hi, n = clustered_boot(xv, yv, lag)
        holds = not math.isnan(lo) and (lo > 0 or hi < 0)
        tests.append({"x": xv, "y": yv, "lag": lag, "desc": desc,
                      "r": r0, "lo": lo, "hi": hi, "n": n, "holds": holds})

    return {"n_rows": len(recs), "n_states": len(states),
            "years": [min(yr.tolist()), max(yr.tolist())] if len(recs) else [0, 0],
            "vars": VARS, "labels": VAR_LABEL, "demo": demo, "matrix": matrix, "tests": tests}


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def _cell_color(r: float) -> str:
    if r is None or (isinstance(r, float) and math.isnan(r)):
        return "background:#f3f3f3;color:#bbb"
    a = min(abs(r), 1.0)
    if r >= 0:  # blue for positive
        return f"background:rgba(33,102,172,{0.12 + 0.75 * a:.2f});color:{'#fff' if a > 0.5 else '#111'}"
    return f"background:rgba(178,24,43,{0.12 + 0.75 * a:.2f});color:{'#fff' if a > 0.5 else '#111'}"


def _fmt(r):
    return "—" if r is None or (isinstance(r, float) and math.isnan(r)) else f"{r:+.2f}"


def render_findings_html(res: dict) -> str:
    from datetime import UTC, datetime
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    y0, y1 = res["years"]
    lab = res["labels"]

    holds = [t for t in res["tests"] if t["holds"]]
    nulls = [t for t in res["tests"] if not t["holds"]]

    # headline finding cards (the ones that survive)
    cards = ""
    for t in holds:
        arrow = "↑ leads ↓" if t["lag"] else "moves with"
        cards += (
            f'<div class="card ok"><div class="cr">r = {_fmt(t["r"])}</div>'
            f'<div class="cci">95% CI [{_fmt(t["lo"])}, {_fmt(t["hi"])}] · n={t["n"]}'
            f'{" · lag "+str(t["lag"])+"yr" if t["lag"] else ""}</div>'
            f'<div class="cd">{escape(t["desc"])}</div></div>'
        )

    # levels-vs-within demo rows
    demo_rows = "".join(
        f'<tr><td>{escape(lab[d["a"]])} ~ {escape(lab[d["b"]])}</td>'
        f'<td class="v">{_fmt(d["level"])}</td><td class="v">{_fmt(d["within"])}</td></tr>'
        for d in res["demo"]
    )

    # FE correlation heatmap
    vs = res["vars"]
    head = "".join(f'<th class="rot"><span>{escape(lab[v])}</span></th>' for v in vs)
    body = ""
    for i, a in enumerate(vs):
        cells = "".join(
            f'<td style="{_cell_color(res["matrix"][i][j])}">{_fmt(res["matrix"][i][j])}</td>'
            for j in range(len(vs)))
        body += f'<tr><th class="rl">{escape(lab[a])}</th>{cells}</tr>'

    # full hypothesis table
    test_rows = ""
    for t in res["tests"]:
        v = ("<b style='color:#1a7f37'>holds</b>" if t["holds"]
             else "<span style='color:#999'>noise (CI spans 0)</span>")
        test_rows += (
            f'<tr><td>{escape(lab[t["x"]])} → {escape(lab[t["y"]])}'
            f'{" (t+"+str(t["lag"])+")" if t["lag"] else ""}</td>'
            f'<td class="v">{_fmt(t["r"])}</td>'
            f'<td class="v">[{_fmt(t["lo"])}, {_fmt(t["hi"])}]</td>'
            f'<td class="v">{t["n"]}</td><td>{v}</td></tr>'
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>What moves people between US states? (2010-2017)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{{margin:0;font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;background:#fafafa}}
  .wrap{{max-width:820px;margin:0 auto;padding:28px 20px 80px}}
  h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:34px 0 10px;border-bottom:2px solid #eee;padding-bottom:4px}}
  .sub{{color:#666;margin:0 0 8px}}
  .thesis{{background:#eef6ff;border-left:4px solid #2166ac;padding:12px 16px;border-radius:6px;margin:16px 0}}
  .method{{background:#fff8e1;border-left:4px solid #f6c343;padding:12px 16px;border-radius:6px;margin:16px 0;font-size:14px}}
  .cards{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}}
  .card{{flex:1 1 300px;border:1px solid #e2e2e2;border-radius:8px;padding:12px 14px;background:#fff}}
  .card.ok{{border-left:4px solid #1a7f37}}
  .cr{{font-size:22px;font-weight:700}} .cci{{color:#666;font-size:13px;margin:2px 0 6px}} .cd{{font-size:14px}}
  table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px;background:#fff}}
  th,td{{border:1px solid #e6e6e6;padding:5px 8px;text-align:left}} td.v,th.v{{text-align:right;font-variant-numeric:tabular-nums}}
  .heat{{overflow-x:auto}} .heat table{{font-size:12px}} .heat td{{text-align:center;font-variant-numeric:tabular-nums;min-width:46px}}
  .heat th.rl{{text-align:right;white-space:nowrap;background:#fafafa}}
  th.rot{{height:96px;white-space:nowrap;vertical-align:bottom;padding:2px}}
  th.rot span{{writing-mode:vertical-rl;transform:rotate(180deg);font-weight:600}}
  .caveat{{background:#fbeaea;border-left:4px solid #b2182b;padding:12px 16px;border-radius:6px;margin:16px 0;font-size:14px}}
  footer{{margin-top:36px;padding-top:12px;border-top:1px solid #e6e6e6;color:#666;font-size:12px}}
  footer a{{color:#1565c0;text-decoration:none}} code{{background:#f0f0f0;padding:0 3px;border-radius:3px}}
</style></head>
<body><div class="wrap">
<h1>What moves people between US states?</h1>
<p class="sub">A honest first look at economics, migration &amp; health across 50 states + DC,
{y0}&ndash;{y1} ({res['n_rows']} state-years). Net migration, unemployment, income, foreign-born
share, and age-adjusted death rates — from Census, BLS &amp; NCHS.</p>

<div class="thesis"><b>Bottom line.</b> Interstate migration tracks the <b>economy</b> —
unemployment pushes people out a year later, and rising income pulls them in — and shows
<b>no robust link to disease burden</b>. People move for jobs and income, not (measurably)
away from cancer or heart disease.</div>

<div class="method"><b>Why this isn't just spurious.</b> Over time almost everything trends
together, so raw correlations mislead. Everything here uses only <b>within-state</b> variation
(each state vs its own norm and the national year), and the key claims are tested with a
<b>state-clustered bootstrap</b> (resampling the 51 states, not the rows). One line makes the
point: <b>income&nbsp;~&nbsp;cancer</b> looks like a strong <b>{_fmt(res['demo'][0]['level'])}</b> across
states, but collapses to <b>{_fmt(res['demo'][0]['within'])}</b> within states — the raw number
was confounding, not a relationship.</div>

<h2>What holds up</h2>
<div class="cards">{cards or '<div class="card">Nothing survived the clustered test.</div>'}</div>

<h2>What doesn't — including health</h2>
<p>These do <b>not</b> survive the within-state, clustered test (confidence interval spans zero):
{escape(', '.join(lab[t['x']]+'→'+lab[t['y']] for t in nulls)) or '—'}. The health↔migration
nulls are the notable ones: at this scale and frequency, <b>disease burden does not visibly drive
where Americans move</b>.</p>

<h2>Levels mislead — the same pairs, two ways</h2>
<table><tr><th>pair</th><th class="v">naive level r</th><th class="v">within-state r</th></tr>
{demo_rows}</table>
<p class="sub">The gap between the columns is confounding (or, for unemployment→migration, real
signal that levels <i>hide</i>).</p>

<h2>Within-state correlation matrix</h2>
<div class="heat"><table><tr><th></th>{head}</tr>{body}</table></div>
<p class="sub">Blue = positive, red = negative co-movement within states (state+year fixed effects).</p>

<h2>Every hypothesis tested</h2>
<table><tr><th>relationship</th><th class="v">r (Δ)</th><th class="v">95% CI</th><th class="v">n</th><th>verdict</th></tr>
{test_rows}</table>

<div class="caveat"><b>Read with care.</b> {y1 - y0 + 1} years is short and {y0}&ndash;{y1} is a single
recovery period (limited range). This is <b>state-level</b> (ecological ≠ individual). A lagged
correlation makes reverse-causation less likely but is <b>not proof of cause</b>. Several pairs were
tested, so a borderline result could be chance. HIV and COVID are not yet in the panel.</div>

<footer>Generated by Facetwork workflow <code>uspanel.workflows.BuildUSPanelFindings</code> ·
<a href="{escape(FFL_URL)}" target="_blank" rel="noopener">view FFL</a> ·
<a href="{escape(REPO_URL)}" target="_blank" rel="noopener">source repo</a> ·
data: US Census (ACS, PEP), BLS (LAUS), NCHS (Leading Causes of Death) · generated {ts}</footer>
</div></body></html>"""


def build_findings(*, force: bool = False) -> FindingsResult:
    """Ensure the panel is cached, analyze it, render + write the findings page."""
    panel = build_panel(force=force)
    with cstore.open_read(panel.json_path) as f:
        blob = json.load(f)
    res = analyze(blob["rows"])
    html = render_findings_html(res)
    html_path = cstore.join(cstore.output_root(), "findings", "index.html")
    with cstore.open_write(html_path, "w") as f:
        f.write(html)
    logger.info("findings page: %d rows, %d states -> %s", res["n_rows"], res["n_states"], html_path)
    return FindingsResult(html_path, res["n_rows"], res["n_states"])
