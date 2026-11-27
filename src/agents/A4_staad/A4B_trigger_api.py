"""Invoke the OpenSTAAD API and guard state during STAAD execution."""

from utils.evidence import frame_signature
from orchestration.state import WorkflowState, fail_workflow
from .A4A_build_json import build_staad_payload


async def send_to_staad(data: dict) -> dict:
    """Run live OpenSTAAD; execution errors must never become simulated passes."""
    from .staad_openstaad import send_to_staad as live_send
    return await live_send(data)


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
        from utils.audit import observe
        with observe(state, 'STAAD native analysis', 'STAAD'):
            staad_res = await send_to_staad(payload)
    except Exception as exc:
        return fail_workflow(state, "A4", f"STAAD execution failed: {exc}")
    if frame_signature(state) != candidate_signature:
        return fail_workflow(state, "A4", "Frame or loads changed during STAAD analysis; the results cannot be used.")
    state.staad_results = staad_res
    staad_res["candidate_signature"] = candidate_signature
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


