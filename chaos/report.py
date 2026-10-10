"""Self-contained HTML report for one run (no external assets; works offline).

    python -m chaos.run report [RUN_JSON]     regenerate from a saved run (default: latest)
"""

import json
from datetime import datetime
from html import escape
from pathlib import Path

from chaos.config import DATA_DIR
from chaos.db import ResultsDB

RUNS_DIR = DATA_DIR / "runs"
REPORTS_DIR = DATA_DIR / "reports"
LATEST = DATA_DIR / "report.html"


# ── data ─────────────────────────────────────────────────────────

def _series(run_id: str):
    """Per-second max of real data age and app-believed age, relative to injection."""
    db = ResultsDB()
    events = {t: ts for ts, t in db.query("SELECT ts, type FROM events WHERE run_id=? ORDER BY ts", (run_id,))}
    t0 = events.get("inject")
    if t0 is None:
        return [], None, None
    buckets = {}
    for ts, real, seen in db.query("SELECT ts, data_age_s, server_age_s FROM samples WHERE run_id=? "
                                   "AND phase IN ('before','during','after') ORDER BY ts", (run_id,)):
        b = buckets.setdefault(round(ts - t0), [None, None])
        if real is not None:
            b[0] = max(b[0] or 0, real)
        if seen is not None:
            b[1] = max(b[1] or 0, seen)
    rb = events.get("rollback")
    return sorted((k, *v) for k, v in buckets.items()), 0.0, (rb - t0 if rb else None)


