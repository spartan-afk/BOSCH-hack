"""Experiment planner: scores and ranks candidate chaos experiments.

Two layers:
1. Deterministic scoring decides what is eligible and ranked
2. LLM adds hypotheses and rationale (stubbed for now)

Score formula:
  score = w_i * impact + w_g * info_gain - w_r * risk - w_h * recency_penalty

The LLM never overrides the safety gate.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from app.models import (
    AbortThresholds,
    DependencyGraph,
    ExperimentSpec,
    ExperimentTarget,
    ExperimentType,
    GraphNode,
    MetricSnapshot,
    ScoredCandidate,
)

logger = logging.getLogger(__name__)

# Starting weights per spec
W_IMPACT = 0.35
W_INFO_GAIN = 0.35
W_RISK = 0.20
W_RECENCY = 0.10

# Experiment templates for each type
TEMPLATES = {
    ExperimentType.POD_KILL: {
        "name_template": "Kill one {service} pod",
        "params": {"pods": 1},
        "duration_s": 60,
        "hypothesis_template": "{service} stays above 95% success because a second replica exists.",
    },
    ExperimentType.NETWORK_DELAY: {
        "name_template": "Add 300ms network delay to {service}",
        "params": {"delay_ms": 300, "jitter_ms": 50},
        "duration_s": 60,
        "hypothesis_template": "Upstream services handle {service} latency gracefully with timeouts/retries.",
    },
    ExperimentType.POD_UNAVAILABLE: {
        "name_template": "Make one {service} pod unavailable",
        "params": {"pods": 1},
        "duration_s": 60,
        "hypothesis_template": "Traffic shifts to remaining {service} replicas without errors.",
    },
}


class ExperimentPlanner:
    """Generates, scores, and ranks candidate experiments."""

    def __init__(self, graph: DependencyGraph, db=None):
        self.graph = graph
        self.db = db
        self._nodes: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
        self._max_blast_radius = max((n.blast_radius for n in graph.nodes), default=1) or 1

    def generate_candidates(self, experiment_types: list[ExperimentType] | None = None
                            ) -> list[ScoredCandidate]:
        """Generate and score all candidate experiments.

        For each allowlisted service x each supported failure type,
        build a spec from templates and score it.
        """
        if experiment_types is None:
            experiment_types = [ExperimentType.POD_KILL]  # Day 1: just pod_kill

        candidates: list[ScoredCandidate] = []

        for node in self.graph.nodes:
            for exp_type in experiment_types:
                template = TEMPLATES.get(exp_type)
                if not template:
                    continue

                spec = ExperimentSpec(
                    name=template["name_template"].format(service=node.id),
                    type=exp_type,
                    target=ExperimentTarget(namespace="boutique", service=node.id),
                    params=template["params"].copy(),
                    duration_s=template["duration_s"],
                    hypothesis=template["hypothesis_template"].format(service=node.id),
                )

                # Score the candidate
                impact = self._score_impact(node)
                info_gain = self._score_info_gain(node.id, exp_type)
                risk = self._score_risk(node, spec)
                recency = self._score_recency(node.id, exp_type)

                score = (W_IMPACT * impact + W_INFO_GAIN * info_gain
                         - W_RISK * risk - W_RECENCY * recency)

                # Reject high-risk specs
                if risk > 0.7:
                    continue

                candidates.append(ScoredCandidate(
                    spec=spec,
                    score=round(score, 4),
                    impact=round(impact, 4),
                    info_gain=round(info_gain, 4),
                    risk=round(risk, 4),
                    recency_penalty=round(recency, 4),
                    rationale=self._build_rationale(node, impact, info_gain, risk),
                ))

        # Sort by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _score_impact(self, node: GraphNode) -> float:
        """Impact score: blast_radius / max + checkout path bonus."""
        base = node.blast_radius / self._max_blast_radius
        bonus = 0.2 if node.critical else 0.0
        return min(base + bonus, 1.0)

    def _score_info_gain(self, service: str, exp_type: ExperimentType) -> float:
        """Info gain: higher if never tested, lower if tested and passed."""
        if not self.db:
            return 1.0  # No history = max gain

        history = self.db.get_experiment_history(service, exp_type.value, limit=5)
        if not history:
            return 1.0

        # Check if a fix was applied (parent_id exists in recent history)
        latest = history[0]
        if latest.parent_id:
            return 0.9  # Fix applied, retest valuable

        # Check latest verdict
        report = self.db.get_report(latest.id)
        if report:
            if report.verdict == "passed":
                return 0.2
            elif report.verdict == "degraded":
                return 0.6
            else:
                return 0.4  # Failed = we know the weakness, moderate gain
        return 0.6  # Tested but no report = ambiguous

    def _score_risk(self, node: GraphNode, spec: ExperimentSpec) -> float:
        """Risk score: fraction of limits used + single_point penalty."""
        # Fraction of limits used
        max_duration = 120
        max_pods = 1
        max_delay = 500

        duration_frac = spec.duration_s / max_duration
        pods_frac = spec.params.get("pods", 1) / max_pods
        delay_frac = spec.params.get("delay_ms", 0) / max_delay

        limit_risk = (duration_frac + pods_frac + delay_frac) / 3

        # Single point penalty
        sp_penalty = 0.3 if node.single_point else 0.0

        return min(limit_risk + sp_penalty, 1.0)

    def _score_recency(self, service: str, exp_type: ExperimentType) -> float:
        """Recency penalty: 1.0 if just run, decaying to 0 over 1 hour."""
        if not self.db:
            return 0.0

        history = self.db.get_experiment_history(service, exp_type.value, limit=1)
        if not history:
            return 0.0

        latest = history[0]
        if not latest.ended_at:
            return 1.0  # Still running

        elapsed = (datetime.utcnow() - latest.ended_at).total_seconds()
        cooldown = 300  # 5 min cooldown
        hour = 3600

        if elapsed < cooldown:
            return 1.0
        elif elapsed < hour:
            return max(0.0, 1.0 - (elapsed - cooldown) / (hour - cooldown))
        return 0.0

    @staticmethod
    def _build_rationale(node: GraphNode, impact: float, info_gain: float, risk: float) -> str:
        parts = []
        if node.critical:
            parts.append("on checkout path")
        if node.blast_radius > 2:
            parts.append(f"high blast radius ({node.blast_radius} upstream services)")
        if node.single_point:
            parts.append("single point of failure")
        if info_gain >= 0.8:
            parts.append("never tested or fix applied")
        if risk < 0.3:
            parts.append("low risk")
        return "; ".join(parts) if parts else "standard candidate"

    def get_top_candidates(self, n: int = 5,
                           experiment_types: list[ExperimentType] | None = None
                           ) -> list[ScoredCandidate]:
        """Return the top N scored candidates."""
        candidates = self.generate_candidates(experiment_types)
        return candidates[:n]
