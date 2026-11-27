"""Geometry validation and A3a/A3b agent execution wrappers."""

from copy import deepcopy
from datetime import datetime
import math
from typing import Any, Dict

from orchestration.state import WorkflowState, AgentLogEntry
from config.settings import workflow_mode
from . import A3A_evaluate_plan, A3B_generate_structure, connection_scope
from .A3A_evaluate_plan import FrameParameters


def check_geometry(candidate: Dict[str, Any]):
    """
    Validates structural geometry using deterministic domain checks:
    - Verifies continuous and bay-split members exist.
    - Confirms all endpoints are finite 3D coordinates.
    - Checks for non-zero length and unique member names.
    - Runs BFS graph connectivity traversal to detect floating or disconnected components.
    """
    for key in ("members", "members_split_beams"):
        members = candidate.get(key, [])
        if not members:
            raise ValueError(f"No {key} were generated in frame candidate")
        names = set()
        for m in members:
            name = m.get("name")
            if not name or name in names:
                raise ValueError(f"Duplicate or missing member name: {name}")
            names.add(name)
            p1, p2 = m.get("p1", []), m.get("p2", [])
            if len(p1) != 3 or len(p2) != 3:
                raise ValueError(f"Member endpoint is not a 3D coordinate: {name}")
            if not all(math.isfinite(v) for v in p1 + p2):
                raise ValueError(f"Non-finite coordinates detected in member: {name}")
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            dz = p2[2] - p1[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length <= 0:
                raise ValueError(f"Zero-length member detected: {name}")

    # BFS Graph Connectivity Check on analytical members (STAAD wireframe)
    adjacency = {}
    for m in candidate.get("members_split_beams", []):
        start = tuple(round(v, 4) for v in m["p1"])
        end = tuple(round(v, 4) for v in m["p2"])
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)

    if adjacency:
        visited = set()
        pending = [next(iter(adjacency))]
        while pending:
            node = pending.pop()
            if node not in visited:
                visited.add(node)
                pending.extend(adjacency[node] - visited)
        if len(visited) != len(adjacency):
            raise ValueError(
                f"Analytical frame contains disconnected components ({len(visited)} of {len(adjacency)} nodes connected)"
            )


def _frame_config(state: WorkflowState) -> Dict[str, Any]:
    cfg = dict(state.grid_config)
    if state.active_column_profile:
        cfg["column_profile"] = state.active_column_profile
    if state.active_beam_profile:
        cfg["beam_profile"] = state.active_beam_profile
    if state.active_long_brace_profile:
        cfg["longitudinal_brace_profile"] = state.active_long_brace_profile
    if state.active_trans_brace_profile:
        cfg["transverse_brace_profile"] = state.active_trans_brace_profile
    return cfg


def _finish_frame(state: WorkflowState, cfg: Dict[str, Any], candidate: Dict[str, Any]) -> WorkflowState:
    check_geometry(candidate)
    state.frame_candidate = candidate
    coverage = connection_scope.template_coverage(candidate)
    state.readiness["template_coverage"] = {k: v for k, v in coverage.items() if k != "joint_groups"}
    if "joint_groups" in coverage:
        from agents.A3_generate_structure.connection_scope import build_connection_scope, template_for_group
        scope = build_connection_scope(coverage["joint_groups"])
        state.readiness["connection_scope"] = scope
        required_missing = [name for name in scope["idea_required_groups"]
                            if not template_for_group(name, scope["mode"] == "reference_procedure").is_file()]
        if required_missing:
            raise ValueError("Missing IDEA templates for calculated scope: " + ", ".join(required_missing))
    if coverage["missing"] and workflow_mode() == "reviewed_engineering":
        raise ValueError("Missing approved IDEA templates for: " + ", ".join(coverage["missing"]) + ". No live models were modified.")
    state.grid_config = cfg
    state.design_generation += 1

    summary = candidate.get("summary", {})
    total_m = summary.get("total_members", len(candidate.get("members", [])))
    col_prof = state.active_column_profile
    beam_prof = state.active_beam_profile
    loop_note = f" (Loop A revision #{state.loop_a_count})" if state.loop_a_count > 0 else ""
    msg = f"Generated {total_m} members with Column={col_prof}, Beam={beam_prof}{loop_note}."
    state.agent_statuses["A3"] = {
        "status": "PASSED", "message": msg, "profile": col_prof, "total_members": total_m
    }
    state.logs.append(AgentLogEntry(
        agent_id="A3", agent_name="Frame Geometry Generator", status="PASSED",
        message=msg, timestamp=datetime.now().astimezone().isoformat(), loop_info=loop_note or None
    ))
    return state


def _fail_frame(state: WorkflowState, exc: Exception) -> WorkflowState:
    err_msg = f"A3 Frame Generation Failed: {exc}"
    state.status = "FAILED"
    state.error = err_msg
    state.agent_statuses["A3"] = {"status": "FAILED", "message": err_msg}
    state.logs.append(AgentLogEntry(
        agent_id="A3", agent_name="Frame Geometry Generator", status="FAILED",
        message=err_msg, timestamp=datetime.now().astimezone().isoformat()
    ))
    return state


def run_a3_frame_generator(state: WorkflowState) -> WorkflowState:
    """Run the existing A3 entry point with its original validation order."""
    state.current_agent = "A3"
    cfg = _frame_config(state)
    try:
        A3A_evaluate_plan.validate_profile_catalogue(cfg)
        return _finish_frame(state, cfg, A3B_generate_structure.generate_members(cfg))
    except Exception as exc:
        return _fail_frame(state, exc)


def evaluate_a3_frame(state: WorkflowState) -> WorkflowState:
    """Prepare a serializable frame plan for the A3b generation stage."""
    state.current_agent = "A3"
    state.frame_plan.clear()
    cfg = _frame_config(state)
    try:
        A3A_evaluate_plan.validate_profile_catalogue(cfg)
        parameters = A3A_evaluate_plan.evaluate_frame_parameters(cfg)
        state.frame_plan = {
            "grid": deepcopy(cfg),
            "parameters": parameters._asdict(),
            "design_generation": state.design_generation,
        }
    except Exception as exc:
        return _fail_frame(state, exc)
    return state


def generate_a3_frame(state: WorkflowState) -> WorkflowState:
    """Generate and validate the frame from the current A3a plan."""
    state.current_agent = "A3"
    try:
        plan = state.frame_plan
        if (not plan or plan.get("grid") != _frame_config(state)
                or plan.get("design_generation") != state.design_generation):
            raise ValueError("Frame evaluation is missing or no longer matches the current inputs.")
        parameters = FrameParameters(**plan["parameters"])
        cfg = plan["grid"]
        return _finish_frame(state, cfg, A3B_generate_structure.generate_members_from_parameters(cfg, parameters))
    except Exception as exc:
        return _fail_frame(state, exc)
    finally:
        state.frame_plan.clear()


async def run_a3_staged(state: WorkflowState) -> WorkflowState:
    """Run frame evaluation and generation in the active subgraph."""
    from agents.A3_generate_structure.graph import run_frame_graph
    return await run_frame_graph(state)


