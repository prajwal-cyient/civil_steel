"""Agent A5 LLM decision handlers (Loop B connection & Loop C audit) and staged runner."""

import services.correction_advisor as correction_advisor
from orchestration.state import WorkflowState, fail_workflow
from . import A5A_build_json, A5B_trigger_api, A5C_evaluate_and_audit
from .joint_corrections import _correct_connection_parameters, apply_failed_joint_grade
from .idea_runtime import IdeaStaticaJointError


def decide_a5(state: WorkflowState, context: dict) -> WorkflowState:
    try:
        A5A_build_json._check_a5_request(state, context)
        joint_group, result, grader = context["joint_group"], context["result"], context["grader"]
        if (state.ideastatica_results.get(joint_group) != context["evaluated_result"]
                or result != context["evaluated_result"]
                or result.get("input_signature") != context["signature"]
                or state.joint_grader_results.get(joint_group) != context["evaluated_grader"]
                or grader != context["evaluated_grader"]
                or A5C_evaluate_and_audit._grade_a5_result(state, result) != grader):
            raise IdeaStaticaJointError("IDEA grade or evidence changed before correction routing.")
        if grader.passed:
            context["index"] += 1
            return state
        parameters = result.get("check_summary", {}).get("operation_parameters", [])
        return apply_failed_joint_grade(
            state, joint_group, result, grader, parameters, bool(context["selected"]),
            _correct_connection_parameters, correction_advisor.choose_correction,
        )
    except Exception as exc:
        return A5A_build_json._fail_a5_step(state, context, exc)



def run_a5_ideastatica_analyzer(state: WorkflowState) -> WorkflowState:
    """Retain the synchronous IDEA entry point for callers outside the graph."""
    context = A5A_build_json.begin_a5(state)
    while state.status != "FAILED":
        A5A_build_json.prepare_a5(state, context)
        if state.status == "FAILED" or context["complete"]:
            break
        if context["skip"]:
            continue
        for operation in (A5B_trigger_api.execute_a5, A5C_evaluate_and_audit.evaluate_a5, decide_a5):
            operation(state, context)
            if state.status == "FAILED" or state.agent_statuses.get("A5", {}).get("status") == "REVISING":
                return state
    return state


async def run_a5_staged(state: WorkflowState) -> WorkflowState:
    from agents.A5_idea_statica.graph import run_idea_graph
    return await run_idea_graph(state)
