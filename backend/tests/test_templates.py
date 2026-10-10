"""Tests for app.chaos.templates - Chaos Mesh CRD builders."""
import pytest
from app.models import ExperimentSpec, ExperimentTarget, ExperimentType
from app.chaos.templates import build_chaos_crd


def test_build_pod_kill_crd():
    spec = ExperimentSpec(
        name="Kill cart",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="boutique", service="cartservice"),
        params={"pods": 1},
        duration_s=60,
    )
    kind, crd_spec, name = build_chaos_crd(spec)
    assert kind == "PodChaos"
    assert crd_spec["action"] == "pod-kill"
    assert crd_spec["duration"] == "60s"
    assert crd_spec["selector"]["namespaces"] == ["boutique"]
    assert crd_spec["selector"]["labelSelectors"]["app"] == "cartservice"


def test_build_network_delay_crd():
    spec = ExperimentSpec(
        name="Delay frontend",
        type=ExperimentType.NETWORK_DELAY,
        target=ExperimentTarget(namespace="boutique", service="frontend"),
        params={"delay_ms": 200, "jitter_ms": 20},
        duration_s=45,
    )
    kind, crd_spec, name = build_chaos_crd(spec)
    assert kind == "NetworkChaos"
    assert crd_spec["action"] == "delay"
    assert crd_spec["delay"]["latency"] == "200ms"
    assert crd_spec["delay"]["jitter"] == "20ms"


def test_build_pod_unavailable_crd():
    spec = ExperimentSpec(
        name="Pod failure",
        type=ExperimentType.POD_UNAVAILABLE,
        target=ExperimentTarget(namespace="boutique", service="recommendationservice"),
        params={"pods": 1},
        duration_s=30,
    )
    kind, crd_spec, name = build_chaos_crd(spec)
    assert kind == "PodChaos"
    assert crd_spec["action"] == "pod-failure"
