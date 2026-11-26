"""Evaluate STAAD analysis evidence using the existing frame grader."""

from math import isfinite
from typing import Any, Dict

from agentic.state import GraderResult
from agentic.run_policy import RunPolicy


def grade_staad_results(staad_res: Dict[str, Any], col_height_mm: float, run_policy=None) -> GraderResult:
    """
    Evaluates STAAD analysis output against structural boundary conditions:
    1. Member Steel Design Unity Ratio <= 1.0 (EC3 / AISC 360).
    2. Lateral drift <= H_col / 200.
    """
    policy = RunPolicy.model_validate(run_policy or {})
    ratio_limit, denominator = policy.max_member_ratio, policy.drift_denominator
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
                      for key, row in member_ratios.items() if row["ratio"] > ratio_limit]

    # Check drift limit
    drift_exceeded = False
    env_summaries = staad_res.get("envelope_summary_results", {})
    for env_id, env_data in env_summaries.items():
        if service_ids and str(env_id) not in service_ids:
            continue
        displacements = env_data.get("node_displacements_limits", {})
        if any(displacements[key] > col_height_mm / denominator for key in drift_fields):
            drift_exceeded = True
            break

    failures = []
    if overall_max_ratio > ratio_limit:
        failures.append(f"Max member stress ratio {overall_max_ratio:.2f} > {ratio_limit:g} (Column={max_col_ratio:.2f}, Beam={max_beam_ratio:.2f})")
    if drift_exceeded:
        failures.append(f"Lateral drift exceeded allowable H/{denominator:g} ({col_height_mm/denominator:.1f} mm)")

    if not failures:
        return GraderResult(
            passed=True,
            score=1.0,
            summary=f"All structural checks passed (Max Ratio: {overall_max_ratio:.2f} <= {ratio_limit:g}, Drift <= H/{denominator:g}).",
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
        score=max(0.0, 1.0 - (overall_max_ratio - ratio_limit)),
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


