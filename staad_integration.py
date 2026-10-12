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
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Set

try:
    from openstaadpy import os_analytical
    from openstaadpy.os_analytical.openstaadroot import OSProperty, OSGeometry, OSSupport, OSLoad, OSCommand, OSOutput, OSDesign, OSTable, OSView, OsErrorBase
except ImportError:  # pragma: no cover - only available on Windows w/ pywin32
    os_analytical = None

# Define constants
length_unit_meter = 4
force_unit_kilonewton = 5
self_weight_direction_global_y = 2
start_end = 0
final_end = 1

LOAD_TYPE_MAP = {
    "Dead": 0,
    "Live": 1,
    "Roof Live": 2,
    "Wind": 3,
    "Seismic-H": 4,
    "Seismic-V": 5,
    "Snow": 6,
    "Fluids": 7,
    "Soil": 8,
    "Rain": 9,
    "Ponding": 10,
    "Dust": 11,
    "Traffic": 12,
    "Temp": 13,
    "Imperfection": 14,
    "Accidental": 15,
    "Flood": 16,
    "Ice": 17,
    "Wind Ice": 18,
    "Crane Hook": 19,
    "Mass": 20,
    "Gravity": 21,
    "Push": 22,
    "None": 23,
}

DIRECTION_TYPE_MAP = {
    "LocalX": 1,
    "LocalY": 2,
    "LocalZ": 3,
    "GlobalX": 4,
    "GlobalY": 5,
    "GlobalZ": 6,
    "ProjectedX": 7,
    "ProjectedY": 8,
    "ProjectedZ": 9,
}

SEISMIC_DIRECTION_TYPE_MAP = {
    "GlobalX": 0,
    "GlobalY": 1,
    "GlobalZ": 2,
}

WIND_DIRECTION_MAP = {
    "Global X": 1,
    "Global Z": 3,
    "Global -X": 4,
    "Global -Z": 6,
}

SECTION_TABLE_COUNTRY_CODE_MAP = {
    "USA": 1,
    "AUSTRALIA": 2,
    "BRITAIN": 3,
    "CANADA": 4,
    "CHINA": 5,
    "NETHERLANDS": 6,
    "EUROPE": 7,
    "FRANCE": 8,
    "GERMANY": 9,
    "INDIA": 10,
    "JAPAN": 11,
    "RUSSIA": 12,
    "SOUTH AFRICA": 13 
}

SECTION_TABLE_COUNTRY_CODE_MAP = {
    "USA": 1,
    "AUSTRALIA": 2,
    "BRITAIN": 3,
    "CANADA": 4,
    "CHINA": 5,
    "NETHERLANDS": 6,
    "EUROPE": 7,
    "FRANCE": 8,
    "GERMANY": 9,
    "INDIA": 10,
    "JAPAN": 11,
    "RUSSIA": 12,
    "SOUTH AFRICA": 13,
    "SPAIN": 14,
    "VENEZUELA": 15,
    "KOREA": 16,
    "American": 1,
    "Australian": 2,
    "British": 3,
    "Canadian": 4,
    "Chinese": 5,
    "Dutch": 6,
    "European": 7,
    "French": 8,
    "German": 9,
    "Indian": 10,
    "Japanese": 11,
    "Russian": 12,
    "SouthAfrican": 13,
    "Spanish": 14,
    "Venezuelan": 15,
    "Korean": 16,
}

"""

Value	Seismic Code
0	AUTO DETECT
1	ALGERIAN: RPA
2	CANADIAN: NRC-1995
3	CANADIAN: NRC-2005
4	CANADIAN: NRC-2010
5	CANADIAN: NRC-2020
6	Chinese: GB50011-2001
7	Chinese: GB50011-2010
8	COLOMBIAN: NSR 95
9	COLOMBIAN: NSR 2010
10	IBC 2000
11	IBC 2003 ASCE 7-02
12	IBC 2006/2009 ASCE 7-05
13	IBC 2012 ASCE 7-10
14	IBC 2015 ASCE 7-10
15	IBC 2018 ASCE 7-16
16	Indian: IS 1893-1984
17	Indian: IS 1893-2002/2005
18	Indian: IS 1893-2016
19	Indian: IS 1893(Part4) 2015
20	JAPANESE (AIJ)
21	MEX: CFE-1993
22	MEX: NTC-1987
23	TURKISH
24	UBC 1985
25	UBC 1994
26	UBC 1997
"""
SEISMIC_LOAD_CODE_TYPE_MAP_AddSeismicDefMemberWeight = {
"AUTO DETECT" : 0,
"ALGERIAN: RPA" : 1,
"CANADIAN: NRC-1995" : 2,
"CANADIAN: NRC-2005" : 3,
"CANADIAN: NRC-2010" : 4,
"CANADIAN: NRC-2020" : 5,
"Chinese: GB50011-2001" : 6,
"Chinese: GB50011-2010" : 7,
"COLOMBIAN: NSR 95" : 8,
"COLOMBIAN: NSR 2010" : 9,
"IBC 2000" : 10,
"IBC 2003 ASCE 7-02" : 11,
"IBC 2006/2009 ASCE 7-05" : 12,
"IBC 2012 ASCE 7-10" : 13,
"IBC 2015 ASCE 7-10" : 14,
"IBC 2018 ASCE 7-16" : 15,
"Indian: IS 1893-1984" : 16,
"Indian: IS 1893-2002/2005" : 17,
"Indian: IS 1893-2016" : 18,
"Indian: IS 1893(Part4) 2015" : 19,
"JAPANESE (AIJ)" : 20,
"MEX: CFE-1993" : 21,
"MEX: NTC-1987" : 22,
"TURKISH" : 23,
"UBC 1985" : 24,
"UBC 1994" : 25,
"UBC 1997" : 26
}

