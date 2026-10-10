"""Tests for LangGraph multi-agent workflow."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.agent.graph import create_chaos_graph
from app.models import (
    DependencyGraph,
    GraphNode,
    GraphEdge,
    MetricSnapshot,
    SafetyCheckResult,
    ExperimentRun,
    ExperimentStatus,
    ExperimentSpec,
    ExperimentTarget,
    ExperimentType,
)


@pytest.fixture
def mock_components():
    # Mock Mapper
    mock_mapper = MagicMock()
    mock_mapper.graph = DependencyGraph(
        nodes=[GraphNode(id="cartservice", replicas=1, blast_radius=2, critical=True)],
        edges=[],
    )
    mock_mapper.build_graph.return_value = mock_mapper.graph

    # Mock Planner Cls
    class MockPlanner:
        def __init__(self, graph, db=None):
            self.graph = graph
        def generate_candidates(self):
            spec = ExperimentSpec(
                name="Kill cart pod",
                type=ExperimentType.POD_KILL,
                target=ExperimentTarget(namespace="boutique", service="cartservice"),
                params={"pods": 1},
                duration_s=60,
                hypothesis="Cart handles pod kill",
            )
            from app.models import ScoredCandidate
            return [ScoredCandidate(spec=spec, score=0.8, impact=0.8, info_gain=0.9, risk=0.1, recency_penalty=0.0)]

    # Mock Safety
    mock_safety = MagicMock()
    mock_safety.validate.return_value = SafetyCheckResult(
        passed=True, checks={"namespace_allowlist": True, "service_allowlist": True}
    )

    # Mock Probe
    mock_probe = MagicMock()
    mock_probe.get_snapshot = AsyncMock(return_value=MetricSnapshot(success_rate=0.999, latency_p99_ms=40.0))

    # Mock Runner
    mock_runner = MagicMock()
    mock_run = ExperimentRun(
        spec=ExperimentSpec(
            name="Kill cart pod",
            type=ExperimentType.POD_KILL,
            target=ExperimentTarget(namespace="boutique", service="cartservice"),
        ),
        status=ExperimentStatus.ANALYZED,
        baseline_snapshot=MetricSnapshot(success_rate=1.0, latency_p99_ms=30.0),
        during_snapshots=[MetricSnapshot(success_rate=0.98, latency_p99_ms=45.0)],
        after_snapshot=MetricSnapshot(success_rate=1.0, latency_p99_ms=32.0),
    )
    mock_runner.run_experiment = AsyncMock(return_value=mock_run)

    # Mock Analyzer
    mock_analyzer = MagicMock()
    from app.models import ExperimentReport, Verdict
    mock_report = ExperimentReport(
        experiment_id="exp-test",
        verdict=Verdict.PASSED,
        interpretation="Cartservice proved resilient under chaos.",
        recommendations=["Scale replicas to 2."],
    )
    mock_analyzer.analyze.return_value = mock_report

    return {
        "mapper": mock_mapper,
        "planner_cls": MockPlanner,
        "safety": mock_safety,
        "runner": mock_runner,
        "analyzer": mock_analyzer,
        "probe": mock_probe,
    }


@pytest.mark.asyncio
async def test_langgraph_pause_at_human_approval(mock_components):
    graph = create_chaos_graph(
        mapper=mock_components["mapper"],
        planner_cls=mock_components["planner_cls"],
        safety=mock_components["safety"],
        runner=mock_components["runner"],
        analyzer=mock_components["analyzer"],
        probe=mock_components["probe"],
        interrupt_for_approval=True,
    )

    thread_id = "test-thread-01"
    config = {"configurable": {"thread_id": thread_id}}

    # Initial run up to human_approval breakpoint
    state = await graph.ainvoke(
        {"thread_id": thread_id, "target_service": "cartservice", "audit_log": []},
        config=config,
    )

    assert state["status"] == "safety_passed"
    assert state["chosen_spec"] is not None
    assert state["chosen_spec"]["target"]["service"] == "cartservice"
    assert state["safety_result"]["passed"] is True

    # Check next node waiting in state snapshot
    snapshot = graph.get_state(config)
    assert "human_approval" in snapshot.next


@pytest.mark.asyncio
async def test_langgraph_resume_after_human_approval(mock_components):
    graph = create_chaos_graph(
        mapper=mock_components["mapper"],
        planner_cls=mock_components["planner_cls"],
        safety=mock_components["safety"],
        runner=mock_components["runner"],
        analyzer=mock_components["analyzer"],
        probe=mock_components["probe"],
        interrupt_for_approval=True,
    )

    thread_id = "test-thread-02"
    config = {"configurable": {"thread_id": thread_id}}

    # 1. Run to pause
    await graph.ainvoke(
        {"thread_id": thread_id, "target_service": "cartservice", "audit_log": []},
        config=config,
    )

    # 2. Operator approves (resume past breakpoint)
    final_state = await graph.ainvoke(None, config=config)

    assert final_state["status"] == "completed"
    assert final_state["human_approved"] is True
    assert final_state["report"] is not None
    assert final_state["report"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_langgraph_safety_rejected_aborts(mock_components):
    # Safety fails
    mock_components["safety"].validate.return_value = SafetyCheckResult(
        passed=False, checks={"service_allowlist": False}, reasons=["Service forbidden"]
    )

    graph = create_chaos_graph(
        mapper=mock_components["mapper"],
        planner_cls=mock_components["planner_cls"],
        safety=mock_components["safety"],
        runner=mock_components["runner"],
        analyzer=mock_components["analyzer"],
        probe=mock_components["probe"],
        interrupt_for_approval=True,
    )

    thread_id = "test-thread-03"
    config = {"configurable": {"thread_id": thread_id}}

    final_state = await graph.ainvoke(
        {"thread_id": thread_id, "target_service": "cartservice", "audit_log": []},
        config=config,
    )

    assert final_state["status"] == "aborted"
    assert final_state["safety_result"]["passed"] is False


def test_api_agent_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        # 1. Orchestrate
        resp = client.post("/api/agent/orchestrate", json={"target_service": "cartservice"})
        assert resp.status_code == 200
        data = resp.json()
        assert "thread_id" in data
        assert "audit_log" in data
        assert data["status"] in ("safety_passed", "safety_rejected", "aborted")

        thread_id = data["thread_id"]

        # 2. Check state endpoint
        state_resp = client.get(f"/api/agent/{thread_id}/state")
        assert state_resp.status_code == 200
        state_data = state_resp.json()
        assert state_data["thread_id"] == thread_id
