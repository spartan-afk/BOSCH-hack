"""Chaos Mesh CRD templates.

Converts ExperimentSpec to Chaos Mesh CRD specs.
Every CRD includes:
  - duration field (dead-man safety — Chaos Mesh auto-cleans even if orchestrator crashes)
  - managed-by=chaos-platform label
"""
from __future__ import annotations

from app.models import ExperimentSpec, ExperimentType


def build_chaos_crd(spec: ExperimentSpec) -> tuple[str, dict, str]:
    """Convert an ExperimentSpec to a Chaos Mesh CRD body.

    Returns:
        (kind, crd_spec, resource_name)
    """
    builders = {
        ExperimentType.POD_KILL: _build_pod_kill,
        ExperimentType.NETWORK_DELAY: _build_network_delay,
        ExperimentType.POD_UNAVAILABLE: _build_pod_unavailable,
        ExperimentType.STRESS_CPU: _build_stress_cpu,
        ExperimentType.IO_FAULT: _build_io_fault,
    }

    builder = builders.get(spec.type)
    if not builder:
        raise ValueError(f"Unsupported experiment type: {spec.type}")

    return builder(spec)


def _build_pod_kill(spec: ExperimentSpec) -> tuple[str, dict, str]:
    """PodChaos: pod-kill action."""
    name = f"chaos-pod-kill-{spec.target.service}-{spec.id[-8:]}"
    crd_spec = {
        "action": "pod-kill",
        "mode": "fixed",
        "value": str(spec.params.get("pods", 1)),
        "duration": f"{spec.duration_s}s",
        "selector": {
            "namespaces": [spec.target.namespace],
            "labelSelectors": {
                "app": spec.target.service,
            },
        },
    }
    return "PodChaos", crd_spec, name


def _build_network_delay(spec: ExperimentSpec) -> tuple[str, dict, str]:
    """NetworkChaos: delay action."""
    name = f"chaos-net-delay-{spec.target.service}-{spec.id[-8:]}"
    delay_ms = spec.params.get("delay_ms", 300)
    jitter_ms = spec.params.get("jitter_ms", 50)
    loss_pct = spec.params.get("loss_percent", 0)

    crd_spec = {
        "action": "delay",
        "mode": "all",
        "duration": f"{spec.duration_s}s",
        "selector": {
            "namespaces": [spec.target.namespace],
            "labelSelectors": {
                "app": spec.target.service,
            },
        },
        "delay": {
            "latency": f"{delay_ms}ms",
            "jitter": f"{jitter_ms}ms",
            "correlation": "50",
        },
    }

    if loss_pct > 0:
        crd_spec["action"] = "loss"
        crd_spec["loss"] = {
            "loss": str(loss_pct),
            "correlation": "50",
        }

    return "NetworkChaos", crd_spec, name


def _build_pod_unavailable(spec: ExperimentSpec) -> tuple[str, dict, str]:
    """PodChaos: pod-failure action (makes pod unavailable)."""
    name = f"chaos-pod-fail-{spec.target.service}-{spec.id[-8:]}"
    crd_spec = {
        "action": "pod-failure",
        "mode": "fixed",
        "value": str(spec.params.get("pods", 1)),
        "duration": f"{spec.duration_s}s",
        "selector": {
            "namespaces": [spec.target.namespace],
            "labelSelectors": {
                "app": spec.target.service,
            },
        },
    }
    return "PodChaos", crd_spec, name


def _build_stress_cpu(spec: ExperimentSpec) -> tuple[str, dict, str]:
    """StressChaos: CPU stress."""
    name = f"chaos-stress-cpu-{spec.target.service}-{spec.id[-8:]}"
    workers = spec.params.get("cpu_workers", 1)
    crd_spec = {
        "mode": "all",
        "duration": f"{spec.duration_s}s",
        "selector": {
            "namespaces": [spec.target.namespace],
            "labelSelectors": {
                "app": spec.target.service,
            },
        },
        "stressors": {
            "cpu": {
                "workers": workers,
                "load": spec.params.get("cpu_load", 80),
            },
        },
    }
    return "StressChaos", crd_spec, name


def _build_io_fault(spec: ExperimentSpec) -> tuple[str, dict, str]:
    """IOChaos: IO delay/fault."""
    name = f"chaos-io-{spec.target.service}-{spec.id[-8:]}"
    crd_spec = {
        "action": "latency",
        "mode": "all",
        "duration": f"{spec.duration_s}s",
        "selector": {
            "namespaces": [spec.target.namespace],
            "labelSelectors": {
                "app": spec.target.service,
            },
        },
        "delay": spec.params.get("io_delay_ms", "100ms"),
        "volumePath": spec.params.get("volume_path", "/"),
        "percent": spec.params.get("percent", 100),
    }
    return "IOChaos", crd_spec, name
