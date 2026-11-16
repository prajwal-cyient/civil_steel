"""
a3_frame_generator_agent.py

Agent A3: Create a Frame (3D Parametric Geometry Generator).
Consolidated & Self-Contained. Computes 3D geometry coordinates for continuous (Tekla)
and bay-split (STAAD FEA) structural members, incorporating Loop A profile upgrades.
"""

from datetime import datetime
import json
import math
from math import sqrt
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from agentic.state import WorkflowState, AgentLogEntry
from agentic.checks import template_coverage
from agentic.config import workflow_mode

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


def generate_members(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parametric 3D Steel Frame Generator.
    Produces:
      - 'members': Continuous physical members spanning bay runs (for Tekla Structures).
      - 'members_split_beams': Discretized bay-split member segments (for STAAD.Pro FEA).
      - 'summary': Component breakdowns and effective height adjustments.
    """
    n_long = cfg["n_long_bays"]
    n_trans = cfg["n_trans_bays"]
    bay_long = cfg["bay_long_mm"]
    bay_trans = cfg["bay_trans_mm"]
    col_height = cfg["col_height_mm"]
    total_structure_height = cfg["total_structure_height"]
    longitudinal_beam_extra_mm = cfg.get("longitudinal_beam_extra_mm", 140.0)
    x0, y0, z0 = cfg.get("starting_point", [0.0, 0.0, 0.0])

    long_brace_bays = cfg.get("longitudinal_brace_bays", [])
    trans_brace_bays = cfg.get("transverse_brace_bays", [])
    vertical_base_eccentricity = cfg.get("vertical_base_eccentricity_mm", 0.0)
    gusset_t = cfg.get("gusset_plate_thickness_mm", 0.0)
    embedded_t = cfg.get("embedded_plate_thickness_mm", 0.0)
    bracing_distance_from_column_mm = cfg.get("bracing_distance_from_column_mm", 0.0)

    beam_material = cfg.get("beam_material", "S355JR")
    column_material = cfg.get("column_material", "S355JR")
    long_brace_material = cfg.get("longitudinal_brace_material", "S355JR")
    trans_brace_material = cfg.get("transverse_brace_material", "S355JR")

    beam_profile = cfg.get("beam_profile", "HEA140")
    column_profile = cfg.get("column_profile", "CFRHS120X120X5")
    long_brace_profile = cfg.get("longitudinal_brace_profile", "CFRHS100X100X4")
    trans_brace_profile = cfg.get("transverse_brace_profile", "CFRHS100X100X4")

    beam_class = cfg.get("beamClass", "3")
    column_class = cfg.get("columnClass", "11")
    long_brace_class = cfg.get("longitudinalClass", "2")
    trans_brace_class = cfg.get("transverseClass", "7")

    beam_height = _lookup_profile_height(beam_profile)
    column_width = _lookup_profile_width(column_profile)
    beam_width = _lookup_profile_width(beam_profile)
    column_section_height = _lookup_profile_height(column_profile)

    max_col_height = total_structure_height - 2 * beam_height
    if max_col_height <= 0:
        raise ValueError(
            f"total_structure_height ({total_structure_height} mm) is too small to fit "
            f"two beam depths ({2 * beam_height} mm) plus any positive column height."
        )
    col_height_eff = min(col_height, max_col_height)    

    col_bottom_z = z0
    col_top_z = col_bottom_z + col_height_eff

    long_beam_z = col_top_z + beam_height
    trans_beam_z = col_top_z + 2 * beam_height

    brace_bottom_z = col_bottom_z + vertical_base_eccentricity
    brace_top_z = col_top_z
    brace_bottom_z_staad = col_bottom_z

    def point(i: int, j: int, z: float) -> List[float]:
        return [x0 + i * bay_long, y0 + j * bay_trans, z]

    def transform_to_staad_co_ordinates(p_in: List[float]) -> List[float]:
        p = list(p_in)
        # Swap Y and Z
        p[1], p[2] = p[2], p[1]
        # Swap X and Z
        p[0], p[2] = p[2], p[0]
        return p

    columns = []
    staad_columns = []
    beams_tekla = []
    beams_staad = []
    braces = []
    staad_braces = []

    # 1. Columns
    for j in range(n_trans + 1):
        for i in range(n_long + 1):
            columns.append({
                "p1": point(i, j, col_bottom_z),
                "p2": point(i, j, col_top_z),
                "profile": column_profile,
                "material": column_material,
                "class": column_class,
                "type": "column",
                "name": f"column-{j}-{i}",
            })
            p1 = transform_to_staad_co_ordinates(point(i, j, col_bottom_z))
            p2 = transform_to_staad_co_ordinates(point(i, j, trans_beam_z))
            staad_columns.append({
                "p1": p1,
                "p2": p2,
                "profile": column_profile,
                "material": column_material,
                "class": column_class,
                "type": "column",
                "name": f"column-{j}-{i}",
                "tekla_name": f"column-{j}-{i}",
            })

    # 2. Longitudinal Beams (X-direction)
    for j in range(n_trans + 1):
        p1 = point(0, j, col_top_z)
        p2 = point(n_long, j, col_top_z)
        p1[0] -= longitudinal_beam_extra_mm
        p2[0] += longitudinal_beam_extra_mm
        beams_tekla.append({
            "p1": p1,
            "p2": p2,
            "profile": beam_profile,
            "material": beam_material,
            "class": beam_class,
            "type": "beam",
            "name": f"beam-x-{j}",
        })
    for j in range(n_trans + 1):
        for i in range(n_long):
            p1 = transform_to_staad_co_ordinates(point(i, j, trans_beam_z))
            p2 = transform_to_staad_co_ordinates(point(i + 1, j, trans_beam_z))
            beams_staad.append({
                "p1": p1,
                "p2": p2,
                "profile": beam_profile,
                "material": beam_material,
                "class": beam_class,
                "type": "beam",
                "name": f"beam-x-{j}-{i}",
                "tekla_name": f"beam-x-{j}",
            })

    # 3. Transverse Beams (Y-direction)
    for i in range(n_long + 1):
        p1 = point(i, 0, col_top_z)
        p2 = point(i, n_trans, col_top_z)
        beams_tekla.append({
            "p1": p1,
            "p2": p2,
            "profile": beam_profile,
            "material": beam_material,
            "class": beam_class,
            "type": "beam",
            "name": f"beam-y-{i}",
        })
    for j in range(n_trans):
        for i in range(n_long + 1):
            p1 = transform_to_staad_co_ordinates(point(i, j, trans_beam_z))
            p2 = transform_to_staad_co_ordinates(point(i, j + 1, trans_beam_z))
            beams_staad.append({
                "p1": p1,
                "p2": p2,
                "profile": beam_profile,
                "material": beam_material,
                "class": beam_class,
                "type": "beam",
                "name": f"beam-y-{j}-{i}",
                "tekla_name": f"beam-y-{i}",
            })

    # 4. Longitudinal Bracing
    for entry in long_brace_bays:
        bay_1based = entry["bay"]
        mirror = entry.get("mirror", False)
        if bay_1based < 1 or bay_1based > n_long:
            continue
        b = bay_1based - 1
        bottom_i, top_i = (b + 1, b) if mirror else (b, b + 1)
        for j in range(n_trans + 1):
            p1 = point(bottom_i, j, brace_bottom_z)
            p2 = point(top_i, j, brace_top_z)
            braces.append({
                "p1": p1,
                "p2": p2,
                "profile": long_brace_profile,
                "material": long_brace_material,
                "class": long_brace_class,
                "type": "longitudinal_brace",
                "name": f"bracing-long-{j}-{bay_1based}",
                "is_mirrored": mirror,
            })
            p1_staad = transform_to_staad_co_ordinates(point(bottom_i, j, brace_bottom_z_staad))
            p2_staad = transform_to_staad_co_ordinates(point(top_i, j, trans_beam_z))
            staad_braces.append({
                "p1": p1_staad,
                "p2": p2_staad,
                "profile": long_brace_profile,
                "material": long_brace_material,
                "class": long_brace_class,
                "type": "longitudinal_brace",
                "name": f"bracing-long-{j}-{bay_1based}",
                "is_mirrored": mirror,
                "tekla_name": f"bracing-long-{j}-{bay_1based}",
            })

    # 5. Transverse Bracing
    for entry in trans_brace_bays:
        bay_1based = entry["bay"]
        mirror = entry.get("mirror", False)
        if bay_1based < 1 or bay_1based > n_trans:
            continue
        b = bay_1based - 1
        bottom_j, top_j = (b + 1, b) if mirror else (b, b + 1)
        for i in range(n_long + 1):
            p1 = point(i, bottom_j, brace_bottom_z)
            p2 = point(i, top_j, brace_top_z)
            braces.append({
                "p1": p1,
                "p2": p2,
                "profile": trans_brace_profile,
                "material": trans_brace_material,
                "class": trans_brace_class,
                "type": "transverse_brace",
                "name": f"bracing-trans-{i}-{bay_1based}",
                "is_mirrored": mirror,
            })
            p1_staad = transform_to_staad_co_ordinates(point(i, bottom_j, brace_bottom_z_staad))
            p2_staad = transform_to_staad_co_ordinates(point(i, top_j, trans_beam_z))
            staad_braces.append({
                "p1": p1_staad,
                "p2": p2_staad,
                "profile": trans_brace_profile,
                "material": trans_brace_material,
                "class": trans_brace_class,
                "type": "transverse_brace",
                "name": f"bracing-trans-{i}-{bay_1based}",
                "is_mirrored": mirror,
                "tekla_name": f"bracing-trans-{i}-{bay_1based}",
            })

    members_tekla = columns + beams_tekla + braces
    members_staad = staad_columns + beams_staad + staad_braces

    members_by_type = {}
    for m in members_tekla:
        members_by_type[m["type"]] = members_by_type.get(m["type"], 0) + 1

    summary = {
        "total_members": len(members_tekla),
        "members_by_type": members_by_type,
        "effective_col_height_mm": col_height_eff,
        "beam_height_mm": beam_height,
    }

    return {
        "grid": cfg,
        "members": members_tekla,
        "members_split_beams": members_staad,
        "summary": summary,
    }


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


def run_a3_frame_generator(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A3: Computes 3D geometry coordinates for continuous and bay-split members.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A3"

    cfg = dict(state.grid_config)
    if state.active_column_profile:
        cfg["column_profile"] = state.active_column_profile
    if state.active_beam_profile:
        cfg["beam_profile"] = state.active_beam_profile
    if state.active_long_brace_profile:
        cfg["longitudinal_brace_profile"] = state.active_long_brace_profile
    if state.active_trans_brace_profile:
        cfg["transverse_brace_profile"] = state.active_trans_brace_profile

    try:
        validate_profile_catalogue(cfg)
        candidate = generate_members(cfg)
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
            "status": "PASSED",
            "message": msg,
            "profile": col_prof,
            "total_members": total_m
        }
        state.logs.append(AgentLogEntry(
            agent_id="A3",
            agent_name="Frame Geometry Generator",
            status="PASSED",
            message=msg,
            timestamp=ts,
            loop_info=loop_note or None
        ))

    except Exception as exc:
        err_msg = f"A3 Frame Generation Failed: {exc}"
        state.status = "FAILED"
        state.error = err_msg
        state.agent_statuses["A3"] = {"status": "FAILED", "message": err_msg}
        state.logs.append(AgentLogEntry(
            agent_id="A3",
            agent_name="Frame Geometry Generator",
            status="FAILED",
            message=err_msg,
            timestamp=ts
        ))

    return state


def validate_profile_catalogue(cfg: dict) -> None:
    """Live workflows must use catalogue dimensions, never preview fallbacks."""
    from common.tekla_common import get_profile_height, get_profile_width
    keys = ("column_profile", "beam_profile", "longitudinal_brace_profile", "transverse_brace_profile")
    for profile in {cfg[key] for key in keys}:
        dimensions = (get_profile_height(profile), get_profile_width(profile))
        if any(value is None or not math.isfinite(float(value)) or value <= 0 for value in dimensions):
            raise ValueError(f"Profile {profile} is unavailable in the live Tekla catalogue.")
