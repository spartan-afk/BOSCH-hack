"""FastAPI routers for the chaos platform API.

Endpoints per spec:
  GET  /api/graph            - Dependency graph
  GET  /api/health           - Current success rate, latency, pod readiness
  POST /api/baseline         - Run and store a baseline snapshot
  GET  /api/plan             - Ranked candidate experiments with score breakdown
  POST /api/experiments      - Validate and queue an experiment spec
  POST /api/experiments/{id}/run   - Start a validated experiment
  GET  /api/experiments/{id}       - State, timeline, metrics, report
  GET  /api/experiments/{id}/stream - SSE stream
  POST /api/experiments/{id}/retest - Rerun the same spec
  GET  /api/experiments/{a}/compare/{b} - Before/after comparison
  POST /api/kill             - Emergency kill switch
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.models import (
    BaselineSnapshot,
    DependencyGraph,
    ExperimentRun,
    ExperimentSpec,
    ExperimentStatus,
    MetricSnapshot,
    SafetyCheckResult,
    ScoredCandidate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# These will be injected by the main app
_k8s = None
_probe = None
_mapper = None
_safety = None
_runner = None
_planner_cls = None
_analyzer = None
_db = None
_chaos_graph = None


def init_dependencies(k8s, probe, mapper, safety, runner, planner_cls, analyzer, db):
    """Inject dependencies from the main app."""
    global _k8s, _probe, _mapper, _safety, _runner, _planner_cls, _analyzer, _db, _chaos_graph
    _k8s = k8s
    _probe = probe
    _mapper = mapper
    _safety = safety
    _runner = runner
    _planner_cls = planner_cls
    _analyzer = analyzer
    _db = db

    try:
        from app.agent.graph import create_chaos_graph
        _chaos_graph = create_chaos_graph(
            mapper=mapper,
            planner_cls=planner_cls,
            safety=safety,
            runner=runner,
            analyzer=analyzer,
            probe=probe,
            db=db,
            interrupt_for_approval=True,
        )
    except Exception as e:
        logger.warning(f"Failed to initialize LangGraph chaos graph: {e}")


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@router.get("/graph")
async def get_graph():
    """Return the dependency graph."""
    if not _mapper:
        raise HTTPException(503, "Mapper not initialized")
    return _mapper.get_graph_json()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
async def get_health():
    """Return current system health from the probe."""
    snapshot = await _probe.get_snapshot(window_s=30.0)
    pod_info = {}
    try:
        if _k8s:
            pods = _k8s.get_pods("boutique")
            pod_info = {
                "total_pods": len(pods),
                "ready_pods": sum(1 for p in pods if p["ready"]),
                "pods": pods,
            }
    except Exception as e:
        pod_info = {"error": str(e)}

    return {
        "success_rate": snapshot.success_rate,
        "error_rate": snapshot.error_rate,
        "p50_latency_ms": snapshot.p50_latency_ms,
        "p95_latency_ms": snapshot.p95_latency_ms,
        "timestamp": snapshot.timestamp.isoformat(),
        "pods": pod_info,
    }


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

class BaselineRequest(BaseModel):
    duration_s: int = 300
    warmup_s: int = 60


@router.post("/baseline")
async def run_baseline(req: BaselineRequest, bg: BackgroundTasks):
    """Run and store a baseline snapshot."""
    if not _probe:
        raise HTTPException(503, "Probe not initialized")

    stats = await _probe.collect_baseline(req.duration_s, req.warmup_s)

    # Check minimum success rate
    if stats["success_rate_mean"] < 0.99:
        return {
            "status": "unhealthy",
            "message": f"Baseline success rate {stats['success_rate_mean']:.2%} < 99%. Fix the app first.",
            "stats": stats,
        }

    # Store baseline
    baseline = BaselineSnapshot(
        duration_s=req.duration_s,
        success_rate_mean=stats["success_rate_mean"],
        success_rate_std=stats["success_rate_std"],
        error_rate_mean=stats["error_rate_mean"],
        p50_latency_mean=stats["p50_latency_mean"],
        p50_latency_std=stats["p50_latency_std"],
        p95_latency_mean=stats["p95_latency_mean"],
        p95_latency_std=stats["p95_latency_std"],
    )

    _db.save_baseline(baseline.id, baseline.model_dump_json())

    return {
        "status": "healthy",
        "baseline_id": baseline.id,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@router.get("/plan")
async def get_plan():
    """Return ranked candidate experiments."""
    if not _mapper or not _mapper.graph:
        _mapper.build_graph()

    graph = _mapper.graph
    planner = _planner_cls(graph, _db)
    candidates = planner.get_top_candidates(n=10)

    return {
        "candidates": [c.model_dump() for c in candidates],
        "count": len(candidates),
    }


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

@router.post("/experiments")
async def create_experiment(spec: ExperimentSpec):
    """Validate and create an experiment."""
    # Get current health
    health = await _probe.get_snapshot(window_s=30.0)

    # Safety validation
    result = _safety.validate(spec, health)
    if not result.passed:
        return {
            "status": "rejected",
            "checks": result.checks,
            "reasons": result.reasons,
        }

    # Save experiment
    _db.save_experiment(
        exp_id=spec.id,
        spec_json=spec.model_dump_json(),
        status="validated",
    )

    return {
        "status": "validated",
        "experiment_id": spec.id,
        "checks": result.checks,
    }


@router.post("/experiments/{exp_id}/run")
async def run_experiment(exp_id: str, bg: BackgroundTasks):
    """Start a validated experiment."""
    exp_row = _db.get_experiment(exp_id)
    if not exp_row:
        raise HTTPException(404, f"Experiment {exp_id} not found")

    if exp_row.status not in ("validated", "planned"):
        raise HTTPException(400, f"Experiment is in status '{exp_row.status}', cannot run")

    if _runner.is_running:
        raise HTTPException(409, "Another experiment is already running")

    spec = ExperimentSpec.model_validate_json(exp_row.spec_json)

    # Run in background
    bg.add_task(_runner.run_experiment, spec)

    return {
        "status": "started",
        "experiment_id": exp_id,
        "message": "Experiment started. Use /api/experiments/{id}/stream for live updates.",
    }


@router.get("/experiments/{exp_id}")
async def get_experiment(exp_id: str):
    """Get experiment state, timeline, metrics, and report."""
    exp_row = _db.get_experiment(exp_id)
    if not exp_row:
        raise HTTPException(404, f"Experiment {exp_id} not found")

    events = _db.get_events(exp_id)
    report = _db.get_report(exp_id)

    result = {
        "id": exp_row.id,
        "status": exp_row.status,
        "spec": json.loads(exp_row.spec_json),
        "parent_id": exp_row.parent_id,
        "created_at": exp_row.created_at.isoformat() if exp_row.created_at else None,
        "started_at": exp_row.started_at.isoformat() if exp_row.started_at else None,
        "ended_at": exp_row.ended_at.isoformat() if exp_row.ended_at else None,
        "abort_reason": exp_row.abort_reason,
        "events": [
            {"timestamp": e.timestamp.isoformat(), "type": e.type, "detail": e.detail}
            for e in events
        ],
    }

    if exp_row.baseline_json:
        result["baseline"] = json.loads(exp_row.baseline_json)
    if exp_row.during_json:
        result["during_metrics"] = json.loads(exp_row.during_json)
    if exp_row.after_json:
        result["after"] = json.loads(exp_row.after_json)
    if report:
        result["report"] = json.loads(report.report_json)

    return result


@router.get("/experiments/{exp_id}/stream")
async def stream_experiment(exp_id: str):
    """SSE stream of live metrics and state changes."""
    async def event_generator() -> AsyncGenerator:
        queue: asyncio.Queue = asyncio.Queue()

        async def callback(event_type: str, data: dict):
            await queue.put({"event": event_type, "data": json.dumps(data)})

        _runner.register_sse_callback(callback)

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(event_generator())


@router.post("/experiments/{exp_id}/retest")
async def retest_experiment(exp_id: str, bg: BackgroundTasks):
    """Rerun the same experiment spec as a retest."""
    exp_row = _db.get_experiment(exp_id)
    if not exp_row:
        raise HTTPException(404, f"Experiment {exp_id} not found")

    spec = ExperimentSpec.model_validate_json(exp_row.spec_json)
    # Generate new ID for the retest
    new_spec = spec.model_copy(update={"id": f"exp-{id(spec) % 100000:05d}-retest"})

    # Validate
    health = await _probe.get_snapshot(window_s=30.0)
    result = _safety.validate(new_spec, health)
    if not result.passed:
        return {"status": "rejected", "reasons": result.reasons}

    # Save and run
    _db.save_experiment(
        exp_id=new_spec.id,
        spec_json=new_spec.model_dump_json(),
        status="validated",
        parent_id=exp_id,
    )

    bg.add_task(_runner.run_experiment, new_spec, exp_id)

    return {
        "status": "started",
        "retest_id": new_spec.id,
        "original_id": exp_id,
    }


@router.get("/experiments/{exp_a}/compare/{exp_b}")
async def compare_experiments(exp_a: str, exp_b: str):
    """Compare two experiment runs side by side."""
    row_a = _db.get_experiment(exp_a)
    row_b = _db.get_experiment(exp_b)
    if not row_a or not row_b:
        raise HTTPException(404, "One or both experiments not found")

    # Build minimal ExperimentRun objects for comparison
    def _build_run(row) -> ExperimentRun:
        spec = ExperimentSpec.model_validate_json(row.spec_json)
        run = ExperimentRun(id=row.id, spec=spec, status=ExperimentStatus(row.status))
        if row.baseline_json:
            run.baseline_snapshot = MetricSnapshot.model_validate_json(row.baseline_json)
        if row.during_json:
            run.during_snapshots = [MetricSnapshot.model_validate(s) for s in json.loads(row.during_json)]
        if row.after_json:
            run.after_snapshot = MetricSnapshot.model_validate_json(row.after_json)
        return run

    run_a = _build_run(row_a)
    run_b = _build_run(row_b)

    result = _analyzer.compare(run_a, run_b)
    return result.model_dump()


# ---------------------------------------------------------------------------
# Experiments list
# ---------------------------------------------------------------------------

@router.get("/experiments")
async def list_experiments():
    """List all experiments."""
    rows = _db.list_experiments(limit=50)
    return {
        "experiments": [
            {
                "id": r.id,
                "status": r.status,
                "spec": json.loads(r.spec_json) if r.spec_json else {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "abort_reason": r.abort_reason,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

@router.post("/kill")
async def kill_switch():
    """Emergency kill switch. Stops everything immediately.

    Deletes all CRDs labelled managed-by=chaos-platform,
    restores replica counts, marks active run as KILLED.
    """
    result = await _runner.kill()
    return result


# ---------------------------------------------------------------------------
# Admin / debug
# ---------------------------------------------------------------------------

@router.post("/admin/reset")
async def admin_reset():
    """Mark all non-terminal experiments as 'cancelled'.

    Use this when an experiment is stuck in 'validated' or another
    intermediate state and blocking new runs.
    """
    non_terminal = ["validated", "planned", "baselining", "injecting", "observing", "recovering"]
    cleared = []
    rows = _db.list_experiments(limit=200)
    for row in rows:
        if row.status in non_terminal:
            _db.update_experiment(
                row.id,
                status="cancelled",
                ended_at=datetime.utcnow(),
                abort_reason="Manually reset via admin endpoint",
            )
            cleared.append(row.id)
    return {"cleared": cleared, "count": len(cleared)}


@router.get("/admin/safety-check/{service}")
async def admin_safety_check(service: str, namespace: str = "boutique"):
    """Show which safety checks pass/fail for a given service target.
    
    Useful for diagnosing 'safety check failed' errors.
    """
    from app.models import ExperimentSpec, ExperimentTarget, AbortThresholds, ExperimentType
    spec = ExperimentSpec(
        name=f"test-{service}",
        type=ExperimentType.POD_KILL,
        target=ExperimentTarget(namespace=namespace, service=service),
        params={"pods": 1},
        duration_s=60,
        hypothesis="test",
        abort=AbortThresholds(success_rate_min=0.9, p95_latency_max_ms=2000, max_duration_s=90),
    )
    health = await _probe.get_snapshot(window_s=30.0)
    result = _safety.validate(spec, health)
    return {
        "passed": result.passed,
        "checks": result.checks,
        "reasons": result.reasons,
        "health_snapshot": {
            "success_rate": health.success_rate,
            "p95_latency_ms": health.p95_latency_ms,
        },
    }


# ---------------------------------------------------------------------------
# LangGraph Multi-Agent Orchestration
# ---------------------------------------------------------------------------

class AgentOrchestrateRequest(BaseModel):
    target_service: Optional[str] = None
    auto_approve: bool = False

AgentOrchestrateRequest.model_rebuild()


@router.post("/agent/orchestrate")
async def orchestrate_agent(req: Optional[AgentOrchestrateRequest] = None):
    """Trigger the LangGraph multi-agent chaos orchestration workflow.
    
    Executes through Topology Mapping -> Planning -> Safety Gate.
    If safety passes, pauses at the Human Approval gate (unless auto_approve=True).
    """
    if req is None:
        req = AgentOrchestrateRequest()
    if not _chaos_graph:
        raise HTTPException(503, "LangGraph orchestrator not initialized")

    import uuid
    thread_id = f"thread-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "thread_id": thread_id,
        "target_service": req.target_service,
        "audit_log": [f"Orchestration thread {thread_id} initiated."],
    }

    # Run up to human approval or completion
    state = await _chaos_graph.ainvoke(initial_state, config=config)

    # If auto_approve requested and paused at human_approval
    if req.auto_approve and state.get("status") == "safety_passed":
        state = await _chaos_graph.ainvoke(None, config=config)

    return {
        "thread_id": thread_id,
        "status": state.get("status"),
        "chosen_spec": state.get("chosen_spec"),
        "hypothesis": state.get("hypothesis"),
        "safety_result": state.get("safety_result"),
        "report": state.get("report"),
        "audit_log": state.get("audit_log", []),
    }


@router.post("/agent/{thread_id}/approve")
async def approve_agent_run(thread_id: str):
    """Resume a paused LangGraph thread past the Human Approval checkpoint."""
    if not _chaos_graph:
        raise HTTPException(503, "LangGraph orchestrator not initialized")

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # Resume execution
        final_state = await _chaos_graph.ainvoke(None, config=config)
    except Exception as e:
        logger.error(f"Error resuming thread {thread_id}: {e}")
        raise HTTPException(500, f"Error executing workflow: {e}")

    return {
        "thread_id": thread_id,
        "status": final_state.get("status"),
        "report": final_state.get("report"),
        "audit_log": final_state.get("audit_log", []),
    }


@router.get("/agent/{thread_id}/state")
async def get_agent_state(thread_id: str):
    """Retrieve current LangGraph state snapshot and audit trail for a thread."""
    if not _chaos_graph:
        raise HTTPException(503, "LangGraph orchestrator not initialized")

    config = {"configurable": {"thread_id": thread_id}}
    state_snapshot = _chaos_graph.get_state(config)

    if not state_snapshot or not state_snapshot.values:
        raise HTTPException(404, f"No agent state found for thread {thread_id}")

    return {
        "thread_id": thread_id,
        "values": state_snapshot.values,
        "next_nodes": list(state_snapshot.next),
    }


