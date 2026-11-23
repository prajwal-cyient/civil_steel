"""
a8_reporting_acceptance_agent.py

Agent A8: Report and Accept (Calculation Synthesizer & Human Gate 3).
Consolidates the complete design and analysis audit trail.
Pauses at Gate 3 for the responsible human engineer to review and sign off.
"""

from datetime import datetime
from typing import Optional
from agentic.state import WorkflowState, AgentLogEntry


def run_a8_reporting(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A8: Prepares calculation package and pauses for Final Engineer Acceptance.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A8"

    # Assemble calculation package summary
    col_prof = state.active_column_profile
    beam_prof = state.active_beam_profile
    staad_ratio = state.frame_grader_result.details.get("overall_max_ratio", 0.0) if state.frame_grader_result else 0.0
    pkg_hash = state.validation_report.get("package_hash", "N/A")
    tekla_created = state.tekla_results.get("created", 0)

    report = {
        "run_id": state.run_id,
        "run_policy": state.run_policy,
        "approved_changes": {k: state.change_scope[k] for k in ("proposal_id", "source_run_id", "groups", "changes", "action", "reuse_calculations") if k in state.change_scope},
        "calculation_source_run": state.readiness.get("publication_resume_source", state.run_id),
        "completed_at": datetime.now().isoformat(),
        "summary": {
            "structure": f"Radiator Support Frame ({state.grid_config.get('n_long_bays')}x{state.grid_config.get('n_trans_bays')} bays)",
            "final_column_profile": col_prof,
            "final_beam_profile": beam_prof,
            "loop_a_iterations": state.loop_a_count,
            "loop_b_iterations": sum(state.loop_b_count.values()),
            "loop_c_iterations": state.loop_c_count,
            "design_generation": state.design_generation,
            "staad_max_stress_ratio": staad_ratio,
            "tekla_members_modeled": tekla_created,
            "package_integrity_hash": pkg_hash
        },
        "loop_a_history": state.loop_a_history,
        "loop_b_history": state.loop_b_history,
        "loop_c_history": state.loop_c_history,
        "connection_summaries": {
            jg: {
                "passed": grader.passed,
                "plate_utilization_percent": grader.details.get("max_plate_utilization"),
                "local_deformation_utilization_percent": grader.details.get("max_deformation_utilization"),
                "bolt_utilization": grader.details.get("max_bolt")
            }
            for jg, grader in state.joint_grader_results.items()
        },
        "connection_scope": state.validation_report.get("connection_scope", {}),
        "standard_detail_notice": "Standard Tekla details reproduce the senior procedure; they are not separate IDEA calculation passes.",
        "gate3_status": "PENDING_ENGINEER_ACCEPTANCE"
    }

    state.final_report = report
    state.status = "PAUSED_AT_GATE3"  # Gate 3 Pause

    msg = f"Calculation package ready for review (STAAD Max Ratio: {staad_ratio:.2f}, Hash: {pkg_hash}). Waiting for engineer sign-off."
    state.agent_statuses["A8"] = {"status": "AWAITING_APPROVAL", "message": msg}
    state.logs.append(AgentLogEntry(
        agent_id="A8",
        agent_name="Reporting & Human Gate",
        status="AWAITING_APPROVAL",
        message=msg,
        timestamp=ts
    ))

    return state


def accept_gate3(state: WorkflowState, engineer_name: str = "Lead Structural Engineer") -> WorkflowState:
    """
    Called when the human engineer clicks 'Accept & Sign Off' on the UI.
    """
    if state.status != "PAUSED_AT_GATE3" or not state.validation_report.get("passed") or state.agent_statuses.get("A7", {}).get("status") != "PASSED":
        raise ValueError("Only a verified package awaiting Gate 3 review can be accepted.")
    ts = datetime.now().strftime("%H:%M:%S")
    state.gate3_approved = True
    state.accepted_by = engineer_name
    state.acceptance_timestamp = datetime.now().isoformat()
    state.status = "COMPLETED"
    if state.final_report:
        state.final_report.update(gate3_status='ACCEPTED', accepted_by=state.accepted_by,
                                  acceptance_timestamp=state.acceptance_timestamp)

    msg = f"Design accepted and signed off by {engineer_name}."
    state.agent_statuses["A8"] = {"status": "PASSED", "message": msg}
    state.logs.append(AgentLogEntry(
        agent_id="A8",
        agent_name="Reporting & Human Gate",
        status="PASSED",
        message=msg,
        timestamp=ts
    ))

    return state

