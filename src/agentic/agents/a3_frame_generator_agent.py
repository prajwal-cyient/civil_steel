"""
a3_frame_generator_agent.py

Agent A3: Create a frame using the profile evaluation and geometry modules.
Computes continuous (Tekla) and bay-split (STAAD FEA) structural members,
incorporating Loop A profile upgrades.
"""

from datetime import datetime
from copy import deepcopy
import json
import math
from math import sqrt
import os
import re
import sys
from typing import Any, Dict, List, Tuple

from agentic.state import WorkflowState, AgentLogEntry
from agentic.checks import template_coverage
from agentic.config import workflow_mode
from agentic.nodes.a3_frame.evaluate import FrameParameters, evaluate_frame_parameters as _evaluate_frame_parameters
from agentic.nodes.a3_frame.generate import generate_frame

# Standard catalog profile dimensions (height mm, width mm) fallback when Tekla is offline:
STANDARD_PROFILE_DIMENSIONS: Dict[str, Tuple[float, float]] = {
    "HEA100": (96.0, 100.0),
    "HEA120": (114.0, 120.0),
    "HEA140": (133.0, 140.0),
    "HEA160": (152.0, 160.0),
    "HEA180": (171.0, 180.0),
    "HEA200": (190.0, 200.0),
    "HEA220": (210.0, 220.0),
    "HEA240": (230.0, 240.0),
    "IPE100": (100.0, 55.0),
    "IPE120": (120.0, 64.0),
    "IPE140": (140.0, 73.0),
    "IPE160": (160.0, 82.0),
    "IPE180": (180.0, 91.0),
    "IPE200": (200.0, 100.0),
    "CFRHS100X100X4": (100.0, 100.0),
    "CFRHS100X100X5": (100.0, 100.0),
    "CFRHS120X120X5": (120.0, 120.0),
    "CFRHS120X120X6": (120.0, 120.0),
    "CFRHS140X140X5": (140.0, 140.0),
    "CFRHS150X150X5": (150.0, 150.0),
    "CFRHS160X160X5": (160.0, 160.0),
    "CFRHS180X180X6": (180.0, 180.0),
    "CFRHS200X200X6": (200.0, 200.0),
}


def _lookup_profile_param(profile_name: str, getter) -> float:
    try:
        value = getter(profile_name)
    except Exception:
        return 0.0
    return value if value is not None else 0.0


def _fallback_dimension(profile_name: str, dim_index: int) -> float:
    p = (profile_name or "").upper().strip()
    if p in STANDARD_PROFILE_DIMENSIONS:
        return STANDARD_PROFILE_DIMENSIONS[p][dim_index]
    m = re.search(r"(\d+)[X*](\d+)", p)
    if m:
        try:
            return float(m.group(dim_index + 1))
        except Exception:
            pass
    return 0.0


def _lookup_profile_height(profile_name: str) -> float:
    """Look up a profile's catalog HEIGHT (mm) via Tekla; falls back to standard catalog if unavailable."""
    val = 0.0
    try:
        from common.tekla_common import get_profile_height
        val = _lookup_profile_param(profile_name, get_profile_height)
    except Exception:
        val = 0.0
    if not val:
        val = _fallback_dimension(profile_name, 0)
    return val


def _lookup_profile_width(profile_name: str) -> float:
    """Look up a profile's catalog WIDTH (mm) via Tekla; falls back to standard catalog if unavailable."""
    val = 0.0
    try:
        from common.tekla_common import get_profile_width
        val = _lookup_profile_param(profile_name, get_profile_width)
    except Exception:
        val = 0.0
    if not val:
        val = _fallback_dimension(profile_name, 1)
    return val


def evaluate_frame_parameters(cfg: Dict[str, Any]) -> FrameParameters:
    """Evaluate profile dimensions and the existing column-height constraint."""
    return _evaluate_frame_parameters(cfg, _lookup_profile_height, _lookup_profile_width)


def generate_members(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parametric 3D Steel Frame Generator.
    Produces:
      - 'members': Continuous physical members spanning bay runs (for Tekla Structures).
      - 'members_split_beams': Discretized bay-split member segments (for STAAD.Pro FEA).
      - 'summary': Component breakdowns and effective height adjustments.
    """
    return generate_frame(cfg, None, evaluate_frame_parameters)


def generate_members_from_parameters(
    cfg: Dict[str, Any], parameters: FrameParameters
) -> Dict[str, Any]:
    """Generate the frame using profile and height evaluation from the same configuration."""
    return generate_frame(cfg, parameters, evaluate_frame_parameters)


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
    coverage = template_coverage(candidate)
    state.readiness["template_coverage"] = {k: v for k, v in coverage.items() if k != "joint_groups"}
    if "joint_groups" in coverage:
        from agentic.reference_procedure import build_connection_scope, template_for_group
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
        validate_profile_catalogue(cfg)
        return _finish_frame(state, cfg, generate_members(cfg))
    except Exception as exc:
        return _fail_frame(state, exc)


def evaluate_a3_frame(state: WorkflowState) -> WorkflowState:
    """Prepare a serializable frame plan for the A3b generation stage."""
    state.current_agent = "A3"
    state.frame_plan.clear()
    cfg = _frame_config(state)
    try:
        validate_profile_catalogue(cfg)
        parameters = evaluate_frame_parameters(cfg)
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
        return _finish_frame(state, cfg, generate_members_from_parameters(cfg, parameters))
    except Exception as exc:
        return _fail_frame(state, exc)
    finally:
        state.frame_plan.clear()


async def run_a3_staged(state: WorkflowState) -> WorkflowState:
    """Run frame evaluation and generation in the active subgraph."""
    from agentic.nodes.a3_frame.graph import run_frame_graph
    return await run_frame_graph(state)


def validate_profile_catalogue(cfg: dict) -> None:
    """Live workflows must use catalogue dimensions, never preview fallbacks."""
    from common.tekla_common import get_profile_height, get_profile_width
    keys = ("column_profile", "beam_profile", "longitudinal_brace_profile", "transverse_brace_profile")
    for profile in {cfg[key] for key in keys}:
        dimensions = (get_profile_height(profile), get_profile_width(profile))
        if any(value is None or not math.isfinite(float(value)) or value <= 0 for value in dimensions):
            raise ValueError(f"Profile {profile} is unavailable in the live Tekla catalogue.")
