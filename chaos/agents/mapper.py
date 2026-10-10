"""Agent 1: Mapper.

Analyzes repo configuration, docker-compose.yml, running container list, and source files.
Outputs chaos/data/map.json:
- services (name, role, ports, criticality 1-5, reason)
- edges (from, to, protocol, purpose)
- data_flows
Guarantees: Never overwrites a manually corrected map.json unless --force is passed.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from pydantic import BaseModel, Field

from chaos.guard import log_decision
from chaos.llm import call_llm_json

logger = logging.getLogger("chaos.agent.mapper")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAP_JSON_PATH = DATA_DIR / "map.json"


# ── Pydantic Schema ─────────────────────────────────────────────────────────

class ServiceNode(BaseModel):
    name: str
    role: str
    ports: List[str]
    criticality: int = Field(ge=1, le=5)
    reason: str


class DependencyEdge(BaseModel):
    from_service: str = Field(alias="from")
    to_service: str = Field(alias="to")
    protocol: str
    purpose: str

    class Config:
        populate_by_name = True


class DataFlow(BaseModel):
    flow_name: str
    source: str
    destination: str
    frequency: str
    description: str


class DependencyMap(BaseModel):
    services: List[ServiceNode]
    edges: List[DependencyEdge]
    data_flows: List[DataFlow]


# ── Deterministic Safe Fallback ─────────────────────────────────────────────

FALLBACK_MAP = DependencyMap(
    services=[
        ServiceNode(
            name="backend",
            role="FastAPI Telematics Ingestion & Alert Engine",
            ports=["8000"],
            criticality=5,
            reason="Core telemetry ingestion endpoint and real-time alert evaluation engine",
        ),
        ServiceNode(
            name="dashboard",
            role="Vite React Monitoring UI",
            ports=["5173"],
            criticality=3,
            reason="Human operator visualization layer; reads state and displays alerts",
        ),
        ServiceNode(
            name="toxiproxy",
            role="Network Latency Injector Proxy",
            ports=["8474", "8001"],
            criticality=4,
            reason="Simulates network degradation and latency on ingestion link",
        ),
        ServiceNode(
            name="fleet-db",
            role="SQLite Telematics & State Store",
            ports=["local file"],
            criticality=4,
            reason="Stores persistent vehicle metadata, recent snapshots, and alert history",
        ),
    ],
    edges=[
        DependencyEdge(
            **{"from": "loadgen", "to": "toxiproxy", "protocol": "HTTP/REST", "purpose": "Telematics upload under latency proxy"}
        ),
        DependencyEdge(
            **{"from": "toxiproxy", "to": "backend", "protocol": "HTTP/REST", "purpose": "Forwarding vehicle telemetry to ingestion port 8000"}
        ),
        DependencyEdge(
            **{"from": "backend", "to": "fleet-db", "protocol": "SQL/SQLite", "purpose": "Persisting telemetry readings and generated alerts"}
        ),
        DependencyEdge(
            **{"from": "dashboard", "to": "backend", "protocol": "HTTP/REST", "purpose": "Fetching fleet vehicle overview and live alert logs"}
        ),
    ],
    data_flows=[
        DataFlow(
            flow_name="Telematics Ingest",
            source="in-vehicle telematics / loadgen",
            destination="backend:8000/api/fleet/telemetry/ingest",
            frequency="1 Hz per vehicle",
            description="High-frequency telemetry stream containing GPS, speed, fuel, and engine metrics",
        ),
        DataFlow(
            flow_name="Vehicle State Monitoring",
            source="backend:8000/api/fleet/vehicles",
            destination="dashboard / observer",
            frequency="1 Hz polling",
            description="Periodic fetch of vehicle state and freshest timestamps",
        ),
    ],
)


# ── Tools ───────────────────────────────────────────────────────────────────

def tool_read_file(relative_path: str) -> str:
    target = REPO_ROOT / relative_path
    if not target.exists() or not target.is_file():
        return f"[File not found: {relative_path}]"
    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()[:5000]
    except Exception as e:
        return f"[Error reading {relative_path}: {e}]"


def tool_list_containers() -> str:
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        return res.stdout.strip() if res.returncode == 0 else "docker ps unavailable"
    except Exception as e:
        return f"docker error: {e}"


# ── Execution ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Agent 1 (MAPPER) in an AI-Driven Chaos Engineering system.
Analyze the target architecture from docker-compose.yml, running containers, and code endpoints.
Return a clean, accurate DependencyMap JSON with:
1. services: list of {name, role, ports, criticality (1-5), reason}
2. edges: list of {from, to, protocol, purpose}
3. data_flows: list of {flow_name, source, destination, frequency, description}
Only return valid JSON."""


async def run_mapper(force: bool = False) -> DependencyMap:
    """Run Mapper Agent to produce chaos/data/map.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Protect manually corrected map.json unless --force
    if MAP_JSON_PATH.exists() and not force:
        logger.info(f"Existing {MAP_JSON_PATH} found and --force not supplied. Keeping existing map.")
        log_decision("mapper", "PRESERVE_MAP", "Preserved existing map.json without overwriting")
        with open(MAP_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return DependencyMap.model_validate(data)

    # Gather inputs
    compose_content = tool_read_file("docker-compose.yml")
    compose_chaos = tool_read_file("docker-compose.chaos.yml")
    containers = tool_list_containers()
    routes_sample = tool_read_file("backend/app/fleet/routes.py")

    user_prompt = f"""
Input files:
docker-compose.yml:
{compose_content}

docker-compose.chaos.yml:
{compose_chaos}

Running containers:
{containers}

Backend Fleet Routes Summary:
{routes_sample[:2500]}
"""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=DependencyMap,
        fallback_instance=FALLBACK_MAP,
        agent_name="mapper",
        reason_desc="system architecture dependency map",
    )

    with open(MAP_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(by_alias=True), f, indent=2)

    logger.info(f"Map successfully written to {MAP_JSON_PATH}")
    log_decision("mapper", "WRITE_MAP", f"Generated dependency map with {len(result.services)} services")
    print(json.dumps(result.model_dump(by_alias=True), indent=2))
    return result
