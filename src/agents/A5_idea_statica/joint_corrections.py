"""Select existing localized connection parameter corrections."""

from datetime import datetime
import hashlib
import json
from typing import Callable

from config.ladders import step_parameter_expression
from orchestration.state import AgentLogEntry, GraderResult, WorkflowState, fail_workflow


def _correct_connection_parameters(parameters: list, fix_type: str, parameter_keys=None) -> tuple:
    """Change only localized exposed parameters; never upsize every weld/plate."""
    updated, changes = [], []
    for parameter in parameters:
        key = parameter["key"]
        lower = key.lower()
        matches = (
            fix_type == "weld_size" and "weld" in lower
            or fix_type == "bolt_dia" and (lower.endswith("_d") or "diameter" in lower)
            or fix_type == "plate_thickness" and (lower.endswith("_th") or "thickness" in lower)
        )
        if parameter_keys is not None:
            matches = matches and key in parameter_keys
        expression = str(parameter.get("expression", ""))
        numeric = expression.strip("\"'")
        new_expression = step_parameter_expression(key, numeric, fix_type) if matches else numeric
        if matches and new_expression != numeric:
            if expression[:1] in ("'", '\"'):
                new_expression = expression[0] + new_expression + expression[0]
            changes.append({"key": key, "old_value": expression, "new_value": new_expression})
            expression = new_expression
        updated.append({"key": key, "expression": expression})
    if parameter_keys is None and len(changes) > 1:
        # A category summary such as "Welds failed" does not identify which
        # weld failed. Preserve already-correct details until the detailed
        # result is mapped to an exact exposed parameter.
        return [{"key": p["key"], "expression": str(p.get("expression", ""))} for p in parameters], []
    return updated, changes


def apply_failed_joint_grade(
    state: WorkflowState,
    joint_group: str,
    result: dict,
    grader: GraderResult,
    parameters: list,
    has_selected_changes: bool,
    correct_parameters: Callable[[list, str, list | None], tuple],
    choose_correction: Callable[[WorkflowState, str, dict, list[dict], str], dict],
) -> WorkflowState:
    if has_selected_changes:
        return fail_workflow(state, "A5", f"{joint_group}: approved values failed engineering checks. Further corrections require a new change preview and approval. {grader.summary}")
    if not grader.corrective_action_needed:
        return fail_workflow(state, "A5", f"{joint_group}: {grader.summary}")
    count = state.loop_b_count.get(joint_group, 0)
    if count >= state.max_loop_b:
        return fail_workflow(state, "A5", f"{joint_group}: retry limit reached. {grader.summary}")
    fix_type = grader.corrective_action["fix_type"]
    localized = result.get("check_summary", {}).get("failed_parameter_keys")
    updated, changes = correct_parameters(parameters, fix_type, localized)
    if not changes:
        localization_error = result.get("check_summary", {}).get("localization_error")
        return fail_workflow(state, "A5", f"{joint_group}: no uniquely localized exposed numeric {fix_type} correction is available. "
                             "Detailed failed-operation-to-parameter mapping is required when several parameters match; "
                             f"already-correct details were preserved. {grader.summary}"
                             + (f" Localization: {localization_error}" if localization_error else ""))
    metric_signature = hashlib.sha256(json.dumps(result["check_summary"]["summary"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    history = [entry for entry in state.loop_b_history if entry["joint_group"] == joint_group]
    if history and history[-1].get("result_signature") == metric_signature:
        return fail_workflow(state, "A5", f"{joint_group}: checks did not change after the confirmed parameter update; inspect calculation details instead of repeating the same correction.")
    options = [{"id": "all", "changes": changes}]
    if len(changes) > 1:
        options += [{"id": change["key"], "changes": [change]} for change in changes]
    selected = options[0]
    if len(options) > 1:
        selected = choose_correction(state, "connection", grader.model_dump(), options, "all")
    changes = selected["changes"]
    overrides = {p["key"]: p for p in state.active_operation_params.get(joint_group, [])}
    changed_keys = {p["key"] for p in changes}
    overrides.update({p["key"]: p for p in updated if p["key"] in changed_keys})
    state.active_operation_params[joint_group] = list(overrides.values())
    state.loop_b_count[joint_group] = count + 1
    state.loop_b_history.append({"joint_group": joint_group, "iteration": count + 1,
        "fix_type": fix_type, "changes": changes, "reason": grader.summary, "result_signature": metric_signature,
        "localization": {key: result["check_summary"][key] for key in
            ("failed_weld_ids", "failed_parameter_keys", "parameter_to_weld_ids", "unrelated_geometry_unchanged", "probe_expressions_restored")
            if key in result["check_summary"]}})
    message = f"Loop B ({count + 1}/{state.max_loop_b}): {joint_group}: {changes}"
    state.agent_statuses["A5"] = {"status": "REVISING", "message": message}
    state.logs.append(AgentLogEntry(agent_id="A5", agent_name="Connection Designer", status="REVISING", message=message, timestamp=datetime.now().astimezone().isoformat()))
    return state


