"""Safety controller: the hard gate between the planner and the runner.

The LLM cannot bypass this. It only ever produces an experiment spec;
the safety controller decides whether that spec is safe to execute.

Pre-flight checks (all must pass):
  - Namespace allowlist
  - Service allowlist
  - Cluster guard (context must match kind-*)
  - Concurrency (zero other active experiments)
  - Health (success rate >= 99%, all pods Ready for 60s)
  - Limits (duration, pods, delay within bounds)
  - Cooldown (same target+type not run in last 5 min)

Runtime watchdog:
  - Polls every 2s
  - Aborts on threshold breach or kill switch
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import yaml

from app.models import (
    ExperimentSpec,
    ExperimentStatus,
    ExperimentType,
    MetricSnapshot,
    SafetyCheckResult,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"


class SafetyController:
    """Validates experiment specs and monitors running experiments."""

    def __init__(self, k8s_client=None, db=None):
        self.k8s = k8s_client
        self.db = db
        self._allowlist = self._load_config("allowlist.yaml")
        self._limits = self._load_config("limits.yaml")
        self._kill_requested = False
        self._watchdog_task: Optional[asyncio.Task] = None

    @staticmethod
    def _load_config(filename: str) -> dict:
        path = CONFIG_DIR / filename
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f) or {}
        logger.warning(f"Config file {path} not found, using defaults")
        return {}

    # ------------------------------------------------------------------
    # Pre-flight validation
    # ------------------------------------------------------------------

    def validate(self, spec: ExperimentSpec,
                 current_health: Optional[MetricSnapshot] = None) -> SafetyCheckResult:
        """Run all pre-flight checks on an experiment spec.

        Returns SafetyCheckResult with pass/fail for each check.
        """
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        # 1. Namespace allowlist
        allowed_ns = self._allowlist.get("namespaces", ["boutique"])
        ns_ok = spec.target.namespace in allowed_ns
        checks["namespace_allowlist"] = ns_ok
        if not ns_ok:
            reasons.append(f"Namespace '{spec.target.namespace}' not in allowlist {allowed_ns}")

        # 2. Service allowlist
        allowed_svcs = self._allowlist.get("services", [])
        forbidden = self._allowlist.get("forbidden", [])
        svc_ok = spec.target.service in allowed_svcs and spec.target.service not in forbidden
        checks["service_allowlist"] = svc_ok
        if not svc_ok:
            reasons.append(f"Service '{spec.target.service}' not in allowlist or is forbidden")

        # 3. Cluster guard
        context_pattern = self._allowlist.get("cluster_context_pattern", "kind-*")
        context_ok = True
        if self.k8s:
            context_ok = fnmatch.fnmatch(self.k8s.current_context, context_pattern)
        checks["cluster_guard"] = context_ok
        if not context_ok:
            reasons.append(
                f"Cluster context '{self.k8s.current_context}' doesn't match '{context_pattern}'")

        # 4. Concurrency check
        concurrency_ok = True
        if self.db:
            active = self.db.get_active_experiment()
            concurrency_ok = active is None
        checks["concurrency"] = concurrency_ok
        if not concurrency_ok:
            reasons.append("Another experiment is currently active")

        # 5. Health check
        health_ok = True
        if current_health:
            min_sr = self._limits.get("baseline", {}).get("min_success_rate", 0.99)
            health_ok = current_health.success_rate >= min_sr
            # Check pod readiness
            if self.k8s:
                try:
                    pods_ready = self.k8s.all_pods_ready(spec.target.namespace)
                    health_ok = health_ok and pods_ready
                except Exception:
                    pass
        checks["health"] = health_ok
        if not health_ok:
            reasons.append(f"System unhealthy: success_rate={current_health.success_rate if current_health else 'N/A'}")

        # 6. Limits check
        exp_limits = self._limits.get("experiment", {})
        limits_ok = True
        limit_issues = []

        max_duration = exp_limits.get("max_duration_s", 120)
        if spec.duration_s > max_duration:
            limits_ok = False
            limit_issues.append(f"duration_s={spec.duration_s} > max={max_duration}")

        max_pods = exp_limits.get("max_pods_affected", 1)
        pods_in_spec = spec.params.get("pods", 1)
        if pods_in_spec > max_pods:
            limits_ok = False
            limit_issues.append(f"pods={pods_in_spec} > max={max_pods}")

        max_delay = exp_limits.get("max_delay_ms", 500)
        delay_in_spec = spec.params.get("delay_ms", 0)
        if delay_in_spec > max_delay:
            limits_ok = False
            limit_issues.append(f"delay_ms={delay_in_spec} > max={max_delay}")

        checks["limits"] = limits_ok
        if not limits_ok:
            reasons.append(f"Exceeds limits: {', '.join(limit_issues)}")

        # 7. Cooldown check
        cooldown_ok = True
        if self.db:
            cooldown_s = exp_limits.get("cooldown_s", 300)
            history = self.db.get_experiment_history(
                spec.target.service, spec.type.value, limit=1
            )
            if history:
                last = history[0]
                if last.ended_at:
                    elapsed = (datetime.utcnow() - last.ended_at).total_seconds()
                    cooldown_ok = elapsed >= cooldown_s
        checks["cooldown"] = cooldown_ok
        if not cooldown_ok:
            reasons.append(f"Cooldown period not elapsed for {spec.target.service}/{spec.type.value}")

        all_passed = all(checks.values())
        return SafetyCheckResult(
            passed=all_passed,
            checks=checks,
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # Runtime watchdog
    # ------------------------------------------------------------------

    async def start_watchdog(
        self,
        spec: ExperimentSpec,
        get_snapshot: Callable,
        on_abort: Callable,
        on_kill: Callable,
    ) -> asyncio.Task:
        """Start the runtime watchdog that polls every 2s.

        Args:
            spec: The experiment spec with abort thresholds
            get_snapshot: async callable returning MetricSnapshot
            on_abort: async callable to invoke on threshold breach
            on_kill: async callable to invoke on kill switch
        """
        self._kill_requested = False

        async def _watchdog_loop():
            poll_interval = self._limits.get("watchdog", {}).get("poll_interval_s", 2)
            consecutive_breaches = 0
            max_breaches = self._limits.get("abort_thresholds", {}).get("consecutive_breaches", 2)
            start_time = time.monotonic()

            while True:
                await asyncio.sleep(poll_interval)

                # Check kill switch
                if self._kill_requested:
                    logger.warning("Kill switch activated!")
                    await on_kill()
                    return

                # Check elapsed time
                elapsed = time.monotonic() - start_time
                if elapsed > spec.abort.max_duration_s:
                    logger.warning(f"Max duration exceeded: {elapsed:.0f}s > {spec.abort.max_duration_s}s")
                    await on_abort(f"Max duration exceeded: {elapsed:.0f}s")
                    return

                # Check metrics
                try:
                    snapshot = await get_snapshot()

                    breach = False
                    breach_reasons = []

                    if snapshot.success_rate < spec.abort.success_rate_min:
                        breach = True
                        breach_reasons.append(
                            f"success_rate={snapshot.success_rate:.2%} < "
                            f"min={spec.abort.success_rate_min:.2%}"
                        )

                    if snapshot.p95_latency_ms > spec.abort.p95_latency_max_ms:
                        breach = True
                        breach_reasons.append(
                            f"p95_latency={snapshot.p95_latency_ms:.0f}ms > "
                            f"max={spec.abort.p95_latency_max_ms:.0f}ms"
                        )

                    if breach:
                        consecutive_breaches += 1
                        logger.warning(
                            f"Threshold breach {consecutive_breaches}/{max_breaches}: "
                            f"{'; '.join(breach_reasons)}"
                        )
                        if consecutive_breaches >= max_breaches:
                            await on_abort(f"Threshold breached: {'; '.join(breach_reasons)}")
                            return
                    else:
                        consecutive_breaches = 0

                except Exception as e:
                    logger.error(f"Watchdog snapshot error: {e}")

        self._watchdog_task = asyncio.create_task(_watchdog_loop())
        return self._watchdog_task

    async def stop_watchdog(self) -> None:
        """Stop the running watchdog."""
        self._kill_requested = True
        if self._watchdog_task:
            try:
                await asyncio.wait_for(self._watchdog_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._watchdog_task.cancel()

    def trigger_kill(self) -> None:
        """Trigger the kill switch (can be called from any context)."""
        self._kill_requested = True
        logger.warning("KILL SWITCH TRIGGERED")

    @property
    def kill_requested(self) -> bool:
        return self._kill_requested
