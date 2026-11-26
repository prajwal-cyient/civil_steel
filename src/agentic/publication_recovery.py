"""Retry a failed Tekla handoff only after independently revalidating its evidence."""
from datetime import datetime

from agentic.state import AgentLogEntry, fail_workflow, workflow_stage


def prepare_publication_retry(source, run_id):
    if source.status != "FAILED" or source.current_agent != workflow_stage(source, "tekla") or not source.validation_report.get("passed"):
        raise ValueError("Publication retry requires a failed Tekla stage with a previously validated calculation package.")
    state = source.model_copy(deep=True)
    state.run_id, state.status, state.error = run_id, "RUNNING", None
    from agentic.audit import event
    event(state, 'correction', 'Tekla', status='AWAITING_RECHECK',
          operation='Publication retry requested', source_run_id=source.run_id,
          action={'reason':source.error, 'previous_errors':source.tekla_results.get('errors', []),
                  'revalidate_before_publication':True,
                  'notice':'Retry only; no engineering parameters automatically changed.'})
    state.readiness["publication_resume_source"] = source.run_id
    state.current_agent = workflow_stage(state, "package")
    state.tekla_results.clear()
    state.tekla_retry_count = 0
    state.tekla_retry_history = []
    state.tekla_prepublication_error = None
    state.tekla_grader_result = None
    state.final_report.clear()
    state.gate3_approved = False
    state.accepted_by = state.acceptance_timestamp = None
    for agent in (workflow_stage(state, "package"), workflow_stage(state, "tekla"), workflow_stage(state, "report")):
        state.agent_statuses.pop(agent, None)
    state.logs.append(AgentLogEntry(agent_id=workflow_stage(state, "package"), agent_name="Publication Recovery", status="RUNNING",
        message=f"Revalidating unchanged calculation evidence from {source.run_id} before retrying Tekla publication.",
        timestamp=datetime.now().astimezone().isoformat()))
    return state


async def run_publication_retry(state):
    from agentic.orchestrator import node_a6, node_a7, node_a8
    if not state.readiness.get("publication_resume_source"):
        return fail_workflow(state, workflow_stage(state, "package"), "No publication recovery source was recorded.")
    state = await node_a6(state)
    if not state.validation_report.get("passed") or state.agent_statuses.get(workflow_stage(state, "package"), {}).get("status") != "PASSED":
        return fail_workflow(state, workflow_stage(state, "package"), "Calculation evidence changed or failed revalidation; run the complete workflow again.")
    state = await node_a7(state)
    if state.agent_statuses.get(workflow_stage(state, "tekla"), {}).get("status") == "PASSED":
        state = await node_a8(state)
    return state
