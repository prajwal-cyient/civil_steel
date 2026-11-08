"""
staad_integration.py

Sends generated structural members — specifically "members_split_beams"
from generate_grid.py (columns + per-bay beam segments + bracing) — into
a running STAAD.Pro session via the OpenSTAAD COM automation API, and
applies whatever "load_configuration" was supplied alongside them.

WHAT'S SOLID VS. BEST-EFFORT IN THIS FILE
-------------------------------------------
Unlike tekla_integration.py (built from Tekla Open API code you'd
already tested), this module was written largely without a live
STAAD.Pro session to verify against. A few things ARE now confirmed
empirically (see below); the rest is still a first pass:
  - CONFIRMED: OpenSTAAD's CLSID has no registered type library at all,
    so early-bound COM dispatch (win32com.client.gencache.EnsureDispatch)
    is not possible by any route — this module uses plain late-bound
    win32com.client.Dispatch() throughout.
  - CONFIRMED: Geometry.CreateNode's real signature is
    CreateNode(node_id, x, y, z) — the caller supplies the node ID; the
    call does not return a usable value even though it does create (or,
    if node_id already exists, move) a real node. _get_or_create_node()
    and STARTING_NODE_ID/_SequentialIdAllocator exist specifically to
    generate safe, non-colliding IDs ourselves rather than relying on a
    return value.
  - UNCONFIRMED: Geometry.CreateBeam's signature is assumed to follow
    the same pattern, CreateBeam(beam_id, node1, node2) — see the note
    in _create_beam() for the likely alternative if this is wrong.
  - Section/material assignment (_assign_property) is best-effort: it
    assumes your profile strings (e.g. "HEA140", "CFRHS100X100X4") match
    an entry in whatever STAAD section table is active for your design
    code. If they don't match STAAD's own naming, fill in
    PROFILE_NAME_MAP below with tekla_name -> staad_table_name pairs.
  - Load application (_apply_load_configuration) does NOT compute
    actual loads from the radiator/wind/seismic design parameters the
    web UI sends (see LOAD CONFIGURATION SCHEMA below for exactly why
    this is deliberately unimplemented rather than guessed at). It does
    support a lower-level "load_cases" schema for whenever that
    calculation exists and needs to emit real loads.
  - Given that CreateNode/CreateBeam calls can "succeed" (not raise)
    without actually doing what's intended, the self-test below leans
    on manual verification in STAAD.Pro's own Nodes/Beams tables rather
    than trusting return values — do the same when troubleshooting.

For the authoritative method list for your installed version, see
STAAD.Pro's OpenSTAAD.chm help file (usually under the STAAD.Pro
install folder, e.g. "...\\STAAD.Pro CONNECT Edition\\STAAD\\Help\\").

REQUIREMENTS
--------------
  - STAAD.Pro installed and RUNNING with a model open — like
    tekla_integration.py, this automates a live GUI session rather than
    working headless.
  - The "pywin32" package installed (`pip install pywin32`), which
    provides win32com.client for COM automation.

LOAD CONFIGURATION SCHEMA
----------------------------
The web UI currently sends "load_configuration" as radiator/wind/seismic
DESIGN PARAMETERS (this is what the "Load Configuration (STAAD)" panel's
fields build):

{
  "radiator_support_type": "support_at_middle",
  "total_radiator_weight_including_liquid_kg": 4380,
  "number_of_supports_on_one_side": 4,
  "support_spacing_mm": 2750,
  "first_support_offset_from_edge_mm": 10,
  "height_of_radiator_mm": 2600,
  "height_of_fan_mm": 200,
  "diameter_of_fan_mm": 800,
  "live_load_intensity_kN_per_m2": 2.87,
  "temperature_rise_Tp_C": 30,
  "temperature_drop_Tm_C": 20,
  "reference_installation_temperature_C": 20,
  "connection_weight_addition_percentage": 10,
  "wind_speed_mph": 90,
  "wind_exposure_category": "B",
  "occupancy_category": "standard_occupancy",
  "frame_pressure_coefficient_Cq": 1.8,
  "equipment_pressure_coefficient_Cpe": 1.4,
    "radiator_wind_force_x_kN": 1.67,
    "radiator_wind_force_y1_kN": 0.74,
    "radiator_wind_force_z_kN": 2.24,
    "radiator_wind_force_y2_kN": 0.78,
  "seismic_zone": "2A",
  "soil_type": "SC",
  "seismic_source_type": "C",
  "distance_to_closet_known_seismic_source_km": 5,
  "type_of_structure": "non_building_structure",
  "framing_system_x": "all_other_self_supported_structures",
  "framing_system_z": "all_other_self_supported_structures"
}

All keys are optional except "title". "member_names" refers to the
"name" field of entries in "members_split_beams".
"""

import argparse
import asyncio
import json
from math import atan2, pi
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from import_path_util import add_parent_dir_to_sys_path
add_parent_dir_to_sys_path(__file__)

try:
    from common.staad_common import *
    import common.staad_common as staad_common
except ImportError:  # pragma: no cover - only available on Windows w/ pywin32
    os_analytical = None

'''
Like memberName2type, but also tells mirrored bracings apart using the member's p1/p2 co-ordinates:
a longitudinal bracing with Z of p2 < Z of p1 is bracing_longitudinal_mirrored, and
a transverse bracing with X of p2 < X of p1 is bracing_transverse_mirrored.
'''
def memberData2type(member_data: dict) -> str:
    member_type = memberName2type(member_data.get("name", ""))
    p1, p2 = member_data.get("p1"), member_data.get("p2")
    if not p1 or not p2:
        return member_type
    # Determine if the member is a mirrored bracing based on its coordinates.
    # TODO: use the is_mirrored field from member_data instead of inferring from coordinates
    if member_type == "bracing_longitudinal" and p2[2] < p1[2]:
        return "bracing_longitudinal_mirrored"
    if member_type == "bracing_transverse" and p2[0] < p1[0]:
        return "bracing_transverse_mirrored"
    return member_type

def memberName2type(name: str) -> str:
    if name.startswith("bracing-long"):
        return "bracing_longitudinal"
    elif name.startswith("bracing-trans"):
        return "bracing_transverse"
    elif name.startswith("beam-x"):
        return "beam_longitudinal"
    elif name.startswith("beam-y"):
        return "beam_transverse"
    else:
        return "column"

# ── Internals: loads (best-effort, minimal schema) ─────────────────────

def _get_top_node_coordinates(
    node_cache: Dict[tuple, int],
    top_nodes: List[int],
) -> Dict[tuple, int]:
    top_node_ids = set(top_nodes)
    top_node_coordinates = {
        point: node_id for point, node_id in node_cache.items()
        if node_id in top_node_ids
    }
    if not top_node_coordinates:
        raise ValueError("Node classification requires top nodes.")
    return top_node_coordinates


def _get_boundary_nodes(node_cache: Dict[tuple, int], top_nodes: List[int], boundary_count: int) -> List[int]:
    top_node_coordinates = _get_top_node_coordinates(node_cache, top_nodes)

    min_x = min(point[0] for point in top_node_coordinates)
    max_x = max(point[0] for point in top_node_coordinates)
    min_z = min(point[2] for point in top_node_coordinates)
    max_z = max(point[2] for point in top_node_coordinates)
    return sorted(
        node_id for point, node_id in top_node_coordinates.items()
        if int(point[0] in (min_x, max_x)) + int(point[2] in (min_z, max_z)) == boundary_count
    )


def _get_corner_nodes(node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]:
    return _get_boundary_nodes(node_cache, top_nodes, 2)


def _get_edge_nodes(node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]:
    return _get_boundary_nodes(node_cache, top_nodes, 1)


def _get_inner_nodes(node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]:
    return _get_boundary_nodes(node_cache, top_nodes, 0)


def _get_edge_members(members: List[Dict[str, Any]], node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]:
    top_node_coordinates = _get_top_node_coordinates(node_cache, top_nodes)
    min_x = min(point[0] for point in top_node_coordinates)
    max_x = max(point[0] for point in top_node_coordinates)
    min_z = min(point[2] for point in top_node_coordinates)
    max_z = max(point[2] for point in top_node_coordinates)
    edge_members = []
    for member in members:
        if member.get("type", "").lower() != "beam":
            continue
        start_point = staad_common._node_key(member["p1"])
        end_point = staad_common._node_key(member["p2"])
        if start_point not in top_node_coordinates or end_point not in top_node_coordinates:
            continue
        on_x_boundary = start_point[0] == end_point[0] and start_point[0] in (min_x, max_x)
        on_z_boundary = start_point[2] == end_point[2] and start_point[2] in (min_z, max_z)
        if on_x_boundary or on_z_boundary:
            edge_members.append(member["id"])
    return sorted(edge_members)


def _apply_distributed_nodal_load(
    load: OSLoad,
    corner_nodes: List[int],
    edge_nodes: List[int],
    inner_nodes: List[int],
    mass_kg: float,
    is_seismic: bool = False,
) -> None:
    node_groups = {1: corner_nodes, 2: edge_nodes, 4: inner_nodes}

    total_multiplier = sum(multiplier * len(nodes) for multiplier, nodes in node_groups.items())
    if not total_multiplier:
        raise ValueError("Distributed nodal load requires top nodes.")
    base_value_kN = float(mass_kg) * 9.81 / 1000.0 / total_multiplier
    for multiplier, nodes in node_groups.items():
        if nodes:
            if is_seismic:
                load.AddSeismicDefJointWeight(multiplier * base_value_kN, sorted(nodes))
            else:
                load.AddNodalLoad(sorted(nodes), 0.0, -multiplier * base_value_kN, 0.0, 0.0, 0.0, 0.0)


def _apply_distributed_seismic_joint_weight(
    load: OSLoad,
    corner_nodes: List[int],
    edge_nodes: List[int],
    inner_nodes: List[int],
    mass_kg: float,
) -> None:
    _apply_distributed_nodal_load(load, corner_nodes, edge_nodes, inner_nodes, mass_kg, is_seismic=True)

def _apply_radiator_installation_load(
    load: OSLoad,
    corner_nodes: List[int],
    edge_nodes: List[int],
    inner_nodes: List[int],
    radiator_mass_kg: float,
) -> None:
    _apply_distributed_nodal_load(load, corner_nodes, edge_nodes, inner_nodes, radiator_mass_kg)


def _apply_radiator_wind_nodal_loads(
    load: OSLoad,
    node_cache: Dict[tuple, int],
    top_nodes: List[int],
    wind_case_title: str,
    load_configuration: Dict[str, Any],
) -> None:
    coordinates = _get_top_node_coordinates(node_cache, top_nodes)
    along_x = wind_case_title in ("W+X", "W-X")
    row_axis = 0 if along_x else 2
    endpoint_axis = 2 if along_x else 0
    rows = sorted({point[row_axis] for point in coordinates})
    if len(rows) < 2:
        raise ValueError(f"{wind_case_title} radiator wind forces require at least two top-node rows.")
    negative_direction = wind_case_title in ("W-X", "W-Z")
    selected_rows = list(reversed(rows[-2:])) if negative_direction else rows[:2]
    horizontal_force = float(load_configuration.get(
        "radiator_wind_force_x_kN" if along_x else "radiator_wind_force_z_kN",
        1.67 if along_x else 2.24,
    ))
    vertical_force = float(load_configuration.get(
        "radiator_wind_force_y1_kN" if along_x else "radiator_wind_force_y2_kN",
        0.74 if along_x else 0.78,
    ))
    horizontal_sign = -1.0 if negative_direction else 1.0
    for row_index, row_coordinate in enumerate(selected_rows):
        row_nodes = {
            point: node_id for point, node_id in coordinates.items()
            if point[row_axis] == row_coordinate
        }
        endpoints = (
            min(point[endpoint_axis] for point in row_nodes),
            max(point[endpoint_axis] for point in row_nodes),
        )
        endpoint_nodes = sorted(node_id for point, node_id in row_nodes.items() if point[endpoint_axis] in endpoints)
        interior_nodes = sorted(node_id for point, node_id in row_nodes.items() if point[endpoint_axis] not in endpoints)
        vertical_sign = 1.0 if row_index == 0 else -1.0
        for multiplier, nodes in ((1, endpoint_nodes), (2, interior_nodes)):
            if nodes:
                horizontal = horizontal_sign * multiplier * horizontal_force
                load.AddNodalLoad(
                    nodes, horizontal if along_x else 0.0,
                    vertical_sign * multiplier * vertical_force,
                    0.0 if along_x else horizontal, 0.0, 0.0, 0.0,
                )