SEISMIC_LOAD_CODE_TYPE_MAP = {
    "UBC 1985": 0,
    "UBC 1994": 1,
    "UBC 1997": 2,
    "Indian: IS 1893-1984": 3,
    "Indian: IS 1893-2002/2005": 4,
    "IBC 2000": 5,
    "IBC 2003": 6,
    "COLOMBIAN: NSR 98": 7,
    "JAPANESE (AIJ)": 8,
    "ALGERIAN: RPA": 9,
    "MEX: CFE-1993": 10,
    "MEX: NTC-1987": 11,
    "Indian: IS 1893-2016": 12,
    "Indian: IS 1893(Part4) 2015": 13,
    "IBC 2006": 14,
    "IBC 2012": 15,
    "IBC 2015": 16,
    "IBC 2018": 17,
    "CANADIAN: NRC-2005": 18,
    "CANADIAN: NRC-2010": 19,
    "CANADIAN: NRC-1995": 20,
    "COLOMBIAN: NSR 2010": 21,
    "Chinese: GB50011-2001": 22,
    "Chinese: GB50011-2010": 23,
    "TURKISH": 24,
}

SEISMIC_LOAD_TYPE = {
    "UNIFORM": 1,
    "CONCENTRATED": 2
}

"""
Seismic Code	Parameters
ALGERIAN: RPA	A Q RX RZ STYPE CT CRDAMP PX PZ
CANADIAN: NRC-1995	V ZA ZV RX RZ I F CT PX PZ
CANADIAN: NRC-2005	SA1 SA2 SA3 SA4 IE SCLASS MVX MVZ JX JZ RDX RDZ ROX ROZ CT PX PZ FA FV
CANADIAN: NRC-2010	SA1 SA2 SA3 SA4 I SCLASS MVX MVZ RDX RDZ ROX ROZ CTX CTZ PX PZ FA FV STX STZ MD
CHINESE: GB50011-2001	INTENSITY FREQUENT RARE GROUP SCLASS DAMP DELN SF PX PZ GFACTOR
Note: For CHINESE: GB50011-2001 FREQUENT/RARE parameter value will be 0, 1 respectively
CHINESE: GB50011-2010	INTENSITY FREQUENT FORTIFIED RARE GROUP SCLASS DAMP GFACTOR DELN SF PX PZ
Note: For CHINESE: GB50011-2010 FREQUENT/FORTIFIED/RARE parameter value will be 0, 1 and 2 respectively
COLOMBIAN: NSR 98	ZONE I S
COLOMBIAN: NSR 2010	AA AV FA FV I CT PX PZ ALPHA
IBC 2000	SDS SD1 S1 I RX RZ SCLASS CT PX PZ
IBC 2003	SDS SD1 S1 I RX RZ SCLASS CT PX PZ
IBC 2006	SS S1 ZIP I RX RZ SCLASS CTX CTZ PX PZ LAT LONG TL FA FV XX XZ
IBC 2012	SS S1 ZIP I RX RZ SCLASS CTX CTZ PX PZ LAT LONG TL FA FV XX XZ
IBC 2015	SS S1 ZIP I RX RZ SCLASS CTX CTZ PX PZ LAT LONG TL FA FV XX XZ
IBC 2018	SS S1 ZIP I RX RZ SCLASS CTX CTZ PX PZ LAT LONG TL FA FV XX XZ
Note: For IBC 2006 - 2018 Please provide any one of ZIP OR LAT LONG OR SS S1
INDIAN: IS 1893-1984	ZONE K I B PX PZ
INDIAN: IS 1893-2002/2005	ZONE RF I SS ST DM PX PZ DT GL SA DF CS AX ES CV DV
INDIAN: IS 1893-2016	ZONE RF I SS ST DM PX PZ DT GL SA DF HT DX DZ
INDIAN: IS 1893(Part4) 2015	ZONE RF I SS ST DM PX PZ SA DF
JAPANESE (AIJ)	ZONE CO TC ALPHA
MEX: CFE-1993	ZONE QX QZ GROUP STYPE REGULAR TS PX PZ
MEX: NTC-1987	ZONE QX QZ GROUP SHADOWED REGULAR REDUCE PX PZ
Note: For SHADOWED, REGULAR and REDUCE parameter value will be 0 or 1 respectively
TURKISH	A TA TB I RX RZ CT PX PZ
UBC 1985	ZONE I K TS
UBC 1994	ZONE I RWX RWZ S CT PX PZ
UBC 1997	ZONE I RWX RWZ STYPE CT PX PZ NA NV
"""
# ── Configuration ────────────────────────────────────────────────────────
# If your profile strings (as generated by generate_grid.py) don't match
# STAAD's own section table naming, add translations here:
#     PROFILE_NAME_MAP = {"CFRHS100X100X4": "TUB100X100X4.0", ...}
PROFILE_NAME_MAP: Dict[str, str] = {
    "W12*26": "W12X26",
    "W14*90": "W14*90",
    "PIPE4.5X0.237": "PIP45000",
    "HEA200": "IPE200",
    "HEA140": "IPE140",
    "HEA400": "IPE400",
    "CFRHS100X100X4": "100x4SHS", # "RHS100X100X4",
    "CFRHS120X120X5": "120x5SHS", # "RHS120X120X5",
    "CFRHS90X90X8": "90x8SHS", # "RHS10X100X4",
}

