"""
a4_staad_analyzer_agent.py

Agent A4: Test the Frame (STAAD.Pro FEA Analyzer & Frame Grader).
Contains the STAAD analysis interface and the deterministic Frame Grader.
Evaluates Eurocode 3 unity ratios (<= 1.0) and lateral drift (<= H/200).
Triggers Loop A corrective action when boundary conditions fail.
"""

from datetime import datetime
from copy import deepcopy
from typing import Any, Dict
from agentic.state import WorkflowState, GraderResult, fail_workflow
from agentic.catalogue import frame_options
from agentic.advisor import choose_correction
from agentic.evidence import frame_signature
from agentic.nodes.a4_staad.api import send_to_staad
from agentic.nodes.a4_staad.build import build_staad_payload
from agentic.nodes.a4_staad.evaluate import grade_staad_results
from agentic.nodes.a4_staad.correction import frame_correction_scope, apply_staad_grade as _apply_staad_grade


async def run_a4_staad_analyzer(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A4: Runs STAAD.Pro analysis, extracts forces, and grades the frame.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A4"

    grid = state.grid_config
    col_height = grid.get("col_height_mm", 5000.0)

    payload = build_staad_payload(state)

    await _run_staad_api(state, payload)
    if state.status == "FAILED":
        return state

    grader = grade_staad_results(state.staad_results, col_height, state.run_policy)
    state.frame_grader_result = grader
    from agentic.audit import evaluation
    evaluation(state, 'STAAD', grader)

    return apply_staad_grade(state, state.staad_results, grader)


async def run_a4_staged(state: WorkflowState) -> WorkflowState:
    """Run prepared analysis, grading and correction in the active subgraph."""
    from agentic.nodes.a4_staad.graph import run_staad_graph
    return await run_staad_graph(state)


async def _run_staad_api(state: WorkflowState, payload: dict) -> WorkflowState:
    candidate_signature = frame_signature(state)
    # A new frame analysis invalidates all dependent connection/publication evidence.
    state.ideastatica_results.clear()
    state.joint_grader_results.clear()
    state.active_operation_params.clear()
    state.loop_b_count.clear()
    state.validation_report.clear()
    state.tekla_results.clear()
    state.final_report.clear()
    state.staad_results.clear()
    state.frame_grader_result = None
    state.gate3_approved = False
    try:
        from agentic.audit import observe
        with observe(state, 'STAAD native analysis', 'STAAD'):
            staad_res = await send_to_staad(payload)
    except Exception as exc:
        return fail_workflow(state, "A4", f"STAAD execution failed: {exc}")
    if frame_signature(state) != candidate_signature:
        return fail_workflow(state, "A4", "Frame or loads changed during STAAD analysis; the results cannot be used.")
    state.staad_results = staad_res
    staad_res["candidate_signature"] = candidate_signature
    return state


def prepare_a4_staad(state: WorkflowState) -> WorkflowState:
    """Capture the STAAD request and bind it to the current frame and loads."""
    state.current_agent = "A4"
    state.staad_request.clear()
    try:
        state.staad_request = {
            "payload": deepcopy(build_staad_payload(state)),
            "candidate_signature": frame_signature(state),
            "design_generation": state.design_generation,
        }
    except (TypeError, ValueError) as exc:
        return fail_workflow(state, "A4", f"STAAD request preparation failed: {exc}")
    return state


async def execute_a4_staad(state: WorkflowState) -> WorkflowState:
    """Call STAAD only when the prepared request still matches the frame."""
    request = state.staad_request
    try:
        valid = (
            bool(request)
            and request.get("design_generation") == state.design_generation
            and request.get("candidate_signature") == frame_signature(state)
            and request.get("payload") == build_staad_payload(state)
        )
    except (TypeError, ValueError) as exc:
        state.staad_request.clear()
        return fail_workflow(state, "A4", f"STAAD request verification failed: {exc}")
    if not valid:
        state.staad_request.clear()
        return fail_workflow(state, "A4", "STAAD request is missing or no longer matches the current frame and loads.")
    try:
        return await _run_staad_api(state, request["payload"])
    finally:
        state.staad_request.clear()


def evaluate_a4_staad(state: WorkflowState) -> WorkflowState:
    """Grade current STAAD evidence before choosing a correction."""
    try:
        if state.staad_results.get("candidate_signature") != frame_signature(state):
            return fail_workflow(state, "A4", "STAAD evidence does not belong to the current frame and loads.")
    except (TypeError, ValueError) as exc:
        return fail_workflow(state, "A4", f"STAAD evidence verification failed: {exc}")
    grader = grade_staad_results(
        state.staad_results, state.grid_config.get("col_height_mm", 5000.0), state.run_policy
    )
    state.frame_grader_result = grader
    from agentic.audit import evaluation
    evaluation(state, 'STAAD', grader)
    return state


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
    return _apply_staad_grade(state, staad_res, grader, frame_options, choose_correction)
