"""Retry a failed Tekla handoff only after independently revalidating its evidence."""
from datetime import datetime

from agentic.state import AgentLogEntry, fail_workflow


def prepare_publication_retry(source, run_id):
    if source.status != "FAILED" or source.current_agent != "A7" or not source.validation_report.get("passed"):
        raise ValueError("Publication retry requires a failed A7 run with a previously validated calculation package.")
    state = source.model_copy(deep=True)
    state.run_id, state.status, state.error = run_id, "RUNNING", None
    state.readiness["publication_resume_source"] = source.run_id
    state.current_agent = "A6"
    state.tekla_results.clear()
    state.final_report.clear()
    state.gate3_approved = False
    state.accepted_by = state.acceptance_timestamp = None
    for agent in ("A6", "A7", "A8"):
        state.agent_statuses.pop(agent, None)
    state.logs.append(AgentLogEntry(agent_id="A6", agent_name="Publication Recovery", status="RUNNING",
        message=f"Revalidating unchanged calculation evidence from {source.run_id} before retrying Tekla publication.",
        timestamp=datetime.now().strftime("%H:%M:%S")))
    return state


async def run_publication_retry(state):
    from agentic.orchestrator import node_a6, node_a7, node_a8
    if not state.readiness.get("publication_resume_source"):
        return fail_workflow(state, "A6", "No publication recovery source was recorded.")
    state = node_a6(state)
    if not state.validation_report.get("passed") or state.agent_statuses.get("A6", {}).get("status") != "PASSED":
        return fail_workflow(state, "A6", "Calculation evidence changed or failed revalidation; run the complete workflow again.")
    state = node_a7(state)
    if state.agent_statuses.get("A7", {}).get("status") == "PASSED":
        state = node_a8(state)
    return state