MATERIAL_MAPPING = {
    "A992": "STEEL",
    "A36": "STEEL",
    "S355JR": "STEEL",
    "S275JR": "STEEL",
    "C30/37": "CONCRETE",
    "4000Psi": "CONCRETE",
}

def searchDictByKeyStart(dict: Dict[str, Any], key_start: str) -> Any:
    for key, value in dict.items():
        if key.startswith(key_start):
            return value
    return None

def tekla2staad(tekla_profile, tekla_material):
    cleaned_profile = "".join(tekla_profile.split())
    cleaned_material = "".join(tekla_material.split())

    staad_profile = PROFILE_NAME_MAP.get(cleaned_profile, cleaned_profile)
    staad_material = MATERIAL_MAPPING.get(cleaned_material, "STEEL")

    if staad_profile.startswith("CFRHS"):
        staad_profile = staad_profile.replace("CFRHS", "RHS")
    if staad_profile.startswith("HEA"):
        staad_profile = staad_profile.replace("HEA", "IPE")

    return staad_profile, staad_material

def country2code(country):
    return SECTION_TABLE_COUNTRY_CODE_MAP.get(country, -1)

# Generic structural-steel material constants used for every member,
# since E/Poisson/density barely vary between common steel grades for
# analysis purposes (yield strength differences matter for design
# checks, not for this geometry/loads push). Units: N, mm, kg — adjust
# to match your model's active unit system if different.
STEEL_MATERIAL_DEFAULTS = {
    "E": 205000.0,       # N/mm^2
    "poisson": 0.3,
    "density": 7.85e-6,  # kg/mm^3 (7850 kg/m^3)
}

# CreateNode/CreateBeam take an explicit ID as their first argument
# rather than generating and returning one — confirmed empirically:
# calling CreateNode with the same node_id and different coordinates
# just moves that existing node rather than erroring or creating a
# second one. To avoid silently overwriting/moving anything already in
# the active model, generated IDs start from a large offset rather than
# from 1. Raise these if a model routinely exceeds this range, or swap
# in a real "highest existing node/beam number" query if you find one in
# OpenSTAAD.chm to seed from instead of a fixed guess.
STARTING_NODE_ID = 900001
STARTING_BEAM_ID = 900001

class _SequentialIdAllocator:
    """Hands out sequential positive integer IDs starting from `start`."""

    def __init__(self, start: int):
        self._next = start

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value


# ── Errors ────────────────────────────────────────────────────────────────

class StaadConnectionError(RuntimeError):
    """Raised when no running STAAD.Pro / OpenSTAAD session can be reached."""


class StaadMemberError(RuntimeError):
    """Raised when a single member fails to be created/configured in STAAD."""


# ── Internals: connection ──────────────────────────────────────────────

