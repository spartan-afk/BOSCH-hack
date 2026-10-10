"""Orchestrator and command line.

    python -m chaos.run map [--force]     Mapper only
    python -m chaos.run baseline          5-minute baseline -> baseline.json
    python -m chaos.run day1              plan, validate, pre-flight, inject, observe and guard,
                                          rollback, recovery timing, analyze
    python -m chaos.run guard-test        same run, fixed 10 s latency, low abort threshold
    python -m chaos.run abort             kill switch
    python -m chaos.run plan              Planner only (dev)
    python -m chaos.run analyze FILE      Analyst only on a saved run JSON (dev)

Agents reason and decide; this file measures, injects and enforces safety.
No agent output reaches the executor without passing validate_experiment().
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime

from chaos import ui
from chaos.config import DATA_DIR, load_config
from chaos.db import ResultsDB, RunState
from chaos.decisions import log_decision
from chaos.docker_util import list_project_containers
from chaos.executor import Executor, ExecutorError
from chaos.guard import ABORT_FLAG, Guard, preflight
from chaos.llm import LLMClient
from chaos.loadgen import LoadGen, ensure_vehicles
from chaos.metrics import summarize
from chaos.observer import Observer
from chaos.safety import validate_experiment

BASELINE_PATH = DATA_DIR / "baseline.json"
RUNS_DIR = DATA_DIR / "runs"


def _load_json(path, what):
    if not path.exists():
        sys.exit(f"{what} not found at {path}; run the step that creates it first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_baseline() -> dict:
    baseline = _load_json(BASELINE_PATH, "baseline.json (run: python -m chaos.run baseline)")
    if baseline.get("observer_sampling") != "random_phase":
        sys.exit("baseline.json was measured with the old fixed-phase observer; "
                 "re-run: python -m chaos.run baseline")
    return baseline


def _run_id(kind: str) -> str:
    return f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _require_preflight(cfg, executor, actor="guard"):
    try:
        executor.ensure_proxies()
    except ExecutorError as e:
        sys.exit(f"Pre-flight failed: {e}\nIs the stack up? docker compose -f docker-compose.yml "
                 f"-f docker-compose.chaos.yml up -d")
    problems = preflight(cfg, executor)
    if problems:
        log_decision(actor, "pre-flight failed; not injecting", "; ".join(problems))
        sys.exit("Pre-flight failed:\n  - " + "\n  - ".join(problems))
    log_decision(actor, "pre-flight passed", "app healthy, project containers running, no active toxics")


class Probes:
    """loadgen + observer for one run."""

    def __init__(self, cfg, kind):
        self.db = ResultsDB()
        # Two loadgens would reuse the same sequence numbers and corrupt each other's measurements.
        busy = self.db.query("SELECT run_id FROM sends WHERE sent_at > ? LIMIT 1", (time.time() - 3,))
        if busy:
            sys.exit(f"Another chaos command is already sending traffic ({busy[0][0]}), probably "
                     f"`python -m chaos.run load` in another terminal. Stop it with Ctrl+C, then retry.")
        self.state = RunState(_run_id(kind))
        self.vehicles = ensure_vehicles(cfg["app"]["base_url"], cfg["loadgen"]["vehicles"])
        self.loadgen = LoadGen(cfg, self.db, self.state, self.vehicles)
        self.observer = Observer(cfg, self.db, self.state, self.vehicles)
        self.db.execute("INSERT INTO runs VALUES (?,?,?,NULL)", (self.state.run_id, kind, time.time()))

    def start(self):
        self.loadgen.start()
        self.observer.start()

    def stop(self):
        self.loadgen.stop()
        self.observer.stop()
        self.db.execute("UPDATE runs SET ended_at=? WHERE run_id=?", (time.time(), self.state.run_id))


# ── map / plan / analyze ────────────────────────────────────────

def cmd_map(args, cfg):
    from chaos.agents.mapper import run_mapper
    run_mapper(LLMClient(cfg), cfg, force=args.force)


def _get_map(cfg) -> dict:
    from chaos.agents.mapper import MAP_PATH, run_mapper
    if MAP_PATH.exists():
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return run_mapper(LLMClient(cfg), cfg)


def cmd_plan(args, cfg):
    from chaos.agents.planner import run_planner
    run_planner(LLMClient(cfg), cfg, _get_map(cfg), _load_json(BASELINE_PATH, "baseline.json"))


def cmd_analyze(args, cfg):
    from pathlib import Path
    from chaos.agents.analyst import run_analyst
    run_data = _load_json(Path(args.file), "run data")
    _, run_data["analysis"] = run_analyst(LLMClient(cfg), cfg, run_data)
    _save_run(run_data)


# ── baseline ────────────────────────────────────────────────────

def cmd_baseline(args, cfg):
    duration = args.duration or cfg["run"]["baseline_duration_s"]
    executor = Executor(cfg)
    _require_preflight(cfg, executor)
    probes = Probes(cfg, "baseline")
    print(f"Baseline run {probes.state.run_id}: {duration}s, vehicles {probes.vehicles}. Ctrl+C stops early.")
    probes.start()
    # Not counted: the first ticks still show the previous run's last update.
    probes.state.phase = "warmup"
    for _ in range(10):
        probes.observer.wait_tick()
    probes.state.phase = "baseline"
    start = time.time()
    try:
        while (elapsed := time.time() - start) < duration:
            samples = probes.observer.wait_tick()
            if int(elapsed) % 30 == 0:
                ages = [s["data_age_s"] for s in samples if s["data_age_s"] is not None]
                print(f"  t={elapsed:5.0f}s  max data age={max(ages) if ages else 'n/a'}s  "
                      f"errors={sum(1 for s in samples if s['error'])}")
    except KeyboardInterrupt:
        print("Stopped early.")
    finally:
        probes.stop()

    measured = round(time.time() - start)
    if measured < 30:
        sys.exit("Less than 30 s measured; baseline not written.")
    baseline = {"run_id": probes.state.run_id, "observer_sampling": "random_phase", "duration_s": measured,
                "vehicles": len(probes.vehicles), **summarize(probes.db, probes.state.run_id, "baseline")}
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    log_decision("orchestrator", "baseline written", json.dumps(baseline))
    print(json.dumps(baseline, indent=2))
    print(f"Wrote {BASELINE_PATH}")


# ── injection run (day1 / guard-test) ───────────────────────────

def app_alerts_by_phase(cfg: dict, vehicle_ids: list[int], windows: dict[str, tuple[float, float]]) -> dict:
    """Alerts the app itself created for the test vehicles, counted by phase and type."""
    import requests
    from datetime import timezone
    counts = {phase: {} for phase in windows}
    try:
        alerts = requests.get(f"{cfg['app']['base_url']}/api/v1/alerts/",
                              params={"hours": 1, "limit": 500}, timeout=10).json()
    except Exception as e:
        return {"error": f"could not read alerts: {e}"}
    for a in alerts:
        if a["vehicle_id"] not in vehicle_ids:
            continue
        ts = datetime.fromisoformat(a["created_at"]).replace(tzinfo=timezone.utc).timestamp()
        for phase, (start, end) in windows.items():
            if start <= ts < end:
                counts[phase][a["alert_type"]] = counts[phase].get(a["alert_type"], 0) + 1
    return {phase: c or {"none": 0} for phase, c in counts.items()}


def recovery_threshold(baseline: dict) -> float:
    age = (baseline.get("data_age_s") or {}).get("max")
    return round(age * 1.5 + 1, 2) if age else 3.0


def run_injection(cfg: dict, exp: dict, kind: str, baseline: dict, steps: tuple[int, int] = (1, 3)) -> dict:
    """Validated experiment in, run data out. Rollback always runs.
    steps = (first step number, total) for the terminal walkthrough."""
    n, total = steps
    problems = validate_experiment(exp, cfg)
    if problems:
        log_decision("orchestrator", f"rejected experiment {exp.get('id')}", "; ".join(problems))
        sys.exit("Experiment rejected by hard limits:\n  - " + "\n  - ".join(problems))

    ui.step(n, total, "SAFETY GATE: hard limits and pre-flight", "CODE")
    ui.ok(f"hard limits: {exp['latency_ms']} ms <= {cfg['limits']['max_latency_ms']}, "
          f"{exp['duration_s']} s <= {cfg['limits']['max_duration_s']}, one allowed link ({exp['target_link']})")
    executor = Executor(cfg)
    ABORT_FLAG.unlink(missing_ok=True)
    _require_preflight(cfg, executor)
    ui.ok("pre-flight: app healthy, project containers running, no fault already active")

    probes = Probes(cfg, kind)
    run_id, db, observer = probes.state.run_id, probes.db, probes.observer
    thresholds = exp["abort_thresholds"]
    max_duration = min(exp["duration_s"], cfg["limits"]["max_duration_s"])
    rec_thr = recovery_threshold(baseline)
    guard_events, t = [], {}

    def event(type_, reason):
        now = time.time()
        rel = round(now - t["injected"], 1) if "injected" in t else None
        db.event(run_id, now, type_, reason)
        guard_events.append({"t_s": rel, "type": type_, "reason": reason})
        log_decision("guard", type_, reason, run_id=run_id)
        style = ("red", "bold") if type_ == "abort" else ("green", "bold")
        print(f"  {ui.c('GUARD ' + type_.upper(), *style)}  {reason}")

    ui.step(n + 1, total, f"INJECT AND WATCH: {exp['latency_ms']} ms delay on {exp['target_link']}", "CODE")
    ui.info("run", run_id)
    ui.info("guard stops if", f"real data age > {thresholds['max_data_age_s']} s for {thresholds['consecutive_s']} s, "
            f"or after {max_duration} s")
    probes.start()
    t["start"] = time.time()
    removed, rollback_ok, interrupted = [], False, False
    try:
        print(f"  {ui.c('trucks driving normally for ' + str(cfg['run']['warmup_s']) + ' s (warm-up) ...', 'dim')}")
        ticks = []
        for _ in range(cfg["run"]["warmup_s"]):
            ticks.append(observer.wait_tick())
            if ABORT_FLAG.exists():
                raise KeyboardInterrupt
        # Judge only the last 10 s: the first ticks still show the previous run's last update.
        tail = [s for tick in ticks[-10:] for s in tick]
        ages = [s["data_age_s"] for s in tail if s["data_age_s"] is not None]
        errors = sum(1 for s in tail if s["error"])
        if not ages or max(ages) > rec_thr or errors > 0.2 * len(tail):
            log_decision("guard", "pre-flight failed; not injecting",
                         f"warm-up not steady: max data age {max(ages) if ages else None}s, {errors} read errors")
            raise RuntimeError(f"warm-up not steady (max data age {max(ages) if ages else None}s, "
                               f"{errors} read errors in last 10 s); not injecting")

        probes.state.phase = "during"
        executor.add_latency(exp["target_link"], exp["latency_ms"])
        t["injected"] = time.time()
        db.event(run_id, t["injected"], "inject", json.dumps(exp))
        log_decision("orchestrator", f"injected {exp['latency_ms']} ms on {exp['target_link']}",
                     exp.get("reason", ""), run_id=run_id)
        print(f"  {ui.c('>>> DELAY INJECTED', 'red', 'bold')}   "
              f"{ui.c('real age = how old the data on screen really is; app thinks = its own timestamp', 'dim')}")

        guard = Guard(thresholds, max_duration)
        while True:
            samples = observer.wait_tick()
            elapsed = time.time() - t["injected"]
            ages = [s["data_age_s"] for s in samples if s["data_age_s"] is not None]
            seen = [s["server_age_s"] for s in samples if s["server_age_s"] is not None]
            real, believed = (max(ages) if ages else None), (max(seen) if seen else None)
            hidden = real is not None and believed is not None and real - believed > 2
            real_s = ui.c(f"{real:5.1f}s" if real is not None else "  n/a", "red" if real and real > 2 else "green")
            seen_s = ui.c(f"{believed:5.1f}s" if believed is not None else "  n/a", "blue")
            errs = sum(1 for s in samples if s["error"])
            print(f"  t={elapsed:5.1f}s   real age {real_s}   app thinks {seen_s}   errors {errs}"
                  + (ui.c("   <- app shows old data as fresh", "yellow") if hidden else ""))
            stop = guard.check(samples, elapsed)
            if stop:
                event(*stop)
                break
    except KeyboardInterrupt:
        interrupted = True
        event("abort", "manual kill switch" if ABORT_FLAG.exists() else "Ctrl+C")
    except (RuntimeError, ExecutorError) as e:
        event("abort", str(e))
    finally:
        try:
            removed = executor.remove_all()
            rollback_ok = True
        except ExecutorError as e:
            print(f"!! ROLLBACK FAILED: {e}\n!! Run: python -m chaos.run abort")
        t["rolled_back"] = time.time()
        probes.state.phase = "after"
        db.event(run_id, t["rolled_back"], "rollback", json.dumps(removed))
        log_decision("orchestrator", "rollback", f"removed {removed}; ok={rollback_ok}", run_id=run_id)
        if "injected" not in t:
            probes.stop()

    if "injected" not in t:
        sys.exit("Run stopped before injection.")

    ui.step(n + 2, total, "ROLL BACK AND RECOVER", "CODE")
    if rollback_ok:
        ui.ok(f"delay removed ({', '.join(removed) or 'nothing left to remove'})")
    # Recovery: all vehicles back under the threshold, with no read errors, for 3 consecutive seconds.
    recovered_at, streak, first_ok = None, 0, None
    try:
        print(f"  {ui.c(f'waiting for data age <= {rec_thr} s for 3 s ...', 'dim')}")
        while time.time() - t["rolled_back"] < cfg["run"]["recovery_max_s"]:
            samples = observer.wait_tick()
            ages = [s["data_age_s"] for s in samples if s["data_age_s"] is not None]
            ok = len(ages) == len(samples) and max(ages) <= rec_thr and not any(s["error"] for s in samples)
            streak = streak + 1 if ok else 0
            if streak == 1:
                first_ok = time.time()
            if streak >= 3:
                recovered_at = first_ok
                break
        for _ in range(cfg["run"]["after_s"]):
            observer.wait_tick()
    except KeyboardInterrupt:
        print("  recovery measurement cut short")
    finally:
        probes.stop()
    t["end"] = time.time()

    recovery_s = round(recovered_at - t["rolled_back"], 1) if recovered_at else None
    if recovery_s is not None:
        ui.ok(f"recovered: data fresh again {recovery_s} s after rollback")
    else:
        ui.bad("did not recover within the measurement window")

    run_data = {
        "run_id": run_id,
        "observer_sampling": "random_phase",
        "executed_plan": {**exp, "abort_thresholds": thresholds,
                          "injected_for_s": round(t["rolled_back"] - t["injected"], 1),
                          "warmup_s": cfg["run"]["warmup_s"]},
        "metrics": {"baseline": {k: baseline.get(k) for k in
                                 ("error_rate", "latency_ms", "data_age_s", "server_age_s", "writes")},
                    "before": summarize(db, run_id, "before"),
                    "during": summarize(db, run_id, "during"),
                    "after": summarize(db, run_id, "after")},
        "app_alerts": app_alerts_by_phase(cfg, list(probes.vehicles.values()), {
            "before": (t["start"], t["injected"]), "during": (t["injected"], t["rolled_back"]),
            "after": (t["rolled_back"], t["end"] + 5)}),
        "recovery_time_s": recovery_s,
        "recovery_threshold_data_age_s": rec_thr,
        # Real data age minus what the app believes: delay the app cannot see.
        "hidden_delay_s": {p: _gap(summarize(db, run_id, p)) for p in ("before", "during", "after")},
        "guard_events": guard_events,
        "rollback": {"ok": rollback_ok, "removed_toxics": removed},
        "notes": "data_age_s = now - client event time of the state the app shows. "
                 "server_age_s = now - the app's own last_telemetry_at. latency_ms = observer read latency "
                 "(direct, not proxied); writes.latency_ms = loadgen round trip through the proxy.",
    }
    _save_run(run_data)
    return run_data


def _gap(summary: dict) -> float | None:
    real, seen = summary["data_age_s"]["mean"], summary["server_age_s"]["mean"]
    return None if real is None or seen is None else round(real - seen, 3)


def _save_run(run_data: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run_data['run_id']}.json").write_text(json.dumps(run_data, indent=2), encoding="utf-8")


def _map_summary() -> dict:
    from chaos.agents.mapper import MAP_META_PATH, MAP_PATH, map_is_hand_edited
    if not MAP_PATH.exists():
        return {}
    smap = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    meta = json.loads(MAP_META_PATH.read_text()) if MAP_META_PATH.exists() else {}
    return {"source": "hand-edited" if map_is_hand_edited() else meta.get("source"),
            "services": [{"name": s["name"], "criticality": s.get("criticality")} for s in smap["services"]],
            "edges": len(smap.get("edges", [])), "data_flows": smap.get("data_flows", [])}


def _analyse_and_report(cfg, llm, run_data, summary_path, step_n, total, open_report=True):
    from chaos.agents.analyst import run_analyst
    from chaos.report import write_report
    ui.step(step_n, total, "ANALYSE: what did we learn?", "AI AGENT")
    _, analysis = run_analyst(llm, cfg, run_data, summary_path)
    run_data["analysis"] = analysis
    run_data["model"] = llm.model
    _save_run(run_data)
    report = write_report(run_data)

    during = run_data["metrics"]["during"]
    alerts = sum(v for k, v in (run_data["app_alerts"].get("during") or {}).items() if k != "none")
    ended = run_data["guard_events"][-1] if run_data["guard_events"] else {}
    fix = analysis.get("recommended_fix") or {}
    rows = [
        ("Injected", f"{run_data['executed_plan']['latency_ms']} ms on {run_data['executed_plan']['target_link']}"),
        ("Real data age", f"{during['data_age_s']['mean']} s avg, {during['data_age_s']['max']} s max "
                          f"(normal: {run_data['metrics']['baseline']['data_age_s']['mean']} s)"),
        ("App believed", f"{during['server_age_s']['mean']} s avg"),
        ("App alerts / errors", f"{alerts} alerts, {during['writes']['failed']} failed requests"),
        ("Guard", f"{ended.get('type', 'n/a')}: {ended.get('reason', '')}"),
        ("Recovery", f"{run_data['recovery_time_s']} s after rollback"),
        ("Hypothesis held", analysis.get("hypothesis_held")),
        ("Weakness", analysis.get("weakness") if analysis.get("weakness_found") else "none found"),
    ]
    if fix:
        rows.append(("Fix", f"{fix.get('change')}  [{fix.get('where')}]"))
    rows.append(("Report", "chaos/data/report.html (opens in your browser)"))
    ui.box("RESULT", rows)
    if open_report and sys.stdout.isatty():
        import webbrowser
        webbrowser.open(report.resolve().as_uri())


def cmd_day1(args, cfg):
    from chaos.agents.planner import derived_thresholds, run_planner
    total = 7
    baseline = _load_baseline()
    llm = LLMClient(cfg)
    print(ui.c("\nAI-DRIVEN CHAOS ENGINEERING: Fleet Management System", "bold"))
    print(ui.c("Question: if truck messages are delayed, does the app notice?", "dim"))

    ui.step(1, total, "MAP THE SYSTEM", "AI AGENT")
    service_map = _get_map(cfg)
    summary = _map_summary()
    ui.info("map", f"{len(service_map['services'])} services, {len(service_map.get('edges', []))} links "
                   f"(source: {summary.get('source')})")
    for s in sorted(service_map["services"], key=lambda s: -s.get("criticality", 0)):
        print(f"      {s['name']:<16} criticality {s.get('criticality')}  {ui.c(s.get('role', '')[:48], 'dim')}")

    ui.step(2, total, "NORMAL BEHAVIOUR (baseline)", "CODE")
    ui.info("data age", f"{baseline['data_age_s']['mean']} s avg, {baseline['data_age_s']['max']} s max")
    ui.info("errors", f"{baseline['error_rate']} read error rate, {baseline['writes']['failed']} failed writes "
                      f"of {baseline['writes']['sent']}")
    ui.info("dashboard API", f"p95 {baseline['latency_ms']['p95']} ms")

    ui.step(3, total, "PLAN EXPERIMENTS", "AI AGENT")
    if args.hardcoded:      # Step 2: fixed plan, thresholds derived from the baseline
        plan = {"source": "hardcoded", "experiments": [{
            "id": "hardcoded", "target_link": cfg["limits"]["allowed_links"][0]["name"],
            "latency_ms": 10000, "duration_s": 60,
            "hypothesis": "10 s ingestion latency makes vehicle data stale by about 10 s.",
            "expected_blast_radius": ["vehicle state freshness"],
            "abort_thresholds": derived_thresholds(baseline, 10000).model_dump(),
            "reason": "Hardcoded Step 2 plan."}]}
    else:
        plan = run_planner(llm, cfg, service_map, baseline)
    for i, e in enumerate(plan["experiments"], 1):
        print(f"  {ui.c(str(i) + '.', 'bold')} {e['latency_ms']} ms for {e['duration_s']} s")
        print(ui.c(ui.wrap(e["hypothesis"]), "dim"))

    for exp in plan["experiments"]:   # most informative first; run the first that passes hard limits
        problems = validate_experiment(exp, cfg)
        if not problems:
            log_decision("orchestrator", f"selected experiment {exp['id']}", "first valid in ranked plan")
            break
        log_decision("orchestrator", f"rejected experiment {exp['id']}", "; ".join(problems))
        ui.bad(f"rejected {exp['id']}: {problems}")
    else:
        sys.exit("No experiment in the plan passed the hard limits.")

    run_data = run_injection(cfg, exp, "day1", baseline, steps=(4, total))
    run_data["plan"] = plan
    run_data["map_summary"] = summary
    _analyse_and_report(cfg, llm, run_data, DATA_DIR / "day1_summary.md", 7, total, not args.no_open)


def cmd_guard_test(args, cfg):
    total = 4
    baseline = _load_baseline()
    gt = cfg["guard"]["guard_test"]
    exp = {**gt["experiment"], "abort_thresholds": gt["abort_thresholds"]}
    print(ui.c("\nGUARD TEST: does the safety guard stop a harmful experiment by itself?", "bold"))
    run_data = run_injection(cfg, exp, "guard-test", baseline, steps=(1, total))
    aborted = any(e["type"] == "abort" for e in run_data["guard_events"])
    print()
    (ui.ok if aborted else ui.bad)(f"GUARD TEST {'PASSED: guard aborted by itself' if aborted else 'FAILED: guard did not abort'}")
    run_data["map_summary"] = _map_summary()
    _analyse_and_report(cfg, LLMClient(cfg), run_data, DATA_DIR / "guard_test_summary.md", 4, total,
                        not args.no_open)


def cmd_report(args, cfg):
    from pathlib import Path
    from chaos.report import latest_run_file, write_report
    path = Path(args.file) if args.file else latest_run_file("day1")
    if not path or not path.exists():
        sys.exit("No run found; run: python -m chaos.run day1")
    report = write_report(json.loads(path.read_text(encoding="utf-8")))
    print(f"Wrote {report}")
    if not args.no_open:
        import webbrowser
        webbrowser.open(report.resolve().as_uri())


# ── steady load only ────────────────────────────────────────────

def cmd_load(args, cfg):
    """Keep the test trucks driving (and the observer measuring) until Ctrl+C. Injects nothing."""
    executor = Executor(cfg)
    try:
        executor.ensure_proxies()
        if executor.active_toxics():
            print("warning: a toxic is active; run `python -m chaos.run abort` for normal traffic")
    except ExecutorError as e:
        sys.exit(f"{e}\nIs the stack up? docker compose -f docker-compose.yml -f docker-compose.chaos.yml up -d")
    probes = Probes(cfg, "load")
    probes.state.phase = "load"
    probes.start()
    print(f"Steady load running ({len(probes.vehicles)} vehicles, 1 update/s each). "
          f"Dashboard: {cfg['app']['base_url']}/  Ctrl+C to stop.")
    start = time.time()
    try:
        while True:
            samples = probes.observer.wait_tick()
            if int(time.time() - start) % 30 == 0:
                ages = [s["data_age_s"] for s in samples if s["data_age_s"] is not None]
                print(f"  t={time.time() - start:5.0f}s  max data age={max(ages) if ages else 'n/a'}s  "
                      f"read errors={sum(1 for s in samples if s['error'])}")
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        probes.stop()


# ── kill switch ─────────────────────────────────────────────────

def cmd_abort(args, cfg):
    """Remove all toxics, re-enable all proxies, start stopped project containers.
    Also signals any running day1/guard-test (via the ABORT flag) to stop and roll back."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ABORT_FLAG.write_text(str(time.time()))
    actions = []
    try:
        removed = Executor(cfg).remove_all()
        actions.append(f"removed toxics {removed}, all proxies enabled")
    except ExecutorError as e:
        actions.append(f"toxiproxy: {e}")

    for c in list_project_containers(cfg["project"], cfg["limits"]["compose_project_label"]):
        if c["state"] in ("exited", "created", "paused"):
            cmd = ["docker", "unpause" if c["state"] == "paused" else "start", c["name"]]
            ok = subprocess.run(cmd, capture_output=True).returncode == 0
            actions.append(f"{cmd[1]} {c['name']}: {'ok' if ok else 'FAILED'}")

    log_decision("user", "kill switch", "; ".join(actions))
    print("KILL SWITCH:\n  - " + "\n  - ".join(actions))


