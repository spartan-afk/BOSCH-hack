"""Experiment analyzer: generates reports from evidence bundles.

Two modes:
1. Rule-based (always available): computes verdict from thresholds,
   generates templated interpretation and recommendations.
2. LLM-enhanced (when API key available): sends evidence bundle to
   LLM for deeper analysis. Falls back to rule-based on failure.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.models import (
    CompareResult,
    CompareVerdict,
    ExperimentReport,
    ExperimentRun,
    ExperimentStatus,
    MetricDelta,
    MetricSnapshot,
    Verdict,
)

logger = logging.getLogger(__name__)


class ExperimentAnalyzer:
    """Analyzes experiment results and generates reports."""

    def __init__(self):
        self._llm_available = bool(os.environ.get("ANTHROPIC_API_KEY"))

    async def analyze(self, run: ExperimentRun) -> ExperimentReport:
        """Analyze an experiment run and return a report.

        Uses rule-based analysis first, then enhances with LLM if available.
        """
        # Compute metric deltas
        deltas = self._compute_deltas(run)

        # Determine verdict from thresholds
        verdict = self._compute_verdict(run, deltas)

        # Generate interpretation and recommendations
        interpretation = self._generate_interpretation(run, deltas, verdict)
        weakness = self._identify_weakness(run, deltas, verdict)
        recommendations = self._generate_recommendations(run, deltas, verdict, weakness)

        report = ExperimentReport(
            experiment_id=run.id,
            verdict=verdict,
            evidence=deltas,
            interpretation=interpretation,
            weakness=weakness,
            recommendations=recommendations,
            retest_spec_id=run.spec.id,
            model="rule-based",
        )

        # LLM enhancement (stubbed for now)
        if self._llm_available:
            try:
                report = await self._enhance_with_llm(run, report)
            except Exception as e:
                logger.warning(f"LLM enhancement failed, using rule-based: {e}")

        return report

    def _compute_deltas(self, run: ExperimentRun) -> list[MetricDelta]:
        """Compute metric deltas between baseline, during, and after."""
        deltas = []
        baseline = run.baseline_snapshot
        after = run.after_snapshot

        if not baseline:
            return deltas

        # Compute "during" averages from snapshots
        during_sr = 1.0
        during_p50 = 0.0
        during_p95 = 0.0
        if run.during_snapshots:
            during_sr = sum(s.success_rate for s in run.during_snapshots) / len(run.during_snapshots)
            during_p50 = sum(s.p50_latency_ms for s in run.during_snapshots) / len(run.during_snapshots)
            during_p95 = sum(s.p95_latency_ms for s in run.during_snapshots) / len(run.during_snapshots)

        after_sr = after.success_rate if after else baseline.success_rate
        after_p50 = after.p50_latency_ms if after else baseline.p50_latency_ms
        after_p95 = after.p95_latency_ms if after else baseline.p95_latency_ms

        def pct_change(base: float, current: float) -> float:
            if base == 0:
                return 0.0
            return round(((current - base) / base) * 100, 2)

        deltas.append(MetricDelta(
            metric="success_rate",
            baseline_value=round(baseline.success_rate, 4),
            during_value=round(during_sr, 4),
            after_value=round(after_sr, 4),
            delta_percent=pct_change(baseline.success_rate, during_sr),
        ))
        deltas.append(MetricDelta(
            metric="p50_latency_ms",
            baseline_value=round(baseline.p50_latency_ms, 2),
            during_value=round(during_p50, 2),
            after_value=round(after_p50, 2),
            delta_percent=pct_change(baseline.p50_latency_ms, during_p50),
        ))
        deltas.append(MetricDelta(
            metric="p95_latency_ms",
            baseline_value=round(baseline.p95_latency_ms, 2),
            during_value=round(during_p95, 2),
            after_value=round(after_p95, 2),
            delta_percent=pct_change(baseline.p95_latency_ms, during_p95),
        ))

        return deltas

    @staticmethod
    def _compute_verdict(run: ExperimentRun, deltas: list[MetricDelta]) -> Verdict:
        """Compute verdict from thresholds, not the LLM."""
        if run.status == ExperimentStatus.ABORTED:
            return Verdict.FAILED

        sr_delta = next((d for d in deltas if d.metric == "success_rate"), None)
        p95_delta = next((d for d in deltas if d.metric == "p95_latency_ms"), None)

        if sr_delta and sr_delta.during_value < 0.95:
            return Verdict.FAILED
        if sr_delta and sr_delta.during_value < 0.99:
            return Verdict.DEGRADED
        if p95_delta and p95_delta.delta_percent > 100:  # 2x increase
            return Verdict.DEGRADED

        return Verdict.PASSED

    @staticmethod
    def _generate_interpretation(run: ExperimentRun, deltas: list[MetricDelta],
                                  verdict: Verdict) -> str:
        """Generate a human-readable interpretation."""
        service = run.spec.target.service
        exp_type = run.spec.type.value

        sr_delta = next((d for d in deltas if d.metric == "success_rate"), None)
        p95_delta = next((d for d in deltas if d.metric == "p95_latency_ms"), None)

        parts = [f"Experiment '{run.spec.name}' targeting {service} ({exp_type}):"]

        if sr_delta:
            parts.append(
                f"Success rate went from {sr_delta.baseline_value:.1%} to "
                f"{sr_delta.during_value:.1%} during chaos "
                f"({sr_delta.delta_percent:+.1f}% change)."
            )
        if p95_delta:
            parts.append(
                f"p95 latency went from {p95_delta.baseline_value:.0f}ms to "
                f"{p95_delta.during_value:.0f}ms during chaos "
                f"({p95_delta.delta_percent:+.1f}% change)."
            )

        if verdict == Verdict.PASSED:
            parts.append(
                f"HYPOTHESIS CONFIRMED: The system handled the {exp_type} of {service} gracefully."
            )
        elif verdict == Verdict.DEGRADED:
            parts.append(
                f"PARTIAL FAILURE: The system degraded during {exp_type} of {service}. "
                "The impact was measurable but did not breach critical thresholds."
            )
        else:
            parts.append(
                f"HYPOTHESIS REJECTED: The {exp_type} of {service} caused significant failure. "
                "This reveals a resilience weakness."
            )

        if run.status == ExperimentStatus.ABORTED:
            parts.append(f"Note: Experiment was auto-aborted: {run.abort_reason}")

        return " ".join(parts)

    @staticmethod
    def _identify_weakness(run: ExperimentRun, deltas: list[MetricDelta],
                           verdict: Verdict) -> str:
        """Identify the primary weakness exposed."""
        if verdict == Verdict.PASSED:
            return ""

        service = run.spec.target.service
        exp_type = run.spec.type.value

        weakness_map = {
            ("pod_kill", Verdict.FAILED): f"no_redundancy_for_{service}",
            ("pod_kill", Verdict.DEGRADED): f"slow_failover_for_{service}",
            ("network_delay", Verdict.FAILED): f"no_timeout_on_{service}_calls",
            ("network_delay", Verdict.DEGRADED): f"missing_retry_config_for_{service}",
            ("pod_unavailable", Verdict.FAILED): f"single_point_of_failure_{service}",
            ("stress_cpu", Verdict.FAILED): f"no_resource_limits_for_{service}",
        }

        return weakness_map.get((exp_type, verdict), f"resilience_issue_in_{service}")

    @staticmethod
    def _generate_recommendations(run: ExperimentRun, deltas: list[MetricDelta],
                                   verdict: Verdict, weakness: str) -> list[str]:
        """Generate ordered fix recommendations."""
        if verdict == Verdict.PASSED:
            return ["No action needed — system is resilient to this failure mode."]

        service = run.spec.target.service
        recs = []

        if "redundancy" in weakness or "single_point" in weakness:
            recs.append(f"Increase {service} replicas to at least 2 in the deployment manifest.")
            recs.append(f"Add a PodDisruptionBudget for {service} with minAvailable: 1.")

        if "failover" in weakness:
            recs.append(f"Check readiness probe configuration for {service}.")
            recs.append(f"Consider adding a circuit breaker to callers of {service}.")

        if "timeout" in weakness or "retry" in weakness:
            recs.append(f"Add timeout configuration to callers of {service} (e.g., 2s timeout).")
            recs.append(f"Add retry with exponential backoff to {service} client calls.")

        if "resource_limits" in weakness:
            recs.append(f"Set CPU/memory resource limits for {service}.")

        if not recs:
            recs.append(f"Investigate {service} resilience configuration.")

        recs.append(f"Retest with the same spec ({run.spec.id}) after applying fixes.")
        return recs

    async def _enhance_with_llm(self, run: ExperimentRun,
                                 report: ExperimentReport) -> ExperimentReport:
        """Enhance the report with LLM analysis (stubbed)."""
        # TODO: Implement when API key is provided
        # The LLM receives the evidence bundle and returns enhanced
        # interpretation and recommendations
        logger.info("LLM enhancement not yet implemented, using rule-based report")
        return report

    def compare(self, original: ExperimentRun, retest: ExperimentRun) -> CompareResult:
        """Compare two experiment runs (original vs retest)."""
        orig_deltas = self._compute_deltas(original)
        retest_deltas = self._compute_deltas(retest)

        comparison_deltas = []
        for od in orig_deltas:
            rd = next((d for d in retest_deltas if d.metric == od.metric), None)
            if rd:
                comparison_deltas.append(MetricDelta(
                    metric=od.metric,
                    baseline_value=od.during_value,  # original's during
                    during_value=rd.during_value,      # retest's during
                    after_value=rd.after_value,
                    delta_percent=round(
                        ((rd.during_value - od.during_value) / od.during_value * 100)
                        if od.during_value != 0 else 0.0,
                        2,
                    ),
                ))

        # Determine improvement
        sr_orig = next((d.during_value for d in orig_deltas if d.metric == "success_rate"), 0)
        sr_retest = next((d.during_value for d in retest_deltas if d.metric == "success_rate"), 0)

        if sr_retest > sr_orig + 0.02:  # 2% improvement threshold
            verdict = CompareVerdict.IMPROVED
        elif sr_retest < sr_orig - 0.02:
            verdict = CompareVerdict.REGRESSED
        else:
            verdict = CompareVerdict.UNCHANGED

        return CompareResult(
            original_id=original.id,
            retest_id=retest.id,
            verdict=verdict,
            metric_deltas=comparison_deltas,
            summary=(
                f"Success rate during chaos: {sr_orig:.1%} (original) vs "
                f"{sr_retest:.1%} (retest). Verdict: {verdict.value}."
            ),
        )
