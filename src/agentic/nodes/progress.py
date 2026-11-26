"""Persist actual LangGraph substage progress without adding stage attempts."""

from functools import wraps


def substage(label):
    def decorate(operation):
        @wraps(operation)
        async def run(graph_state):
            workflow = graph_state["workflow"]
            if workflow.layout_version != 2:
                return await operation(graph_state)
            from agentic.orchestrator import _sync_state
            workflow.substage_statuses[label] = {"status": "RUNNING"}
            _sync_state(workflow)
            try:
                result = await operation(graph_state)
            except BaseException:
                workflow.substage_statuses[label] = {"status": "FAILED"}
                _sync_state(workflow)
                raise
            workflow = result["workflow"]
            status = "FAILED" if workflow.status == "FAILED" else "PASSED"
            if status == "PASSED" and label == "A4c" and workflow.frame_grader_result and not workflow.frame_grader_result.passed:
                status = "REVISING"
            if status == "PASSED" and label == "A5c":
                context = result["context"]
                if context["complete"]:
                    status = workflow.agent_statuses.get("A5c", {}).get("status", "FAILED")
                elif not context["grader"].passed:
                    status = "REVISING"
            if status == "PASSED" and label == "A6c" and workflow.tekla_publication_passed is False:
                recoverable = bool(
                    workflow.tekla_grader_result.corrective_action_needed
                    if workflow.tekla_grader_result is not None
                    else workflow.tekla_prepublication_error is not None
                )
                status = "REVISING" if recoverable and workflow.tekla_retry_count < workflow.max_tekla_retries else "FAILED"
            if status == "PASSED" and label == "A6b" and workflow.tekla_prepublication_error is not None:
                status = "REVISING"
            if status == "PASSED" and label.endswith("d"):
                stage = label[:2]
                if workflow.agent_statuses.get(stage, {}).get("status") == "REVISING":
                    status = "REVISING"
            workflow.substage_statuses[label] = {"status": status}
            _sync_state(workflow)
            return result
        return run
    return decorate