def main(argv=None):
    # LLM text can contain characters the Windows console code page cannot print.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m chaos.run")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("map", help="Mapper agent -> map.json")
    p.add_argument("--force", action="store_true", help="overwrite a hand-edited map.json")
    p.set_defaults(fn=cmd_map)

    p = sub.add_parser("baseline", help="measure normal behaviour -> baseline.json")
    p.add_argument("--duration", type=int, help="seconds (default from config: 300)")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("day1", help="full Day-1 run")
    p.add_argument("--hardcoded", action="store_true", help="skip the planner; 10000 ms for 60 s")
    p.add_argument("--no-open", action="store_true", help="do not open the HTML report")
    p.set_defaults(fn=cmd_day1)

    p = sub.add_parser("guard-test", help="10000 ms with an 8 s data-age abort")
    p.add_argument("--no-open", action="store_true", help="do not open the HTML report")
    p.set_defaults(fn=cmd_guard_test)

    p = sub.add_parser("report", help="HTML report for a saved run (default: latest day1)")
    p.add_argument("file", nargs="?")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_report)
    sub.add_parser("load", help="steady truck traffic until Ctrl+C (no injection)").set_defaults(fn=cmd_load)
    sub.add_parser("abort", help="kill switch").set_defaults(fn=cmd_abort)
    sub.add_parser("plan", help="Planner agent only (dev)").set_defaults(fn=cmd_plan)

    p = sub.add_parser("analyze", help="Analyst agent on a saved run JSON (dev)")
    p.add_argument("file")
    p.set_defaults(fn=cmd_analyze)

    args = parser.parse_args(argv)
    args.fn(args, load_config())


if __name__ == "__main__":
    main()
