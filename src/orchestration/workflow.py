"""
orchestrator.py

The central LangGraph StateGraph for the current A1-A7 workflow.
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
from orchestration.state import WorkflowState, fail_workflow, workflow_stage
from config.settings import settings, deep_agents_enabled
from utils.agent_timing import start_timing, finish_timing
from utils.run_archive import guarded, start_stage, finish_stage
from utils.audit import observe, stage_outcome, exception_record
from agents.A1_requirements import run_a1_requirements
from agents.A2_readiness.readiness_agent import run_a2_readiness
from agents.A3_generate_structure import run_a3_staged
from agents.A4_staad import run_a4_staged
from agents.A5_idea_statica import run_a5_staged
from agents.A5_idea_statica.graph import run_idea_graph
from agents.A5_idea_statica.package_validation import run_a6_package_validator
from agents.A6_tekla import run_a7_staged
from agents.A7_reporting import run_a8_reporting


# ─── LangGraph Node Wrappers ───────────────────────────────────────────────────

def _sync_state(state: WorkflowState):
    try:
        from services.workflow_store import WORKFLOW_STORE
        WORKFLOW_STORE.save_state(state)
    except Exception as exc:
        exception_record(state, 'persist_workflow_state', exc)
    try:
        from orchestration.dashboard_router import RUN_STORE
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
    outcome_recorded = False
    history_sizes = {key: len(getattr(state, key)) for key in
                     ('loop_a_history', 'loop_b_history', 'loop_c_history')}
    try:
        with observe(state, f'Agent {agent_id}', kind='agent') as audit:
            if deep_agents_enabled():
                from services.deep_agents_runtime import run_deep_stage
                result = await run_deep_stage(state, agent_id, operation, _sync_state)
            else:
                candidate = operation(state)
                if inspect.isawaitable(candidate):
                    candidate = await candidate
                result = candidate
            stage_outcome(result, history_sizes)
            outcome_recorded = True
            audit.update(status=result.agent_statuses.get(agent_id, {}).get('status', result.status),
                         reason=result.agent_statuses.get(agent_id, {}).get('message'))
        return result
    except BaseException as exc:
        archive_error = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if not outcome_recorded:
            stage_outcome(result, history_sizes)
        finish_timing(result, agent_id, started)
        _sync_state(result)
        guarded(result, finish_stage, agent_id, archive_error)


async def node_a1(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A1", run_a1_requirements)


async def node_a2(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A2", run_a2_readiness)


async def node_a3(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A3", run_a3_staged)


async def node_a4(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A4", run_a4_staged)


async def node_a5(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, "A5", run_a5_staged)


async def node_a5_full(state: WorkflowState) -> WorkflowState:
    async def design_and_audit(candidate):
        return await run_idea_graph(candidate, audit_package=True)
    return await _run_stage(state, "A5", design_and_audit)


async def node_a6(state: WorkflowState) -> WorkflowState:
    def audit_package(candidate):
        agent_id = workflow_stage(candidate, "package")
        result = run_a6_package_validator(candidate, agent_id=agent_id)
        if (result.status != "FAILED"
                and result.agent_statuses.get(agent_id, {}).get("status") == "REVISING"
                and (result.loop_c_count > result.max_loop_c
                     or result.validation_report.get("recovery_target") != "a5_ideastatica_analyzer")):
            return fail_workflow(result, agent_id, "Package recovery is invalid or exhausted; start a new workflow.")
        return result
    return await _run_stage(state, workflow_stage(state, "package"), audit_package)


async def node_a7(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, workflow_stage(state, "tekla"), run_a7_staged)


async def node_a8(state: WorkflowState) -> WorkflowState:
    return await _run_stage(state, workflow_stage(state, "report"), run_a8_reporting)


async def node_gate3(state: WorkflowState) -> WorkflowState:
    if (state.status != "PAUSED_AT_GATE3" or state.gate3_approved
            or not state.validation_report.get("passed")
            or state.agent_statuses.get(workflow_stage(state, "tekla"), {}).get("status") != "PASSED"
            or state.agent_statuses.get(workflow_stage(state, "report"), {}).get("status") != "AWAITING_APPROVAL"
            or state.final_report.get("gate3_status") != "PENDING_ENGINEER_ACCEPTANCE"):
        state = fail_workflow(state, workflow_stage(state, "report"), "Gate 3 requires a verified publication and a report awaiting human approval.")
        _sync_state(state)
    return state


# ─── Conditional Routing Edges (Loop A & Loop B) ───────────────────────────────

from orchestration.routing import route_after_a4, route_after_a5, route_after_a6

# ─── Graph Construction ────────────────────────────────────────────────────────

def build_workflow_graph(entry_stage: Literal["a1_requirements", "a2_readiness"] = "a1_requirements") -> StateGraph:
    """
    Constructs the LangGraph cyclical state machine.
    """
    graph = StateGraph(WorkflowState)

    # Register the outer stages; engineering substages execute in their subgraphs.
    graph.add_node("a1_requirements", node_a1)
    graph.add_node("a2_readiness", node_a2)
    graph.add_node("a3_frame_generator", node_a3)
    graph.add_node("a4_staad_analyzer", node_a4)
    graph.add_node("a5_ideastatica_analyzer", node_a5_full)
    graph.add_node("a6_tekla_modeler", node_a7)
    graph.add_node("a7_reporting", node_a8)
    graph.add_node("gate3_human_review", node_gate3)

    # Standard transitions
    if entry_stage not in ("a1_requirements", "a2_readiness"):
        raise ValueError(f"Unsupported workflow entry stage: {entry_stage}")
    graph.set_entry_point(entry_stage)
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
            "a6_tekla_modeler": "a6_tekla_modeler",
            "__end__": END
        }
    )

    graph.add_conditional_edges("a6_tekla_modeler",
        lambda state: "__end__" if state.status == "FAILED" else "a7_reporting",
        {"__end__": END, "a7_reporting": "a7_reporting"})
    graph.add_conditional_edges("a7_reporting",
        lambda state: "__end__" if state.status == "FAILED" else "gate3_human_review",
        {"__end__": END, "gate3_human_review": "gate3_human_review"})
    graph.add_edge("gate3_human_review", END)

    return graph


# ─── Runner Execution Function ─────────────────────────────────────────────────

async def run_agentic_workflow(
    raw_input: Dict[str, Any],
    run_id: Optional[str] = None,
    *,
    chat_parameter_rerun: bool = False,
) -> WorkflowState:
    """
    Runs the complete A1-A7 workflow through the Gate 3 boundary.
    """
    if not run_id:
        run_id = f"run_{uuid.uuid4().hex[:8]}"

    initial_state = WorkflowState(
        run_id=run_id,
        layout_version=2,
        status="RUNNING",
        raw_input=raw_input,
        max_loop_a=settings().frame_revisions,
        max_loop_b=settings().connection_revisions,
    )
    _sync_state(initial_state)

    # Asynchronously invoke graph
    try:
        if chat_parameter_rerun:
            initial_state = await node_a1(initial_state)
            if initial_state.status == "FAILED":
                return initial_state
            if initial_state.agent_statuses.get("A1", {}).get("status") != "PASSED":
                initial_state = fail_workflow(initial_state, "A1", "Validated requirements are required before entering readiness.")
                _sync_state(initial_state)
                return initial_state
        graph = build_workflow_graph("a2_readiness" if chat_parameter_rerun else "a1_requirements")
        app = graph.compile()
        # Each connection group may need its own bounded corrections.
        final_output = await app.ainvoke(initial_state, config={"recursion_limit": 256})
    except Exception as exc:
        from services.workflow_store import WORKFLOW_STORE
        latest = WORKFLOW_STORE.get_state(run_id) or initial_state
        latest = fail_workflow(latest, latest.current_agent, f"Workflow execution failed: {exc}")
        _sync_state(latest)
        return latest

    if isinstance(final_output, dict):
        return WorkflowState(**final_output)
    return final_output
