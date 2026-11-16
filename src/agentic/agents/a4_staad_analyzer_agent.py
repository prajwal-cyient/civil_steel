"""
a4_staad_analyzer_agent.py

Agent A4: Test the Frame (STAAD.Pro FEA Analyzer & Frame Grader).
Contains the STAAD analysis interface and the deterministic Frame Grader.
Evaluates Eurocode 3 unity ratios (<= 1.0) and lateral drift (<= H/200).
Triggers Loop A corrective action when boundary conditions fail.
"""

from datetime import datetime
from copy import deepcopy
from math import isfinite
import asyncio
from typing import Any, Dict, List, Optional
from agentic.state import WorkflowState, AgentLogEntry, GraderResult, fail_workflow
from agentic.ladders import get_next_profile
from agentic.catalogue import frame_options
from agentic.advisor import choose_correction
from agentic.evidence import frame_signature


async def send_to_staad(data: dict) -> dict:
    """Run live OpenSTAAD; execution errors must never become simulated passes."""
    from radiator_support.staad_integration import send_to_staad as live_send
    return await live_send(data)


def frame_correction_scope(results: dict, target: str) -> list[dict]:
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
        (affected if ratio > 1.0 else passing).append(entry)
    if passing:
        raise ValueError(f"Localized correction required: {len(affected)} failing and {len(passing)} passing {target} members. "
                         f"Failing locations: {[(row['id'], row['name']) for row in affected[:8]]}. "
                         "The reference shared-profile variable would also change passing elements; mixed-profile geometry/templates "
                         "are not validated. No section was changed.")
    return affected


def grade_staad_results(staad_res: Dict[str, Any], col_height_mm: float) -> GraderResult:
    """
    Evaluates STAAD analysis output against structural boundary conditions:
    1. Member Steel Design Unity Ratio <= 1.0 (EC3 / AISC 360).
    2. Lateral drift <= H_col / 200.
    """
    max_ratios = staad_res.get("member_type_max_ratios") or staad_res.get("member_type_max_ratios_by_type_max") or {}
    invalid = []
    status = staad_res.get("analysis_status") or {}
    if staad_res.get("analysis_failed") or status.get("ReturnValue") not in (2, 3) or status.get("NoOfErrors", 0):
        invalid.append(f"STAAD analysis did not complete successfully: {status}")
    for field in ("failed", "property_assignment_failed", "load_cases_failed", "errors", "property_errors", "load_errors"):
        if staad_res.get(field):
            invalid.append(f"STAAD reported {field}: {staad_res[field]}")
    if staad_res.get("simulation"):
        invalid.append("Simulated results cannot verify a live design.")
    member_ratios = {str(key): row for key, row in staad_res.get("member_ratios", {}).items()}
    if staad_res.get("member_data_by_id") and not member_ratios:
        invalid.append("Individual design ratios are missing for generated members.")
    if "member_ratios" in staad_res:
        expected = {str(key) for key in staad_res.get("member_data_by_id", {})}
        if expected - set(member_ratios):
            invalid.append("Individual design ratios are missing for generated members.")
        for identifier, row in member_ratios.items():
            value = row.get("ratio") if isinstance(row, dict) else None
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                invalid.append(f"Invalid individual design ratio for member {identifier}.")
        if not invalid:
            for kind, identifiers in staad_res.get("member_ids_by_type", {}).items():
                values = [member_ratios[str(i)]["ratio"] for i in identifiers if str(i) in member_ratios]
                if values and (kind not in max_ratios or abs(max(values)-max_ratios[kind]) > 1e-7):
                    invalid.append(f"Member-type maximum disagrees with individual ratios for {kind}.")
    if invalid:
        return GraderResult(passed=False, score=0.0, summary="; ".join(invalid),
                            details={"invalid_results": True}, corrective_action_needed=False)
    if not max_ratios or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v < 0 for v in max_ratios.values()):
        invalid.append("Missing, negative, or nonfinite member design ratios.")
    expected_types = {kind for kind, ids in staad_res.get("member_ids_by_type", {}).items() if ids}
    if expected_types - set(max_ratios):
        invalid.append("Member design ratios do not cover all generated member types.")
    envelopes = staad_res.get("envelope_summary_results") or {}
    drift_fields = {"min_X", "max_X", "min_Z", "max_Z"}
    if staad_res.get("units", {}).get("displacement") != "mm":
        invalid.append("Displacements must be explicitly normalized to mm.")
    for env in envelopes.values():
        limits = env.get("node_displacements_limits", {})
        flags = env.get("node_displacements_limits_exceed_col_height_200", {})
        valid_values = drift_fields.issubset(limits) and all(
            type(limits[key]) in (int, float) and isfinite(limits[key]) and limits[key] >= 0 for key in drift_fields)
        valid_flags = drift_fields.issubset(flags) and all(type(flags[key]) is bool for key in drift_fields)
        if not valid_values or not valid_flags:
            invalid.append("Missing or incomplete displacement checks.")
        elif any(flags[key] != (limits[key] > col_height_mm / 200.0) for key in drift_fields):
            invalid.append("Reported displacement checks disagree with the actual displacements.")
    if not envelopes:
        invalid.append("No displacement envelopes returned.")
    service_ids = {str(env["envelopeId"]) for env in staad_res.get("load_envelopes", []) if env.get("envelopeType") == "SERVICEABILITY"}
    if "load_envelopes" in staad_res and (not service_ids or not service_ids.issubset({str(key) for key in envelopes})):
        invalid.append("Serviceability envelope evidence is missing.")
    if invalid:
        return GraderResult(passed=False, score=0.0, summary="; ".join(invalid),
                            details={"invalid_results": True}, corrective_action_needed=False)
    max_col_ratio = max_ratios.get("column", 0.0)
    max_beam_ratio = max(max_ratios.get("beam", 0.0), max_ratios.get("beam_longitudinal", 0.0), max_ratios.get("beam_transverse", 0.0))
    overall_max_ratio = max([v for v in max_ratios.values()] or [0.0])
    failed_members = [{"id": key, "name": row.get("name"), "ratio": row["ratio"]}
                      for key, row in member_ratios.items() if row["ratio"] > 1.0]

    # Check drift limit
    drift_exceeded = False
    env_summaries = staad_res.get("envelope_summary_results", {})
    for env_id, env_data in env_summaries.items():
        if service_ids and str(env_id) not in service_ids:
            continue
        exceed_dict = env_data.get("node_displacements_limits_exceed_col_height_200", {})
        if any(exceed_dict.values()):
            drift_exceeded = True
            break

    failures = []
    if overall_max_ratio > 1.0:
        failures.append(f"Max member stress ratio {overall_max_ratio:.2f} > 1.0 (Column={max_col_ratio:.2f}, Beam={max_beam_ratio:.2f})")
    if drift_exceeded:
        failures.append(f"Lateral drift exceeded allowable H/200 ({col_height_mm/200:.1f} mm)")

    if not failures:
        return GraderResult(
            passed=True,
            score=1.0,
            summary=f"All structural checks passed (Max Ratio: {overall_max_ratio:.2f} <= 1.0, Drift <= H/200).",
            details={
                "overall_max_ratio": overall_max_ratio,
                "column_ratio": max_col_ratio,
                "beam_ratio": max_beam_ratio,
                "drift_exceeded": False
            },
            corrective_action_needed=False
        )

    # Failed boundary conditions -> build corrective action
    governing_type = max(max_ratios, key=max_ratios.get)
    if drift_exceeded or governing_type == "column":
        fix_target = "column"
    elif "bracing_longitudinal" in governing_type or "longitudinal_brace" in governing_type:
        fix_target = "longitudinal_brace"
    elif "bracing_transverse" in governing_type or "transverse_brace" in governing_type:
        fix_target = "transverse_brace"
    elif "beam" in governing_type:
        fix_target = "beam"
    else:
        return GraderResult(passed=False, score=0, summary=f"No approved correction mapping for member type {governing_type}.", details={"invalid_results": True})
    return GraderResult(
        passed=False,
        score=max(0.0, 1.0 - (overall_max_ratio - 1.0)),
        summary="; ".join(failures),
        details={
            "overall_max_ratio": overall_max_ratio,
            "column_ratio": max_col_ratio,
            "beam_ratio": max_beam_ratio,
            "drift_exceeded": drift_exceeded,
            "fix_target": fix_target,
            "failed_members": failed_members
        },
        corrective_action_needed=True,
        corrective_action={"fix_target": fix_target}
    )


