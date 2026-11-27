"""Per-connection IDEA request, solve, evaluation and correction graph."""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from orchestration.state import WorkflowState
from orchestration.progress import substage


class IdeaState(TypedDict):
    workflow: WorkflowState
    context: dict[str, Any]


@substage("A5a")
async def _build(state: IdeaState) -> IdeaState:
    from .A5A_build_json import prepare_a5
    prepare_a5(state["workflow"], state["context"])
    return state


@substage("A5b")
async def _api(state: IdeaState) -> IdeaState:
    from .A5B_trigger_api import execute_a5
    execute_a5(state["workflow"], state["context"])
    return state


@substage("A5c")
async def _evaluate(state: IdeaState) -> IdeaState:
    if state["context"]["complete"]:
        from agents.A5_idea_statica.package_validation import run_a6_package_validator
        workflow = run_a6_package_validator(state["workflow"], agent_id="A5c")
        if workflow.validation_report:
            from utils.audit import evaluation
            evaluation(workflow, "Package", workflow.validation_report)
        return state
    from .A5C_evaluate_and_audit import evaluate_a5
    evaluate_a5(state["workflow"], state["context"])
    return state


@substage("A5d")
async def _decide(state: IdeaState) -> IdeaState:
    if state["context"]["complete"]:
        workflow = state["workflow"]
        if workflow.status != "FAILED":
            audit = workflow.agent_statuses.get("A5c", {})
            if audit.get("status") == "REVISING":
                if (workflow.loop_c_count > workflow.max_loop_c
                        or workflow.validation_report.get("recovery_target") != "a5_ideastatica_analyzer"):
                    from orchestration.state import fail_workflow
                    fail_workflow(workflow, "A5", "Package recovery is invalid or exhausted; start a new workflow.")
                else:
                    from services.correction_advisor import choose_correction
                    choice = choose_correction(
                        workflow,
                        "package",
                        {
                            "audit": workflow.validation_report,
                            "loop_c_count": workflow.loop_c_count,
                            "max_loop_c": workflow.max_loop_c,
                        },
                        [{"id": "retry_idea"}, {"id": "stop"}],
                        "retry_idea",
                    )
                    if choice["id"] == "stop":
                        from orchestration.state import fail_workflow
                        fail_workflow(workflow, "A5", "Package recovery decision stopped IDEA re-run before connection updates.")
                    else:
                        workflow.agent_statuses["A5"] = {
                            "status": "REVISING",
                            "message": audit.get("message", "Package evidence requires connection recovery."),
                        }
            elif audit.get("status") != "PASSED" or not workflow.validation_report.get("passed"):
                from orchestration.state import fail_workflow
                fail_workflow(workflow, "A5", "Package audit did not return a verified passing result.")
        if workflow.status != "FAILED":
            workflow.current_agent = "A5"
        return state
    from .A5D_llm_reasoning import decide_a5
    decide_a5(state["workflow"], state["context"])
    return state


def _after_build(state: IdeaState):
    if state["workflow"].status == "FAILED":
        return END
    if state["context"]["complete"]:
        return "a5c_evaluate" if state["context"].get("audit_package") else END
    return "a5a_build" if state["context"]["skip"] else "a5b_api"


def _after_decision(state: IdeaState):
    if state["context"]["complete"]:
        return END
    if state["workflow"].status == "FAILED" or not state["context"]["grader"].passed:
        return END
    return "a5a_build"


def build_idea_graph():
    graph = StateGraph(IdeaState)
    for name, operation in (("a5a_build", _build), ("a5b_api", _api),
                            ("a5c_evaluate", _evaluate), ("a5d_decide", _decide)):
        graph.add_node(name, operation)
    graph.set_entry_point("a5a_build")
    graph.add_conditional_edges("a5a_build", _after_build,
                                {END: END, "a5a_build": "a5a_build", "a5b_api": "a5b_api",
                                 "a5c_evaluate": "a5c_evaluate"})
    for source, target in (("a5b_api", "a5c_evaluate"), ("a5c_evaluate", "a5d_decide")):
        graph.add_conditional_edges(
            source, lambda state, next_node=target: END if state["workflow"].status == "FAILED" else next_node,
            {END: END, target: target},
        )
    graph.add_conditional_edges("a5d_decide", _after_decision, {END: END, "a5a_build": "a5a_build"})
    return graph.compile()


async def run_idea_graph(state: WorkflowState, *, audit_package: bool = False) -> WorkflowState:
    from .A5A_build_json import begin_a5
    context = begin_a5(state)
    context["audit_package"] = audit_package
    if state.status == "FAILED":
        return state
    result = await build_idea_graph().ainvoke(
        {"workflow": state, "context": context},
        config={"recursion_limit": max(256, len(context["groups"]) * 5 + 10)},
    )
    return result["workflow"]
