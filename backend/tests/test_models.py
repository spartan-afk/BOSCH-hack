"""Tests for app.models — Pydantic models, enums, defaults, serialization."""
import json
import pytest
from datetime import datetime

from app.models import (
    AbortThresholds,
    BaselineSnapshot,
    CompareResult,
    CompareVerdict,
    DependencyGraph,
    ExperimentEvent,
    ExperimentReport,
    ExperimentRun,
    ExperimentSpec,
    ExperimentStatus,
    ExperimentTarget,
    ExperimentType,
    GraphEdge,
    GraphNode,
    MetricDelta,
    MetricSnapshot,
    SafetyCheckResult,
    ScoredCandidate,
    Verdict,
)


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnums:
    def test_experiment_type_values(self):
        assert ExperimentType.POD_KILL.value == "pod_kill"
        assert ExperimentType.NETWORK_DELAY.value == "network_delay"
        assert ExperimentType.STRESS_CPU.value == "stress_cpu"
        assert ExperimentType.IO_FAULT.value == "io_fault"
        assert ExperimentType.TRAFFIC_SPIKE.value == "traffic_spike"
        assert ExperimentType.POD_UNAVAILABLE.value == "pod_unavailable"

    def test_experiment_status_values(self):
        all_statuses = [
            "planned", "validated", "baselining", "injecting",
            "observing", "recovering", "analyzed", "aborted", "killed", "failed",
        ]
        assert sorted(s.value for s in ExperimentStatus) == sorted(all_statuses)

    def test_verdict_values(self):
        assert Verdict.PASSED.value == "passed"
        assert Verdict.DEGRADED.value == "degraded"
        assert Verdict.FAILED.value == "failed"

    def test_compare_verdict_values(self):
        assert CompareVerdict.IMPROVED.value == "improved"
        assert CompareVerdict.UNCHANGED.value == "unchanged"
        assert CompareVerdict.REGRESSED.value == "regressed"


# ── ExperimentSpec ────────────────────────────────────────────────────────────

class TestExperimentSpec:
    def test_defaults(self):
        spec = ExperimentSpec(
            name="test", type=ExperimentType.POD_KILL,
            target=ExperimentTarget(service="frontend"),
        )
        assert spec.id.startswith("exp-")
        assert spec.duration_s == 60
        assert spec.hypothesis == ""
        assert spec.abort.success_rate_min == 0.90

    def test_custom_fields(self):
        spec = ExperimentSpec(
            id="exp-custom",
            name="custom", type=ExperimentType.NETWORK_DELAY,
            target=ExperimentTarget(namespace="ns", service="svc"),
            params={"delay_ms": 200}, duration_s=30,
            hypothesis="it works",
            abort=AbortThresholds(success_rate_min=0.80),
        )
        assert spec.id == "exp-custom"
        assert spec.target.namespace == "ns"
        assert spec.params["delay_ms"] == 200

    def test_serialization_roundtrip(self):
        spec = ExperimentSpec(
            name="test", type=ExperimentType.POD_KILL,
            target=ExperimentTarget(service="cartservice"),
        )
        j = spec.model_dump_json()
        recovered = ExperimentSpec.model_validate_json(j)
        assert recovered.name == spec.name
        assert recovered.type == spec.type
        assert recovered.target.service == "cartservice"


# ── ExperimentTarget ──────────────────────────────────────────────────────────

class TestExperimentTarget:
    def test_default_namespace(self):
        t = ExperimentTarget(service="svc")
        assert t.namespace == "boutique"


# ── AbortThresholds ──────────────────────────────────────────────────────────

class TestAbortThresholds:
    def test_defaults(self):
        a = AbortThresholds()
        assert a.success_rate_min == 0.90
        assert a.p95_latency_max_ms == 2000.0
        assert a.max_duration_s == 90


# ── MetricSnapshot ────────────────────────────────────────────────────────────

