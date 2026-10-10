"""Pydantic models for the chaos platform domain objects.

These models define the experiment spec, metrics, reports, and graph schema
shared between the planner, safety controller, runner, and analyzer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ExperimentType(str, Enum):
    POD_KILL = "pod_kill"
    NETWORK_DELAY = "network_delay"
    POD_UNAVAILABLE = "pod_unavailable"
    STRESS_CPU = "stress_cpu"
    IO_FAULT = "io_fault"
    TRAFFIC_SPIKE = "traffic_spike"


class ExperimentStatus(str, Enum):
    PLANNED = "planned"
    VALIDATED = "validated"
    BASELINING = "baselining"
    INJECTING = "injecting"
    OBSERVING = "observing"
    RECOVERING = "recovering"
    ANALYZED = "analyzed"
    ABORTED = "aborted"
    KILLED = "killed"
    FAILED = "failed"


class Verdict(str, Enum):
    PASSED = "passed"
    DEGRADED = "degraded"
    FAILED = "failed"


class CompareVerdict(str, Enum):
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"


# ---------------------------------------------------------------------------
# Experiment spec (the unit shared by planner, safety, runner)
# ---------------------------------------------------------------------------

class ExperimentTarget(BaseModel):
    namespace: str = "boutique"
    service: str


class AbortThresholds(BaseModel):
    success_rate_min: float = 0.90
    p95_latency_max_ms: float = 2000.0
    max_duration_s: int = 90


class ExperimentSpec(BaseModel):
    """The complete specification for one chaos experiment."""
    id: str = Field(default_factory=lambda: f"exp-{uuid.uuid4().hex[:8]}")
    name: str
    type: ExperimentType
    target: ExperimentTarget
    params: dict = Field(default_factory=dict)
    duration_s: int = 60
    hypothesis: str = ""
    abort: AbortThresholds = Field(default_factory=AbortThresholds)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class MetricSnapshot(BaseModel):
    """A snapshot of system health at a point in time."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    success_rate: float = 1.0
    error_rate: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    pod_restarts: dict[str, int] = Field(default_factory=dict)  # service -> count
    pod_ready: dict[str, bool] = Field(default_factory=dict)  # service -> ready


class BaselineSnapshot(BaseModel):
    """Steady-state baseline computed from a 5-min probe run."""
    id: str = Field(default_factory=lambda: f"bl-{uuid.uuid4().hex[:8]}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_s: int = 300
    success_rate_mean: float = 0.0
    success_rate_std: float = 0.0
    error_rate_mean: float = 0.0
    p50_latency_mean: float = 0.0
    p50_latency_std: float = 0.0
    p95_latency_mean: float = 0.0
    p95_latency_std: float = 0.0
    pod_restarts: dict[str, int] = Field(default_factory=dict)
    raw_samples: list[MetricSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

class GraphNode(BaseModel):
    id: str
    kind: str = "service"
    replicas: int = 1
    critical: bool = False
    protocol: str = "grpc"
    blast_radius: int = 0
    single_point: bool = False


class GraphEdge(BaseModel):
    source: str = Field(..., alias="from")
    target: str = Field(..., alias="to")
    edge_source: str = Field("env", alias="source")
    confidence: float = 0.9

    model_config = {"populate_by_name": True}


class DependencyGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Experiment run record
# ---------------------------------------------------------------------------

class ExperimentEvent(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    type: str  # e.g. "injection_start", "breach_detected", "abort", "recovery"
    detail: str = ""


class ExperimentRun(BaseModel):
    """Complete record of a single experiment execution."""
    id: str = Field(default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}")
    spec: ExperimentSpec
    status: ExperimentStatus = ExperimentStatus.PLANNED
    parent_id: Optional[str] = None  # for retests
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    baseline_snapshot: Optional[MetricSnapshot] = None
    during_snapshots: list[MetricSnapshot] = Field(default_factory=list)
    after_snapshot: Optional[MetricSnapshot] = None
    events: list[ExperimentEvent] = Field(default_factory=list)
    report: Optional[ExperimentReport] = None
    abort_reason: Optional[str] = None

    def add_event(self, event_type: str, detail: str = "") -> None:
        self.events.append(ExperimentEvent(type=event_type, detail=detail))

    def transition(self, new_status: ExperimentStatus, detail: str = "") -> None:
        self.add_event("status_change", f"{self.status.value} -> {new_status.value}: {detail}")
        self.status = new_status


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class MetricDelta(BaseModel):
    metric: str
    baseline_value: float
    during_value: float
    after_value: float
    delta_percent: float


class ExperimentReport(BaseModel):
    """Analysis report for one experiment."""
    experiment_id: str
    verdict: Verdict
    evidence: list[MetricDelta] = Field(default_factory=list)
    interpretation: str = ""
    weakness: str = ""
    recommendations: list[str] = Field(default_factory=list)
    retest_spec_id: Optional[str] = None
    model: str = "rule-based"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CompareResult(BaseModel):
    """Before/after comparison of two experiment runs."""
    original_id: str
    retest_id: str
    verdict: CompareVerdict
    metric_deltas: list[MetricDelta] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class ScoredCandidate(BaseModel):
    spec: ExperimentSpec
    score: float
    impact: float
    info_gain: float
    risk: float
    recency_penalty: float
    rationale: str = ""


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

class SafetyCheckResult(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


# Update forward ref
ExperimentRun.model_rebuild()
