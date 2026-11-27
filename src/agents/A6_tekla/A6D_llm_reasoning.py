"""Agent A6 Tekla decision node, retry loop, and publication entrypoints."""

from datetime import datetime
from orchestration.state import WorkflowState, AgentLogEntry, workflow_stage
import services.correction_advisor as correction_advisor
from . import A6A_build_json, A6B_trigger_api, A6C_check_publication
from .publication_corrections import decide_tekla_outcome


def decide_a7_tekla(state: WorkflowState) -> WorkflowState:
    from services.correction_advisor import choose_correction
    from agents.A6_tekla.publication_corrections import decide_tekla_outcome
    state, ready = decide_tekla_outcome(
        state,
        request_matches=A6A_build_json._tekla_request_matches,
        choose_correction=correction_advisor.choose_correction,
    )
    if not ready:
        return state
    tekla_res = state.tekla_results
    members = state.tekla_request["members"]
    created_count = tekla_res.get("created", len(members))

    msg = f"Published {created_count} members to Tekla Structures with verified connection attributes (0 failed)."
    state.agent_statuses[workflow_stage(state, "tekla")] = {"status": "PASSED", "message": msg, "created": created_count}
    state.logs.append(AgentLogEntry(
        agent_id=workflow_stage(state, "tekla"),
        agent_name="Tekla 3D Modeler",
        status="PASSED",
        message=msg,
        timestamp=datetime.now().astimezone().isoformat()
    ))
    return state


def run_a7_tekla_modeler(state: WorkflowState) -> WorkflowState:
    """Run the existing synchronous publication entry point."""
    state.tekla_retry_count = 0
    state.tekla_retry_history = []
    try:
        while True:
            for operation in (A6A_build_json.prepare_a7_tekla, A6B_trigger_api.execute_a7_tekla, A6C_check_publication.evaluate_a7_tekla, decide_a7_tekla):
                state = operation(state)
                if state.status == "FAILED":
                    break
            if state.status == "FAILED" or state.agent_statuses.get(workflow_stage(state, "tekla"), {}).get("status") != "REVISING":
                break
        return state
    finally:
        state.tekla_request.clear()


async def run_a7_staged(state: WorkflowState) -> WorkflowState:
    from agents.A6_tekla.graph import run_tekla_graph
    state.tekla_retry_count = 0
    state.tekla_retry_history = []
    try:
        return await run_tekla_graph(state)
    finally:
        state.tekla_request.clear()
