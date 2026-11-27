"""Select approved STAAD corrections and apply frame-grade outcomes."""

from datetime import datetime
from math import isfinite
from typing import Any, Callable, Dict

from orchestration.state import WorkflowState, AgentLogEntry, GraderResult, fail_workflow
from config.run_policy import policy_for


def frame_correction_scope(results: dict, target: str, max_member_ratio: float = 1.0) -> list[dict]:
    """Identify every member changed by a shared section variable.

    The reference generator has uniform category profiles and connection
    templates. Mixed per-member profiles are not yet a validated design mode.
    Therefore a category-wide revision is allowed only if ALL affected members
    actually fail; otherwise stop rather than resize already passing elements.
    """
    categories = {"column": ("column",), "beam": ("beam_longitudinal", "beam_transverse", "beam"),
                  "longitudinal_brace": ("bracing_longitudinal", "bracing_longitudinal_mirrored"),
                  "transverse_brace": ("bracing_transverse", "bracing_transverse_mirrored")}[target]
    ids = [identifier for kind, identifiers in results.get("member_ids_by_type", {}).items()
           if kind in categories for identifier in identifiers]
    ratios = {str(key): value for key, value in results.get("member_ratios", {}).items()}
    data = {str(key): value for key, value in results.get("member_data_by_id", {}).items()}
    if not ids or any(str(identifier) not in ratios or str(identifier) not in data for identifier in ids):
        raise ValueError("Exact per-member correction evidence is missing; no section was changed.")
    affected, passing = [], []
    for identifier in ids:
        row, member = ratios[str(identifier)], data[str(identifier)]
        ratio = row.get("ratio")
        if type(ratio) not in (int, float) or not isfinite(ratio) or ratio < 0:
            raise ValueError(f"Invalid correction evidence for member {identifier}.")
        entry = {"id": identifier, "name": member.get("name", row.get("name")),
                 "physical_member": member.get("tekla_name", member.get("name")),
                 "ratio": ratio, "profile": member.get("profile")}
        (affected if ratio > max_member_ratio else passing).append(entry)
    if passing:
        raise ValueError(f"Localized correction required: {len(affected)} failing and {len(passing)} passing {target} members. "
                         f"Failing locations: {[(row['id'], row['name']) for row in affected[:8]]}. "
                         "The reference shared-profile variable would also change passing elements; mixed-profile geometry/templates "
                         "are not validated. No section was changed.")
    return affected


def apply_staad_grade(
    state: WorkflowState,
    staad_res: Dict[str, Any],
    grader: GraderResult,
    frame_options: Callable[[str, str, str], list[dict]],
    choose_correction: Callable[[WorkflowState, str, dict, list[dict], str], dict],
) -> WorkflowState:
    """Apply the existing pass, correction, and stop decisions to a frame grade."""
    if grader.details.get("invalid_results"):
        return fail_workflow(state, "A4", grader.summary)

    if grader.passed:
        msg = f"STAAD Analysis Passed! {grader.summary}"
        state.agent_statuses["A4"] = {"status": "PASSED", "message": msg, "max_ratio": grader.details.get("overall_max_ratio")}
        state.logs.append(AgentLogEntry(
            agent_id="A4",
            agent_name="STAAD Structural Analyzer",
            status="PASSED",
            message=msg,
            timestamp=datetime.now().astimezone().isoformat()
        ))
    else:
        # Boundary conditions failed -> Check if Loop A can correct it
        if state.loop_a_count < state.max_loop_a:
            target = grader.details.get("fix_target", "column")
            profile_fields = {
                "column": "active_column_profile", "beam": "active_beam_profile",
                "longitudinal_brace": "active_long_brace_profile", "transverse_brace": "active_trans_brace_profile",
            }
            field = profile_fields[target]
            old_prof = getattr(state, field)
            try:
                affected_members = frame_correction_scope(staad_res, target, policy_for(state).max_member_ratio)
            except ValueError as exc:
                return fail_workflow(state, "A4", f"{grader.summary} Correction stopped: {exc}")
            options = frame_options(old_prof, target, state.load_config.get("country", "EUROPE"))
            if not options:
                return fail_workflow(state, "A4", f"No larger approved STAAD catalogue section is available for {target} ({old_prof}).")
            selected = options[0]
            # Deterministic default first; when LLM advisor is enabled and multiple
            # verified larger sections exist, let GPT choose among them cleanly.
            if len(options) > 1:
                selected = choose_correction(state, "frame", grader.model_dump(), options, selected["id"])
            new_prof = selected["profile"]
            profiles = {name: getattr(state, attr) for name, attr in profile_fields.items()}
            profiles[target] = new_prof
            cand_signature = str(sorted(profiles.items()))

            if new_prof and new_prof != old_prof and cand_signature not in state.candidate_history:
                state.candidate_history.append(cand_signature)
                state.loop_a_count += 1
                setattr(state, field, new_prof)

                state.loop_a_history.append({
                    "iteration": state.loop_a_count,
                    "target": target,
                    "failing_profile": old_prof,
                    "upgraded_profile": new_prof,
                    "reason": grader.summary,
                    "evidence": grader.details,
                    "affected_members": affected_members,
                    "unrelated_sections_changed": False,
                })
                msg = f"Loop A ({state.loop_a_count}/{state.max_loop_a}): {grader.summary} -> Upgraded {target.capitalize()} to {new_prof}"
                state.agent_statuses["A4"] = {"status": "REVISING", "message": msg, "target": target, "next_profile": new_prof}
                state.logs.append(AgentLogEntry(
                    agent_id="A4",
                    agent_name="STAAD Structural Analyzer",
                    status="REVISING",
                    message=msg,
                    timestamp=datetime.now().astimezone().isoformat(),
                    loop_info=f"Loop A #{state.loop_a_count}"
                ))
                return state

        # If ceiling reached or cannot fix automatically
        msg = f"STAAD Analysis Failed: {grader.summary} (No permitted revision remains or retry limit reached)"
        state.status = "FAILED"
        state.error = msg
        state.agent_statuses["A4"] = {"status": "FAILED", "message": msg}
        state.logs.append(AgentLogEntry(
            agent_id="A4",
            agent_name="STAAD Structural Analyzer",
            status="FAILED",
            message=msg,
            timestamp=datetime.now().astimezone().isoformat()
        ))

    return state
