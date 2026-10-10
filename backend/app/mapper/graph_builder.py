"""Dependency graph builder for Online Boutique.

Input sources (in priority order):
1. Manual overrides from config/deps_override.yaml (always wins)
2. Kubernetes Deployments env vars ending in _ADDR
3. Hard-coded Online Boutique topology as fallback

Output: DependencyGraph with nodes, edges, blast_radius, and critical flags.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from pathlib import Path
from typing import Optional

import yaml

from app.models import DependencyGraph, GraphEdge, GraphNode

logger = logging.getLogger(__name__)

# Config path relative to project root
CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"

# Known Fleet Management System service wiring (telematics, alerts, tracking)
KNOWN_DEPENDENCIES = {
    "dashboard-api": ["vehicle-manager", "alert-engine", "tracking-service"],
    "telematics-ingest": ["alert-engine", "fleet-db", "gps-tracker"],
    "alert-engine": ["fleet-db", "dtc-analyzer", "notification-service"],
    "tracking-service": ["fleet-db", "gps-tracker", "geofence-monitor"],
    "vehicle-manager": ["fleet-db"],
    "dtc-analyzer": ["fleet-db"],
    "telematics-simulator": ["telematics-ingest"],
}


class DependencyMapper:
    """Builds a dependency graph from Kubernetes state and manual overrides."""

    def __init__(self, k8s_client=None):
        self.k8s = k8s_client
        self._graph: Optional[DependencyGraph] = None
        self._overrides = self._load_overrides()

    def _load_overrides(self) -> dict:
        """Load manual dependency overrides from config."""
        override_path = CONFIG_DIR / "deps_override.yaml"
        if override_path.exists():
            with open(override_path) as f:
                data = yaml.safe_load(f) or {}
                logger.info(f"Loaded {len(data.get('overrides', {}).get('additions', []))} manual edge overrides")
                return data.get("overrides", {})
        return {}

    def build_graph(self, namespace: str = "boutique") -> DependencyGraph:
        """Build the full dependency graph.

        Strategy:
        1. Get deployments from K8s (if available) for replica counts + env vars
        2. Discover edges from _ADDR env vars
        3. Fall back to known dependencies for anything missed
        4. Apply manual overrides (additions, removals, properties)
        5. Compute blast_radius and single_point
        """
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        edge_set: set[tuple[str, str]] = set()

        # Step 1: Get deployments from K8s
        deployments = []
        if self.k8s:
            try:
                deployments = self.k8s.get_deployments(namespace)
            except Exception as e:
                logger.warning(f"Failed to query K8s deployments: {e}")

        # Build nodes from deployments
        for dep in deployments:
            name = dep["name"]
            nodes[name] = GraphNode(
                id=name,
                kind="service",
                replicas=dep.get("replicas", 1),
            )

            # Step 2: Discover edges from _ADDR env vars
            for env_name, env_value in dep.get("env_vars", {}).items():
                if env_name.endswith("_ADDR"):
                    # Extract service name: PAYMENT_SERVICE_ADDR -> paymentservice
                    target = self._addr_to_service(env_name, env_value)
                    if target and target != name:
                        edge_key = (name, target)
                        if edge_key not in edge_set:
                            edges.append(GraphEdge(**{
                                "from": name,
                                "to": target,
                                "source": "env",
                                "confidence": 0.9,
                            }))
                            edge_set.add(edge_key)

        # Step 3: Fallback to known dependencies
        for source, targets in KNOWN_DEPENDENCIES.items():
            if source not in nodes:
                nodes[source] = GraphNode(id=source)
            for target in targets:
                if target not in nodes:
                    nodes[target] = GraphNode(id=target)
                edge_key = (source, target)
                if edge_key not in edge_set:
                    edges.append(GraphEdge(**{
                        "from": source,
                        "to": target,
                        "source": "known",
                        "confidence": 0.8,
                    }))
                    edge_set.add(edge_key)

        # Step 4: Apply manual overrides
        # Additions
        for addition in self._overrides.get("additions", []):
            src = addition["from"]
            tgt = addition["to"]
            if src not in nodes:
                nodes[src] = GraphNode(id=src)
            if tgt not in nodes:
                nodes[tgt] = GraphNode(id=tgt)
            edge_key = (src, tgt)
            if edge_key not in edge_set:
                edges.append(GraphEdge(**{
                    "from": src,
                    "to": tgt,
                    "source": "manual",
                    "confidence": 1.0,
                }))
                edge_set.add(edge_key)

        # Removals
        removal_set = {(r["from"], r["to"]) for r in self._overrides.get("removals", [])}
        edges = [e for e in edges if (e.source, e.target) not in removal_set]

        # Property overrides
        for svc_name, props in self._overrides.get("properties", {}).items():
            if svc_name in nodes:
                if "critical" in props:
                    nodes[svc_name].critical = props["critical"]
                if "protocol" in props:
                    nodes[svc_name].protocol = props["protocol"]

        # Step 5: Compute blast_radius and single_point
        # Build reverse adjacency (who depends on whom)
        reverse_adj: dict[str, set[str]] = {n: set() for n in nodes}
        for edge in edges:
            src = edge.source  # this is aliased as "from"
            tgt = edge.target  # this is aliased as "to"
            if tgt in reverse_adj:
                reverse_adj[tgt].add(src)

        for name, node in nodes.items():
            node.blast_radius = self._compute_blast_radius(name, reverse_adj)
            node.single_point = node.replicas == 1 and node.blast_radius > 0

        self._graph = DependencyGraph(
            nodes=list(nodes.values()),
            edges=edges,
        )
        return self._graph

    @staticmethod
    def _compute_blast_radius(service: str, reverse_adj: dict[str, set[str]]) -> int:
        """BFS: count of upstream services that transitively depend on this service."""
        visited = set()
        queue = deque([service])
        while queue:
            current = queue.popleft()
            for upstream in reverse_adj.get(current, set()):
                if upstream not in visited:
                    visited.add(upstream)
                    queue.append(upstream)
        return len(visited)

    @staticmethod
    def _addr_to_service(env_name: str, env_value: str) -> Optional[str]:
        """Convert an _ADDR env var to a service name.

        Examples:
          PAYMENT_SERVICE_ADDR=paymentservice:50051 -> paymentservice
          CART_SERVICE_ADDR=cartservice:7070 -> cartservice
        """
        # Try to extract from the value (host:port)
        host = env_value.split(":")[0].strip()
        if host:
            return host

        # Fallback: parse from env name
        name = env_name.replace("_ADDR", "").replace("_SERVICE", "").lower()
        return name + "service" if name else None

    @property
    def graph(self) -> Optional[DependencyGraph]:
        return self._graph

    def get_graph_json(self) -> dict:
        """Return the graph as a JSON-serializable dict."""
        if not self._graph:
            self.build_graph()
        return self._graph.model_dump(by_alias=True) if self._graph else {"nodes": [], "edges": []}
