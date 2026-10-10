"""Tests for app.safety.controller - SafetyController pre-flight and rules."""
import pytest
from app.models import ExperimentSpec, ExperimentTarget, ExperimentType, MetricSnapshot
from app.safety.controller import SafetyController


@pytest.fixture
def safety_controller():
    return SafetyController(k8s_client=None, db=None)


def test_validate_valid_spec(safety_controller):
    spec = ExperimentSpec(
        name="Kill frontend",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="boutique", service="frontend"),
        params={"pods": 1},
        duration_s=60,
        hypothesis="Frontend stays healthy"
    )
    health = MetricSnapshot(success_rate=0.999, latency_p99_ms=40.0)
    result = safety_controller.validate(spec, current_health=health)
    assert result.passed is True
    assert result.checks["namespace_allowlist"] is True
    assert result.checks["service_allowlist"] is True
    assert result.checks["limits"] is True


def test_validate_disallowed_namespace(safety_controller):
    spec = ExperimentSpec(
        name="Kill kube-system",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="kube-system", service="kube-dns"),
        params={"pods": 1},
        duration_s=60,
    )
    result = safety_controller.validate(spec)
    assert result.passed is False
    assert result.checks["namespace_allowlist"] is False


def test_validate_forbidden_service(safety_controller):
    spec = ExperimentSpec(
        name="Kill payment",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="boutique", service="paymentservice"),
        params={"pods": 1},
        duration_s=60,
    )
    # paymentservice is typically in forbidden list or check how config treats it
    # Let's test non-allowlisted service:
    spec_unknown = ExperimentSpec(
        name="Kill unknown",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="boutique", service="unknown-svc"),
        params={"pods": 1},
        duration_s=60,
    )
    result = safety_controller.validate(spec_unknown)
    assert result.passed is False
    assert result.checks["service_allowlist"] is False


def test_validate_duration_limit_exceeded(safety_controller):
    spec = ExperimentSpec(
        name="Kill frontend long",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="boutique", service="frontend"),
        params={"pods": 1},
        duration_s=999999, # Exceeds limit
    )
    result = safety_controller.validate(spec)
    assert result.passed is False
    assert result.checks["limits"] is False


def test_validate_unhealthy_system(safety_controller):
    spec = ExperimentSpec(
        name="Kill frontend",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace="boutique", service="frontend"),
        params={"pods": 1},
        duration_s=60,
    )
    unhealthy = MetricSnapshot(success_rate=0.85, latency_p99_ms=400.0)
    result = safety_controller.validate(spec, current_health=unhealthy)
    assert result.passed is False
    assert result.checks["health"] is False
