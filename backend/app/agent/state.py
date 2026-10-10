"""State definitions for the LangGraph chaos engineering agent workflow."""
from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict


class ChaosState(TypedDict, total=False):
    """Unified state dictionary passed between LangGraph nodes."""
    thread_id: str
    target_service: Optional[str]
    topology: Optional[dict]
    candidates: list[dict]
    chosen_spec: Optional[dict]
    hypothesis: Optional[str]
    safety_result: Optional[dict]
    human_approved: bool
    status: str  # planned, safety_passed, safety_rejected, running, completed, aborted
    baseline_metrics: Optional[dict]
    during_metrics: list[dict]
    recovery_metrics: Optional[dict]
    report: Optional[dict]
    audit_log: list[str]
    error: Optional[str]
