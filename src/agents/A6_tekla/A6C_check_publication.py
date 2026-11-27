"""Evaluate native Tekla fabrication/publication output and read-back evidence."""

from orchestration.state import GraderResult


def grade_tekla_publication(
    result: dict,
    payload: dict,
    *,
    prepublication_error: str | None = None,
    change_scope: bool = False,
) -> GraderResult:
    """Grade Tekla Open API publication and read-back verification against the validated package."""
    expected_members = len(payload.get("members") or [])
    expected_connections = len(payload.get("connections") or [])
    plate_policy = bool(payload.get("plate_material_policy"))

    if prepublication_error is not None:
        return GraderResult(
            passed=False,
            score=0.0,
            summary=f"Tekla connection failed before model write: {prepublication_error}",
            corrective_action_needed=not change_scope,
            corrective_action={"fix_type": "retry_publish", "phase": "before_model_write"} if not change_scope else None,
            details={
                "phase": "before_model_write",
                "recoverable": not change_scope,
                "simulation": False,
                "change_scope": change_scope,
                "reason": prepublication_error,
                "failed_reasons": [f"Tekla connection failed before model write: {prepublication_error}"],
                "metrics": {
                    "expected_members": float(expected_members),
                    "expected_connections": float(expected_connections),
                    "created": 0.0,
                    "verified_members": 0.0,
                },
            },
        )

    result = result or {}
    created = int(result.get("created") or 0)
    verified_members = int(result.get("verified_members") or 0)
    has_conn_count = "verified_connections" in result and result.get("verified_connections") is not None
    verified_connections = int(result.get("verified_connections") or 0) if has_conn_count else expected_connections
    failed = int(result.get("failed") or 0)
    errors = list(result.get("errors") or [])
    plate_verified = bool(result.get("plate_materials_verified"))
    is_simulation = bool(result.get("simulation"))

    reasons: list[str] = []
    if is_simulation:
        reasons.append("Tekla reported simulation output instead of native Open API publication.")
    if expected_members <= 0:
        reasons.append("Tekla payload contains zero structural members.")
    if created != expected_members:
        reasons.append(f"Created {created}/{expected_members} members in Tekla.")
    if verified_members != expected_members:
        reasons.append(f"Read-back verified {verified_members}/{expected_members} members in Tekla.")
    if expected_connections > 0 and has_conn_count and verified_connections < expected_connections:
        reasons.append(
            f"Read-back verified {verified_connections}/{expected_connections} connections in Tekla."
        )
    if plate_policy and not plate_verified:
        reasons.append("Tekla plate and bolt material grades were not verified against IDEA evidence.")
    if failed:
        reasons.append(f"Tekla Open API reported {failed} failed operation(s).")
    for err in errors[:3]:
        msg = str(err.get("error", err)) if isinstance(err, dict) else str(err)
        if msg:
            reasons.append(f"Tekla API error: {msg}")

    passed = not reasons
    summary = (
        f"Tekla fabrication check passed ({verified_members}/{expected_members} members verified)."
        if passed
        else "; ".join(reasons)
    )
    score = 1.0 if passed else (0.0 if (is_simulation or expected_members <= 0) else round(max(0.0, min(1.0, verified_members / expected_members)), 3))
    # A failed member or connection read-back retries the same validated payload.
    # Simulation, an empty payload, and a selected-change scope do not.
    recoverable = bool(reasons) and not is_simulation and expected_members > 0 and not change_scope
    return GraderResult(
        passed=passed,
        score=score,
        summary=summary,
        corrective_action_needed=recoverable,
        corrective_action={"fix_type": "retry_publish", "phase": "fabrication_readback"} if recoverable else None,
        details={
            "phase": "fabrication_readback",
            "recoverable": recoverable,
            "simulation": is_simulation,
            "change_scope": change_scope,
            "created": created,
            "verified_members": verified_members,
            "verified_connections": result.get("verified_connections"),
            "failed": failed,
            "errors": errors[:5],
            "failed_reasons": reasons,
            "metrics": {
                "expected_members": float(expected_members),
                "expected_connections": float(expected_connections),
                "created": float(created),
                "verified_members": float(verified_members),
                "verified_connections": float(verified_connections),
                "failed": float(failed),
                "error_count": float(len(errors)),
                "plate_materials_verified": 1.0 if plate_verified else 0.0,
            },
        },
    )


def publication_passed(result: dict, payload: dict) -> bool:
    return grade_tekla_publication(result, payload).passed

from orchestration.state import WorkflowState, workflow_stage
from . import A6A_build_json


def evaluate_a7_tekla(state: WorkflowState) -> WorkflowState:
    from orchestration.state import fail_workflow
    # local grade_tekla_publication
    try:
        if not A6A_build_json._tekla_request_matches(state):
            return fail_workflow(state, workflow_stage(state, "tekla"), "Tekla request no longer matches the validated package; inspect the model.")
    except ValueError as exc:
        return fail_workflow(state, workflow_stage(state, "tekla"), f"Tekla request verification failed: {exc}; inspect the model.")
    payload = state.tekla_request
    if state.tekla_prepublication_error is not None:
        state.tekla_grader_result = grade_tekla_publication(
            {},
            payload,
            prepublication_error=state.tekla_prepublication_error,
        )
        state.tekla_publication_passed = False
        return state
    tekla_res = state.tekla_results
    from utils.audit import evaluation
    grader = grade_tekla_publication(tekla_res, payload)
    state.tekla_grader_result = grader
    valid_publication = grader.passed
    state.tekla_publication_passed = valid_publication
    evaluation(state, 'Tekla', {'passed': valid_publication, 'created': tekla_res.get('created'),
        'verified_members': tekla_res.get('verified_members'),
        'verified_connections': tekla_res.get('verified_connections'), 'errors': tekla_res.get('errors', []),
        'failed_reasons': grader.details.get('failed_reasons', [])})
    return state