def _create_directional_wind_cases(
    load: OSLoad,
    members: List[Dict[str, Any]],
    grid_spec: Dict[str, Any],
    wind_definition_id: int,
    first_case_id: int,
    is_open_structure: bool,
) -> List[int]:
    longitudinal_rows_m = sorted({
        member["p1"][0] / 1000.0 for member in members
        if member.get("type", "").lower() == "beam" and member.get("name", "").startswith("beam-x")
    })
    transverse_rows_m = sorted({
        member["p1"][2] / 1000.0 for member in members
        if member.get("type", "").lower() == "beam" and member.get("name", "").startswith("beam-y")
    })
    max_height_m = max(
        grid_spec.get("total_structure_height", 0),
        max(max(member["p1"][1], member["p2"][1]) for member in members),
    ) / 1000.0
    transverse_extent_m = grid_spec["n_trans_bays"] * grid_spec["bay_trans_mm"] / 1000.0
    longitudinal_extent_m = grid_spec["n_long_bays"] * grid_spec["bay_long_mm"] / 1000.0
    wind_cases = []
    for title, direction, factor, rows, axis in (
        ("W+X", "Global X", 1.0, longitudinal_rows_m, "X"),
        ("W-X", "Global X", -1.0, longitudinal_rows_m, "X"),
        ("W+Z", "Global Z", 1.0, transverse_rows_m, "Z"),
        ("W-Z", "Global Z", -1.0, transverse_rows_m, "Z"),
    ):
        wind_case = load.CreateNewPrimaryLoadEx2(title, LOAD_TYPE_MAP["Wind"], first_case_id + len(wind_cases))
        wind_cases.append(wind_case)
        load.SetLoadActive(wind_case)
        for row_index, coordinate_m in enumerate(rows):
            strip_min_m = -0.1 if row_index == 0 else coordinate_m - 0.1
            strip_max_m = strip_min_m + 0.2
            if axis == "X":
                dx_min, dx_max = strip_min_m, strip_max_m
                dz_min, dz_max = -1.0, longitudinal_extent_m + 0.2
            else:
                dx_min, dx_max = -1.0, transverse_extent_m + 0.2
                dz_min, dz_max = strip_min_m, strip_max_m
            load.AddWindLoad(
                wind_definition_id, WIND_DIRECTION_MAP[direction], factor, int(is_open_structure),
                0.0, max_height_m, dz_min, dz_max, dx_min, dx_max,
            )
    return wind_cases

def create_design_parameters(design: OSDesign, grid_spec: Dict[str, Any], members: List[Dict[str, Any]], longitudinal_beams: List[Any], transverse_beams: List[Any], columns: List[Any], longitudinal_braces: List[Any], transverse_braces: List[Any]) -> None:
    design_code_str = grid_spec.get("design_code", "BS EN 1993-1-1:2005")
    design_code = DESIGN_CODES_MAP[design_code_str]
    member_id_list = [member["id"] for member in members]
    print(f'Creating design parameters for design code {design_code_str}: {design_code}')
    designRefId = design.CreateDesignBrief(design_code)
    #design.AssignDesignParameter(designRefId, "CODE", design_code_str, [])
    design.AssignDesignParameter(designRefId, "BEAM", "3", member_id_list)
    design.AssignDesignParameter(designRefId, "NA", "0", member_id_list)
    design.AssignDesignParameter(designRefId, "SGR", "2", member_id_list)
    # For beams: Assume I cross-section profile (I-beam)
    design.AssignDesignParameter(designRefId, "SBLT", "0", longitudinal_beams + transverse_beams)
    # For columns and braces: Assume standard square or rectangular cross-section profile
    design.AssignDesignParameter(designRefId, "SBLT", "2", columns + longitudinal_braces + transverse_braces)
    design.AssignDesignParameter(designRefId, "GM1", "1", member_id_list)
    design.AssignDesignParameter(designRefId, "GM2", "1.25", member_id_list)
    design.AssignDesignParameter(designRefId, "GM0", "1", member_id_list)
    # Calculate and assign lengths for columns, longitudinal beams, and transverse beams
    columnObjs = list(filter(lambda m: m.get("type", "").lower() == "column", members))
    column_length = (columnObjs[0].get("p2")[1] - columnObjs[0].get("p1")[1])/1000.0 if columnObjs else 0
    design.AssignDesignParameter(designRefId, "LY", str(column_length), columns)
    design.AssignDesignParameter(designRefId, "LZ", str(column_length), columns)
    design.AssignDesignParameter(designRefId, "UNL", str(column_length), columns)
    print(f'Assigned column length LY, LZ, UNL = {column_length} to columns: {columns}')

    longitudinal_beam_objs = list(filter(lambda m: m.get("type", "").lower() == "beam" and m.get("name", "").lower().startswith("beam-x"), members))
    longitudinal_beam_length = (longitudinal_beam_objs[0].get("p2")[2] - longitudinal_beam_objs[0].get("p1")[2])/1000.0 if longitudinal_beam_objs else 0
    design.AssignDesignParameter(designRefId, "LY", str(longitudinal_beam_length), longitudinal_beams)
    design.AssignDesignParameter(designRefId, "LZ", str(longitudinal_beam_length), longitudinal_beams)
    design.AssignDesignParameter(designRefId, "UNL", str(longitudinal_beam_length), longitudinal_beams)
    print(f'Assigned longitudinal beam length LY, LZ, UNL = {longitudinal_beam_length} to longitudinal beams: {longitudinal_beams}')

    transverse_beam_objs = list(filter(lambda m: m.get("type", "").lower() == "beam" and m.get("name", "").lower().startswith("beam-y"), members))
    transverse_beam_length = (transverse_beam_objs[0].get("p2")[0] - transverse_beam_objs[0].get("p1")[0])/1000.0 if transverse_beam_objs else 0
    design.AssignDesignParameter(designRefId, "LY", str(transverse_beam_length), transverse_beams)
    design.AssignDesignParameter(designRefId, "LZ", str(transverse_beam_length), transverse_beams)
    design.AssignDesignParameter(designRefId, "UNL", str(transverse_beam_length), transverse_beams)
    print(f'Assigned transverse beam length LY, LZ, UNL = {transverse_beam_length} to transverse beams: {transverse_beams}')

    design.AssignDesignParameter(designRefId, "KY", "1", columns)
    design.AssignDesignParameter(designRefId, "KZ", "1", columns)
    design.AssignDesignParameter(designRefId, "TRACK", "2", columns)

    design.AssignDesignCommand(designRefId, "CHECK CODE", "", member_id_list)
    design.AssignDesignCommand(designRefId, "MEMBER TAKE OFF", "", member_id_list)

