"""
orchestrator.py

The central LangGraph StateGraph that connects all 8 agents.
Implements:
- Loop A (Frame Sizing: A4 -> A3)
- Loop B (Joint Detailing: A5 -> A5)
- Automated Gate 1 and Gate 2
- Gate 3 (Final Engineer Acceptance pause)
"""

from typing import Any, Dict, Literal, Optional
import inspect
import uuid
from langgraph.graph import StateGraph, END
from agentic.state import WorkflowState, fail_workflow
from agentic.config import settings, deep_agents_enabled
from agentic.agent_timing import start_timing, finish_timing
from agentic.run_archive import guarded, start_stage, finish_stage
from agentic.agents.a1_requirements_agent import run_a1_requirements
from agentic.agents.a2_readiness_agent import run_a2_readiness
from agentic.agents.a3_frame_generator_agent import run_a3_frame_generator
from agentic.agents.a4_staad_analyzer_agent import run_a4_staad_analyzer
from agentic.agents.a5_ideastatica_analyzer_agent import run_a5_ideastatica_analyzer
from agentic.agents.a6_package_validator_agent import run_a6_package_validator
from agentic.agents.a7_tekla_modeler_agent import run_a7_tekla_modeler
from agentic.agents.a8_reporting_acceptance_agent import run_a8_reporting


# ─── LangGraph Node Wrappers ───────────────────────────────────────────────────

def _sync_state(state: WorkflowState):
    try:
        from agentic.store import WORKFLOW_STORE
        WORKFLOW_STORE.save_state(state)
    except Exception:
        pass
    try:
        from agentic.router import RUN_STORE
        RUN_STORE[state.run_id] = state
    except Exception:
        pass


def _start_agent(state: WorkflowState, agent_id: str):
    state.current_agent = agent_id
    state.agent_statuses[agent_id] = {"status": "RUNNING", "message": "Calculating current candidate…"}
    _sync_state(state)


async def _run_stage(state, agent_id, operation):
    started = start_timing(state, agent_id)
    _start_agent(state, agent_id)
    guarded(state, start_stage, agent_id)
    result = state
    archive_error = None
    try:
        if deep_agents_enabled():
            from agentic.deep_runtime import run_deep_stage
            result = await run_deep_stage(state, agent_id, operation, _sync_state)
        else:
            candidate = operation(state)
            if inspect.isawaitable(candidate):
                candidate = await candidate
            result = candidate
        return result
    except BaseException as exc:
        archive_error = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        finish_timing(result, agent_id, started)
        _sync_state(result)
        guarded(result, finish_stage, agent_id, archive_error)


