"""Experiment runner: orchestrates the full lifecycle of a chaos experiment.

States: PLANNED -> VALIDATED -> BASELINING -> INJECTING -> OBSERVING ->
        RECOVERING -> ANALYZED
Terminal: ABORTED (threshold), KILLED (manual), FAILED (error)

The runner:
1. Snapshots baseline metrics
2. Creates the Chaos Mesh CRD
3. Starts the watchdog
4. Collects during-chaos metrics
5. Deletes the CRD on completion/abort/kill
6. Waits for recovery
7. Snapshots post-chaos metrics
8. Triggers analysis
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional

from app.chaos.templates import build_chaos_crd
from app.models import (
    ExperimentEvent,
    ExperimentRun,
    ExperimentSpec,
    ExperimentStatus,
    MetricSnapshot,
)

logger = logging.getLogger(__name__)


class ExperimentRunner:
    """Manages the lifecycle of chaos experiments."""

    def __init__(self, k8s_client, probe, safety_controller, db, analyzer=None):
        self.k8s = k8s_client
        self.probe = probe
        self.safety = safety_controller
        self.db = db
        self.analyzer = analyzer
        self._current_run: Optional[ExperimentRun] = None
        self._chaos_crd_info: Optional[tuple[str, str, str]] = None  # kind, name, namespace
        self._pre_run_replicas: dict[str, int] = {}
        self._sse_callbacks: list[Callable] = []

    @property
    def current_run(self) -> Optional[ExperimentRun]:
        return self._current_run

    @property
    def is_running(self) -> bool:
        return self._current_run is not None and self._current_run.status in (
            ExperimentStatus.BASELINING,
            ExperimentStatus.INJECTING,
            ExperimentStatus.OBSERVING,
            ExperimentStatus.RECOVERING,
        )

    def register_sse_callback(self, callback: Callable) -> None:
        """Register a callback for SSE events."""
        self._sse_callbacks.append(callback)

    async def _emit_event(self, event_type: str, data: dict) -> None:
        """Emit an event to all SSE listeners."""
        for cb in self._sse_callbacks:
            try:
                await cb(event_type, data)
            except Exception as e:
                logger.debug(f"SSE callback error: {e}")

    async def run_experiment(self, spec: ExperimentSpec, parent_id: str | None = None) -> ExperimentRun:
        """Execute a full experiment lifecycle.

        This is the main entry point. It runs the complete loop:
        validate -> baseline -> inject -> observe -> recover -> analyze
        """
        run = ExperimentRun(spec=spec, parent_id=parent_id)
        self._current_run = run

        # Save to DB
        self.db.save_experiment(
            exp_id=run.id,
            spec_json=spec.model_dump_json(),
            status=run.status.value,
            parent_id=parent_id,
        )

        try:
            # 1. Validate
            await self._transition(run, ExperimentStatus.VALIDATED, "Pre-flight passed")

            # 2. Record pre-run replica counts for restoration
            self._pre_run_replicas = {}
            try:
                deployments = self.k8s.get_deployments(spec.target.namespace)
                for dep in deployments:
                    self._pre_run_replicas[dep["name"]] = dep["replicas"]
            except Exception as e:
                logger.warning(f"Could not record replica counts: {e}")

            # 3. Baseline snapshot
            await self._transition(run, ExperimentStatus.BASELINING, "Collecting baseline snapshot")
            run.baseline_snapshot = await self.probe.get_snapshot(window_s=30.0)
            self.db.add_event(run.id, "baseline_captured",
                              f"success_rate={run.baseline_snapshot.success_rate:.2%}")

            # 4. Build and inject chaos CRD
            await self._transition(run, ExperimentStatus.INJECTING, "Creating Chaos Mesh CRD")
            kind, crd_spec, crd_name = build_chaos_crd(spec)
            self._chaos_crd_info = (kind, crd_name, spec.target.namespace)

            self.k8s.create_chaos_crd(
                kind=kind,
                spec=crd_spec,
                namespace=spec.target.namespace,
                name=crd_name,
            )
            run.add_event("injection_start", f"Created {kind}/{crd_name}")
            self.db.add_event(run.id, "injection_start", f"{kind}/{crd_name}")

            # 5. Observe with watchdog
            await self._transition(run, ExperimentStatus.OBSERVING, "Monitoring under chaos")

            aborted = False
            abort_reason = ""

            async def on_abort(reason: str):
                nonlocal aborted, abort_reason
                aborted = True
                abort_reason = reason

            async def on_kill():
                nonlocal aborted, abort_reason
                aborted = True
                abort_reason = "Kill switch activated"

            watchdog_task = await self.safety.start_watchdog(
                spec=spec,
                get_snapshot=lambda: self.probe.get_snapshot(window_s=10.0),
                on_abort=on_abort,
                on_kill=on_kill,
            )

            # Collect during-chaos metrics
            observe_start = time.monotonic()
            while not aborted and (time.monotonic() - observe_start) < spec.duration_s:
                await asyncio.sleep(2)
                snapshot = await self.probe.get_snapshot(window_s=10.0)
                run.during_snapshots.append(snapshot)

                # Emit SSE event
                await self._emit_event("metrics", {
                    "experiment_id": run.id,
                    "status": run.status.value,
                    "elapsed_s": round(time.monotonic() - observe_start, 1),
                    "success_rate": snapshot.success_rate,
                    "p95_latency_ms": snapshot.p95_latency_ms,
                    "p50_latency_ms": snapshot.p50_latency_ms,
                })

            # Stop watchdog
            await self.safety.stop_watchdog()

            # 6. Cleanup: delete chaos CRD
            if self._chaos_crd_info:
                kind, name, ns = self._chaos_crd_info
                try:
                    self.k8s.delete_chaos_crd(kind, name, ns)
                    run.add_event("injection_end", f"Deleted {kind}/{name}")
                    self.db.add_event(run.id, "injection_end", f"Deleted {kind}/{name}")
                except Exception as e:
                    logger.error(f"Failed to delete CRD: {e}")
                self._chaos_crd_info = None

            if aborted:
                status = ExperimentStatus.KILLED if "Kill" in abort_reason else ExperimentStatus.ABORTED
                run.abort_reason = abort_reason
                await self._transition(run, status, abort_reason)
                self.db.add_event(run.id, status.value, abort_reason)
            else:
                # 7. Recovery
                await self._transition(run, ExperimentStatus.RECOVERING, "Waiting for recovery")
                await self._wait_for_recovery(run, spec)

            # 8. Post-chaos snapshot
            run.after_snapshot = await self.probe.get_snapshot(window_s=30.0)

            # 9. Analyze (if not aborted/killed)
            if run.status == ExperimentStatus.RECOVERING:
                await self._transition(run, ExperimentStatus.ANALYZED, "Analysis complete")

            # Restore replica counts if needed
            await self._restore_replicas(spec.target.namespace)

            # Update DB
            self.db.update_experiment(
                run.id,
                status=run.status.value,
                ended_at=datetime.utcnow(),
                baseline_json=run.baseline_snapshot.model_dump_json() if run.baseline_snapshot else None,
                during_json=json.dumps([s.model_dump() for s in run.during_snapshots], default=str),
                after_json=run.after_snapshot.model_dump_json() if run.after_snapshot else None,
                events_json=json.dumps([e.model_dump() for e in run.events], default=str),
                abort_reason=run.abort_reason,
            )

            # Generate report
            if self.analyzer and run.status in (ExperimentStatus.ANALYZED, ExperimentStatus.ABORTED):
                try:
                    report = await self.analyzer.analyze(run)
                    run.report = report
                    self.db.save_report(
                        run.id, report.verdict.value,
                        report.model_dump_json(), report.model,
                    )
                except Exception as e:
                    logger.error(f"Analysis failed: {e}")

        except Exception as e:
            logger.error(f"Experiment failed: {e}")
            run.abort_reason = str(e)
            await self._transition(run, ExperimentStatus.FAILED, str(e))
            self.db.update_experiment(run.id, status="failed", ended_at=datetime.utcnow(),
                                     abort_reason=str(e))
            # Emergency cleanup
            await self._emergency_cleanup(spec.target.namespace)

        finally:
            self._current_run = None

        return run

    async def kill(self) -> dict:
        """Emergency kill switch: delete all CRDs, restore replicas.

        Returns status dict.
        """
        logger.warning("KILL SWITCH ACTIVATED")
        self.safety.trigger_kill()

        # Delete all chaos CRDs
        deleted = 0
        try:
            deleted = self.k8s.delete_all_chaos_crds("boutique")
        except Exception as e:
            logger.error(f"Kill switch CRD cleanup error: {e}")

        # Restore replicas
        await self._restore_replicas("boutique")

        # Update current run
        if self._current_run:
            self._current_run.abort_reason = "Kill switch"
            self._current_run.status = ExperimentStatus.KILLED
            self.db.update_experiment(
                self._current_run.id,
                status="killed",
                ended_at=datetime.utcnow(),
                abort_reason="Kill switch",
            )
            self.db.add_event(self._current_run.id, "killed", "Manual kill switch activated")

        return {
            "status": "killed",
            "crds_deleted": deleted,
            "replicas_restored": bool(self._pre_run_replicas),
        }

    async def _transition(self, run: ExperimentRun, status: ExperimentStatus, detail: str = "") -> None:
        """Transition the experiment to a new status and notify listeners."""
        run.transition(status, detail)
        self.db.update_experiment(run.id, status=status.value)
        self.db.add_event(run.id, "status_change", f"{status.value}: {detail}")
        await self._emit_event("status", {
            "experiment_id": run.id,
            "status": status.value,
            "detail": detail,
        })
        logger.info(f"Experiment {run.id}: {status.value} - {detail}")

    async def _wait_for_recovery(self, run: ExperimentRun, spec: ExperimentSpec,
                                  timeout_s: int = 120) -> None:
        """Wait for the system to recover after fault removal."""
        start = time.monotonic()
        stable_since: Optional[float] = None
        stable_window = 30.0
        target_sr = 0.99  # 99% of baseline

        if run.baseline_snapshot:
            target_sr = run.baseline_snapshot.success_rate * 0.99

        while (time.monotonic() - start) < timeout_s:
            await asyncio.sleep(2)
            snapshot = await self.probe.get_snapshot(window_s=10.0)

            if snapshot.success_rate >= target_sr:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) >= stable_window:
                    recovery_time = time.monotonic() - start
                    run.add_event("recovery_complete", f"Recovered in {recovery_time:.1f}s")
                    self.db.add_event(run.id, "recovery_complete", f"{recovery_time:.1f}s")
                    return
            else:
                stable_since = None

        run.add_event("recovery_timeout", f"Did not recover within {timeout_s}s")
        self.db.add_event(run.id, "recovery_timeout", f"{timeout_s}s")

    async def _restore_replicas(self, namespace: str) -> None:
        """Restore deployment replica counts to pre-run values."""
        for name, replicas in self._pre_run_replicas.items():
            try:
                current = self.k8s.get_deployment_replicas(name, namespace)
                if current != replicas:
                    self.k8s.scale_deployment(name, replicas, namespace)
                    logger.info(f"Restored {name} replicas: {current} -> {replicas}")
            except Exception as e:
                logger.warning(f"Failed to restore {name} replicas: {e}")

    async def _emergency_cleanup(self, namespace: str) -> None:
        """Emergency cleanup: delete all CRDs and try to restore replicas."""
        try:
            self.k8s.delete_all_chaos_crds(namespace)
        except Exception:
            pass
        await self._restore_replicas(namespace)