def _apply_load_configuration(
    load: OSLoad,
    design: OSDesign,
    node_cache: Dict[tuple, int],
    load_configuration: Dict[str, Any],
    members: List[Dict[str, Any]],
    support_nodes: Set[int],
    grid_spec: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Applies load_configuration to the model, if it's in a shape this
    function actually knows how to act on.

    IMPORTANT: the web UI currently sends the radiator/wind/seismic
    DESIGN PARAMETERS documented in the module docstring's "Radiator
    design parameters" schema (radiator_support_type, wind_speed_mph,
    seismic_zone, etc.) — not ready-made STAAD load cases. Turning those
    parameters into actual loads (distributing self-weight across
    supports, computing live/wind/seismic forces) requires implementing
    the specific design code's formulas (this looks like it follows
    UBC-97's naming — "seismic_zone": "2A", "soil_type": "SC",
    "seismic_source_type" — but that's inferred, not confirmed). That
    calculation is NOT implemented here: getting a structural load
    calculation wrong has real safety consequences and should be
    confirmed against your domain expert's exact code/version/formulas
    first, rather than guessed at. If load_configuration doesn't contain
    a "load_cases" key (i.e. it's the radiator design parameters, not
    the older ad-hoc schema below), this returns an explicit error
    rather than silently applying nothing.

    Returns
    -------
    dict
        {"applied": <int>, "errors": [{"title": ..., "error": ...}, ...]}
    """
    errors = []

    top_nodes = list(set(node_cache.values()) - set(support_nodes))
    corner_nodes = _get_corner_nodes(node_cache, top_nodes)
    edge_nodes = _get_edge_nodes(node_cache, top_nodes)
    inner_nodes = _get_inner_nodes(node_cache, top_nodes)
    edge_members = _get_edge_members(members, node_cache, top_nodes)
    longitudinal_beams = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "beam" and m.get("name", "").startswith("beam-x"), members)))
    transverse_beams = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "beam" and m.get("name", "").startswith("beam-y"), members)))
    longitudinal_edge_members = sorted(set(longitudinal_beams) & set(edge_members))
    transverse_edge_members = sorted(set(transverse_beams) & set(edge_members))
    dead_load_intensity_kN_per_m2 = load_configuration.get("dead_load_intensity_kN_per_m2", 0.5)
    longitudinal_beam_length_m = grid_spec["bay_long_mm"] / 1000.0
    transverse_beam_length_m = grid_spec["bay_trans_mm"] / 1000.0
    columnObjs = list(filter(lambda m: m.get("type", "").lower() == "column", members))
    columns = list(map(lambda m: m["id"], columnObjs))
    longitudinal_bracings = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "longitudinal_brace", members)))
    transverse_bracings = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "transverse_brace", members)))
    print(f'Longitudinal beams: {len(longitudinal_beams)}, Transverse beams: {len(transverse_beams)}, Columns: {len(columns)}, Longitudinal bracings: {len(longitudinal_bracings)}, Transverse bracings: {len(transverse_bracings)}')
    print(f'Longitudinal beam length: {longitudinal_beam_length_m} m, Transverse beam length: {transverse_beam_length_m} m')

    windDefId = 1
    #windExposure = load_configuration.get("wind_exposure", 1.0)
    windDefCreated = load.AddWindDefinition(windDefId, "Wind 1")
    print(f'Wind definition created: {windDefCreated}, wind definition id {windDefId}')
    intensity_kN_per_m2 = load_configuration.get("wind_intensity_kN_per_m2", [2.19551, 2.32465, 2.42797, 2.53129, 2.68627, 2.81541, 2.91873, 3.02205, 3.12537, 3.20286, 3.25452, 3.38366, 3.51281, 3.5903, 3.69362, 3.77111, 3.95191, 4.10689, 4.23604, 4.36518, 4.4685, 4.57182])
    height_list = load_configuration.get("wind_height_list", [4.6, 6.1, 7.6, 9.1, 12.2, 15.2, 18, 21.3, 24.4, 27.4, 30.5, 36.6, 42.7, 48.8, 54.9, 61, 76.2, 91.4, 106.7, 121.9, 137.2, 152.4])
    windDefCreated = load.AddWindIntensity(windDefId, intensity_kN_per_m2, height_list)
    print(f'Wind intensity added: {windDefCreated}, wind definition id {windDefId}')
    #windDefCreated = load.AddWindExposure(windDefId, windExposure, top_nodes)
    #print(f'Wind exposure added: {windDefCreated}, wind definition id {windDefId}')

    windDef2Id = 2
    #windExposure = load_configuration.get("wind_exposure", 1.0)
    windDefCreated = load.AddWindDefinition(windDef2Id, "Wind 2")
    print(f'Wind definition created: {windDefCreated}, wind definition id {windDef2Id}')
    intensity_kN_per_m2 = load_configuration.get("wind2_intensity_kN_per_m2", [0.113, 0.12, 0.125, 0.13])
    height_list = load_configuration.get("wind2_height_list", [4.6, 6.1, 7.6, 9.1])
    windDefCreated = load.AddWindIntensity(windDef2Id, intensity_kN_per_m2, height_list)
    print(f'Wind intensity added: {windDefCreated}, wind definition id {windDef2Id}')
    #windDefCreated = load.AddWindExposure(windDef2Id, windExposure, list(support_nodes))
    #print(f'Wind exposure added: {windDefCreated}, wind definition id {windDef2Id}')
    
    modes = load_configuration.get("cut_off_modes", 200)  # Default to 200 modes if not specified
    frequency = load_configuration.get("cut_off_frequency", 200)  # Default to 200 Hz if not specified
    mode_command = f"CUT OFF MODE SHAPE {modes}"
    freq_command = f"CUT OFF FREQUENCY {frequency}"
    # TODO: Add mode and frequency commands to the STAAD input file or active session
    # Seismic load (to be implemented)
    defineSeismicLoad = load_configuration.get("define_seismic_load", True)
    if defineSeismicLoad:
        seismic_code = load_configuration.get("seismic_code", "UBC 1997")  # Default to UBC 1997 if not specified. Earlier versions might use "IBC 2018"
        seismicCode = SEISMIC_LOAD_CODE_TYPE_MAP[seismic_code] # "UBC 1985"
        isAccidental = load_configuration.get("is_seismic_accidental", 0)  # 1 or 0 depending on whether the seismic load is accidental
        seismicLoadFactor = load_configuration.get("seismic_load_factor", 1)  # Default to 1 if not specified
        isSeismicLoadDefCreated = load.AddSeismicDefinition(seismicCode, isAccidental)
        print(f"Seismic definition created: {isSeismicLoadDefCreated} for seismic code: {seismicCode} {seismic_code} and load factor {seismicLoadFactor}")
        # UBC 1985
        #seismicParams = load_configuration.get("seismic_params", {"ZONE": 0.095, "K": 1.01, "I": 1.02, "TS": 0.5})
        # UBC 1997
        seismicParams = load_configuration.get("seismic_params", {"ZONE": 0.075, "I": 1, "RWX": 2.9, "RWZ": 2.9, "STYPE": 4, "CT": 0.0488, "NA": 1, "NV": 1}) # GUI Name STYP for STYPE
        # IBC 2018
        #seismicParams = load_configuration.get("seismic_params", {"SS": 0.679, "S1": 0.241, "I": 1.25, "RX": 1.5, "RZ": 1.5, "SCLASS": 3, "CTX": 0.426, "CTZ": 0.426, "TL": 8, "FA": 1.128, "FV": 1.559, "XX": 0.75, "XZ": 0.75})
        print(f"Seismic parameters: {seismicParams}")
        for param, value in seismicParams.items():
            load.ModifySeismicDefinitionParams(param, value)
        # Define seismic load cases first, then others
        # IBC LOAD CASES MUST BE DEFINED BEFORE ANY OTHER LOAD CASE.
        seismic_self_weight_factor = load_configuration.get("seismic_self_weight_factor", 1.1)
        # FIXME: there is a bug in openstaadpy's implementation of OSLoad.AddSeismicDefSelfWeight
        # It calls self._load.AddSeismicDefMemberWeight(varWeightFactor) instead of self._load.AddSeismicDefSelfWeight and that was the reason SELFWEIGHT wasn't getting added under seismic definition.
        seismicLoadApplied = load._load.AddSeismicDefSelfWeight(float(seismic_self_weight_factor))
        print(f"Seismic self weight factor with AddSeismicDefSelfWeight: {seismic_self_weight_factor}, load applied: {seismicLoadApplied}")
        #seismic_code = "AUTO DETECT"
        seismicCode = searchDictByKeyStart(SEISMIC_LOAD_CODE_TYPE_MAP_AddSeismicDefMemberWeight, seismic_code)
        print(f"Seismic code for member weight: {seismicCode}")
        conc_start_dist = load_configuration.get("seismic_member_concentrated_weight_start_dist", 0.2)  # Example default value, adjust as needed
        conc_end_dist = load_configuration.get("seismic_member_concentrated_weight_end_dist", 0.6)  # Example default value, adjust as needed
        if longitudinal_edge_members:
            seismicLoadApplied = load.AddSeismicDefMemberWeight(seismicCode, SEISMIC_LOAD_TYPE["UNIFORM"], dead_load_intensity_kN_per_m2 * longitudinal_beam_length_m, 0.0, 0.0, longitudinal_edge_members)
            print(f"Seismic load applied for longitudinal edge member weight: {seismicLoadApplied}")
        if transverse_edge_members:
            seismicLoadApplied = load.AddSeismicDefMemberWeight(seismicCode, SEISMIC_LOAD_TYPE["UNIFORM"], dead_load_intensity_kN_per_m2 * transverse_beam_length_m, 0.0, 0.0, transverse_edge_members)
            print(f"Seismic load applied for transverse edge member weight: {seismicLoadApplied}")
        _apply_distributed_seismic_joint_weight(
            load, corner_nodes, edge_nodes, inner_nodes,
            load_configuration["total_radiator_weight_including_liquid_kg"],
        )
        #seismicLoadApplied = load.AddSeismicDefJointWeight(load_configuration.get("seismic_joint_weight_bottom_nodes", 3.92), support_nodes)  # Example weight, adjust as needed
        #print(f"Seismic load applied for bottom nodes: {seismicLoadApplied}")
        # seismicLoadApplied = load.AddSeismicDefMemberWeight(seismicCode, SEISMIC_LOAD_TYPE["CONCENTRATED"], load_configuration.get("seismic_longitudinal_brace_concentrated_weight", 17.5), conc_start_dist, conc_end_dist, longitudinal_bracings)
        # print(f"Seismic load applied for longitudinal bracings: {seismicLoadApplied}")
        # seismicLoadApplied = load.AddSeismicDefMemberWeight(seismicCode, SEISMIC_LOAD_TYPE["CONCENTRATED"], load_configuration.get("seismic_transverse_brace__concentrated_weight", 15.5), conc_start_dist, conc_end_dist, transverse_bracings)
        # print(f"Seismic load applied for transverse bracings: {seismicLoadApplied}")

        #seismicLoadApplied = load.AddSeismicLoad(SEISMIC_DIRECTION_TYPE_MAP["GlobalX"], seismicLoadFactor)
    # Create load case and add self-weight
    currentLoadCaseId = 1
    seismic_case_1 = load.CreateNewPrimaryLoadEx2("EQ +X", LOAD_TYPE_MAP["None"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(seismic_case_1)
    seismicLoadApplied = load.AddSeismicLoad(SEISMIC_DIRECTION_TYPE_MAP["GlobalX"], seismicLoadFactor)

    seismic_case_2 = load.CreateNewPrimaryLoadEx2("EQ +Z", LOAD_TYPE_MAP["None"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(seismic_case_2)
    seismicLoadApplied = load.AddSeismicLoad(SEISMIC_DIRECTION_TYPE_MAP["GlobalZ"], seismicLoadFactor)

    seismic_case_3 = load.CreateNewPrimaryLoadEx2("EQ +X Local", LOAD_TYPE_MAP["Seismic-H"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(seismic_case_3)
    top_nodes_first_half = top_nodes[:len(top_nodes)//2]
    seismic_local_load_1 = load_configuration.get("seismic_local_load_1", 1.64)
    load.AddNodalLoad(top_nodes_first_half, 0.0, seismic_local_load_1/len(top_nodes_first_half), 0.0, 0.0, 0.0, 0.0)

    seismic_case_4 = load.CreateNewPrimaryLoadEx2("EQ +Z Local", LOAD_TYPE_MAP["Seismic-H"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(seismic_case_4)
    top_nodes_second_half = top_nodes[len(top_nodes)//2:]
    seismic_local_load_2 = load_configuration.get("seismic_local_load_2", -1.64)
    load.AddNodalLoad(top_nodes_second_half, 0.0, seismic_local_load_2/len(top_nodes_second_half), 0.0, 0.0, 0.0, 0.0)

    seismic_case_5 = load.CreateNewPrimaryLoadEx2("SL SNOW LOAD", LOAD_TYPE_MAP["Seismic-V"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(seismic_case_5)
    support_nodes_first_half = list(support_nodes)[:len(support_nodes)//2]
    seismic_local_load_3 = load_configuration.get("seismic_local_load_3", -3.88)
    load.AddNodalLoad(support_nodes_first_half, 0.0, seismic_local_load_3/len(support_nodes_first_half), 0.0, 0.0, 0.0, 0.0)

    load_case = load.CreateNewPrimaryLoadEx2("DL DEAD LOAD", LOAD_TYPE_MAP["Dead"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(load_case)
    load.AddSelfWeightInXYZ(self_weight_direction_global_y, -1.0)
    # Member loads
    #TODO: Apply live load to beams based on their orientation and length
    #print(f'Grid Spec {grid_spec}, load configuration {load_configuration}')
    # Load direction (1 to 9 for LocalX, LocalY, LocalZ, GlobalX, GlobalY, GlobalZ, ProjectedX, ProjectedY, ProjectedZ respectively)
    # For uniform load in the global Y direction (downwards), use direction 6 (GlobalY)
    if longitudinal_edge_members:
        load.AddMemberUniformForce(longitudinal_edge_members, DIRECTION_TYPE_MAP["GlobalY"], -dead_load_intensity_kN_per_m2 * longitudinal_beam_length_m, 0.0, 0.0, 0.0)
    if transverse_edge_members:
        load.AddMemberUniformForce(transverse_edge_members, DIRECTION_TYPE_MAP["GlobalY"], -dead_load_intensity_kN_per_m2 * transverse_beam_length_m, 0.0, 0.0, 0.0)

    installation_case = load.CreateNewPrimaryLoadEx2("INST", LOAD_TYPE_MAP["Dead"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(installation_case)
    _apply_radiator_installation_load(
        load, corner_nodes, edge_nodes, inner_nodes,
        load_configuration["total_radiator_weight_including_liquid_kg"],
    )

    load_case_2 = load.CreateNewPrimaryLoadEx2("LL LIVE LOAD", LOAD_TYPE_MAP["Live"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(load_case_2)
    live_weight = load_configuration.get("total_live_weight_including_liquid_kg", 30.0)
    _apply_distributed_nodal_load(load, corner_nodes, edge_nodes, inner_nodes, live_weight)
    # Member loads
    #TODO: Apply live load to beams based on their orientation and length
    #print(f'Grid Spec {grid_spec}, load configuration {load_configuration}')
    live_load_intensity_kN_per_m2 = load_configuration["live_load_intensity_kN_per_m2"]
    longitudinal_beam_length_m = grid_spec["bay_long_mm"] / 1000.0
    transverse_beam_length_m = grid_spec["bay_trans_mm"] / 1000.0
    # Load direction (1 to 9 for LocalX, LocalY, LocalZ, GlobalX, GlobalY, GlobalZ, ProjectedX, ProjectedY, ProjectedZ respectively)
    # For uniform load in the global Y direction (downwards), use direction 6 (GlobalY)
    #load.AddMemberUniformForce(longitudinal_beams, DIRECTION_TYPE_MAP["GlobalY"], -live_load_intensity_kN_per_m2 * longitudinal_beam_length_m, 0.0, longitudinal_beam_length_m, 0.0)
    #load.AddMemberUniformForce(transverse_beams, DIRECTION_TYPE_MAP["GlobalY"], -live_load_intensity_kN_per_m2 * transverse_beam_length_m, 0.0, transverse_beam_length_m, 0.0)

    temperature_case1 = load.CreateNewPrimaryLoadEx2("T+VE", LOAD_TYPE_MAP["Live"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(temperature_case1)
    temperature_delta = load_configuration.get("temperature_rise_Tp_C", 61.1)
    load.AddTemperatureLoad(columns + longitudinal_beams + transverse_beams + longitudinal_bracings + transverse_bracings, temperature_delta, 0.0, 0.0)
    print(f'Applied temperature rise load of {temperature_delta} °C to all members')
    temperature_case2 = load.CreateNewPrimaryLoadEx2("T-VE", LOAD_TYPE_MAP["Live"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(temperature_case2)
    temperature_delta = load_configuration.get("temperature_drop_Tm_C", -61.1)
    load.AddTemperatureLoad(columns + longitudinal_beams + transverse_beams + longitudinal_bracings + transverse_bracings, temperature_delta, 0.0, 0.0)
    print(f'Applied temperature drop load of {temperature_delta} °C to all members')

    wind_cases = _create_directional_wind_cases(
        load, members, grid_spec, windDefId, currentLoadCaseId,
        load_configuration.get("is_open_structure", True),
    )
    currentLoadCaseId += len(wind_cases)
    for wind_case, title in zip(wind_cases, ("W+X", "W-X", "W+Z", "W-Z")):
        load.SetLoadActive(wind_case)
        _apply_radiator_wind_nodal_loads(load, node_cache, top_nodes, title, load_configuration)

    #TODO: Wind uniform load on members
    #load.AddMemberUniformForce(columns + longitudinal_bracings + transverse_bracings, DIRECTION_TYPE_MAP["GlobalX"], 0.6, 0.0, 0.0, 0.0)  # 4=GlobalX

    # Load Combination 3 (75% of 1 & 2)
    comboLoadCases: list[int] = []
    currentLoadCaseId = max(currentLoadCaseId, startingComboLoadCaseId)
    primary_load_cases = [load_case, installation_case, load_case_2, *wind_cases, seismic_case_1, seismic_case_2, seismic_case_3, seismic_case_4, seismic_case_5, temperature_case1, temperature_case2]

    envelopCreationResults: list[Dict[str, Any]] = []
    envelopeId = 1
    startingComboCaseIdIndex = 0

    load_case_factors = [1, 1, 0.8] + [0.75] * len(wind_cases) + [0.75] * 5 + [1.2, 1.2]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)

    load_case_factors = [1, 1, 0.95] + [1.2] * len(wind_cases) + [1.1] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    # Add the first load envelope (serviceability)
    ret = load.CreateLoadEnvelop(envelopeId, ENVELOP_TYPE_MAP["SERVICEABILITY"], comboLoadCases[startingComboCaseIdIndex:])
    envelopCreationResults.append({"envelopeId": envelopeId, "envelopeType": "SERVICEABILITY", "loadCases": comboLoadCases[startingComboCaseIdIndex:], "result": ret})
    envelopeId += 1
    startingComboCaseIdIndex = len(comboLoadCases)

    load_case_factors = [1, 1, 0.95] + [1.0] * len(wind_cases) + [1.2] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    load_case_factors = [1, 1, 0.95] + [1.2] * len(wind_cases) + [1.1] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    load_case_factors = [1, 1, 0.95] + [1.2] * len(wind_cases) + [1.1] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    # Add the next load envelope (strength)
    ret = load.CreateLoadEnvelop(envelopeId, ENVELOP_TYPE_MAP["STRENGTH"], comboLoadCases[startingComboCaseIdIndex:])
    envelopCreationResults.append({"envelopeId": envelopeId, "envelopeType": "STRENGTH", "loadCases": comboLoadCases[startingComboCaseIdIndex:], "result": ret})
    envelopeId += 1
    startingComboCaseIdIndex = len(comboLoadCases)

    load_case_factors = [1, 1, 0.95] + [1.3] * len(wind_cases) + [1.4] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    load_case_factors = [1, 1, 0.95] + [0.9] * len(wind_cases) + [0.9] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    load_case_factors = [1, 1, 0.95] + [1.5] * len(wind_cases) + [1.5] * 5 + [1.5, 1.5]
    currentLoadCaseId = staad_common._create_combo_load_case(load, currentLoadCaseId, f"Comb DL LL WL {len(comboLoadCases) + 1}", comboLoadCases, primary_load_cases, load_case_factors)
    # Add the next load envelope (strength)
    ret = load.CreateLoadEnvelop(envelopeId, ENVELOP_TYPE_MAP["STRENGTH"], comboLoadCases[startingComboCaseIdIndex:])
    envelopCreationResults.append({"envelopeId": envelopeId, "envelopeType": "STRENGTH", "loadCases": comboLoadCases[startingComboCaseIdIndex:], "result": ret})
    envelopeId += 1
    startingComboCaseIdIndex = len(comboLoadCases)

    print(f'Envelope creation results: {envelopCreationResults}')
    #load.AddLoadCasesToEnvelop(envelopeId-1, comboLoadCases)
    # TODO: Here we define the load envelope case IDs to be included in the load envelope list.
    loadEnvelopeCaseIds = [envelopeId-1] # pick the last one
    ret = load.CreateLoadList(LOAD_LIST_TYPE_MAP["load_envelope_list"], loadEnvelopeCaseIds)

    # create design parameters
    create_design_parameters(design, grid_spec, members, longitudinal_beams, transverse_beams, columns, longitudinal_bracings, transverse_bracings)

    return {"applied": load.GetPrimaryLoadCaseCount() + load.GetLoadCombinationCaseCount(), "errors": errors, "envelopCreationResults": envelopCreationResults}

def is_node_on_beams(node: int, beam_ids: list[int], beam_node_map: Dict[int, Tuple[int, int]]) -> bool:
    for beam_id in beam_ids:
        if node in beam_node_map[beam_id]:
            return True
    return False

def add_to_group(groups: dict, group_name: str, node: int, joint_member_ends: Dict[int, List[Tuple[int, int]]], member_data_by_id: dict[int, dict]) -> None:
    groups.setdefault(group_name, {
        "member_count": len(joint_member_ends[node]),
        "joints": [],
    })["joints"].append({
        "node": node,
        "members": [
            {
                "beam": beam_id,
                "name": member_data_by_id[beam_id].get("name"),
                "tekla_name": member_data_by_id[beam_id].get("tekla_name"),
                "member_type": memberName2type(member_data_by_id[beam_id].get("name")),
                "is_mirrored": member_data_by_id[beam_id].get("is_mirrored", False),
                "end": member_end,
            }
            for beam_id, member_end in joint_member_ends[node]
        ],
    })
    return groups

def find_num_bracings(node: int, joint_member_ends: Dict[int, List[Tuple[int, int]]], longitudinal_bracings: list[int], transverse_bracings: list[int]) -> Tuple[int, int]:
    beam_id_node_ends = joint_member_ends[node]
    num_longitudinal_bracings = 0
    num_transverse_bracings = 0
    for beam_id, start_or_end in beam_id_node_ends:
        if beam_id in longitudinal_bracings:
            num_longitudinal_bracings += 1
        elif beam_id in transverse_bracings:
            num_transverse_bracings += 1
    return num_longitudinal_bracings, num_transverse_bracings

def group_name_suffix_from_bracings(num_longitudinal_bracings: int, num_transverse_bracings: int) -> str:
        group_name = "no_bracing"
        if num_longitudinal_bracings == 1 and num_transverse_bracings == 0:
            group_name = f"1_longitudinal_bracing"
        elif num_longitudinal_bracings == 2 and num_transverse_bracings == 0:
            group_name = f"2_longitudinal_bracings"
        elif num_longitudinal_bracings == 0 and num_transverse_bracings == 1:
            group_name = f"1_transverse_bracing"
        elif num_longitudinal_bracings == 0 and num_transverse_bracings == 2:
            group_name = f"2_transverse_bracings"
        elif num_longitudinal_bracings == 1 and num_transverse_bracings == 1:
            group_name = f"1_longitudinal_1_transverse_bracing"
        elif num_longitudinal_bracings == 2 and num_transverse_bracings == 1:
            group_name = f"2_longitudinal_1_transverse_bracing"
        elif num_longitudinal_bracings == 1 and num_transverse_bracings == 2:
            group_name = f"1_longitudinal_2_transverse_bracings"
        elif num_longitudinal_bracings == 2 and num_transverse_bracings == 2:
            group_name = f"2_longitudinal_2_transverse_bracings"
        return group_name

def _create_joint_groups(joint_member_ends: Dict[int, List[Tuple[int, int]]], member_data_by_id: dict[int, dict], node_cache: Dict[tuple, int], beam_node_map: Dict[int, Tuple[int, int]], support_nodes: Set[int]) -> dict:
    """Return joint groups based on the types and numbers of connected members.
    """
    members = list(member_data_by_id.values())
    top_nodes = list(set(node_cache.values()) - set(support_nodes))
    corner_nodes = _get_corner_nodes(node_cache, top_nodes)
    edge_nodes = _get_edge_nodes(node_cache, top_nodes)
    inner_nodes = _get_inner_nodes(node_cache, top_nodes)
    edge_members = _get_edge_members(members, node_cache, top_nodes)
    longitudinal_beams = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "beam" and m.get("name", "").startswith("beam-x"), members)))
    transverse_beams = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "beam" and m.get("name", "").startswith("beam-y"), members)))
    longitudinal_edge_members = sorted(set(longitudinal_beams) & set(edge_members))
    transverse_edge_members = sorted(set(transverse_beams) & set(edge_members))
    columnObjs = list(filter(lambda m: m.get("type", "").lower() == "column", members))
    columns = list(map(lambda m: m["id"], columnObjs))
    longitudinal_bracings = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "longitudinal_brace", members)))
    transverse_bracings = list(map(lambda m: m["id"], filter(lambda m: m.get("type", "").lower() == "transverse_brace", members)))

    groups = {}
    # 1. All support nodes
    for support_node in support_nodes:
        num_longitudinal_bracings, num_transverse_bracings = find_num_bracings(support_node, joint_member_ends, longitudinal_bracings, transverse_bracings)
        group_name = f"support_node_{group_name_suffix_from_bracings(num_longitudinal_bracings, num_transverse_bracings)}"
        add_to_group(groups, group_name, support_node, joint_member_ends, member_data_by_id)
    # 2. All corner nodes
    for corner_node in corner_nodes:
        group_name = f"corner_node"
        # Assumption: corner nodes are treated as a separate group regardless of the number of connected members
        # No bracings attach to corner nodes
        add_to_group(groups, group_name, corner_node, joint_member_ends, member_data_by_id)
    edge_nodes_except_corners = set(edge_nodes) - set(corner_nodes)
    # 3. Longitudinal edge nodes
    longitudinal_edge_nodes = [node for node in edge_nodes_except_corners if is_node_on_beams(node, longitudinal_edge_members, beam_node_map)]
    for edge_node in longitudinal_edge_nodes:
        # Joint with 4 members (no bracing) by default, will update if more members are connected
        num_longitudinal_bracings, num_transverse_bracings = find_num_bracings(edge_node, joint_member_ends, longitudinal_bracings, transverse_bracings)
        group_name = f"longitudinal_edge_node_{group_name_suffix_from_bracings(num_longitudinal_bracings, num_transverse_bracings)}"
        add_to_group(groups, group_name, edge_node, joint_member_ends, member_data_by_id)
    # 4. Transverse edge nodes
    transverse_edge_nodes = [node for node in edge_nodes_except_corners if is_node_on_beams(node, transverse_edge_members, beam_node_map)]
    for edge_node in transverse_edge_nodes:
        # Joint with 4 members (no bracing) by default, will update if more members are connected
        num_longitudinal_bracings, num_transverse_bracings = find_num_bracings(edge_node, joint_member_ends, longitudinal_bracings, transverse_bracings)
        group_name = f"transverse_edge_node_{group_name_suffix_from_bracings(num_longitudinal_bracings, num_transverse_bracings)}"
        add_to_group(groups, group_name, edge_node, joint_member_ends, member_data_by_id)
    # 5. Inner nodes: All to nodes not classified as edge or corner nodes
    # inner nodes are already determined, hence no need to recalculate them
    #inner_nodes = set(top_nodes) - set(edge_nodes_except_corners) - set(corner_nodes)
    for node in inner_nodes:
        num_longitudinal_bracings, num_transverse_bracings = find_num_bracings(node, joint_member_ends, longitudinal_bracings, transverse_bracings)
        group_name = f"inner_node_{group_name_suffix_from_bracings(num_longitudinal_bracings, num_transverse_bracings)}"
        add_to_group(groups, group_name, node, joint_member_ends, member_data_by_id)

    return groups

'''
Create a summary of forces/displacements for member/node in the staad style:
Given a list of member id/node id and its forces/displacements in a load case, this function returns max/min values for each component.
'''
def summarize_forces_displacements(data: dict[int, list[list[float]]]) -> Dict[str, Any]:
    entity_values = list(data.values())[0][0] if data else []
    num_components = len(entity_values)
    #print("id_key:", id_key, "value_key:", value_key, "Initial entity values:", entity_values)
    
    ret = []
    num_entities = len(list(data.values())[0])
    for i in range(num_entities):
        summary = {"max": [float("-inf") for _ in range(num_components)], "min": [float("inf") for _ in range(num_components)],
                   "max_ids": [None for _ in range(num_components)], "min_ids": [None for _ in range(num_components)]}
        for entity_id, entity_entry in data.items():
            entity_values = entity_entry[i]
            for index in range(len(entity_values)):
                value = entity_values[index]
                if value > summary["max"][index]:
                    summary["max"][index] = value
                    summary["max_ids"][index] = entity_id
                if value < summary["min"][index]:
                    summary["min"][index] = value
                    summary["min_ids"][index] = entity_id
        ret.append(summary)
    return ret

'''
This function traverses one level from the given node to find connected members and their corresponding start or end nodes.
Then it finds forces applied on those members at the corresponding start or end nodes.
'''
def find_connected_members_forces(node_id: int, joint_member_ends: Dict[int, List[Tuple[int, int]]], forces: dict[int, list[list[float]]]) -> List[Dict[str, Any]]:
    connected_forces: List[Dict[str, Any]] = []
    for beam_id, start_or_end in joint_member_ends[node_id]:
        connected_forces.append({"beam": beam_id, "end": start_or_end, "forces": forces[beam_id][start_or_end]})
    return connected_forces

# Force components whose max/min decide the governing load cases for a joint's connection design, per member type.
JOINT_DESIGN_FORCE_COMPONENTS: Dict[str, List[str]] = {
    "beam_longitudinal": ["Fx", "Fy", "Fz"],
    "beam_transverse": ["Fx", "Fy", "Fz"],
    "bracing_longitudinal": ["Fx"],
    "bracing_transverse": ["Fx"],
    "bracing_longitudinal_mirrored": ["Fx"],
    "bracing_transverse_mirrored": ["Fx"],
}
IDEA_STATICA_FORCE_NAMES = ["n", "vy", "vz", "mx", "my", "mz"]

'''
For one joint group, find the load case and joint at which each governing force component
(JOINT_DESIGN_FORCE_COMPONENTS) reaches its max and min over the given load cases.
Only the force at the joint's own end of each member is considered, never the member's far end.
Returns {member_type: {"max_Fx": {"value", "load_case", "node", "beam", "end"}, "min_Fx": {...}, ...}}.
Member types not present in the group are omitted.
'''
def find_joint_group_governing_forces(joint_group: Dict[str, Any], forces_by_load_case: Dict[Any, dict[int, list[list[float]]]], load_cases: List[Any], component_map: Dict[str, List[str]] = JOINT_DESIGN_FORCE_COMPONENTS) -> Dict[str, Dict[str, Dict[str, Any]]]:
    force_components = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    governing: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for lc in load_cases:
        forces = forces_by_load_case[lc]
        for joint in joint_group["joints"]:
            for member in joint["members"]:
                member_type = member["member_type"]
                if member_type not in component_map:
                    continue
                values = forces[member["beam"]][member["end"]]
                extremes = governing.setdefault(member_type, {})
                for component in component_map[member_type]:
                    value = values[force_components.index(component)]
                    for extreme, is_better in (("max", lambda new, old: new > old), ("min", lambda new, old: new < old)):
                        key = f"{extreme}_{component}"
                        if key not in extremes or is_better(value, extremes[key]["value"]):
                            extremes[key] = {"value": value, "load_case": lc, "node": joint["node"],
                                             "beam": member["beam"], "end": member["end"]}
    return governing

'''
Build an IDEA StatiCa load effect from the forces at the joint end of every member of the joint.
Members of the same type are told apart by "position": "Begin" for the member starting at the joint (end 0),
"End" for the member ending at it (end 1).
'''
def joint_load_effect(joint: Dict[str, Any], forces: dict[int, list[list[float]]], name: str) -> Dict[str, Any]:
    member_loadings = []
    for member in joint["members"]:
        values = forces[member["beam"]][member["end"]]
        member_loadings.append({
            "member_name": member["member_type"],
            **dict(zip(IDEA_STATICA_FORCE_NAMES, values)),
            "position": "Begin" if member["end"] == 0 else "End",
        })
    return {"name": name, "member_loadings": member_loadings}

'''
For one joint group and one envelope, return the governing forces and one IDEA StatiCa load effect per unique
(load case, joint) pair at which a governing max/min occurs. "governs" lists the extremes each load effect covers.
'''
def build_joint_group_load_effects(joint_group: Dict[str, Any], forces_by_load_case: Dict[Any, dict[int, list[list[float]]]], load_cases: List[Any], load_case_titles: Dict[Any, str]) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], List[Dict[str, Any]]]:
    governing = find_joint_group_governing_forces(joint_group, forces_by_load_case, load_cases)
    joints_by_node = {joint["node"]: joint for joint in joint_group["joints"]}
    load_effects_by_key: Dict[Tuple[Any, int], Dict[str, Any]] = {}
    for member_type, extremes in governing.items():
        for extreme_key, extreme in extremes.items():
            key = (extreme["load_case"], extreme["node"])
            if key not in load_effects_by_key:
                lc, node = key
                load_effect = joint_load_effect(joints_by_node[node], forces_by_load_case[lc],
                                                f"LC{lc} node {node} - {load_case_titles.get(lc, '')}")
                load_effect["load_case"] = lc
                load_effect["node"] = node
                load_effect["governs"] = []
                load_effects_by_key[key] = load_effect
            load_effects_by_key[key]["governs"].append(f"{member_type} {extreme_key}")
    return governing, list(load_effects_by_key.values())

'''
Calculate the max/min of forces for each component and assign other elements accordingly.
This function is typically used to summarize the extreme values and their corresponding entities for further analysis or reporting.
'''
def find_extreme_forces(forces: dict[int, list[list[float]]], forces_summary: list[Dict[str, Any]], summary_key: str, force_key: str, beam_node_map: Dict[int, Tuple[int, int]], lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any], ends: List[int] = [0, 1]) -> Dict[str, Any]:
    force_components = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    force_key_index = force_components.index(force_key)
    summary_value_key = f'{summary_key}_{force_key}'
    for start_or_end in ends:
        comparison = forces_summary[start_or_end][summary_key][force_key_index] > summary_results["forces"][summary_value_key][force_key] if summary_key == "max" else forces_summary[start_or_end][summary_key][force_key_index] < summary_results["forces"][summary_value_key][force_key]
        if comparison:
            beam_no = forces_summary[start_or_end][f"{summary_key}_ids"][force_key_index]
            for comp in force_components:
                summary_results["forces"][summary_value_key][comp] = forces[beam_no][start_or_end][force_components.index(comp)]
            summary_results["forces"][summary_value_key][force_key] = forces_summary[start_or_end][summary_key][force_key_index]
            summary_results["forces"][summary_value_key]["beam"] = beam_no
            summary_results["forces"][summary_value_key]["node"] = beam_node_map[beam_no][start_or_end]
            summary_results["forces"][summary_value_key]["load_case"] = lc
            summary_results["forces"][summary_value_key]["load_case_title"] = lc_title
            summary_results["forces"][summary_value_key]["connected_forces"] = find_connected_members_forces(beam_node_map[beam_no][start_or_end], joint_member_ends, forces)
    return summary_results

'''
Calculate the max/min of member displacements for each component and assign other elements accordingly.
This function is typically used to summarize the extreme values and their corresponding entities for further analysis or reporting.
'''
def find_extreme_member_displacements(member_displacements: dict[int, list[list[float]]], member_displacements_summary: list[Dict[str, Any]], summary_key: str, displacement_key: str, beam_node_map: Dict[int, Tuple[int, int]], lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any]) -> Dict[str, Any]:
    displacement_components = ["X", "Y", "Z"]
    displacement_key_index = displacement_components.index(displacement_key)
    summary_value_key = f'{summary_key}_{displacement_key}'
    ends = [0, 1]
    for start_or_end in ends:
        comparison = member_displacements_summary[start_or_end][summary_key][displacement_key_index] > summary_results["member_displacements"][summary_value_key][displacement_key] if summary_key == "max" else member_displacements_summary[start_or_end][summary_key][displacement_key_index] < summary_results["member_displacements"][summary_value_key][displacement_key]
        if comparison:
            beam_no = member_displacements_summary[start_or_end][f"{summary_key}_ids"][displacement_key_index]
            for comp in displacement_components:
                summary_results["member_displacements"][summary_value_key][comp] = member_displacements[beam_no][start_or_end][displacement_components.index(comp)]
            summary_results["member_displacements"][summary_value_key][displacement_key] = member_displacements_summary[start_or_end][summary_key][displacement_key_index]
            summary_results["member_displacements"][summary_value_key]["beam"] = beam_no
            summary_results["member_displacements"][summary_value_key]["node"] = beam_node_map[beam_no][start_or_end]
            summary_results["member_displacements"][summary_value_key]["load_case"] = lc
            summary_results["member_displacements"][summary_value_key]["load_case_title"] = lc_title
    return summary_results

'''
Calculate the max/min of displacements for each component and assign other elements accordingly.
This function is typically used to summarize the extreme values and their corresponding entities for further analysis or reporting.
'''
def find_extreme_node_displacements(displacements: dict[int, list[list[float]]], displacements_summary: list[Dict[str, Any]], summary_key: str, displacement_key: str, lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any]) -> Dict[str, Any]:
    displacement_components = ["X", "Y", "Z", "rX", "rY", "rZ"]
    displacement_key_index = displacement_components.index(displacement_key)
    summary_value_key = f'{summary_key}_{displacement_key}'
    comparison = displacements_summary[0][summary_key][displacement_key_index] > summary_results["displacements"][summary_value_key][displacement_key] if summary_key == "max" else displacements_summary[0][summary_key][displacement_key_index] < summary_results["displacements"][summary_value_key][displacement_key]
    if comparison:
        node_no = displacements_summary[0][f"{summary_key}_ids"][displacement_key_index]
        for comp in displacement_components:
            summary_results["displacements"][summary_value_key][comp] = displacements[node_no][0][displacement_components.index(comp)]
        summary_results["displacements"][summary_value_key][displacement_key] = displacements_summary[0][summary_key][displacement_key_index]
        summary_results["displacements"][summary_value_key]["node"] = node_no
        summary_results["displacements"][summary_value_key]["load_case"] = lc
        summary_results["displacements"][summary_value_key]["load_case_title"] = lc_title
    return summary_results

'''
Calculate the max/min of displacements for each component and assign other elements accordingly.
This function is typically used to summarize the extreme values and their corresponding entities for further analysis or reporting.
'''
def find_extreme_support_reactions(support_reactions: dict[int, list[list[float]]], support_reactions_summary: list[Dict[str, Any]], summary_key: str, force_key: str, lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any]) -> Dict[str, Any]:
    force_components = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    force_key_index = force_components.index(force_key)
    summary_value_key = f'{summary_key}_{force_key}'
    comparison = support_reactions_summary[0][summary_key][force_key_index] > summary_results["support_reactions"][summary_value_key][force_key] if summary_key == "max" else support_reactions_summary[0][summary_key][force_key_index] < summary_results["support_reactions"][summary_value_key][force_key]
    if comparison:
        node_no = support_reactions_summary[0][f"{summary_key}_ids"][force_key_index]
        for comp in force_components:
            summary_results["support_reactions"][summary_value_key][comp] = support_reactions[node_no][0][force_components.index(comp)]
        summary_results["support_reactions"][summary_value_key][force_key] = support_reactions_summary[0][summary_key][force_key_index]
        summary_results["support_reactions"][summary_value_key]["node"] = node_no
        summary_results["support_reactions"][summary_value_key]["load_case"] = lc
        summary_results["support_reactions"][summary_value_key]["load_case_title"] = lc_title
    return summary_results

def initialize_summary_results() -> Dict[str, Any]:
    summary_results = {
                        "displacements": {"max_X": {"node": None, "load_case": None, "load_case_title": None, "X": float("-inf"), "Y": None, "Z": None, "rX": None, "rY": None, "rZ": None},
                                          "min_X": {"node": None, "load_case": None, "load_case_title": None, "X": float("inf"), "Y": None, "Z": None, "rX": None, "rY": None, "rZ": None},
                                          "max_Y": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": float("-inf"), "Z": None, "rX": None, "rY": None, "rZ": None},
                                          "min_Y": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": float("inf"), "Z": None, "rX": None, "rY": None, "rZ": None},
                                          "max_Z": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": float("-inf"), "rX": None, "rY": None, "rZ": None},
                                          "min_Z": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": float("inf"), "rX": None, "rY": None, "rZ": None},
                                          "max_rX": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": None, "rX": float("-inf"), "rY": None, "rZ": None},
                                          "min_rX": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": None, "rX": float("inf"), "rY": None, "rZ": None},
                                          "max_rY": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": None, "rX": None, "rY": float("-inf"), "rZ": None},
                                          "min_rY": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": None, "rX": None, "rY": float("inf"), "rZ": None},
                                          "max_rZ": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": None, "rX": None, "rY": None, "rZ": float("-inf")},
                                          "min_rZ": {"node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": None, "rX": None, "rY": None, "rZ": float("inf")}
                        },
                        "member_displacements": {"max_X": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "X": float("-inf"), "Y": None, "Z": None},
                                          "min_X": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "X": float("inf"), "Y": None, "Z": None},
                                          "max_Y": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "X": None, "Y": float("-inf"), "Z": None},
                                          "min_Y": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "X": None, "Y": float("inf"), "Z": None},
                                          "max_Z": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": float("-inf")},
                                          "min_Z": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "X": None, "Y": None, "Z": float("inf")},
                        },
                        "forces": {"max_Fx": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": float("-inf"), "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": None},
                                  "min_Fx": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": float("inf"), "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": None},
                                  "max_Fy": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": float("-inf"), "Fz": None, "Mx": None, "My": None, "Mz": None},
                                  "min_Fy": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": float("inf"), "Fz": None, "Mx": None, "My": None, "Mz": None},
                                  "max_Fz": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": float("-inf"), "Mx": None, "My": None, "Mz": None},
                                  "min_Fz": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": float("inf"), "Mx": None, "My": None, "Mz": None},
                                  "max_Mx": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": float("-inf"), "My": None, "Mz": None},
                                  "min_Mx": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": float("inf"), "My": None, "Mz": None},
                                  "max_My": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": float("-inf"), "Mz": None},
                                  "min_My": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": float("inf"), "Mz": None},
                                  "max_Mz": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": float("-inf")},
                                  "min_Mz": {"beam": None, "node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": float("inf")},
                        },
                        "support_reactions": {"max_Fx": {"node": None, "load_case": None, "load_case_title": None, "Fx": float("-inf"), "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": None},
                                               "min_Fx": {"node": None, "load_case": None, "load_case_title": None, "Fx": float("inf"), "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": None},
                                               "max_Fy": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": float("-inf"), "Fz": None, "Mx": None, "My": None, "Mz": None},
                                               "min_Fy": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": float("inf"), "Fz": None, "Mx": None, "My": None, "Mz": None},
                                               "max_Fz": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": float("-inf"), "Mx": None, "My": None, "Mz": None},
                                               "min_Fz": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": float("inf"), "Mx": None, "My": None, "Mz": None},
                                               "max_Mx": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": float("-inf"), "My": None, "Mz": None},
                                               "min_Mx": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": float("inf"), "My": None, "Mz": None},
                                               "max_My": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": float("-inf"), "Mz": None},
                                               "min_My": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": float("inf"), "Mz": None},
                                               "max_Mz": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": float("-inf")},
                                               "min_Mz": {"node": None, "load_case": None, "load_case_title": None, "Fx": None, "Fy": None, "Fz": None, "Mx": None, "My": None, "Mz": float("inf")}
                        }
                    }
    return summary_results

def initialize_forces_summary_results(force_types: list[str]) -> Dict[str, Any]:
    summary_results = {"forces": {}}
    all_force_types = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    for force_type in force_types:
        summary_results["forces"][f"max_{force_type}"] = {"beam": None, "node": None, "load_case": None, "load_case_title": None, force_type: float("-inf")}
        summary_results["forces"][f"min_{force_type}"] = {"beam": None, "node": None, "load_case": None, "load_case_title": None, force_type: float("inf")}
        for other_force_type in all_force_types:
            if other_force_type != force_type:
                summary_results["forces"][f"max_{force_type}"][other_force_type] = None
                summary_results["forces"][f"min_{force_type}"][other_force_type] = None
    return summary_results

def compute_summary_results(forces, forces_summary,
                            support_reactions, support_reactions_summary, beam_node_map, node_displacements,
                            node_displacements_summary, member_displacements, member_displacements_summary,
                            lc, lc_title, joint_member_ends, summary_results) -> None:
    summary_keys = ["max", "min"]
    force_components = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    node_displacement_components = ["X", "Y", "Z", "rX", "rY", "rZ"]
    member_displacement_components = ["X", "Y", "Z"]

    # Compute extreme forces for start and end of members
    for component in force_components:
        for extreme in summary_keys:
            find_extreme_forces(forces, forces_summary, extreme, component, beam_node_map, lc, lc_title, joint_member_ends, summary_results)
            find_extreme_support_reactions(support_reactions, support_reactions_summary, extreme, component, lc, lc_title, joint_member_ends, summary_results)
    for component in node_displacement_components:
        for extreme in summary_keys:
            find_extreme_node_displacements(node_displacements, node_displacements_summary, extreme, component, lc, lc_title, joint_member_ends, summary_results)

    for component in member_displacement_components:
        for extreme in summary_keys:
            find_extreme_member_displacements(member_displacements, member_displacements_summary, extreme, component, beam_node_map, lc, lc_title, joint_member_ends, summary_results)

async def send_to_staad(data: dict) -> dict:
    """
    Send every member in data["members_split_beams"] to the currently
    running STAAD.Pro model, then apply data.get("load_configuration")
    if present.

    Parameters
    ----------
    data : dict
        Expected to contain "members_split_beams" (a list of member
        dicts, each with p1, p2, profile, material, type, name — the
        same shape generate_grid.py produces) and optionally
        "load_configuration" (see the module docstring's LOAD
        CONFIGURATION SCHEMA).

    Returns
    -------
    dict
        {
          "created": <int>,               # members with geometry (node+beam) successfully created
          "failed": <int>,                 # members whose geometry creation itself failed
          "errors": [{"name": ..., "error": ...}, ...],
          "property_assignment_failed": <int>,  # created members whose profile/material assignment failed
          "property_errors": [{"name": ..., "error": ...}, ...],
          "load_cases_applied": <int>,     # load cases that succeeded
          "load_cases_failed": <int>,
          "load_errors": [{"title": ..., "error": ...}, ...],
        }

    Note: geometry creation and property/material assignment are
    tracked separately on purpose. A member whose beam+nodes were
    created successfully but whose profile/material assignment then
    failed still counts toward "created" (the geometry is genuinely
    there) — it's only flagged in "property_errors", not "errors". Prior
    to this, a property-assignment failure incorrectly marked the whole
    member as failed even when its geometry had already landed.
    """
    # Clean the STAAD.Pro model before adding new members
    clean_staad()
    members = data.get("members_split_beams", [])
    load_configuration = data.get("load_configuration")
    grid_spec = data.get("grid")

    openstaad = staad_common._get_openstaad()
    openstaad.SetInputUnitForLength(length_unit_meter)
    openstaad.SetInputUnitForForce(force_unit_kilonewton)
    geometry = openstaad.Geometry
    property = openstaad.Property
    load = openstaad.Load
    support = openstaad.Support
    command = openstaad.Command
    design = openstaad.Design

    end_my_mz_release_spec_id = property.CreateMemberReleaseSpec(1, [0, 0, 0, 0, 1, 1], [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    truss_spec_id = property.CreateMemberTrussSpec()
    #property.AssignMemberSpecToBeam([900001, 900002, 900003], end_my_mz_release_spec_id)
    #property.AssignMemberSpecToBeam([900148, 900154, 900160, 900166], truss_spec_id)

    # Mapping of X, Y, Z co-ordinates to node Ids
    # Key: (x, y, z) tuple, Value: node ID
    node_cache: Dict[tuple, int] = {}
    # Node ID allocator for generating unique node IDs
    node_ids = staad_common._SequentialIdAllocator(STARTING_NODE_ID)
    # Beam ID allocator for generating unique beam IDs
    beam_ids = staad_common._SequentialIdAllocator(STARTING_BEAM_ID)
    # Mapping of member names to beam IDs:
    # beam names: column-j-i, beam-x-j-i for longitudinal beams, beam-y-j-i for transverse beams, 
    # bracing-long-j-bay, bracing-trans-i-bay
    # where j ranges from 1 to number transverse bays and i ranges from 1 to number longitudinal bays
    # for bracing-long-j-bay, j ranges from 1 to number transverse bays
    # for bracing-trans-i-bay, i ranges from 1 to number longitudinal bays
    # Key: member name, Value: beam ID
    beam_by_name: Dict[str, int] = {}
    # key: member id, value: member data including name
    member_data_by_id: Dict[int, Dict] = {}
    # Set of nodes that have been assigned supports: Bottom nodes of columns
    support_nodes: Set[int] = set()
    # mapping of member types to their corresponding beam IDs
    # column, beam, longitudinal_brace, transverse_brace
    member_ids_by_type: Dict[str, List[int]] = {}
    # Key: node ID, Value: list of tuples (beam ID, start_or_end)
    # start_or_end: 0 for start node, 1 for end node
    joint_member_ends: Dict[int, List[Tuple[int, int]]] = {}
    # Mapping of beam IDs to their start and end node IDs
    beam_node_map: Dict[int, Tuple[int, int]] = {}  # maps beam_id to its start and end node IDs

    created = 0
    beam_no = 0
    errors = []
    property_errors = []
    property_id_cache: Dict[str, int] = {}
    country = load_configuration.get("country", "EUROPE")

    fixed_support_id = support.CreateSupportFixed()
    for member in members:
        try:
            beam_no, n1, n2 = staad_common._create_beam(geometry, node_cache, node_ids, beam_ids, member)
            beam_by_name[member.get("name", "")] = beam_no
            member["id"] = beam_no
            member["start"] = n1
            member["end"] = n2
            member_data_by_id[beam_no] = member
            created += 1
            joint_member_ends.setdefault(n1, []).append((beam_no, start_end))
            joint_member_ends.setdefault(n2, []).append((beam_no, final_end))
            beam_node_map[beam_no] = (n1, n2)
        except Exception as exc:  # keep going; report this member and move on
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})
            continue  # nothing to assign a property to if geometry creation failed

        try:
            staad_common._assign_property(property, beam_no, member, property_id_cache, country)
        except Exception as exc:
            property_errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

        memberType = memberName2type(member.get("name", "")).lower()
        if memberType not in member_ids_by_type:
            member_ids_by_type[memberType] = []
        member_ids_by_type[memberType].append(beam_no)

        if memberType == "column":
            property.AssignMemberSpecToBeam([beam_no], end_my_mz_release_spec_id)
            if n1 not in support_nodes:
                support._support.AssignSupportToNode(int(n1), fixed_support_id)
                support_nodes.add(n1)
        elif memberType in ["bracing_longitudinal", "bracing_transverse", "bracing_longitudinal_mirrored", "bracing_transverse_mirrored"]:
            property.AssignMemberSpecToBeam([beam_no], truss_spec_id)

    load_applied = 0
    load_errors: List[Dict[str, str]] = []
    if load_configuration:
        load_result = _apply_load_configuration(
            load, design, node_cache, load_configuration, members, support_nodes, grid_spec
        )
        load_applied = load_result["applied"]
        load_errors = load_result["errors"]
        load_envelopes = load_result.get("envelopCreationResults", [])

    # Update structure before analysis
    openstaad.UpdateStructure()
    await asyncio.sleep(2)

    print("Starting PerformAnalysis...")
    command.PerformAnalysis(6)
    await asyncio.sleep(4)  # give STAAD.Pro a moment to complete the analysis
    openstaad.SaveModel(True)
    await asyncio.sleep(2)

    staad_update_status = {
        "created": created,
        "failed": len(errors),
        "errors": errors,
        "property_assignment_failed": len(property_errors),
        "property_errors": property_errors,
        "load_envelopes": load_envelopes,
        "load_cases_applied": load_applied,
        "load_cases_failed": len(load_errors),
        "load_errors": load_errors,
        "beam_by_name": beam_by_name,
        "member_data_by_id": member_data_by_id,
        "support_nodes": list(support_nodes),
        "member_ids_by_type": member_ids_by_type,
        "joint_member_ends": joint_member_ends,
        "beam_node_map": beam_node_map,
        "joint_groups": _create_joint_groups(
            joint_member_ends, member_data_by_id, node_cache, beam_node_map, support_nodes
        ),
    }
    
    # Start analysis
    print("Starting Analyze...")
    useSynchronousAnalysis = False
    if useSynchronousAnalysis:
        analysis_status = openstaad.AnalyzeEx(1, 1, 1)
        print(f"Initial analysis status: {analysis_status}")
        if analysis_status not in [2, 3]:
            staad_update_status["analysis_failed"] = True
            analysis_status = openstaad.GetAnalysisStatus()
            staad_update_status["analysis_status"] = analysis_status
            return staad_update_status

        while openstaad.IsAnalyzing():
            print("...Analyzing")
            await asyncio.sleep(2)
        print("Analysis complete.")
    else:
        openstaad.Analyze()
        # Wait for analysis to complete with timeout
        analysis_deadline = time.time() + 300

        while openstaad.IsAnalyzing():
            if time.time() > analysis_deadline:
                staad_update_status["analysis_timeout"] = True
                return staad_update_status

            print("...Analyzing")
            await asyncio.sleep(2)

        await asyncio.sleep(3)

    # Check analysis status
    analysis_status = openstaad.GetAnalysisStatus()
    staad_update_status["analysis_status"] = analysis_status

    try:
        staad_update_status["errors"] = openstaad.GetAnalysisErrorMessages()
    except Exception as e:
        staad_update_status["errors"] = []
        print(f"Failed to extract analysis error messages: {e}")
    try:
        staad_update_status["warnings"] = openstaad.GetAnalysisWarningMessages()
    except Exception as e:
        staad_update_status["warnings"] = []
        print(f"Failed to extract analysis warning messages: {e}")
    print(f"Analysis status: {analysis_status}, errors: {staad_update_status['errors']}, warnings: {staad_update_status['warnings']}")
    print("Analysis results extracted and status updated.")

    if analysis_status["ReturnValue"] not in [2, 3]:
        staad_update_status["analysis_failed"] = True
        return staad_update_status

    await asyncio.sleep(3)

    staad_update_status["analysis_complete"] = True

    # Extract results
    output = openstaad.Output
    base_unit = openstaad.GetBaseUnit()
    if base_unit not in ("English", "Metric"):
        raise ValueError(f"Unsupported STAAD base unit system: {base_unit}")
    force_factor = 4.4482216152605 if base_unit == "English" else 1.0
    moments_factor = force_factor * 0.0254 if base_unit == "English" else 1.0

    displacement_unit = output.GetOutputUnitForDisplacement()
    force_unit = output.GetOutputUnitForForce()
    moment_unit = output.GetOutputUnitForMoment()
    rotation_unit = output.GetOutputUnitForRotation()
    print(f"Output units - Displacement unit: {displacement_unit}, Force unit: {force_unit}, Moment unit: {moment_unit}, Rotation unit: {rotation_unit}")

    # GetMemberEndForces returns FX, FY, FZ, MX, MY, and MZ (in order)
    results_local = load_configuration.get("results_local_or_global", 0)
    loadCaseMap = {load.GetLoadCaseTitle(lc): lc for lc in load.GetLoadCombinationCaseNumbers()} # GetPrimaryLoadCaseNumbers
    print(f"Load case map: {loadCaseMap}")

    joint_groups_member_types_map: dict[str, set[str]] = {}
    joint_groups_member_metadata_map: dict[str, list[dict[str, Any]]] = {}
    joint_groups = staad_update_status["joint_groups"]
    for joint_group_id, joint_group in joint_groups.items():
        joint_groups_member_types_map[joint_group_id] = set()
        joint_groups_member_metadata_map[joint_group_id] = []
        joint_members = joint_group["joints"][0]["members"]
        suffix_for_multiple_members_of_same_type: dict[str, int] = {}
        unique_joint_members: set[str] = set()
        for joint_member in joint_members:
            member_type = joint_member["member_type"]
            if member_type in joint_groups_member_types_map[joint_group_id] and member_type not in ['column', 'beam_transverse', 'beam_longitudinal']:
                suffix_for_multiple_members_of_same_type[member_type] = suffix_for_multiple_members_of_same_type.get(member_type, 0) + 1
            beam_id = joint_member["beam"]
            beam_profile = member_data_by_id[beam_id].get("profile")
            beam_material = member_data_by_id[beam_id].get("material")
            suffix_for_multiple_members = suffix_for_multiple_members_of_same_type.get(member_type, 0)
            member_name = member_type + (f"_{suffix_for_multiple_members}" if suffix_for_multiple_members > 0 else "")
            if member_name in unique_joint_members:
                continue
            unique_joint_members.add(member_name)
            ideastatica_member_update_data = {
                # IDEA StatiCa can't set aligned_plate/related_plate yet, and updating beam_transverse (even to the
                # same cross-section) corrupts them, so the template's placeholder member is updated instead.
                "member_name": "beam_transverse_placeholder" if member_type == "beam_transverse" else member_name,
                "cross_section_mprl_name": tekla2ideastatica_profile(beam_profile),
                "material_mprl_name": beam_material
            }
            if member_type.startswith('bracing_longitudinal'):
                column_height = grid_spec['col_height_mm']
                bay_long = grid_spec["bay_long_mm"]
                ideastatica_member_update_data["gama_pitch"] = -atan2(column_height, bay_long)*180/pi
            elif member_type.startswith('bracing_transverse'):
                column_height = grid_spec['col_height_mm']
                bay_trans = grid_spec["bay_trans_mm"]
                ideastatica_member_update_data["gama_pitch"] = -atan2(column_height, bay_trans)*180/pi
            joint_groups_member_metadata_map[joint_group_id].append(ideastatica_member_update_data)
            joint_groups_member_types_map[joint_group_id].add(member_type)

    summary_results = initialize_summary_results()
    for lc_title, lc in loadCaseMap.items():
        print(f"Processing load case: {lc_title} (ID: {lc})")
        start_forces = {line_id: output.GetMemberEndForces(int(line_id), start_end, int(lc), results_local) for line_id in beam_by_name.values()}
        end_forces = {line_id: [-1*x for x in output.GetMemberEndForces(int(line_id), final_end, int(lc), results_local)] for line_id in beam_by_name.values()}
        # Combine start and end forces for each member into a single dictionary entry
        # key: line_id, value: [start_forces, end_forces] 
        # with start_forces and end_forces being lists of member end forces Fx, Fy, Fz, Mx, My, and Mz respectively
        forces = {line_id: [start_forces[line_id], end_forces[line_id]] for line_id in beam_by_name.values()}

        support_reactions = {support_id: [output.GetSupportReactions(int(support_id), int(lc))] for support_id in support_nodes}

        member_start_displacements = {line_id: output.GetMemberEndDisplacements(int(line_id), start_end, int(lc)) for line_id in beam_by_name.values()}
        member_end_displacements = {line_id: output.GetMemberEndDisplacements(int(line_id), final_end, int(lc)) for line_id in beam_by_name.values()}
        # Combine start and end displacements for each member into a single dictionary entry
        # key: line_id, value: [member_start_displacements, member_end_displacements] 
        # with member_start_displacements and member_end_displacements being lists of displacements X, Y, Z respectively
        member_displacements = {line_id: [member_start_displacements[line_id], member_end_displacements[line_id]] for line_id in beam_by_name.values()}

        top_nodes = list(set(node_cache.values()) - set(support_nodes))
        node_displacements = {node_id: [output.GetNodeDisplacements(int(node_id), int(lc))] for node_id in top_nodes}

        # convert to kN and kN-m for forces and moments respectively
        for _, force_entries in forces.items():
            for i in range(len(force_entries[0])):
                force_entries[0][i] = force_entries[0][i] * (force_factor if i < 3 else moments_factor)
                force_entries[1][i] = force_entries[1][i] * (force_factor if i < 3 else moments_factor)
        for _, force_entries in support_reactions.items():
            for i in range(len(force_entries[0])):
                force_entries[0][i] = force_entries[0][i] * (force_factor if i < 3 else moments_factor)

        # convert displacements from inches to mm
        if displacement_unit.startswith("in"):
            for _, member_displacements_entries in member_displacements.items():
                for i in range(len(member_displacements_entries[0])):
                    member_displacements_entries[0][i] = member_displacements_entries[0][i] * 25.4
                    member_displacements_entries[1][i] = member_displacements_entries[1][i] * 25.4
            for _, node_displacements_entries in node_displacements.items():
                for i in range(len(node_displacements_entries[0])):
                    node_displacements_entries[0][i] = node_displacements_entries[0][i] * 25.4

        forces_summary = summarize_forces_displacements(forces)
        support_reactions_summary = summarize_forces_displacements(support_reactions)
        member_displacements_summary = summarize_forces_displacements(member_displacements)
        node_displacements_summary = summarize_forces_displacements(node_displacements)

        compute_summary_results(forces, forces_summary,
                            support_reactions, support_reactions_summary, beam_node_map, node_displacements,
                            node_displacements_summary, member_displacements, member_displacements_summary,
                            lc, lc_title, joint_member_ends, summary_results)

        staad_update_status[lc] = {}
        # results metadata
        staad_update_status[lc]["load_case_id"] = int(lc)
        staad_update_status[lc]["load_case_title"] = lc_title
        # store summarized results in the update status dictionary
        staad_update_status[lc]["summary_forces"] = forces_summary
        staad_update_status[lc]["summary_support_reactions"] = support_reactions_summary
        staad_update_status[lc]["summary_member_displacements"] = member_displacements_summary
        staad_update_status[lc]["summary_node_displacements"] = node_displacements_summary
        # store raw results in the update status dictionary
        staad_update_status[lc]["member_displacements"] = member_displacements
        staad_update_status[lc]["node_displacements"] = node_displacements
        staad_update_status[lc]["forces"] = forces
        staad_update_status[lc]["support_reactions"] = support_reactions

    # Find envelope wise summary of results
    envelope_summary_results: Dict[int, Any] = {}
    envelope_governing_forces_by_joint_group: Dict[int, Dict[str, Dict[str, Any]]] = {}
    envelope_load_cases_by_joint_group: Dict[int, Dict[str, Any]] = {}
    for envelopeDetail in load_envelopes:
        envelopeId = envelopeDetail["envelopeId"]
        envelopeType = envelopeDetail["envelopeType"]
        loadCases = envelopeDetail["loadCases"]
        print(f"Processing envelope {envelopeId} of type {envelopeType} with load cases {loadCases}.")
        env_summary_results = initialize_summary_results()
        for lc in loadCases:
            lc_title = load.GetLoadCaseTitle(lc)
            print(f"Processing load case {lc} {lc_title} in envelope {envelopeId}")
            forces = staad_update_status[lc]["forces"]
            member_displacements = staad_update_status[lc]["member_displacements"]
            node_displacements = staad_update_status[lc]["node_displacements"]
            support_reactions = staad_update_status[lc]["support_reactions"]
            forces_summary = staad_update_status[lc]["summary_forces"]
            support_reactions_summary = staad_update_status[lc]["summary_support_reactions"]
            member_displacements_summary = staad_update_status[lc]["summary_member_displacements"]
            node_displacements_summary = staad_update_status[lc]["summary_node_displacements"]
            compute_summary_results(forces, forces_summary,
                            support_reactions, support_reactions_summary, beam_node_map, node_displacements,
                            node_displacements_summary, member_displacements, member_displacements_summary,
                            lc, lc_title, joint_member_ends, env_summary_results)

        node_displacements_limits = {"min_X": abs(env_summary_results['displacements']['min_X']['X']),
                                     "max_X": abs(env_summary_results['displacements']['max_X']['X']),
                                     "min_Z": abs(env_summary_results['displacements']['min_Z']['Z']),
                                     "max_Z": abs(env_summary_results['displacements']['max_Z']['Z'])}
        node_displacements_limits_exceed_col_height_200 = {key: displacement_limit >= grid_spec.get('col_height_mm')/200 for key, displacement_limit in node_displacements_limits.items()}
        env_summary_results['node_displacements_limits'] = node_displacements_limits
        env_summary_results['node_displacements_limits_exceed_col_height_200'] = node_displacements_limits_exceed_col_height_200
        envelope_summary_results[envelopeId] = env_summary_results
        # Determine the governing load cases for each joint group
        # This prepares the payload for the idea statica API for each joint group and current envelope
        forces_by_load_case = {lc: staad_update_status[lc]["forces"] for lc in loadCases}
        load_case_titles = {lc: staad_update_status[lc]["load_case_title"] for lc in loadCases}
        load_cases_by_joint_group: dict[str, Any] = {}
        governing_forces_by_joint_group: dict[str, Any] = {}
        for joint_group_id, joint_group in joint_groups.items():
            governing, load_effects = build_joint_group_load_effects(joint_group, forces_by_load_case, loadCases, load_case_titles)
            governing_forces_by_joint_group[joint_group_id] = governing
            load_cases_by_joint_group[joint_group_id] = {"member_updates": joint_groups_member_metadata_map[joint_group_id],
                                                         "load_effects": load_effects,
                                                         "template_project_path": f'sample_projects/{joint_group_id}.ideaCon',
                                                         "connection_selector": {"name": "CON1"},
                                                         "output_dir": "output_CON1",
                                                         "save_project_path": "output_CON1/CON1_designed.ideaCon",
                                                         "operation_parameters": []}
        envelope_governing_forces_by_joint_group[envelopeId] = governing_forces_by_joint_group
        envelope_load_cases_by_joint_group[envelopeId] = load_cases_by_joint_group

    staad_update_status["min_fail_ratio"] = output.GetMemberSteelDesignMinFailureRatio()
    staad_update_status["max_fail_ratio"] = output.GetMemberSteelDesignMaxFailureRatio()
    member_ratios = {line_id: {"name": name, "ratio": output.GetMemberSteelDesignRatio(line_id)} for name, line_id in beam_by_name.items()}
    member_ratios_by_type: dict[str, list[float]] = {}
    for line_id, ratio in member_ratios.items():
        name = ratio["name"]
        ratio_value = ratio["ratio"]
        member_type = memberName2type(name)
        if member_type not in member_ratios_by_type:
            member_ratios_by_type[member_type] = []
        member_ratios_by_type[member_type].append(ratio_value)

    member_type_max_ratios: dict[str, float] = {}
    for member_type, ratios in member_ratios_by_type.items():
        member_type_max_ratios[member_type] = max(ratios)
    member_type_min_ratios: dict[str, float] = {}
    for member_type, ratios in member_ratios_by_type.items():
        member_type_min_ratios[member_type] = min(ratios)

    staad_update_status["member_type_max_ratios"] = member_type_max_ratios
    staad_update_status["member_type_min_ratios"] = member_type_min_ratios

    #TODO: maybe include member_max_ratios for each parameter block if needed
    #member_max_ratios_param_block = [{"beam": line_id, "name": name, "ratio": output.GetMultipleMemberSteelDesignRatio("PARAMETER 1", line_id)} for name, line_id in beam_by_name.items()]
    staad_update_status["member_ratios"] = member_ratios
    staad_update_status["member_ratios_by_type"] = member_ratios_by_type
    staad_update_status["envelope_summary_results"] = envelope_summary_results
    staad_update_status["envelope_governing_forces_by_joint_group"] = envelope_governing_forces_by_joint_group
    staad_update_status["envelope_load_cases_by_joint_group"] = envelope_load_cases_by_joint_group
    staad_update_status["results_local_or_global"] = results_local
    staad_update_status["units"] = {"force": "kN", "moment": "kN-m", "displacement": "m"}
    staad_update_status["summary_results"] = summary_results

    try:
        # Any additional processing that might raise an exception can go here
        member_max_ratios = {line_id: {"name": name, "ratio": output.GetMultipleMemberSteelDesignMaxRatio(line_id)} for name, line_id in beam_by_name.items()}
        member_max_ratios_by_type: dict[str, list[float]] = {}
        for line_id, ratio in member_max_ratios.items():
            name = ratio["name"]
            ratio_value = ratio["ratio"]
            member_type = name.split('-')[0]
            if member_type not in member_max_ratios_by_type:
                member_max_ratios_by_type[member_type] = []
            member_max_ratios_by_type[member_type].append(ratio_value)

        member_type_max_ratios_by_type_max: dict[str, float] = {}
        for member_type, ratios in member_max_ratios_by_type.items():
            member_type_max_ratios_by_type_max[member_type] = max(ratios)
        member_type_min_ratios_by_type_min: dict[str, float] = {}
        for member_type, ratios in member_max_ratios_by_type.items():
            member_type_min_ratios_by_type_min[member_type] = min(ratios)
        staad_update_status["member_type_max_ratios_by_type_max"] = member_type_max_ratios_by_type_max
        staad_update_status["member_type_min_ratios_by_type_min"] = member_type_min_ratios_by_type_min
        staad_update_status["member_max_ratios_by_type"] = member_max_ratios_by_type
        staad_update_status["member_max_ratios"] = member_max_ratios
    except Exception as e:
        print(f"Error occurred while obtaining/calculating member max ratios: {e}")

    return staad_update_status


# ── Standalone self-test ─────────────────────────────────────────────────
#
# Run this file directly (`python staad_integration.py`) on the machine
# that has STAAD.Pro + pywin32 installed, with a model open, to check
# that the whole chain — COM import, OpenSTAAD connection, node/beam
# creation, property/material assignment, and a minimal load case —
# actually works, independently of generate_grid.py or the web UI.
#
# This creates one real, visible dummy column (and, if it succeeds, one
# dummy load case) and prints a pass/fail summary for each step. It does
# NOT delete the dummy member afterward — expect one extra
# "SELF_TEST_DUMMY_COLUMN" member left in your model; delete it manually
# once you've confirmed the test passed.

async def main() -> None:
    print("=== staad_integration.py self-test ===\n")

    parser = argparse.ArgumentParser(
        description="Generate a beam/column/bracing grid from a JSON spec."
    )
    parser.add_argument("--input", help="Path to input JSON file", default="./sample_projects/generated_grid_8x5_no_mirror_bracing.json")
    parser.add_argument("--output", help="Path to output JSON file", default="analysis_output_staad.json")
    args = parser.parse_args()
    input_json_path = args.input
    if not input_json_path:
        print("Error: --input argument is required.")
        return
    print(f"Input JSON path: {input_json_path}")
    with open(input_json_path, "r") as f:
        input_data = json.load(f)
    print(f"Loaded input JSON from {input_json_path}:")
    #print(json.dumps(input_data, indent=2))
    print("Proceeding with STAAD.Pro self-test...")
    analysis_output = await send_to_staad(input_data)  # Assuming there's a function to send the input data to STAAD.Pro
    if args.output:
        with open(args.output, "w") as f:
            json.dump(analysis_output, f, indent=2)
        print(f"Analysis output written to {args.output}")

    print(f"Check STAAD.Pro's Nodes/Beams tables for IDs {STARTING_NODE_ID} onward, and load")
    print("\n=== self-test complete ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
