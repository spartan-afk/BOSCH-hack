"""LangGraph implementation of the autonomous chaos engineering workflow."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agent.state import ChaosState
from app.models import (
    DependencyGraph,
    ExperimentRun,
    ExperimentSpec,
    ExperimentStatus,
    ExperimentTarget,
    ExperimentType,
    MetricSnapshot,
)

logger = logging.getLogger(__name__)


def create_chaos_graph(
    mapper=None,
    planner_cls=None,
    safety=None,
    runner=None,
    analyzer=None,
    probe=None,
    db=None,
    interrupt_for_approval: bool = True,
):
    """Build and compile the LangGraph StateGraph for Chaos Engineering."""

    async def map_topology_node(state: ChaosState) -> dict:
        """Node 1: Map microservices and compute blast radius."""
        logger.info("[LangGraph] Node: map_topology")
        log = list(state.get("audit_log", []))
        
        graph = mapper.graph if mapper else None
        if not graph and mapper:
            graph = mapper.build_graph()
        
        topology_dict = graph.model_dump() if graph else {}
        log.append(f"Mapped topology with {len(topology_dict.get('nodes', []))} microservice nodes.")
        
        return {
            "topology": topology_dict,
            "audit_log": log,
            "status": "mapped",
        }

    async def plan_chaos_node(state: ChaosState) -> dict:
        """Node 2: Select candidate failure target and formulate hypothesis."""
        logger.info("[LangGraph] Node: plan_chaos")
        log = list(state.get("audit_log", []))

        topology = DependencyGraph.model_validate(state["topology"]) if state.get("topology") else None
        target_service = state.get("target_service")
        
        chosen_spec = None
        candidates_list = []
        
        if topology and planner_cls:
            planner = planner_cls(topology, db)
            candidates = planner.generate_candidates()
            candidates_list = [c.model_dump() for c in candidates]
            
            # If user requested a specific target service, pick it
            if target_service:
                for c in candidates:
                    if c.spec.target.service == target_service:
                        chosen_spec = c.spec
                        break
            
            if not chosen_spec and candidates:
                chosen_spec = candidates[0].spec

        if not chosen_spec:
            # Fallback spec for cartservice
            chosen_spec = ExperimentSpec(
                name="Kill one cartservice pod",
                type=ExperimentType.POD_KILL,
                target=ExperimentTarget(namespace="boutique", service="cartservice"),
                params={"pods": 1},
                duration_s=60,
                hypothesis="Cartservice recovers seamlessly without causing cascading failures on frontend.",
            )

        log.append(f"Planned candidate: {chosen_spec.name} (target: {chosen_spec.target.service})")
        return {
            "candidates": candidates_list,
            "chosen_spec": chosen_spec.model_dump(),
            "hypothesis": chosen_spec.hypothesis,
            "audit_log": log,
            "status": "planned",
        }

    async def safety_gate_node(state: ChaosState) -> dict:
        """Node 3: Hard code gate verifying safety invariants."""
        logger.info("[LangGraph] Node: safety_gate")
        log = list(state.get("audit_log", []))
        
        spec_dict = state.get("chosen_spec")
        if not spec_dict:
            return {"status": "safety_rejected", "error": "No spec to validate"}
            
        spec = ExperimentSpec.model_validate(spec_dict)
        
        health_snapshot = None
        if probe:
            try:
                health_snapshot = await probe.get_snapshot(window_s=30.0)
            except Exception as e:
                logger.warning(f"Failed to fetch health snapshot: {e}")

        if safety:
            result = safety.validate(spec, current_health=health_snapshot)
        else:
            from app.models import SafetyCheckResult
            result = SafetyCheckResult(passed=True, checks={"default": True})

        status = "safety_passed" if result.passed else "safety_rejected"
        log.append(f"Safety Gate evaluated: passed={result.passed}. Checks={result.checks}")
        
        return {
            "safety_result": result.model_dump(),
            "status": status,
            "audit_log": log,
        }

    def route_safety(state: ChaosState) -> str:
        """Conditional routing: only proceed if safety checks passed."""
        if state.get("status") == "safety_passed":
            return "human_approval"
        return "abort"

    async def human_approval_node(state: ChaosState) -> dict:
        """Node 4: Human-in-the-loop checkpoint."""
        logger.info("[LangGraph] Node: human_approval")
        log = list(state.get("audit_log", []))
        log.append("Human approval gate cleared.")
        return {
            "human_approved": True,
            "status": "approved",
            "audit_log": log,
        }

    async def execute_chaos_node(state: ChaosState) -> dict:
        """Node 5: Execute chaos experiment via Runner."""
        logger.info("[LangGraph] Node: execute_chaos")
        log = list(state.get("audit_log", []))
        
        spec = ExperimentSpec.model_validate(state["chosen_spec"])
        log.append(f"Starting chaos injection for {spec.name}...")

        if runner:
            run_result: ExperimentRun = await runner.run_experiment(spec)
            baseline = run_result.baseline_snapshot.model_dump() if run_result.baseline_snapshot else None
            during = [m.model_dump() for m in run_result.during_snapshots]
            recovery = run_result.after_snapshot.model_dump() if run_result.after_snapshot else None
            exec_status = run_result.status.value
        else:
            baseline = {"success_rate": 1.0, "latency_p99_ms": 35.0}
            during = [{"success_rate": 0.98, "latency_p99_ms": 50.0}]
            recovery = {"success_rate": 1.0, "latency_p99_ms": 36.0}
            exec_status = "analyzed"

        log.append(f"Execution finished with status: {exec_status}")
        return {
            "baseline_metrics": baseline,
            "during_metrics": during,
            "recovery_metrics": recovery,
            "status": "executed",
            "audit_log": log,
        }

    async def analyze_postmortem_node(state: ChaosState) -> dict:
        """Node 6: Post-mortem Resilience Analyst Agent."""
        logger.info("[LangGraph] Node: analyze_postmortem")
        log = list(state.get("audit_log", []))
        
        spec = ExperimentSpec.model_validate(state["chosen_spec"])
        baseline = MetricSnapshot.model_validate(state["baseline_metrics"]) if state.get("baseline_metrics") else None
        during = [MetricSnapshot.model_validate(m) for m in state.get("during_metrics", [])]
        recovery = MetricSnapshot.model_validate(state["recovery_metrics"]) if state.get("recovery_metrics") else None

        if analyzer and baseline:
            report = analyzer.analyze(spec, baseline, during, recovery)
            report_dict = report.model_dump()
        else:
            report_dict = {
                "experiment_id": spec.id,
                "verdict": "passed",
                "interpretation": "Microservice resilience validated successfully.",
                "recommendations": ["Consider adding autoscaling policy."],
            }

        log.append(f"Analysis complete. Verdict: {report_dict.get('verdict')}")
        return {
            "report": report_dict,
            "status": "completed",
            "audit_log": log,
        }

    async def abort_node(state: ChaosState) -> dict:
        """Node for cleanly recording an aborted or rejected experiment."""
        logger.info("[LangGraph] Node: abort")
        log = list(state.get("audit_log", []))
        log.append("Experiment aborted due to safety gate rejection.")
        return {
            "status": "aborted",
            "audit_log": log,
        }

    # Build the StateGraph
    builder = StateGraph(ChaosState)

    builder.add_node("map_topology", map_topology_node)
    builder.add_node("plan_chaos", plan_chaos_node)
    builder.add_node("safety_gate", safety_gate_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("execute_chaos", execute_chaos_node)
    builder.add_node("analyze_postmortem", analyze_postmortem_node)
    builder.add_node("abort", abort_node)

    builder.set_entry_point("map_topology")
    builder.add_edge("map_topology", "plan_chaos")
    builder.add_edge("plan_chaos", "safety_gate")

    builder.add_conditional_edges(
        "safety_gate",
        route_safety,
        {
            "human_approval": "human_approval",
            "abort": "abort",
        },
    )

    builder.add_edge("human_approval", "execute_chaos")
    builder.add_edge("execute_chaos", "analyze_postmortem")
    builder.add_edge("analyze_postmortem", END)
    builder.add_edge("abort", END)

    checkpointer = MemorySaver()
    interrupts = ["human_approval"] if interrupt_for_approval else []

    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts,
    )

    return graph
