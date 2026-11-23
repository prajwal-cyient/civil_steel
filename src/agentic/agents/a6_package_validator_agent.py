"""
a6_package_validator_agent.py

Agent A6: Validate the Package (Data Integrity Auditor).
Cross-checks evidence from A3, A4, and A5.
Verifies that 100% of joints passed and data is intact before Tekla publication.
"""

from datetime import datetime
import hashlib
import json
from agentic.state import WorkflowState, AgentLogEntry
from agentic.agents.a4_staad_analyzer_agent import grade_staad_results
from agentic.agents.a5_ideastatica_analyzer_agent import grade_joint_check_summary
from agentic.agents import a5_ideastatica_analyzer_agent as idea_agent
from agentic.evidence import frame_signature
from agentic.connection_materials import reference_plate_material_policy, verify_plate_material_evidence


def run_a6_package_validator(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A6: Audits all calculation evidence and prepares the verified package.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A6"

    from agentic.agents.a7_tekla_modeler_agent import unmapped_connection_corrections
    unsupported = unmapped_connection_corrections(state)
    if unsupported:
        from agentic.state import fail_workflow
        return fail_workflow(state, "A6", "Connection corrections lack a validated Tekla attribute mapping; "
                             f"publication stopped before changing the physical model: {unsupported}")

    failures = []
    if state.staad_results.get("candidate_signature") != frame_signature(state):
        failures.append("STAAD evidence does not belong to the current frame and loads.")

    # Recheck source evidence instead of trusting previously cached PASS flags.
    raw_frame_grade = grade_staad_results(state.staad_results, state.grid_config.get("col_height_mm", 5000), state.run_policy)
    if not raw_frame_grade.passed:
        failures.append(f"STAAD source evidence is invalid or failed: {raw_frame_grade.summary}")

    # 1. Check frame candidate
    if not state.frame_candidate.get("members"):
        failures.append("No members found in Frame Candidate.")

    # 2. Check STAAD compliance
    if not state.frame_grader_result or not state.frame_grader_result.passed:
        failures.append("STAAD structural analysis did not receive a PASS from Frame Grader.")

    # 3. Check Connection design coverage
    joint_groups = state.staad_results.get("joint_groups", {})
    if not joint_groups:
        failures.append("No connection groups returned by STAAD.")
    from agentic.reference_procedure import scope_for_state
    try:
        scope = scope_for_state(state)
    except ValueError as exc:
        from agentic.state import fail_workflow
        return fail_workflow(state, "A6", str(exc))
    plate_policy = reference_plate_material_policy() if scope["mode"] == "reference_procedure" else None
    for jg_id in scope["idea_required_groups"]:
        grader = state.joint_grader_results.get(jg_id)
        if not grader or not grader.passed:
            failures.append(f"Connection for joint group '{jg_id}' did not receive a PASS.")
        result = state.ideastatica_results.get(jg_id, {})
        try:
            idea_agent.validate_calculation_check_coverage(result.get("check_summary", {}).get("summary", []))
        except idea_agent.IdeaStaticaJointError as exc:
            failures.append(f"Connection check coverage for '{jg_id}' is incomplete: {exc}")
        if result.get("passed") is not True or result.get("simulation") or not grade_joint_check_summary(result.get("check_summary", {}), state.run_policy).passed:
            failures.append(f"Connection source evidence for '{jg_id}' is missing, simulated, or failed.")
        try:
            verify_plate_material_evidence(result.get("plate_material_evidence"), plate_policy,
                result.get("iom_connection_data_file"))
            signature = idea_agent._connection_signature(idea_agent._connection_payload(state, jg_id), state.design_generation)
            if result.get("input_signature") != signature:
                failures.append(f"Connection evidence for '{jg_id}' belongs to different inputs or template.")
        except Exception as exc:
            failures.append(f"Connection inputs for '{jg_id}' cannot be verified: {exc}")

    if failures:
        # Loop C: Validation Recovery
        if state.loop_c_count < state.max_loop_c:
            state.loop_c_count += 1
            # Determine recovery target based on primary failure
            target = "a5_ideastatica_analyzer" if any("connection" in f.lower() for f in failures) else "a3_frame_generator"
            state.validation_report = {
                "passed": False,
                "recovery_target": target,
                "failures": failures,
                "iteration": state.loop_c_count,
            }
            state.loop_c_history.append({
                "iteration": state.loop_c_count,
                "target": target,
                "failures": failures,
            })
            msg = f"Loop C ({state.loop_c_count}/{state.max_loop_c}): Missing/failed package evidence -> Routing recovery to {target}"
            state.agent_statuses["A6"] = {"status": "REVISING", "message": msg, "target": target}
            state.logs.append(AgentLogEntry(
                agent_id="A6",
                agent_name="Package Validator",
                status="REVISING",
                message=msg,
                timestamp=ts,
                loop_info=f"Loop C #{state.loop_c_count}",
            ))
            return state

        err_msg = "Package Validation Failed: " + "; ".join(failures)
        state.status = "FAILED"
        state.error = err_msg
        state.agent_statuses["A6"] = {"status": "FAILED", "message": err_msg}
        state.logs.append(AgentLogEntry(
            agent_id="A6",
            agent_name="Package Validator",
            status="FAILED",
            message=err_msg,
            timestamp=ts
        ))
        return state

    # Build validated joint_groups payload for Tekla
    validated_joint_groups = {}
    for jg_id in scope["idea_required_groups"]:
        jg_data = joint_groups[jg_id]
        op_params = state.ideastatica_results[jg_id]["check_summary"].get("operation_parameters", [])
        # Tekla needs evaluated values, not IDEA formulas such as GetBoltDiameter(...).
        op_params = [{**p, "expression": str(p.get("value") if p.get("value") is not None else p.get("expression", "")).strip("\"'")} for p in op_params]
        validated_joint_groups[jg_id] = {
            "joints": jg_data.get("joints", []),
            "operation_parameters": op_params
        }

    # Compute package integrity hash
    pkg_str = json.dumps({
        "run_policy": state.run_policy,
        "grid": state.grid_config,
        "col_profile": state.active_column_profile,
        "beam_profile": state.active_beam_profile,
        "frame_signature": frame_signature(state),
        "connection_scope": scope,
        "plate_material_policy": plate_policy,
        "joint_groups": validated_joint_groups
    }, sort_keys=True)
    pkg_hash = hashlib.sha256(pkg_str.encode("utf-8")).hexdigest()[:12]
    state.package_hash = pkg_hash

    state.validation_report = {
        "passed": True,
        "run_policy": state.run_policy,
        "package_hash": pkg_hash,
        "joint_groups_verified": len(validated_joint_groups),
        "connection_scope": scope,
        "plate_material_policy": plate_policy,
        "total_members": len(state.frame_candidate.get("members", [])),
        "validated_joint_groups_payload": validated_joint_groups
    }

    msg = (f"Package validated (Hash: {pkg_hash}): frame checks and {len(validated_joint_groups)} IDEA families verified; "
           f"{len(scope['standard_detail_groups'])} families use declared standard Tekla details.")
    state.agent_statuses["A6"] = {"status": "PASSED", "message": msg, "hash": pkg_hash}
    state.logs.append(AgentLogEntry(
        agent_id="A6",
        agent_name="Package Validator",
        status="PASSED",
        message=msg,
        timestamp=ts
    ))

    return state