def _get_openstaad():
    """
    Connects to a running STAAD.Pro session via OpenSTAAD.

    Uses plain late-bound win32com.client.Dispatch(). Early binding
    (gencache.EnsureDispatch) was tried and ruled out: OpenSTAAD's CLSID
    has no registered type library at all (confirmed via the Windows
    registry), so there's nothing for gencache to build a wrapper from,
    by any route.
    """
    try:
        openstaad = os_analytical.connect()
        openstaad.SetSilentMode(True)
    except Exception as exc:
        raise StaadConnectionError(
            "Could not connect to STAAD.Pro via OpenSTAAD. Make sure "
            "STAAD.Pro is running with a model open.\n"
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc

    return openstaad


# ── Internals: geometry ─────────────────────────────────────────────────

def _format_com_error(exc: Exception) -> str:
    """
    pywin32 COM errors (pywintypes.com_error) carry their useful text
    buried in exc.args[2] (a tuple: source, description, helpfile,
    helpcontext, scode) rather than in str(exc) itself, which is often
    just a generic "COM Error" message. Pull the description out if
    it's there so failures are actually diagnosable.
    """
    args = getattr(exc, "args", None)
    if args and len(args) >= 3 and isinstance(args[2], (tuple, list)) and len(args[2]) >= 2:
        source, description = args[2][0], args[2][1]
        parts = [p for p in (description, source) if p]
        if parts:
            return " / ".join(str(p) for p in parts)
    return str(exc)


def _node_key(point: List[float]) -> tuple:
    # Round to avoid floating-point duplicates creating separate
    # coincident nodes at what should be the same joint.
    return tuple(round(c, 3) for c in point)


def _get_or_create_node(
    geometry: OSGeometry,
    node_cache: Dict[tuple, int],
    node_ids: "_SequentialIdAllocator",
    point: List[float],
) -> int:
    """
    Returns the node ID for `point`, creating it in STAAD if it hasn't
    already been created during this call (tracked via node_cache, keyed
    by rounded coordinates so coincident joints reuse one node).

    CreateNode's confirmed signature is CreateNode(node_id, x, y, z) — it
    does NOT return a usable value (confirmed empirically: it comes back
    None even though the node is genuinely created), so the ID used here
    is one we generate ourselves via node_ids, not something read back
    from the call.
    """
    key = _node_key(point)
    if key in node_cache:
        return node_cache[key]

    node_id = node_ids.next()
    try:
        point_in_m = list(map(lambda c: c/1000.0, point))
        geometry.CreateNode(node_id, *point_in_m)
    except Exception as exc:
        raise StaadMemberError(
            f"Geometry.CreateNode({node_id}, {tuple(point)}) raised an error: "
            f"{_format_com_error(exc)}"
        ) from exc

    node_cache[key] = node_id
    return node_id


def _create_beam(
    geometry: OSGeometry,
    node_cache: Dict[tuple, int],
    node_ids: "_SequentialIdAllocator",
    beam_ids: "_SequentialIdAllocator",
    member: dict,
) -> tuple[int, int, int]:
    """
    Creates a beam between member["p1"] and member["p2"], returning the
    beam ID used.

    CreateBeam's exact signature is UNCONFIRMED — by analogy with
    CreateNode's confirmed CreateNode(node_id, x, y, z) pattern, this
    assumes CreateBeam(beam_id, node1, node2) (ID first, then the two
    node IDs). If this raises an argument-count/type error, the likely
    alternative is CreateBeam(node1, node2, beam_id) (ID last) — swap
    the argument order below and re-run the self-test to check.
    """
    n1 = _get_or_create_node(geometry, node_cache, node_ids, member["p1"])
    n2 = _get_or_create_node(geometry, node_cache, node_ids, member["p2"])

    beam_id = beam_ids.next()
    try:
        geometry.CreateBeam(beam_id, n1, n2)
    except Exception as exc:
        raise StaadMemberError(
            f"Geometry.CreateBeam({beam_id}, {n1}, {n2}) raised an error: "
            f"{_format_com_error(exc)}"
        ) from exc

    return beam_id, n1, n2


# ── Internals: section/material assignment (best-effort) ──────────────

def _resolve_profile_name(profile: str) -> str:
    profile, material = tekla2staad(profile, "STEEL")
    return profile

def _assign_property(property: OSProperty, beam_no: int, member: dict, property_id_cache: Dict[str, int] = {}, country: str = "EUROPE") -> None:
    """
    Assigns a section profile and generic steel material constants to a
    single beam. Best-effort — see the module docstring's "WHAT'S SOLID
    VS. BEST-EFFORT" section before relying on this in production.
    """

    profile_name, material_name = tekla2staad(member["profile"], member['material'])
    '''
    try:
        status = property.CreateIsotropicMaterialSteel(
           material_name,
            STEEL_MATERIAL_DEFAULTS["E"],
            STEEL_MATERIAL_DEFAULTS["poisson"],
            STEEL_MATERIAL_DEFAULTS["density"],
        )
    except Exception as exc:
        # Material assignment failing isn't fatal to member creation;
        # the beam still exists with its section, just without
        # constants set. Swallow rather than aborting the whole member.
        #print(f'Failed to create material {member.get("material", "STEEL")}: {exc}'),
        pass
    '''

    try:
        property_id = property_id_cache.get(profile_name, None)
        if property_id is None:
            property_id = property.CreateBeamPropertyFromTable(country2code(country), profile_name, 0, 0.0, 0.0)
            print(f"Created beam property for profile '{profile_name}' with ID {property_id} country {country}, country code {country2code(country)}")
            property_id_cache[profile_name] = property_id
        raw_property = property._property
        raw_property.SetMaterialName(material_name)
        raw_property.AssignBeamProperty(int(beam_no), property_id)
        raw_property.AssignMaterialToMember(material_name, int(beam_no))
    except Exception as exc:
        # Material assignment failing isn't fatal to member creation;
        # the beam still exists with its section, just without
        # constants set. Swallow rather than aborting the whole member.
        print(f'Failed to assign material to beam {beam_no}: {exc}')


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
        start_point = _node_key(member["p1"])
        end_point = _node_key(member["p2"])
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
) -> None:
    node_groups = {1: corner_nodes, 2: edge_nodes, 4: inner_nodes}

    total_multiplier = sum(multiplier * len(nodes) for multiplier, nodes in node_groups.items())
    if not total_multiplier:
        raise ValueError("Distributed nodal load requires top nodes.")
    base_value_kN = float(mass_kg) * 9.81 / 1000.0 / total_multiplier
    for multiplier, nodes in node_groups.items():
        if nodes:
            load.AddNodalLoad(sorted(nodes), 0.0, -multiplier * base_value_kN, 0.0, 0.0, 0.0, 0.0)


