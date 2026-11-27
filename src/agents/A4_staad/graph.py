"""STAAD request, execution, evaluation and correction subgraph."""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from orchestration.state import WorkflowState
from orchestration.progress import substage


class StaadState(TypedDict):
    workflow: WorkflowState


@substage("A4a")
async def _build(state: StaadState) -> StaadState:
    from .A4A_build_json import prepare_a4_staad
    return {"workflow": prepare_a4_staad(state["workflow"])}


@substage("A4b")
async def _api(state: StaadState) -> StaadState:
    from .A4B_trigger_api import execute_a4_staad
    return {"workflow": await execute_a4_staad(state["workflow"])}


@substage("A4c")
async def _evaluate(state: StaadState) -> StaadState:
    from .A4C_evaluate_results import evaluate_a4_staad
    return {"workflow": evaluate_a4_staad(state["workflow"])}


@substage("A4d")
async def _decide(state: StaadState) -> StaadState:
    from .A4D_llm_reasoning import decide_a4_staad
    return {"workflow": decide_a4_staad(state["workflow"])}


def build_staad_graph():
    graph = StateGraph(StaadState)
    for name, operation in (("a4a_build", _build), ("a4b_api", _api),
                            ("a4c_evaluate", _evaluate), ("a4d_decide", _decide)):
        graph.add_node(name, operation)
    graph.set_entry_point("a4a_build")
    for source, target in (("a4a_build", "a4b_api"), ("a4b_api", "a4c_evaluate"),
                           ("a4c_evaluate", "a4d_decide")):
        graph.add_conditional_edges(
            source, lambda state, next_node=target: END if state["workflow"].status == "FAILED" else next_node,
            {END: END, target: target},
        )
    graph.add_edge("a4d_decide", END)
    return graph.compile()


async def run_staad_graph(state: WorkflowState) -> WorkflowState:
    result = await build_staad_graph().ainvoke({"workflow": state})
    return result["workflow"]