async def node_a1(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A1", run_a1_requirements)


async def node_a2(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A2", run_a2_readiness)


async def node_a3(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A3", run_a3_frame_generator)


async def node_a4(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A4", run_a4_staad_analyzer)


async def node_a5(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A5", run_a5_ideastatica_analyzer)


async def node_a6(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A6", run_a6_package_validator)


async def node_a7(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A7", run_a7_tekla_modeler)


async def node_a8(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A8", run_a8_reporting)


# ─── Conditional Routing Edges (Loop A & Loop B) ───────────────────────────────

def route_after_a4(state: WorkflowState) -> Literal["a3_frame_generator", "a5_ideastatica_analyzer", "__end__"]:
    """
    Evaluates Frame Grader results.
    If boundary conditions failed and retries remain -> routes back to A3 (Loop A).
    If passed -> advances to A5.
    If failed with max retries -> ends with error.
    """
    if state.status == "FAILED":
        return "__end__"

    status_a4 = state.agent_statuses.get("A4", {}).get("status", "")
    if status_a4 == "REVISING" and state.loop_a_count <= state.max_loop_a:
        return "a3_frame_generator"  # Loop A trigger!

    if status_a4 == "PASSED":
        return "a5_ideastatica_analyzer"

    return "__end__"


def route_after_a5(state: WorkflowState) -> Literal["a5_ideastatica_analyzer", "a6_package_validator", "__end__"]:
    """
    Evaluates Joint Grader results.
    If connection checks failed and retries remain -> loops back to A5 (Loop B).
    If passed -> advances to A6.
    If failed with max retries -> ends with error.
    """
    if state.status == "FAILED":
        return "__end__"

    status_a5 = state.agent_statuses.get("A5", {}).get("status", "")
    if status_a5 == "REVISING":
        return "a5_ideastatica_analyzer"  # Loop B trigger!

    if status_a5 == "PASSED":
        return "a6_package_validator"

    return "__end__"


def route_after_a6(state: WorkflowState) -> Literal["a3_frame_generator", "a5_ideastatica_analyzer", "a7_tekla_modeler", "__end__"]:
    if state.status == "FAILED":
        return "__end__"
    status_a6 = state.agent_statuses.get("A6", {}).get("status", "")
    if status_a6 == "REVISING" and state.loop_c_count <= state.max_loop_c:
        target = state.validation_report.get("recovery_target", "a5_ideastatica_analyzer")
        return target
    if status_a6 == "PASSED":
        return "a7_tekla_modeler"
    return "__end__"


# ─── Graph Construction ────────────────────────────────────────────────────────

def build_workflow_graph() -> StateGraph:
    """
    Constructs the LangGraph cyclical state machine.
    """
    graph = StateGraph(WorkflowState)

    # Register all 8 agent nodes
    graph.add_node("a1_requirements", node_a1)
    graph.add_node("a2_readiness", node_a2)
    graph.add_node("a3_frame_generator", node_a3)
    graph.add_node("a4_staad_analyzer", node_a4)
    graph.add_node("a5_ideastatica_analyzer", node_a5)
    graph.add_node("a6_package_validator", node_a6)
    graph.add_node("a7_tekla_modeler", node_a7)
    graph.add_node("a8_reporting", node_a8)

    # Standard transitions
    graph.set_entry_point("a1_requirements")
    for source, destination in (("a1_requirements", "a2_readiness"),
                                ("a2_readiness", "a3_frame_generator"),
                                ("a3_frame_generator", "a4_staad_analyzer")):
        graph.add_conditional_edges(source,
            lambda state, target=destination: "__end__" if state.status == "FAILED" else target,
            {"__end__": END, destination: destination})

    # Conditional Branch: Loop A (A4 -> A3 or A4 -> A5)
    graph.add_conditional_edges(
        "a4_staad_analyzer",
        route_after_a4,
        {
            "a3_frame_generator": "a3_frame_generator",
            "a5_ideastatica_analyzer": "a5_ideastatica_analyzer",
            "__end__": END
        }
    )

    # Conditional Branch: Loop B (A5 -> A5 or A5 -> A6)
    graph.add_conditional_edges(
        "a5_ideastatica_analyzer",
        route_after_a5,
        {
            "a5_ideastatica_analyzer": "a5_ideastatica_analyzer",
            "a6_package_validator": "a6_package_validator",
            "__end__": END
        }
    )

    # Conditional Branch: Package Verification & Loop C Recovery (A6 -> A7 / A5 / A3)
    graph.add_conditional_edges(
        "a6_package_validator",
        route_after_a6,
        {
            "a3_frame_generator": "a3_frame_generator",
            "a5_ideastatica_analyzer": "a5_ideastatica_analyzer",
            "a7_tekla_modeler": "a7_tekla_modeler",
            "__end__": END
        }
    )

    graph.add_conditional_edges("a7_tekla_modeler",
        lambda state: "__end__" if state.status == "FAILED" else "a8_reporting",
        {"__end__": END, "a8_reporting": "a8_reporting"})
    graph.add_edge("a8_reporting", END)

    return graph


# ─── Runner Execution Function ─────────────────────────────────────────────────

async def run_agentic_workflow(raw_input: Dict[str, Any], run_id: Optional[str] = None) -> WorkflowState:
    """
    Runs the complete 8-agent workflow end-to-end.
    """
    if not run_id:
        run_id = f"run_{uuid.uuid4().hex[:8]}"

    initial_state = WorkflowState(
        run_id=run_id,
        status="RUNNING",
        raw_input=raw_input,
        max_loop_a=settings().frame_revisions,
        max_loop_b=settings().connection_revisions,
    )
    _sync_state(initial_state)

    graph = build_workflow_graph()
    app = graph.compile()

    # Asynchronously invoke graph
    try:
        # Each connection group may need its own bounded corrections.
        final_output = await app.ainvoke(initial_state, config={"recursion_limit": 256})
    except Exception as exc:
        from agentic.store import WORKFLOW_STORE
        latest = WORKFLOW_STORE.get_state(run_id) or initial_state
        latest = fail_workflow(latest, latest.current_agent, f"Workflow execution failed: {exc}")
        _sync_state(latest)
        return latest

    if isinstance(final_output, dict):
        return WorkflowState(**final_output)
    return final_output