def _apply_distributed_seismic_joint_weight(
    load: OSLoad,
    corner_nodes: List[int],
    edge_nodes: List[int],
    inner_nodes: List[int],
    mass_kg: float,
) -> None:
    node_groups = {1: corner_nodes, 2: edge_nodes, 4: inner_nodes}
    total_multiplier = sum(multiplier * len(nodes) for multiplier, nodes in node_groups.items())
    if not total_multiplier:
        raise ValueError("Seismic joint weight requires top nodes.")
    base_value_kN = float(mass_kg) * 9.81 / 1000.0 / total_multiplier
    for multiplier, nodes in node_groups.items():
        if nodes:
            load.AddSeismicDefJointWeight(multiplier * base_value_kN, sorted(nodes))


def _apply_radiator_installation_load(
    load: OSLoad,
    corner_nodes: List[int],
    edge_nodes: List[int],
    inner_nodes: List[int],
    radiator_mass_kg: float,
) -> None:
    _apply_distributed_nodal_load(load, corner_nodes, edge_nodes, inner_nodes, radiator_mass_kg)


def _apply_load_configuration(
    load: OSLoad,
    node_cache: Dict[tuple, int],
    load_configuration: Dict[str, Any],
    members: List[Dict[str, Any]],
    support_nodes: List[int],
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
        load.AddMemberUniformForce(longitudinal_edge_members, DIRECTION_TYPE_MAP["GlobalY"], -dead_load_intensity_kN_per_m2 * longitudinal_beam_length_m, 0.0, longitudinal_beam_length_m, 0.0)
    if transverse_edge_members:
        load.AddMemberUniformForce(transverse_edge_members, DIRECTION_TYPE_MAP["GlobalY"], -dead_load_intensity_kN_per_m2 * transverse_beam_length_m, 0.0, transverse_beam_length_m, 0.0)

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

    wind_case = load.CreateNewPrimaryLoadEx2("WIND 1", LOAD_TYPE_MAP["Wind"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(wind_case)
    windLoadFraction_x = load_configuration.get("wind_load_fraction_x", 1)
    windLoadFraction_z = load_configuration.get("wind_load_fraction_z", 1)
    windLoadFraction_nx = load_configuration.get("wind_load_fraction_nx", 0.25)
    windLoadFraction_nz = load_configuration.get("wind_load_fraction_nz", 0.25)
    isOpenStructure = load_configuration.get("is_open_structure", True)
    total_structure_height = grid_spec.get("total_structure_height", 0)
    column_height = columnObjs[0]["p2"][1] - columnObjs[0]["p1"][1]
    isOpenStructure = 1 if isOpenStructure else 0
    windDirection = WIND_DIRECTION_MAP["Global X"]
    dyMin = 0
    dyMax = max(total_structure_height, column_height)/1000.0
    dzMin = 0
    dzMax = grid_spec.get("bay_long_mm", 3500)*grid_spec.get("n_long_bays", 1)/2000.0
    dxMin = 0
    dxMax = grid_spec.get("bay_trans_mm", 3500)*grid_spec.get("n_trans_bays", 1)/2000.0
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_x, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)
    windDirection = WIND_DIRECTION_MAP["Global -X"]
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_nx, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)
    windDirection = WIND_DIRECTION_MAP["Global Z"]
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_z, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)
    windDirection = WIND_DIRECTION_MAP["Global -Z"]
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_nz, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)

    wind_case_2 = load.CreateNewPrimaryLoadEx2("WIND 2", LOAD_TYPE_MAP["Wind"], currentLoadCaseId)
    currentLoadCaseId += 1
    load.SetLoadActive(wind_case_2)
    windLoadFraction_x = load_configuration.get("wind_load_fraction_x", 1)
    windLoadFraction_z = load_configuration.get("wind_load_fraction_z", 1)
    windLoadFraction_nx = load_configuration.get("wind_load_fraction_nx", 0.25)
    windLoadFraction_nz = load_configuration.get("wind_load_fraction_nz", 0.25)
    isOpenStructure = load_configuration.get("is_open_structure", False)
    isOpenStructure = 1 if isOpenStructure else 0
    windDirection = WIND_DIRECTION_MAP["Global X"]
    dyMin = 0
    dyMax = max(total_structure_height, column_height)/1000.0
    dzMin = grid_spec.get("bay_long_mm", 3500)*grid_spec.get("n_long_bays", 1)/2000.0
    dzMax = grid_spec.get("bay_long_mm", 3500)*grid_spec.get("n_long_bays", 1)/1000.0
    dxMin = grid_spec.get("bay_long_mm", 3500)*grid_spec.get("n_long_bays", 1)/2000.0
    dxMax = grid_spec.get("bay_trans_mm", 3500)*grid_spec.get("n_trans_bays", 1)/1000.0
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_x, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)
    windDirection = WIND_DIRECTION_MAP["Global -X"]
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_nx, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)
    windDirection = WIND_DIRECTION_MAP["Global Z"]
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_z, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)
    windDirection = WIND_DIRECTION_MAP["Global -Z"]
    load.AddWindLoad(windDefId, windDirection, windLoadFraction_nz, isOpenStructure, dyMin, dyMax, dzMin, dzMax, dxMin, dxMax)

    #TODO: Wind uniform load on members
    #load.AddMemberUniformForce(columns + longitudinal_bracings + transverse_bracings, DIRECTION_TYPE_MAP["GlobalX"], 0.6, 0.0, 0.0, 0.0)  # 4=GlobalX

    # Load Combination 3 (75% of 1 & 2)
    combo3 = load.CreateNewLoadCombination("Comb DL LL WL", currentLoadCaseId)
    currentLoadCaseId += 1
    #TODO: Add the two load cases (DL and LL) with appropriate factors to the combination
    load.AddLoadAndFactorToCombination(combo3, load_case, 1)
    load.AddLoadAndFactorToCombination(combo3, installation_case, 1)
    load.AddLoadAndFactorToCombination(combo3, load_case_2, 0.80)
    load.AddLoadAndFactorToCombination(combo3, wind_case, 0.75)
    load.AddLoadAndFactorToCombination(combo3, wind_case_2, 0.75)
    load.AddLoadAndFactorToCombination(combo3, seismic_case_1, 0.75)
    load.AddLoadAndFactorToCombination(combo3, seismic_case_2, 0.75)
    load.AddLoadAndFactorToCombination(combo3, seismic_case_3, 0.75)
    load.AddLoadAndFactorToCombination(combo3, seismic_case_4, 0.75)
    load.AddLoadAndFactorToCombination(combo3, seismic_case_5, 0.75)
    load.AddLoadAndFactorToCombination(combo3, temperature_case1, 1.2)
    load.AddLoadAndFactorToCombination(combo3, temperature_case2, 1.2)

    combo4 = load.CreateNewLoadCombination("Comb DL LL WL 2", currentLoadCaseId)
    currentLoadCaseId += 1
    #TODO: Add the two load cases (DL and LL) with appropriate factors to the combination
    load.AddLoadAndFactorToCombination(combo4, load_case, 1)
    load.AddLoadAndFactorToCombination(combo4, installation_case, 1)
    load.AddLoadAndFactorToCombination(combo4, load_case_2, 0.95)
    load.AddLoadAndFactorToCombination(combo4, wind_case, 1.2)
    load.AddLoadAndFactorToCombination(combo4, wind_case_2, 1.2)
    load.AddLoadAndFactorToCombination(combo4, seismic_case_1, 1.1)
    load.AddLoadAndFactorToCombination(combo4, seismic_case_2, 1.1)
    load.AddLoadAndFactorToCombination(combo4, seismic_case_3, 1.1)
    load.AddLoadAndFactorToCombination(combo4, seismic_case_4, 1.1)
    load.AddLoadAndFactorToCombination(combo4, seismic_case_5, 1.1)
    load.AddLoadAndFactorToCombination(combo4, temperature_case1, 1.5)
    load.AddLoadAndFactorToCombination(combo4, temperature_case2, 1.5)

    return {"applied": currentLoadCaseId - 1 - len(errors), "errors": errors}


