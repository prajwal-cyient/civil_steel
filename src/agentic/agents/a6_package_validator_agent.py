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


def run_a6_package_validator(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A6: Audits all calculation evidence and prepares the verified package.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A6"

    failures = []

    # 1. Check frame candidate
    if not state.frame_candidate.get("members"):
        failures.append("No members found in Frame Candidate.")

    # 2. Check STAAD compliance
    if not state.frame_grader_result or not state.frame_grader_result.passed:
        failures.append("STAAD structural analysis did not receive a PASS from Frame Grader.")

    # 3. Check Connection design coverage
    joint_groups = state.staad_results.get("joint_groups", {})
    for jg_id in joint_groups.keys():
        grader = state.joint_grader_results.get(jg_id)
        if not grader or not grader.passed:
            failures.append(f"Connection for joint group '{jg_id}' did not receive a PASS.")

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
    for jg_id, jg_data in joint_groups.items():
        op_params = state.active_operation_params.get(jg_id, [])
        validated_joint_groups[jg_id] = {
            "joints": jg_data.get("joints", []),
            "operation_parameters": op_params
        }

    # Compute package integrity hash
    pkg_str = json.dumps({
        "grid": state.grid_config,
        "col_profile": state.active_column_profile,
        "beam_profile": state.active_beam_profile,
        "joint_groups": validated_joint_groups
    }, sort_keys=True)
    pkg_hash = hashlib.sha256(pkg_str.encode("utf-8")).hexdigest()[:12]

    state.validation_report = {
        "passed": True,
        "package_hash": pkg_hash,
        "joint_groups_verified": len(validated_joint_groups),
        "total_members": len(state.frame_candidate.get("members", [])),
        "validated_joint_groups_payload": validated_joint_groups
    }

    msg = f"Package validated successfully (Hash: {pkg_hash}). All stress, drift, and connection checks verified."
    state.agent_statuses["A6"] = {"status": "PASSED", "message": msg, "hash": pkg_hash}
    state.logs.append(AgentLogEntry(
        agent_id="A6",
        agent_name="Package Validator",
        status="PASSED",
        message=msg,
        timestamp=ts
    ))

    return state

