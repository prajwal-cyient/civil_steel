"""Frame evaluation and generation subgraph."""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from agentic.state import WorkflowState
from agentic.nodes.progress import substage


class FrameState(TypedDict):
    workflow: WorkflowState


@substage("A3a")
async def _evaluate(state: FrameState) -> FrameState:
    from agentic.agents.a3_frame_generator_agent import evaluate_a3_frame
    return {"workflow": evaluate_a3_frame(state["workflow"])}


@substage("A3b")
async def _generate(state: FrameState) -> FrameState:
    from agentic.agents.a3_frame_generator_agent import generate_a3_frame
    return {"workflow": generate_a3_frame(state["workflow"])}


def build_frame_graph():
    graph = StateGraph(FrameState)
    graph.add_node("a3a_evaluate", _evaluate)
    graph.add_node("a3b_generate", _generate)
    graph.set_entry_point("a3a_evaluate")
    graph.add_conditional_edges(
        "a3a_evaluate",
        lambda state: END if state["workflow"].status == "FAILED" else "a3b_generate",
        {END: END, "a3b_generate": "a3b_generate"},
    )
    graph.add_edge("a3b_generate", END)
    return graph.compile()


async def run_frame_graph(state: WorkflowState) -> WorkflowState:
    result = await build_frame_graph().ainvoke({"workflow": state})
    return result["workflow"]
