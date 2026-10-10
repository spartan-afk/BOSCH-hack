"""Day 1 Chaos Workflow Orchestrator.

Implements:
1. Pre-flight health checks (guard)
2. Toxiproxy proxy initialization
3. Loadgen routing through Toxiproxy (port 8001 -> backend:8000)
4. Observer monitoring directly (port 8000)
5. Injection of network latency (hardcoded or planned)
6. Real-time safety guard with automated abortion on threshold breach (e.g. data age > 8s)
7. Guaranteed rollback in `finally` block & Ctrl+C
8. Post-injection recovery measurement
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from chaos.executor import ToxiproxyExecutor
from chaos.guard import SafetyGuard, log_decision
from chaos.loadgen import LoadGenerator
from chaos.observer import Observer, compute_metrics_summary

logger = logging.getLogger("chaos.workflow")

DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_SUMMARY_PATH = DATA_DIR / "day1_experiment.json"


async def run_day1_workflow(
    target_link: str = "fleet-ingest",
    latency_ms: int = 10000,
    duration_s: int = 60,
    guard_test_mode: bool = False,
    custom_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute the Day 1 Latency Chaos Experiment."""
    logger.info("==================================================")
    mode_str = "GUARD ABORT TEST" if guard_test_mode else "DAY 1 LATENCY INJECTION"
    logger.info(f"STARTING {mode_str}: target={target_link}, latency={latency_ms}ms, duration={duration_s}s")
    logger.info("==================================================")

    # Planner agent: if no custom plan is given and not in guard_test_mode, run Planner
    if not guard_test_mode:
        from chaos.agents.planner import run_planner
        try:
            generated_plan = await run_planner()
            if generated_plan and generated_plan.ranked_experiments:
                top_exp = generated_plan.ranked_experiments[0]
                target_link = top_exp.target_link
                latency_ms = top_exp.latency_ms
                duration_s = top_exp.duration_s
                logger.info(f"[PLANNER] Selected top-ranked plan: target={target_link}, latency={latency_ms}ms, duration={duration_s}s")
        except Exception as pe:
            logger.warning(f"Planner note: {pe}; proceeding with default plan parameters.")

    executor = ToxiproxyExecutor()
    guard = SafetyGuard(
        data_age_threshold_s=8.0 if guard_test_mode else 12.0,
        consecutive_breaches_to_abort=3,
    )

    # Validate experiment limits
    try:
        executor.validate_plan(target_link, latency_ms, duration_s)
    except ValueError as ve:
        logger.error(f"Plan validation rejected: {ve}")
        return {"status": "rejected", "reason": str(ve)}

    # Pre-flight check
    passed, reason = guard.pre_flight_check()
    if not passed:
        logger.error(f"Pre-flight health check failed: {reason}")
        return {"status": "pre_flight_failed", "reason": reason}

    # Pause internal simulator loop if running so loadgen is the single source of truth
    sim_was_running = False
    try:
        status_resp = httpx.get("http://localhost:8000/api/fleet/status", timeout=3.0)
        if status_resp.status_code == 200 and status_resp.json().get("simulation_running"):
            httpx.post("http://localhost:8000/api/fleet/simulate/toggle", timeout=3.0)
            sim_was_running = True
            logger.info("Paused background simulator loop; loadgen is now sole telemetry driver.")
    except Exception as e:
        logger.debug(f"Simulator toggle check: {e}")

    # Ensure proxy exists
    proxy_ok = executor.ensure_proxy(
        name="fleet-ingest",
        listen="0.0.0.0:8001",
        upstream="host.docker.internal:8000",
    )
    if not proxy_ok:
        logger.error("Could not configure Toxiproxy proxy on port 8001.")
        return {"status": "setup_failed", "reason": "Toxiproxy proxy creation failed"}

    # Initialize loadgen sending through Toxiproxy (port 8001)
    loadgen = LoadGenerator(target_url="http://localhost:8001/api/fleet/telemetry/ingest")
    # Observer monitors app directly (port 8000)
    observer = Observer(app_url="http://localhost:8000/api/fleet/vehicles", phase="injection")

    # Start background components
    loadgen_task = asyncio.create_task(loadgen.run())
    observer_task = asyncio.create_task(observer.run())

    # Allow 5s of steady traffic through proxy before injecting latency
    logger.info("Verifying steady-state traffic through Toxiproxy proxy (5s)...")
    await asyncio.sleep(5.0)

    # INJECT LATENCY
    log_decision("orchestrator", "INJECT", f"Injecting {latency_ms}ms latency on {target_link}")
    injected = executor.inject_latency(proxy_name="fleet-ingest", latency_ms=latency_ms)
    if not injected:
        executor.reset_all()
        loadgen.stop()
        observer.stop()
        return {"status": "injection_failed", "reason": "Toxiproxy refused toxic"}

    injection_start_time = time.time()
    aborted = False
    abort_reason = None
    stale_data_observed = False

    try:
        # Monitoring loop
        while True:
            elapsed = time.time() - injection_start_time
            recent_obs = list(observer.last_observations.values())
            max_age = max((o.get("data_age_s", 0) for o in recent_obs), default=0)

            if max_age > 3.0:
                stale_data_observed = True

            logger.info(
                f"[T+{elapsed:.1f}s] Observing App | Max Data Age: {max_age:.2f}s | "
                f"Active Vehicles: {len(recent_obs)}"
            )

            should_abort, reason = guard.check(recent_obs, elapsed, max_duration_s=float(duration_s))
            if should_abort:
                aborted = True
                abort_reason = reason
                logger.warning(f"CIRCUIT BREAKER TRIGGERED: {reason}")
                break

            await asyncio.sleep(1.0)

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("KeyboardInterrupt received! Initiating emergency rollback...")
        abort_reason = "MANUAL_INTERRUPT"
        aborted = True
    finally:
        # GUARANTEED ROLLBACK (safe to run twice)
        rollback_start = time.time()
        logger.info("Executing automatic rollback / toxic removal...")
        executor.reset_all()
        log_decision("orchestrator", "ROLLBACK", "Toxics removed and proxy restored", {"abort_reason": abort_reason})

        # Observe recovery
        logger.info("Observing recovery to normal data age...")
        recovery_time_s = None
        for _ in range(15):
            await asyncio.sleep(1.0)
            recent_obs = list(observer.last_observations.values())
            curr_age = max((o.get("data_age_s", 999) for o in recent_obs), default=999)
            if curr_age < 2.0:
                recovery_time_s = round(time.time() - rollback_start, 2)
                logger.info(f"System recovered to normal data age ({curr_age:.2f}s) in {recovery_time_s}s!")
                break

        loadgen.stop()
        observer.stop()
        await asyncio.sleep(1.0)

        # Restore background simulation if it was running before
        if sim_was_running:
            try:
                httpx.post("http://localhost:8000/api/fleet/simulate/toggle", timeout=3.0)
                logger.info("Restored background simulator loop.")
            except Exception:
                pass

    # Compute summary
    summary = compute_metrics_summary("injection")
    result = {
        "target_link": target_link,
        "injected_latency_ms": latency_ms,
        "stale_data_confirmed": stale_data_observed,
        "aborted_by_guard": aborted,
        "abort_reason": abort_reason,
        "recovery_time_s": recovery_time_s,
        "metrics": summary,
    }

    with open(RESULTS_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    # Agent 3: Analyst generates day1_summary.md
    logger.info("Invoking Agent 3 (Analyst) to generate day1_summary.md...")
    try:
        from chaos.agents.analyst import run_analyst
        await run_analyst()
    except Exception as ae:
        logger.error(f"Analyst generation error: {ae}")

    logger.info("=== Experiment Run Finished ===")
    return result
