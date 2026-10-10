"""Tests for app.planner.scoring - ExperimentPlanner ranking and candidate generation."""
import pytest
from app.models import DependencyGraph, GraphEdge, GraphNode, ExperimentType
from app.planner.scoring import ExperimentPlanner


@pytest.fixture
def sample_graph():
    nodes = [
        GraphNode(id="frontend", replicas=2, blast_radius=5, critical=True),
        GraphNode(id="cartservice", replicas=1, blast_radius=2, critical=True),
        GraphNode(id="shippingservice", replicas=1, blast_radius=1, critical=False),
    ]
    edges = [
        GraphEdge(source="frontend", target="cartservice"),
        GraphEdge(source="frontend", target="shippingservice"),
    ]
    return DependencyGraph(nodes=nodes, edges=edges)


def test_generate_candidates_pod_kill(sample_graph):
    planner = ExperimentPlanner(graph=sample_graph)
    candidates = planner.generate_candidates(experiment_types=[ExperimentType.POD_KILL])

    assert len(candidates) > 0
    # Higher blast radius & critical node should have higher score or impact
    # Candidates are sorted descending by score
    assert candidates[0].score >= candidates[-1].score
    for c in candidates:
        assert c.spec.type == ExperimentType.POD_KILL
        assert 0.0 <= c.impact <= 1.0
        assert 0.0 <= c.risk <= 1.0


def test_generate_candidates_multiple_types(sample_graph):
    planner = ExperimentPlanner(graph=sample_graph)
    candidates = planner.generate_candidates(
        experiment_types=[ExperimentType.POD_KILL, ExperimentType.NETWORK_DELAY]
    )
    assert len(candidates) > 0
    types = {c.spec.type for c in candidates}
    assert ExperimentType.POD_KILL in types