def _latest_run(kind: str) -> dict | None:
    files = sorted(RUNS_DIR.glob(f"{kind}-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


# ── formatting ───────────────────────────────────────────────────

def _f(v, unit="", nd=2):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        v = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return f"{v}{unit}"


def _alerts(d: dict, phase: str) -> int:
    counts = (d.get("app_alerts") or {}).get(phase) or {}
    return sum(v for k, v in counts.items() if k != "none")


def _chart(series, inj, rb, thr) -> str:
    if not series:
        return "<p class='muted'>No time series available for this run.</p>"
    W, H, L, R, T, B = 920, 320, 52, 16, 18, 40
    xs = [p[0] for p in series]
    x0, x1 = min(xs), max(xs)
    ymax = max([v for p in series for v in p[1:] if v is not None] + [thr or 0]) * 1.12 or 1
    X = lambda x: L + (x - x0) / ((x1 - x0) or 1) * (W - L - R)
    Y = lambda y: T + (1 - y / ymax) * (H - T - B)

    def line(idx, cls):
        pts = " ".join(f"{X(p[0]):.1f},{Y(p[idx]):.1f}" for p in series if p[idx] is not None)
        return f"<polyline class='{cls}' points='{pts}'/>"

    step_y = next(s for s in (1, 2, 5, 10, 20) if ymax / s <= 8)
    grid = "".join(f"<line class='grid' x1='{L}' x2='{W - R}' y1='{Y(v):.1f}' y2='{Y(v):.1f}'/>"
                   f"<text class='tick' x='{L - 8}' y='{Y(v) + 4:.1f}' text-anchor='end'>{v}s</text>"
                   for v in range(0, int(ymax) + 1, step_y))
    xticks = "".join(f"<text class='tick' x='{X(v):.1f}' y='{H - B + 18}' text-anchor='middle'>{v:+d}s</text>"
                     for v in range((x0 // 10) * 10, x1 + 1, 10) if x0 <= v <= x1)
    band = (f"<rect class='band' x='{X(inj):.1f}' y='{T}' width='{X(rb) - X(inj):.1f}' height='{H - T - B}'/>"
            f"<text class='bandlabel' x='{X(inj) + 6:.1f}' y='{T + 14}'>delay injected</text>"
            if rb is not None else "")
    thr_line = (f"<line class='thr' x1='{L}' x2='{W - R}' y1='{Y(thr):.1f}' y2='{Y(thr):.1f}'/>"
                f"<text class='thrlabel' x='{W - R - 4}' y='{Y(thr) - 6:.1f}' text-anchor='end'>"
                f"guard stops at {_f(thr)}s</text>" if thr else "")
    return (f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Data age over time'>{band}{grid}{thr_line}"
            f"{xticks}<text class='tick' x='{(L + W) / 2}' y='{H - 4}' text-anchor='middle'>"
            f"seconds relative to injection</text>{line(1, 'real')}{line(2, 'seen')}</svg>"
            "<div class='legend'><span><i class='sw real'></i>Real data age (measured by us)</span>"
            "<span><i class='sw seen'></i>What the app believes (its own timestamp)</span></div>")


# ── page ─────────────────────────────────────────────────────────

def render(d: dict) -> str:
    exp, m = d["executed_plan"], d["metrics"]
    a = d.get("analysis") or {}
    plan = d.get("plan") or {}
    smap = d.get("map_summary") or {}
    series, inj, rb = _series(d["run_id"])
    during = m["during"]
    aborted = next((e for e in d["guard_events"] if e["type"] == "abort"), None)
    ended = d["guard_events"][-1] if d["guard_events"] else None
    gap = (during["data_age_s"]["mean"] or 0) - (during["server_age_s"]["mean"] or 0)
    E = lambda s: escape(str(s or ""))

    # Plain-language headline from measured numbers; the analyst's wording follows below it.
    n_alerts = _alerts(d, "during")
    if gap > 0.5:
        headline = (f"The app showed truck data that was really {_f(during['data_age_s']['mean'], ' s')} old "
                    f"while believing it was {_f(during['server_age_s']['mean'], ' s')} old, and raised "
                    f"{n_alerts} alert{'s' if n_alerts != 1 else ''}.")
    elif aborted:
        headline = f"The safety guard stopped the experiment by itself: {E(aborted['reason'])}."
    else:
        headline = "The app kept its data fresh during this experiment."
    analyst_note = (f"<p class='muted' style='font-size:14px;font-weight:400;margin-top:8px'>"
                    f"<b>Analyst ({'AI' if a.get('source', '').startswith('llm') else 'safe default'}):</b> "
                    f"{E(a.get('weakness'))}</p>") if a.get("weakness") else ""

    tiles = [
        ("Real data age", _f(during["data_age_s"]["mean"], " s"), f"avg during fault · max {_f(during['data_age_s']['max'], ' s')}", "bad"),
        ("App believed", _f(during["server_age_s"]["mean"], " s"), f"avg · blind to {_f(gap, ' s')}" if gap > 0.5 else "avg", "warn" if gap > 0.5 else ""),
        ("Alerts raised by app", str(_alerts(d, "during")), "during the fault", "bad" if _alerts(d, "during") == 0 and gap > 2 else ""),
        ("Failed requests", str(during["writes"]["failed"]), f"of {during['writes']['sent']} writes", ""),
        ("Recovery", _f(d.get("recovery_time_s"), " s"), "after rollback", "good"),
    ]
    tiles_html = "".join(f"<div class='tile {cls}'><div class='tl'>{t}</div><div class='tv'>{v}</div>"
                         f"<div class='ts'>{s}</div></div>" for t, v, s, cls in tiles)

    def src(s):
        """Only show the source when it is not a plain first-try LLM answer."""
        label = {"llm_retry": "corrected after a code check", "fallback": "safe default",
                 "hand-edited": "corrected by hand", "hardcoded": "fixed plan"}.get(s)
        return f"<span class='src {escape(s or '')}'>{escape(label)}</span>" if label else ""

    exps = plan.get("experiments") or [exp]
    plan_rows = "".join(
        f"<tr class='{'chosen' if e['id'] == exp['id'] else ''}'><td>{i}</td><td>{E(e['id'])}"
        f"{' <b>▶ run</b>' if e['id'] == exp['id'] else ''}</td><td>{e['latency_ms']} ms</td>"
        f"<td>{e['duration_s']} s</td><td>{E(e['hypothesis'])}</td></tr>"
        for i, e in enumerate(exps, 1))

    def mrow(label, get, unit=""):
        return (f"<tr><td>{label}</td>" + "".join(f"<td>{_f(get(m[p]), unit)}</td>"
                for p in ("baseline", "during", "after")) + "</tr>")
    metrics = "".join([
        mrow("Real data age, mean", lambda s: s["data_age_s"]["mean"], " s"),
        mrow("Real data age, max", lambda s: s["data_age_s"]["max"], " s"),
        mrow("App-believed age, mean", lambda s: s["server_age_s"]["mean"], " s"),
        mrow("Write latency p95 (through proxy)", lambda s: s["writes"]["latency_ms"]["p95"], " ms"),
        mrow("Read latency p95 (dashboard API)", lambda s: s["latency_ms"]["p95"], " ms"),
        mrow("Read error rate", lambda s: s["error_rate"]),
    ])

    services = "".join(f"<span class='chip crit{s.get('criticality', 1)}'>{E(s['name'])}"
                       f"<small>crit {s.get('criticality')}</small></span>" for s in smap.get("services", []))
    flows = "".join(f"<div class='flow'>{' <span>→</span> '.join(E(p) for p in f['path'])}</div>"
                    for f in smap.get("data_flows", [])[:2])

    guard_test = _latest_run("guard-test") if not d["run_id"].startswith("guard-test") else None
    gt_html = ""
    if guard_test:
        gt_abort = next((e for e in guard_test["guard_events"] if e["type"] == "abort"), None)
        gt_html = (f"<li><b>Guard test</b> ({E(guard_test['run_id'])}): {guard_test['executed_plan']['latency_ms']} ms "
                   f"injected, guard {'aborted by itself at t=' + _f(gt_abort['t_s'], ' s') if gt_abort else 'did not abort'}"
                   f"; recovered in {_f(guard_test.get('recovery_time_s'), ' s')}.</li>")

    fix = a.get("recommended_fix") or {}
    status = (f"Guard aborted at t={_f(aborted['t_s'], ' s')}" if aborted else
              f"Ran to planned end ({_f(exp.get('injected_for_s'), ' s')})")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Chaos Run Report</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#14171c;--muted:#5d6672;--line:#e3e6ea;--accent:#3e63dd;
--bad:#d93036;--warn:#c27c0e;--good:#18794e;--band:rgba(217,48,54,.08);--chip:#eef1f5}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1216;--card:#171b21;--ink:#e8eaed;--muted:#99a2ad;
--line:#2a3039;--accent:#7b9bff;--bad:#ff6369;--warn:#f1a10d;--good:#3dd68c;--band:rgba(255,99,105,.10);--chip:#222833}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:28px 16px 48px}}
h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:17px;margin:0 0 12px}}
.muted,.sub{{color:var(--muted)}}.sub{{margin:0 0 20px;font-size:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}}
.finding{{border-left:5px solid var(--bad)}}.finding .k{{font-size:12px;font-weight:700;letter-spacing:.06em;
text-transform:uppercase;color:var(--bad)}}.finding p{{font-size:19px;font-weight:600;margin:6px 0 0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.tl{{font-size:12px;color:var(--muted)}}.tv{{font-size:28px;font-weight:700;margin:2px 0}}.ts{{font-size:12px;color:var(--muted)}}
.tile.bad .tv{{color:var(--bad)}}.tile.warn .tv{{color:var(--warn)}}.tile.good .tv{{color:var(--good)}}
svg{{width:100%;height:auto}}.grid{{stroke:var(--line)}}.tick{{fill:var(--muted);font-size:11px}}
.band{{fill:var(--band)}}.bandlabel{{fill:var(--bad);font-size:11px;font-weight:600}}
.thr{{stroke:var(--muted);stroke-dasharray:5 4}}.thrlabel{{fill:var(--muted);font-size:11px}}
polyline{{fill:none;stroke-width:2.5;stroke-linejoin:round}}polyline.real{{stroke:var(--bad)}}polyline.seen{{stroke:var(--accent)}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-top:6px}}
.sw{{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}}
.sw.real{{background:var(--bad)}}.sw.seen{{background:var(--accent)}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.step{{border:1px solid var(--line);border-radius:10px;padding:14px}}.step h3{{font-size:14px;margin:0 0 6px}}
.step .n{{display:inline-block;width:22px;height:22px;border-radius:50%;background:var(--chip);text-align:center;
font-size:12px;line-height:22px;margin-right:6px}}.step p{{margin:4px 0;font-size:13.5px}}
.ai{{font-size:11px;font-weight:700;color:#fff;background:#8e4ec6;border-radius:4px;padding:1px 6px;margin-left:6px}}
.code{{font-size:11px;font-weight:700;color:#fff;background:#0d74ce;border-radius:4px;padding:1px 6px;margin-left:6px}}
.src{{font-size:11px;border-radius:4px;padding:1px 6px;background:var(--chip);color:var(--muted)}}
.src.fallback{{color:var(--warn)}}
.chip{{display:inline-block;background:var(--chip);border-radius:6px;padding:3px 8px;margin:2px;font-size:12.5px}}
.chip small{{color:var(--muted);margin-left:4px}}.crit5,.crit4{{outline:1px solid var(--bad)}}
.flow{{font-size:12.5px;margin-top:6px}}.flow span{{color:var(--muted)}}
.tablewrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th,td{{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-weight:600;font-size:12px}}tr.chosen td{{background:var(--chip)}}
.fix{{border-left:5px solid var(--good)}}.where{{font-family:ui-monospace,Consolas,monospace;font-size:13px;
background:var(--chip);padding:2px 6px;border-radius:4px}}ul{{margin:0;padding-left:20px}}li{{margin:4px 0}}
</style></head><body><main>
<h1>Chaos Engineering Report: Fleet Management System</h1>
<p class="sub">Run <b>{E(d['run_id'])}</b> · {E(exp['latency_ms'])} ms network latency on
<b>{E(exp['target_link'])}</b> · {status} · generated {datetime.now().strftime('%Y-%m-%d %H:%M')}
{(' · model ' + E(d.get('model'))) if d.get('model') else ''}</p>

<div class="card finding"><div class="k">Key finding</div><p>{headline}</p>{analyst_note}</div>

<div class="tiles">{tiles_html}</div>

<div class="card"><h2>What happened to data freshness</h2>{_chart(series, inj, rb, exp['abort_thresholds'].get('max_data_age_s'))}
<p class="muted" style="font-size:13px;margin:8px 0 0">The red line is how old the truck data on screen really was.
The blue line is how old the app thought it was. When they split apart, the app is showing old data as fresh.</p></div>

<div class="card"><h2>How the run worked</h2><div class="steps">
<div class="step"><h3><span class="n">1</span>Map the system<span class="ai">AI</span> {src(smap.get('source'))}</h3>
<p>{len(smap.get('services', []))} services, {smap.get('edges', 0)} links found.</p><div>{services}</div>{flows}</div>
<div class="step"><h3><span class="n">2</span>Measure normal<span class="code">CODE</span></h3>
<p>Baseline: data age {_f(m['baseline']['data_age_s']['mean'], ' s')} avg, {_f(m['baseline']['error_rate'])} errors,
read p95 {_f(m['baseline']['latency_ms']['p95'], ' ms')}.</p></div>
<div class="step"><h3><span class="n">3</span>Plan experiments<span class="ai">AI</span> {src(plan.get('source'))}</h3>
<p>{len(exps)} experiments proposed, ranked most informative first.</p><p><b>Hypothesis:</b> {E(exp['hypothesis'])}</p></div>
<div class="step"><h3><span class="n">4</span>Safety gate<span class="code">CODE</span></h3>
<ul><li>Hard limits checked: ≤15000 ms, ≤60 s, one allowed link</li><li>Pre-flight: app healthy, no fault active</li>
<li>Guard stops at data age &gt; {_f(exp['abort_thresholds']['max_data_age_s'], ' s')} for {exp['abort_thresholds']['consecutive_s']} s</li></ul></div>
<div class="step"><h3><span class="n">5</span>Inject, watch, roll back<span class="code">CODE</span></h3>
<p>{E(exp['latency_ms'])} ms delay for {_f(exp.get('injected_for_s'), ' s')}. {E(ended['reason']) if ended else ''}.</p>
<p>Rollback: {'removed ' + E(', '.join(d['rollback']['removed_toxics'])) if d['rollback']['ok'] else 'FAILED'}.
Recovered in {_f(d.get('recovery_time_s'), ' s')}.</p></div>
<div class="step"><h3><span class="n">6</span>Analyse<span class="ai">AI</span> {src(a.get('source'))}</h3>
<p><b>Hypothesis held:</b> {E(a.get('hypothesis_held', 'n/a'))}. {E(a.get('hypothesis_reason'))}</p></div>
</div></div>

{f'''<div class="card fix"><h2>Recommended fix</h2><p>{E(fix.get('change'))}</p>
<p>Where: <span class="where">{E(fix.get('where'))}</span></p></div>''' if fix else ''}

<div class="card"><h2>Measurements</h2><div class="tablewrap"><table>
<tr><th>Metric</th><th>Baseline</th><th>During fault</th><th>After rollback</th></tr>{metrics}
<tr><td>Alerts raised by the app</td><td>n/a</td><td>{_alerts(d, 'during')}</td><td>{_alerts(d, 'after')}</td></tr>
</table></div></div>

<div class="card"><h2>Experiments proposed by the planner</h2><div class="tablewrap"><table>
<tr><th>#</th><th>Experiment</th><th>Latency</th><th>Duration</th><th>Hypothesis</th></tr>{plan_rows}</table></div></div>

<div class="card"><h2>Safety evidence</h2><ul>
<li>Agents cannot inject faults: every plan passes code-enforced hard limits first.</li>
<li>Rollback runs in a <code>finally</code> block and on Ctrl+C; safe to run twice.</li>
<li>Kill switch: <code>python -m chaos.run abort</code> removes all faults from any terminal.</li>
<li>Only containers of this Compose project are touched.</li>{gt_html}</ul></div>

<p class="muted" style="font-size:12px">AI agents reason and decide; code measures, injects and enforces safety.
Every agent decision is logged in chaos/data/decisions.jsonl.</p>
</main></body></html>"""


def write_report(run_data: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html = render(run_data)
    path = REPORTS_DIR / f"{run_data['run_id']}.html"
    path.write_text(html, encoding="utf-8")
    LATEST.write_text(html, encoding="utf-8")
    return path


def latest_run_file(prefix: str = "day1") -> Path | None:
    files = sorted(RUNS_DIR.glob(f"{prefix}-*.json"))
    return files[-1] if files else None