async def run_a4_staad_analyzer(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A4: Runs STAAD.Pro analysis, extracts forces, and grades the frame.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A4"

    candidate = state.frame_candidate
    members_split = candidate.get("members_split_beams", [])
    grid = state.grid_config
    load_cfg = state.load_config
    col_height = grid.get("col_height_mm", 5000.0)

    payload = {
        "grid": grid,
        "members_split_beams": deepcopy(members_split),
        "load_configuration": load_cfg,
        "expected_staad_model_path": state.readiness.get("staad_model_path"),
    }

    # Calls send_to_staad directly from this agent
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
        staad_res = await send_to_staad(payload)
    except Exception as exc:
        return fail_workflow(state, "A4", f"STAAD execution failed: {exc}")
    state.staad_results = staad_res
    staad_res["candidate_signature"] = frame_signature(state)

    # Run the Frame Grader
    grader = grade_staad_results(staad_res, col_height)
    state.frame_grader_result = grader

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
            timestamp=ts
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
                affected_members = frame_correction_scope(staad_res, target)
            except ValueError as exc:
                return fail_workflow(state, "A4", f"{grader.summary} Correction stopped: {exc}")
            options = frame_options(old_prof, target, state.load_config.get("country", "EUROPE"))
            if not options:
                return fail_workflow(state, "A4", f"No larger approved STAAD catalogue section is available for {target} ({old_prof}).")
            selected = options[0]
            # Straightforward failures use the deterministic next section. GPT
            # may choose among verified larger sections for severe/repeated failures.
            if state.loop_a_count or grader.details.get("overall_max_ratio", 0) > 1.5:
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
                    timestamp=ts,
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
            timestamp=ts
        ))

    return state
