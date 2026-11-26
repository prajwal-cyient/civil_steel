"""Bounded LLM-advised decision logic for Tekla stage A6d."""

from collections.abc import Callable

from agentic.nodes.a6_tekla.evaluate import grade_tekla_publication, publication_passed
from agentic.state import WorkflowState, fail_workflow, workflow_stage


def decide_tekla_outcome(
    state: WorkflowState,
    *,
    request_matches: Callable[[WorkflowState], bool],
    choose_correction: Callable[[WorkflowState, str, dict, list[dict], str], dict],
) -> tuple[WorkflowState, bool]:
    """Verify Tekla evidence and return ``(state, ready_to_finalize)``.

    A connection failure before model writes, or a failed member or connection
    read-back, loops ``A6d -> A6a`` with the same validated payload. The retry
    does not change a section. Simulation output and a selected-change scope stop.
    """
    stage = workflow_stage(state, "tekla")
    if state.tekla_prepublication_error is not None:
        try:
            if not request_matches(state) or state.tekla_publication_passed is not False:
                return fail_workflow(state, stage, "Tekla retry evidence changed before decision."), False
        except ValueError as exc:
            return fail_workflow(state, stage, f"Tekla retry verification failed: {exc}"), False
    else:
        try:
            if (
                not request_matches(state)
                or state.tekla_publication_passed is None
                or publication_passed(state.tekla_results, state.tekla_request) != state.tekla_publication_passed
            ):
                return fail_workflow(state, stage, "Tekla publication evidence changed before decision; inspect the model."), False
        except ValueError as exc:
            return fail_workflow(state, stage, f"Tekla decision verification failed: {exc}; inspect the model."), False

    if state.tekla_publication_passed:
        return state, True

    if state.tekla_prepublication_error is not None:
        grader = grade_tekla_publication(
            {},
            state.tekla_request,
            prepublication_error=state.tekla_prepublication_error,
        )
    else:
        grader = grade_tekla_publication(state.tekla_results or {}, state.tekla_request)
    state.tekla_grader_result = grader
    failed_reasons = list(grader.details.get("failed_reasons") or [])
    phase = grader.details.get("phase") or "fabrication_readback"
    if grader.details.get("simulation"):
        msg = "; ".join(failed_reasons) or "Tekla reported simulation output; native Open API publication required."
        return fail_workflow(state, stage, msg), False
    if not grader.corrective_action_needed or grader.details.get("change_scope"):
        reasons = "; ".join(failed_reasons)
        msg = reasons or "Tekla publication check failed and cannot be retried automatically."
        if phase == "fabrication_readback":
            msg = f"Tekla publication incomplete: {reasons or grader.summary} Inspect the model before retrying."
        return fail_workflow(state, stage, msg), False

    if state.tekla_retry_count >= state.max_tekla_retries:
        reason = state.tekla_prepublication_error or (failed_reasons[0] if failed_reasons else grader.summary)
        return fail_workflow(
            state,
            stage,
            f"Tekla publication check failed; retry limit reached: {reason}",
        ), False

    evidence = {
        "failure_phase": phase,
        "retries_used": state.tekla_retry_count,
        "retry_limit": state.max_tekla_retries,
    }
    choice = choose_correction(state, "tekla", evidence,
                               [{"id": "retry_publish"}, {"id": "stop"}], "retry_publish")
    if choice["id"] == "stop":
        if phase == "before_model_write":
            message = "Tekla recovery decision stopped publication before model changes."
        else:
            message = "Tekla recovery decision stopped publication retry."
        return fail_workflow(state, stage, message), False

    state.tekla_retry_count += 1
    reason = state.tekla_prepublication_error or grader.summary
    state.tekla_retry_history.append({
        "attempt": state.tekla_retry_count,
        "phase": phase,
        "action": choice["id"],
        "reason": reason,
    })
    if phase == "before_model_write":
        message = f"Tekla connection failed before model changes; retry {state.tekla_retry_count}/{state.max_tekla_retries}."
    else:
        message = f"Tekla member or connection check failed; retrying the same payload {state.tekla_retry_count}/{state.max_tekla_retries}."
    state.agent_statuses[stage] = {"status": "REVISING", "message": message}
    return state, False
