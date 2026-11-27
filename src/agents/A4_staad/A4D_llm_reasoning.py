"""Agent A4 LLM correction decisions, Loop A routing, and STAAD entrypoints."""

from datetime import datetime
from typing import Any, Dict

from config.catalogue import frame_options
import services.correction_advisor as correction_advisor
from orchestration.state import WorkflowState, GraderResult, fail_workflow
from utils.evidence import frame_signature
from . import A4B_trigger_api
from .A4A_build_json import build_staad_payload
from .A4C_evaluate_results import grade_staad_results
from .frame_corrections import frame_correction_scope, apply_staad_grade as _apply_staad_grade


def decide_a4_staad(state: WorkflowState) -> WorkflowState:
    """Apply the grader's existing correction and stop decisions."""
    if state.frame_grader_result is None:
        return fail_workflow(state, "A4", "A STAAD frame grade is required before correction routing.")
    try:
        if state.staad_results.get("candidate_signature") != frame_signature(state):
            return fail_workflow(state, "A4", "STAAD evidence does not belong to the current frame and loads.")
    except (TypeError, ValueError) as exc:
        return fail_workflow(state, "A4", f"STAAD evidence verification failed: {exc}")
    current_grade = grade_staad_results(
        state.staad_results, state.grid_config.get("col_height_mm", 5000.0), state.run_policy
    )
    if current_grade != state.frame_grader_result:
        return fail_workflow(state, "A4", "STAAD grade no longer matches the current analysis evidence and run policy.")
    return apply_staad_grade(state, state.staad_results, state.frame_grader_result)


def apply_staad_grade(
    state: WorkflowState, staad_res: Dict[str, Any], grader: GraderResult
) -> WorkflowState:
    return _apply_staad_grade(state, staad_res, grader, frame_options, correction_advisor.choose_correction)


async def run_a4_staad_analyzer(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A4: Runs STAAD.Pro analysis, extracts forces, and grades the frame.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A4"

    grid = state.grid_config
    col_height = grid.get("col_height_mm", 5000.0)

    payload = build_staad_payload(state)

    await A4B_trigger_api._run_staad_api(state, payload)
    if state.status == "FAILED":
        return state

    grader = grade_staad_results(state.staad_results, col_height, state.run_policy)
    state.frame_grader_result = grader
    from utils.audit import evaluation
    evaluation(state, 'STAAD', grader)

    return apply_staad_grade(state, state.staad_results, grader)


async def run_a4_staged(state: WorkflowState) -> WorkflowState:
    """Run prepared analysis, grading and correction in the active subgraph."""
    from agents.A4_staad.graph import run_staad_graph
    return await run_staad_graph(state)


