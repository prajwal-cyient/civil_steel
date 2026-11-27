"""
state.py

Defines the centralized state and data models for the LangGraph agentic workflow.
Every agent reads from and updates this typed WorkflowState.
"""

from typing import Any, Dict, List, Literal, Optional
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
    The shared state dictionary for the single A1-A7 workflow.
    """
    run_id: str
    layout_version: Literal[2] = Field(default=2, frozen=True)
    status: str = "INITIALIZED"  # "RUNNING", "PAUSED_AT_GATE3", "COMPLETED", "FAILED"
    current_agent: str = "A1"
    logs: List[AgentLogEntry] = Field(default_factory=list)
    agent_statuses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    substage_statuses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    agent_timings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    output_archive: Dict[str, Any] = Field(default_factory=dict)
    audit_events: List[Dict[str, Any]] = Field(default_factory=list)

    # A1 Inputs
    raw_input: Dict[str, Any] = Field(default_factory=dict)
    run_policy: Dict[str, Any] = Field(default_factory=dict)
    change_scope: Dict[str, Any] = Field(default_factory=dict)
    grid_config: Dict[str, Any] = Field(default_factory=dict)
    load_config: Dict[str, Any] = Field(default_factory=dict)

    # A2 Readiness
    readiness: Dict[str, Any] = Field(default_factory=dict)
    simulation_mode: bool = False  # Set to True if software is offline to enable safe testing
    model_call_count: int = 0
    model_events: List[Dict[str, Any]] = Field(default_factory=list)
    # Deep Agent requests are audited separately from optional correction proposals.
    deep_agent_model_call_count: int = 0
    deep_agent_events: List[Dict[str, Any]] = Field(default_factory=list)

    # A3 Frame Generation
    frame_candidate: Dict[str, Any] = Field(default_factory=dict)
    frame_plan: Dict[str, Any] = Field(default_factory=dict)
    active_column_profile: str = ""
    active_beam_profile: str = ""
    active_long_brace_profile: str = ""
    active_trans_brace_profile: str = ""
    design_generation: int = 0
    candidate_history: List[str] = Field(default_factory=list)

    # A4 STAAD Structural Analysis
    staad_request: Dict[str, Any] = Field(default_factory=dict)
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

    # A5c Package Validation & Loop C Recovery (inside A5)
    validation_report: Dict[str, Any] = Field(default_factory=dict)
    loop_c_count: int = 0
    max_loop_c: int = 2
    loop_c_history: List[Dict[str, Any]] = Field(default_factory=list)
    package_hash: str = ""

    # A6 Tekla 3D Detailing Model
    tekla_results: Dict[str, Any] = Field(default_factory=dict)
    tekla_request: Dict[str, Any] = Field(default_factory=dict)
    tekla_publication_passed: Optional[bool] = None
    tekla_grader_result: Optional[GraderResult] = None
    tekla_prepublication_error: Optional[str] = None
    tekla_retry_count: int = 0
    max_tekla_retries: int = 5
    tekla_retry_history: List[Dict[str, Any]] = Field(default_factory=list)

    # A7 Final Report & Gate 3 Human Acceptance
    final_report: Dict[str, Any] = Field(default_factory=dict)
    gate3_approved: bool = False
    accepted_by: Optional[str] = None
    acceptance_timestamp: Optional[str] = None

    # Error handling
    error: Optional[str] = None


def fail_workflow(state: WorkflowState, agent_id: str, message: str) -> WorkflowState:
    """Record a terminal failure rather than leaving an ended run as RUNNING."""
    from datetime import datetime
    from utils.audit import clean
    message = clean(message)

    state.status = "FAILED"
    state.current_agent = agent_id
    state.error = message
    state.agent_statuses[agent_id] = {"status": "FAILED", "message": message}
    state.logs.append(AgentLogEntry(
        agent_id=agent_id, agent_name=agent_id, status="FAILED",
        message=message, timestamp=datetime.now().astimezone().isoformat(),
    ))
    return state


def workflow_stage(state: WorkflowState, role: str) -> str:
    """Resolve roles in the single supported A1-A7 layout."""
    stages = {
        "package": "A5c",
        "tekla": "A6",
        "report": "A7",
    }
    return stages[role]

