"""Conditional workflow routing rules for Loop A, Loop B, and Loop C."""

from typing import Literal
from orchestration.state import WorkflowState, workflow_stage

def route_after_a4(state: WorkflowState) -> Literal["a3_frame_generator", "a5_ideastatica_analyzer", "__end__"]:
    """
    Evaluates Frame Grader results.
    If boundary conditions failed and retries remain -> routes back to A3 (Loop A).
    If passed -> advances to A5.
    If failed with max retries -> ends with error.
    """
    if state.status == "FAILED":
        return "__end__"

    status_a4 = state.agent_statuses.get("A4", {}).get("status", "")
    if status_a4 == "REVISING" and state.loop_a_count <= state.max_loop_a:
        return "a3_frame_generator"  # Loop A trigger!

    if status_a4 == "PASSED":
        return "a5_ideastatica_analyzer"

    return "__end__"


def route_after_a5(state: WorkflowState) -> Literal["a5_ideastatica_analyzer", "a6_tekla_modeler", "__end__"]:
    """
    Evaluates Joint Grader results.
    If connection checks failed and retries remain -> loops back to A5 (Loop B).
    If joint design and the package audit passed -> advances to Tekla.
    If failed with max retries -> ends with error.
    """
    if state.status == "FAILED":
        return "__end__"

    status_a5 = state.agent_statuses.get("A5", {}).get("status", "")
    if status_a5 == "REVISING":
        return "a5_ideastatica_analyzer"  # Loop B trigger!

    if (status_a5 == "PASSED" and state.validation_report.get("passed")
            and state.agent_statuses.get("A5c", {}).get("status") == "PASSED"):
        return "a6_tekla_modeler"

    return "__end__"


def route_after_package_audit(state: WorkflowState) -> Literal["a5_ideastatica_analyzer", "a6_tekla_modeler", "__end__"]:
    if state.status == "FAILED":
        return "__end__"
    status_a6 = state.agent_statuses.get(workflow_stage(state, "package"), {}).get("status", "")
    if status_a6 == "REVISING" and state.loop_c_count <= state.max_loop_c:
        if state.validation_report.get("recovery_target") == "a5_ideastatica_analyzer":
            return "a5_ideastatica_analyzer"
        return "__end__"
    if status_a6 == "PASSED":
        return "a6_tekla_modeler"
    return "__end__"