class TestMetricSnapshot:
    def test_defaults(self):
        snap = MetricSnapshot()
        assert snap.success_rate == 1.0
        assert snap.error_rate == 0.0
        assert snap.p50_latency_ms == 0.0

    def test_custom_values(self):
        snap = MetricSnapshot(
            success_rate=0.95, error_rate=0.05,
            p50_latency_ms=100, p95_latency_ms=250,
            pod_restarts={"frontend": 2},
        )
        assert snap.pod_restarts["frontend"] == 2


# ── BaselineSnapshot ─────────────────────────────────────────────────────────

class TestBaselineSnapshot:
    def test_defaults(self):
        bl = BaselineSnapshot()
        assert bl.id.startswith("bl-")
        assert bl.duration_s == 300
        assert bl.success_rate_mean == 0.0


# ── GraphNode / GraphEdge / DependencyGraph ──────────────────────────────────

class TestGraph:
    def test_graph_node_defaults(self):
        n = GraphNode(id="svc")
        assert n.kind == "service"
        assert n.replicas == 1
        assert n.critical is False
        assert n.blast_radius == 0
        assert n.single_point is False

    def test_graph_edge_aliases(self):
        e = GraphEdge(**{"from": "a", "to": "b", "source": "env"})
        assert e.source == "a"  # aliased from "from"
        assert e.target == "b"  # aliased from "to"
        assert e.edge_source == "env"  # aliased from "source"

    def test_dependency_graph(self):
        g = DependencyGraph(
            nodes=[GraphNode(id="a"), GraphNode(id="b")],
            edges=[GraphEdge(**{"from": "a", "to": "b", "source": "env"})],
        )
        assert len(g.nodes) == 2
        assert len(g.edges) == 1


# ── ExperimentRun ─────────────────────────────────────────────────────────────

class TestExperimentRun:
    def _make_run(self):
        spec = ExperimentSpec(
            name="test", type=ExperimentType.POD_KILL,
            target=ExperimentTarget(service="frontend"),
        )
        return ExperimentRun(spec=spec)

    def test_defaults(self):
        run = self._make_run()
        assert run.status == ExperimentStatus.PLANNED
        assert run.parent_id is None
        assert run.during_snapshots == []
        assert run.events == []
        assert run.report is None

    def test_add_event(self):
        run = self._make_run()
        run.add_event("injection_start", "created CRD")
        assert len(run.events) == 1
        assert run.events[0].type == "injection_start"

    def test_transition(self):
        run = self._make_run()
        run.transition(ExperimentStatus.VALIDATED, "OK")
        assert run.status == ExperimentStatus.VALIDATED
        assert len(run.events) == 1
        assert "PLANNED" in run.events[0].detail.upper() or "planned" in run.events[0].detail


# ── ExperimentReport ──────────────────────────────────────────────────────────

class TestExperimentReport:
    def test_defaults(self):
        r = ExperimentReport(experiment_id="exp-1", verdict=Verdict.PASSED)
        assert r.model == "rule-based"
        assert r.evidence == []
        assert r.recommendations == []


# ── CompareResult ─────────────────────────────────────────────────────────────

class TestCompareResult:
    def test_basic(self):
        cr = CompareResult(
            original_id="a", retest_id="b",
            verdict=CompareVerdict.IMPROVED,
            summary="Better",
        )
        assert cr.verdict == CompareVerdict.IMPROVED


# ── ScoredCandidate ──────────────────────────────────────────────────────────

class TestScoredCandidate:
    def test_fields(self):
        spec = ExperimentSpec(
            name="test", type=ExperimentType.POD_KILL,
            target=ExperimentTarget(service="x"),
        )
        sc = ScoredCandidate(
            spec=spec, score=0.5, impact=0.6, info_gain=0.7,
            risk=0.2, recency_penalty=0.1, rationale="good",
        )
        assert sc.score == 0.5


# ── SafetyCheckResult ────────────────────────────────────────────────────────

class TestSafetyCheckResult:
    def test_passed(self):
        r = SafetyCheckResult(passed=True, checks={"a": True})
        assert r.passed
        assert r.reasons == []

    def test_failed(self):
        r = SafetyCheckResult(passed=False, checks={"a": False}, reasons=["bad"])
        assert not r.passed
        assert "bad" in r.reasons