# ── Public API ───────────────────────────────────────────────────────────

def _joint_force_maxima(joint_member_ends: dict, start_forces: list, end_forces: list) -> dict:
    """Return componentwise absolute member-end maxima grouped by joint degree.

    Forces retain the requested STAAD coordinate system (local or global).
    Each component is an independent envelope, not a simultaneous force vector.
    """
    forces_by_end = {
        start_end: {entry["beam"]: entry["forces"] for entry in start_forces},
        final_end: {entry["beam"]: entry["forces"] for entry in end_forces},
    }
    components = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")
    groups = {}
    for node_id, member_ends in sorted(joint_member_ends.items()):
        member_count = len({beam_id for beam_id, _ in member_ends})
        group = groups.setdefault(str(member_count), {
            "member_count": member_count,
            "joints": [],
            "max_abs_forces": {component: 0.0 for component in components},
            "max_force_sources": {},
        })
        group["joints"].append({
            "node": node_id,
            "members": [
                {
                    "beam": beam_id,
                    "end": "start" if member_end == start_end else "end",
                    "forces": dict(zip(components, forces_by_end[member_end][beam_id])),
                }
                for beam_id, member_end in member_ends
            ],
        })
        for beam_id, member_end in member_ends:
            forces = forces_by_end[member_end][beam_id]
            for index, component in enumerate(components):
                signed_value = forces[index]
                magnitude = abs(signed_value)
                if (component not in group["max_force_sources"]
                        or magnitude > group["max_abs_forces"][component]):
                    group["max_abs_forces"][component] = magnitude
                    group["max_force_sources"][component] = {
                        "node": node_id,
                        "beam": beam_id,
                        "end": "start" if member_end == start_end else "end",
                        "signed_value": signed_value,
                        "forces": dict(zip(components, forces)),
                    }
    return groups


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
    members = data.get("members_split_beams", [])
    load_configuration = data.get("load_configuration")
    grid_spec = data.get("grid")

    openstaad = _get_openstaad()
    openstaad.SetInputUnitForLength(length_unit_meter)
    openstaad.SetInputUnitForForce(force_unit_kilonewton)
    geometry = openstaad.Geometry
    property = openstaad.Property
    load = openstaad.Load
    support = openstaad.Support
    command = openstaad.Command

    end_my_mz_release_spec_id = property.CreateMemberReleaseSpec(1, [0, 0, 0, 0, 1, 1], [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    truss_spec_id = property.CreateMemberTrussSpec()
    #property.AssignMemberSpecToBeam([900001, 900002, 900003], end_my_mz_release_spec_id)
    #property.AssignMemberSpecToBeam([900148, 900154, 900160, 900166], truss_spec_id)

    node_cache: Dict[tuple, int] = {}
    node_ids = _SequentialIdAllocator(STARTING_NODE_ID)
    beam_ids = _SequentialIdAllocator(STARTING_BEAM_ID)
    beam_by_name: Dict[str, int] = {}
    support_nodes: Set[int] = set()
    member_ids_by_type: Dict[str, List[int]] = {}
    joint_member_ends = {}

    created = 0
    beam_no = 0
    errors = []
    property_errors = []
    property_id_cache: Dict[str, int] = {}
    country = load_configuration.get("country", "EUROPE")

    fixed_support_id = support.CreateSupportFixed()
    for member in members:
        try:
            beam_no, n1, n2 = _create_beam(geometry, node_cache, node_ids, beam_ids, member)
            beam_by_name[member.get("name", "")] = beam_no
            member["id"] = beam_no
            created += 1
            joint_member_ends.setdefault(n1, []).append((beam_no, start_end))
            joint_member_ends.setdefault(n2, []).append((beam_no, final_end))
        except Exception as exc:  # keep going; report this member and move on
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})
            continue  # nothing to assign a property to if geometry creation failed

        try:
            _assign_property(property, beam_no, member, property_id_cache, country)
        except Exception as exc:
            property_errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

        memberType = member.get("type", "unknown").lower()
        if memberType not in member_ids_by_type:
            member_ids_by_type[memberType] = []
        member_ids_by_type[memberType].append(beam_no)

        if memberType == "column":
            property.AssignMemberSpecToBeam([beam_no], end_my_mz_release_spec_id)
            if n1 not in support_nodes:
                support._support.AssignSupportToNode(int(n1), fixed_support_id)
                support_nodes.add(n1)
        elif memberType in ["longitudinal_brace", "transverse_brace"]:
            property.AssignMemberSpecToBeam([beam_no], truss_spec_id)

    load_applied = 0
    load_errors: List[Dict[str, str]] = []
    if load_configuration:
        load_result = _apply_load_configuration(
            load, node_cache, load_configuration, members, support_nodes, grid_spec
        )
        load_applied = load_result["applied"]
        load_errors = load_result["errors"]

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
        "load_cases_applied": load_applied,
        "load_cases_failed": len(load_errors),
        "load_errors": load_errors,
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
    print(f"Analysis status: {analysis_status}")

    if analysis_status["ReturnValue"] not in [2, 3]:
        staad_update_status["analysis_failed"] = True
        staad_update_status["analysis_status"] = analysis_status
        return staad_update_status

    await asyncio.sleep(3)

    # Extract results
    output = openstaad.Output
    base_unit = openstaad.GetBaseUnit()
    if base_unit not in ("English", "Metric"):
        raise ValueError(f"Unsupported STAAD base unit system: {base_unit}")
    force_factor = 4.4482216152605 if base_unit == "English" else 1.0
    moments_factor = force_factor * 0.0254 if base_unit == "English" else 1.0

    # GetMemberEndForces returns FX, FY, FZ, MX, MY, and MZ (in order)
    results_local = load_configuration.get("results_local_or_global", 0)
    loadCaseMap = {load.GetLoadCaseTitle(lc): lc for lc in load.GetLoadCombinationCaseNumbers()} # GetPrimaryLoadCaseNumbers
    print(f"Load case map: {loadCaseMap}")
    for lc_title, lc in loadCaseMap.items():
        print(f"Processing load case: {lc_title} (ID: {lc})")
        end_forces = [
            {"beam": line_id, "forces": output.GetMemberEndForces(int(line_id), final_end, int(lc), results_local)}
            for line_id in beam_by_name.values()
        ]

        start_forces = [
            {"beam": line_id, "forces": output.GetMemberEndForces(int(line_id), start_end, int(lc), results_local)}
            for line_id in beam_by_name.values()
        ]

        support_reactions = [
            {"support": support_id, "forces": output.GetSupportReactions(int(support_id), int(lc))}
            for support_id in support_nodes
        ]

        for force_entries in (start_forces, end_forces, support_reactions):
            for entry in force_entries:
                entry["forces"] = [
                    value * (force_factor if index < 3 else moments_factor)
                    for index, value in enumerate(entry["forces"])
                ]

        staad_update_status[lc_title] = {}
        staad_update_status[lc_title]["units"] = {"force": "kN", "moment": "kN-m"}
        staad_update_status[lc_title]["load_case_id"] = int(lc)
        staad_update_status[lc_title]["results_local_or_global"] = results_local
        staad_update_status[lc_title]["end_forces"] = end_forces
        staad_update_status[lc_title]["start_forces"] = start_forces
        staad_update_status[lc_title]["support_reactions"] = support_reactions
        staad_update_status[lc_title]["joint_force_maxima"] = _joint_force_maxima(
            joint_member_ends, start_forces, end_forces
        )
        staad_update_status[lc_title]["analysis_status"] = analysis_status
        staad_update_status[lc_title]["analysis_complete"] = True
        print("Analysis results extracted and status updated.")
    
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
    parser.add_argument("--input", help="Path to input JSON file")
    parser.add_argument("--output", help="Path to output JSON file", default="analysis_output.json")
    args = parser.parse_args()
    input_json_path = args.input
    if not input_json_path:
        print("Error: --input argument is required.")
        return
    with open(input_json_path, "r") as f:
        input_data = json.load(f)
    print(f"Loaded input JSON from {input_json_path}:")
    print(json.dumps(input_data, indent=2))
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
