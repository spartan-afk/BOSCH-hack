"""Kubernetes client wrapper for the chaos platform.

Provides a clean interface for reading pods, deployments, services,
and for managing Chaos Mesh CRDs.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Chaos Mesh CRD group/version
CHAOS_GROUP = "chaos-mesh.org"
CHAOS_VERSION = "v1alpha1"
CHAOS_LABEL = "managed-by"
CHAOS_LABEL_VALUE = "chaos-platform"


class K8sClient:
    """Wrapper around the Kubernetes Python client."""

    def __init__(self):
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()
        self._current_context = self._get_current_context()

    @staticmethod
    def _get_current_context() -> str:
        try:
            _, active_context = config.list_kube_config_contexts()
            return active_context.get("name", "unknown")
        except Exception:
            return "in-cluster"

    @property
    def current_context(self) -> str:
        return self._current_context

    # -- Pods ---------------------------------------------------------------

    def get_pods(self, namespace: str = "boutique") -> list[dict]:
        """Get all pods in a namespace with status info."""
        pods = self.core.list_namespaced_pod(namespace)
        result = []
        for pod in pods.items:
            containers = pod.status.container_statuses or []
            restarts = sum(c.restart_count for c in containers)
            ready = all(c.ready for c in containers) if containers else False
            result.append({
                "name": pod.metadata.name,
                "namespace": namespace,
                "labels": pod.metadata.labels or {},
                "status": pod.status.phase,
                "ready": ready,
                "restarts": restarts,
                "node": pod.spec.node_name,
            })
        return result

    def all_pods_ready(self, namespace: str = "boutique") -> bool:
        """Check if all pods in the namespace are Ready."""
        pods = self.get_pods(namespace)
        return all(p["ready"] for p in pods) if pods else False

    def get_pod_restarts(self, namespace: str = "boutique") -> dict[str, int]:
        """Get restart counts keyed by service label."""
        pods = self.get_pods(namespace)
        restarts: dict[str, int] = {}
        for pod in pods:
            svc = pod["labels"].get("app", pod["name"])
            restarts[svc] = restarts.get(svc, 0) + pod["restarts"]
        return restarts

    # -- Deployments --------------------------------------------------------

    def get_deployments(self, namespace: str = "boutique") -> list[dict]:
        """Get all deployments with replica counts and env vars."""
        deps = self.apps.list_namespaced_deployment(namespace)
        result = []
        for dep in deps.items:
            env_vars = {}
            containers = dep.spec.template.spec.containers or []
            for container in containers:
                if container.env:
                    for ev in container.env:
                        if ev.value:
                            env_vars[ev.name] = ev.value

            result.append({
                "name": dep.metadata.name,
                "namespace": namespace,
                "replicas": dep.spec.replicas or 1,
                "ready_replicas": dep.status.ready_replicas or 0,
                "env_vars": env_vars,
            })
        return result

    def get_deployment_replicas(self, name: str, namespace: str = "boutique") -> int:
        """Get the current replica count for a deployment."""
        dep = self.apps.read_namespaced_deployment(name, namespace)
        return dep.spec.replicas or 1

    def scale_deployment(self, name: str, replicas: int, namespace: str = "boutique") -> None:
        """Scale a deployment to the given replica count."""
        body = {"spec": {"replicas": replicas}}
        self.apps.patch_namespaced_deployment_scale(name, namespace, body)
        logger.info(f"Scaled {name} in {namespace} to {replicas} replicas")

    # -- Services -----------------------------------------------------------

    def get_services(self, namespace: str = "boutique") -> list[dict]:
        """Get all services in a namespace."""
        svcs = self.core.list_namespaced_service(namespace)
        return [
            {
                "name": svc.metadata.name,
                "namespace": namespace,
                "type": svc.spec.type,
                "ports": [{"port": p.port, "target_port": p.target_port, "protocol": p.protocol}
                          for p in (svc.spec.ports or [])],
            }
            for svc in svcs.items
        ]

    # -- Chaos Mesh CRDs ---------------------------------------------------

    def create_chaos_crd(self, kind: str, spec: dict, namespace: str = "boutique",
                         name: str | None = None) -> dict:
        """Create a Chaos Mesh CRD in the cluster.

        Args:
            kind: e.g. "PodChaos", "NetworkChaos", "StressChaos"
            spec: the CRD spec body
            namespace: target namespace
            name: CRD resource name
        """
        plural = self._kind_to_plural(kind)
        body = {
            "apiVersion": f"{CHAOS_GROUP}/{CHAOS_VERSION}",
            "kind": kind,
            "metadata": {
                "name": name or f"chaos-{kind.lower()}-{id(spec) % 10000:04d}",
                "namespace": namespace,
                "labels": {CHAOS_LABEL: CHAOS_LABEL_VALUE},
            },
            "spec": spec,
        }

        result = self.custom.create_namespaced_custom_object(
            group=CHAOS_GROUP,
            version=CHAOS_VERSION,
            namespace=namespace,
            plural=plural,
            body=body,
        )
        logger.info(f"Created {kind} CRD: {body['metadata']['name']}")
        return result

    def delete_chaos_crd(self, kind: str, name: str, namespace: str = "boutique") -> None:
        """Delete a specific Chaos Mesh CRD."""
        plural = self._kind_to_plural(kind)
        try:
            self.custom.delete_namespaced_custom_object(
                group=CHAOS_GROUP,
                version=CHAOS_VERSION,
                namespace=namespace,
                plural=plural,
                name=name,
            )
            logger.info(f"Deleted {kind} CRD: {name}")
        except ApiException as e:
            if e.status != 404:
                raise
            logger.warning(f"{kind} CRD {name} already deleted")

    def delete_all_chaos_crds(self, namespace: str = "boutique") -> int:
        """Delete ALL Chaos Mesh CRDs labelled with managed-by=chaos-platform.

        Returns the number of CRDs deleted.
        """
        count = 0
        for kind in ["PodChaos", "NetworkChaos", "StressChaos", "IOChaos"]:
            plural = self._kind_to_plural(kind)
            try:
                result = self.custom.list_namespaced_custom_object(
                    group=CHAOS_GROUP,
                    version=CHAOS_VERSION,
                    namespace=namespace,
                    plural=plural,
                    label_selector=f"{CHAOS_LABEL}={CHAOS_LABEL_VALUE}",
                )
                for item in result.get("items", []):
                    name = item["metadata"]["name"]
                    self.delete_chaos_crd(kind, name, namespace)
                    count += 1
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"Error listing {kind}: {e}")
        return count

    def list_chaos_crds(self, namespace: str = "boutique") -> list[dict]:
        """List all active Chaos Mesh CRDs managed by us."""
        results = []
        for kind in ["PodChaos", "NetworkChaos", "StressChaos", "IOChaos"]:
            plural = self._kind_to_plural(kind)
            try:
                result = self.custom.list_namespaced_custom_object(
                    group=CHAOS_GROUP,
                    version=CHAOS_VERSION,
                    namespace=namespace,
                    plural=plural,
                    label_selector=f"{CHAOS_LABEL}={CHAOS_LABEL_VALUE}",
                )
                for item in result.get("items", []):
                    results.append({
                        "kind": kind,
                        "name": item["metadata"]["name"],
                        "namespace": namespace,
                        "spec": item.get("spec", {}),
                    })
            except ApiException:
                pass
        return results

    @staticmethod
    def _kind_to_plural(kind: str) -> str:
        """Convert CRD kind to plural resource name."""
        mapping = {
            "PodChaos": "podchaos",
            "NetworkChaos": "networkchaos",
            "StressChaos": "stresschaos",
            "IOChaos": "iochaos",
        }
        return mapping.get(kind, kind.lower())
