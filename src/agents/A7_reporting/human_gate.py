"""Gate 3 human engineer acceptance handler."""

from datetime import datetime
from orchestration.state import WorkflowState, AgentLogEntry, workflow_stage


def accept_gate3(state: WorkflowState, engineer_name: str = "Lead Structural Engineer") -> WorkflowState:
    """
    Called when the human engineer clicks 'Accept & Sign Off' on the UI.
    """
    if state.status != "PAUSED_AT_GATE3" or not state.validation_report.get("passed") or state.agent_statuses.get(workflow_stage(state, "tekla"), {}).get("status") != "PASSED":
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
    state.agent_statuses[workflow_stage(state, "report")] = {"status": "PASSED", "message": msg}
    state.logs.append(AgentLogEntry(
        agent_id=workflow_stage(state, "report"),
        agent_name="Reporting & Human Gate",
        status="PASSED",
        message=msg,
        timestamp=datetime.now().astimezone().isoformat()
    ))

    return state

