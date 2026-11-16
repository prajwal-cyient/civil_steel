"""
state.py

Defines the centralized state and data models for the LangGraph agentic workflow.
Every agent reads from and updates this typed WorkflowState.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GraderResult(BaseModel):
    """Evaluation result produced by an Analyzer Grader."""
    passed: bool
    score: float = 1.0  # 1.0 = perfect, < 1.0 = degradation
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)
    corrective_action_needed: bool = False
    corrective_action: Optional[Dict[str, Any]] = None


class AgentLogEntry(BaseModel):
    """Individual execution log for an agent."""
    agent_id: str
    agent_name: str
    status: str  # "PENDING", "RUNNING", "PASSED", "REVISING", "FAILED"
    message: str
    timestamp: str
    loop_info: Optional[str] = None


class WorkflowState(BaseModel):
    """
    The shared state dictionary passed across all 8 agents in LangGraph.
    """
    run_id: str
    status: str = "INITIALIZED"  # "RUNNING", "PAUSED_AT_GATE3", "COMPLETED", "FAILED"
    current_agent: str = "A1"
    logs: List[AgentLogEntry] = Field(default_factory=list)
    agent_statuses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # A1 Inputs
    raw_input: Dict[str, Any] = Field(default_factory=dict)
    grid_config: Dict[str, Any] = Field(default_factory=dict)
    load_config: Dict[str, Any] = Field(default_factory=dict)

    # A2 Readiness
    readiness: Dict[str, Any] = Field(default_factory=dict)
    simulation_mode: bool = False  # Set to True if software is offline to enable safe testing
    model_call_count: int = 0
    model_events: List[Dict[str, Any]] = Field(default_factory=list)

    # A3 Frame Generation
    frame_candidate: Dict[str, Any] = Field(default_factory=dict)
    active_column_profile: str = ""
    active_beam_profile: str = ""
    active_long_brace_profile: str = ""
    active_trans_brace_profile: str = ""
    design_generation: int = 0
    candidate_history: List[str] = Field(default_factory=list)

    # A4 STAAD Structural Analysis
    # OpenSTAAD returns integer load-case IDs alongside named result sections.
    staad_results: Dict[str | int, Any] = Field(default_factory=dict)
    frame_grader_result: Optional[GraderResult] = None
    loop_a_count: int = 0
    max_loop_a: int = 3
    loop_a_history: List[Dict[str, Any]] = Field(default_factory=list)

    # A5 IDEA StatiCa Joint Design
    ideastatica_results: Dict[str, Any] = Field(default_factory=dict)
    joint_grader_results: Dict[str, GraderResult] = Field(default_factory=dict)
    active_operation_params: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    loop_b_count: Dict[str, int] = Field(default_factory=dict)
    max_loop_b: int = 3
    loop_b_history: List[Dict[str, Any]] = Field(default_factory=list)

    # A6 Package Validation & Loop C Recovery
    validation_report: Dict[str, Any] = Field(default_factory=dict)
    loop_c_count: int = 0
    max_loop_c: int = 2
    loop_c_history: List[Dict[str, Any]] = Field(default_factory=list)
    package_hash: str = ""

    # A7 Tekla 3D Detailing Model
    tekla_results: Dict[str, Any] = Field(default_factory=dict)

    # A8 Final Report & Gate 3 Human Acceptance
    final_report: Dict[str, Any] = Field(default_factory=dict)
    gate3_approved: bool = False
    accepted_by: Optional[str] = None
    acceptance_timestamp: Optional[str] = None

    # Error handling
    error: Optional[str] = None


def fail_workflow(state: WorkflowState, agent_id: str, message: str) -> WorkflowState:
    """Record a terminal failure rather than leaving an ended run as RUNNING."""
    from datetime import datetime

    state.status = "FAILED"
    state.current_agent = agent_id
    state.error = message
    state.agent_statuses[agent_id] = {"status": "FAILED", "message": message}
    state.logs.append(AgentLogEntry(
        agent_id=agent_id, agent_name=agent_id, status="FAILED",
        message=message, timestamp=datetime.now().strftime("%H:%M:%S"),
    ))
    return state

