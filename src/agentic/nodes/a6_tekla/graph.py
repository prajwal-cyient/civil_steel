"""Tekla request, publication, read-back evaluation and decision subgraph."""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from agentic.state import WorkflowState, workflow_stage
from agentic.nodes.progress import substage


class TeklaState(TypedDict):
    workflow: WorkflowState


@substage("A6a")
async def _build(state: TeklaState) -> TeklaState:
    from agentic.agents.a7_tekla_modeler_agent import prepare_a7_tekla
    return {"workflow": prepare_a7_tekla(state["workflow"])}


@substage("A6b")
async def _api(state: TeklaState) -> TeklaState:
    from agentic.agents.a7_tekla_modeler_agent import execute_a7_tekla
    return {"workflow": execute_a7_tekla(state["workflow"])}


@substage("A6c")
async def _evaluate(state: TeklaState) -> TeklaState:
    from agentic.agents.a7_tekla_modeler_agent import evaluate_a7_tekla
    return {"workflow": evaluate_a7_tekla(state["workflow"])}


@substage("A6d")
async def _decide(state: TeklaState) -> TeklaState:
    from agentic.agents.a7_tekla_modeler_agent import decide_a7_tekla
    return {"workflow": decide_a7_tekla(state["workflow"])}


def build_tekla_graph():
    graph = StateGraph(TeklaState)
    for name, operation in (("a6a_build", _build), ("a6b_api", _api),
                            ("a6c_evaluate", _evaluate), ("a6d_decide", _decide)):
        graph.add_node(name, operation)
    graph.set_entry_point("a6a_build")
    for source, target in (("a6a_build", "a6b_api"), ("a6b_api", "a6c_evaluate"),
                           ("a6c_evaluate", "a6d_decide")):
        graph.add_conditional_edges(
            source, lambda state, next_node=target: END if state["workflow"].status == "FAILED" else next_node,
            {END: END, target: target},
        )
    graph.add_conditional_edges(
        "a6d_decide",
        lambda state: "a6a_build" if (
            state["workflow"].status != "FAILED"
            and state["workflow"].agent_statuses.get(workflow_stage(state["workflow"], "tekla"), {}).get("status") == "REVISING"
        ) else END,
        {END: END, "a6a_build": "a6a_build"},
    )
    return graph.compile()


async def run_tekla_graph(state: WorkflowState) -> WorkflowState:
    result = await build_tekla_graph().ainvoke(
        {"workflow": state}, config={"recursion_limit": 4 * (state.max_tekla_retries + 1) + 10}
    )
    return result["workflow"]
