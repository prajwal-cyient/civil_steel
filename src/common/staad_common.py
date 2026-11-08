"""Reset the active STAAD analytical input, reopen it, and save.

Importing this module does not connect to STAAD. Calling main() is destructive.
Requires openstaadpy, as used by staad_integration.py.
"""

from pathlib import Path
import shutil
import tempfile
import time
import re
import sys
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from openstaadpy import os_analytical
    from openstaadpy.os_analytical.openstaadroot import OSProperty, OSGeometry, OSSupport, OSLoad, OSCommand, OSOutput, OSDesign, OSTable, OSView, OsErrorBase
    from common.staad_common import *
    import common.staad_common as staad_common
except ImportError:  # pragma: no cover - only available on Windows w/ pywin32
    os_analytical = None

# Define constants
length_unit_meter = 4
force_unit_kilonewton = 5
self_weight_direction_global_y = 2
start_end = 0
final_end = 1
startingComboLoadCaseId = 100

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

ENVELOP_TYPE_MAP = {
    "NONE": 0,
    "STRESS": 1,
    "SERVICEABILITY": 2,
    "COLUMN": 3,
    "CONNECTION": 4,
    "STRENGTH": 5,
    "TEMPORARY": 6,
}

# Load list type: 0 and 1 for load list and load envelope list,
LOAD_LIST_TYPE_MAP: Dict[str, int] = {
    "load_list": 0,
    "load_envelope_list": 1,
}

# STAAD.Pro openstaadpy Design Code Mapping Dictionary
DESIGN_CODES_MAP: Dict[str, int] = {
    "AASHTO ASD": 1001,
    "AISC ASD": 1002,
    "AS 4100-1998": 1003,
    "BS 5950-1:2000": 1004,
    "BS 5400:Part 3:1982": 1005,
    "CAN/CSA-S16-01": 1006,
    "French CM66 1977": 1007,
    "DIN 18 800 Part 1": 1008,
    "IS 800 1984, ASD": 1009,
    "Japan AIJ 2002": 1010,
    "AISC LRFD": 1011,
    "Norway NS 3472 2001": 1012,
    "Norway NPD 1993": 1014,
    "API 2A-WSD": 1016,
    "ASCE 10-97": 1020,
    "Russia SNiP 2.23-81* 1990": 1025,
    "Canada S136-94": 1027,
    "IS 801 1975": 1028,
    "IS 802 1995": 1029,
    "Mexico NTC 1987": 1030,
    "IS 800 2007, LSD": 1032,
    "IS 800 2007, WSD": 1052, # Note: Mapping duplicated/corrected per original list references
    "BS 5950-5:1998": 1033,
    "South Africa SANS 10162-1:2011": 1034,
    "AASHTO LRFD": 1044,
    "ANSI/AISC 360-05": 1045,
    "ASME NF 3000 (various years)": 1053, # Note: Kept the last instance (1053) for unique dictionary keys
    "ASCE 52": 1060,
    "ANSI/AISC 360-10": 1061,
    "Canadian S16_09": 1062,
    "Russia SP 16.13330.2011": 1063,
    "South Africa SANS10162-1:1993": 1064,
    "Canadian S16_14": 1065,
    "NZS3404_1997": 1066,
    "ANSI/AISC 360-16": 1067,
    "AISI S100-2016": 1068,
    "Canadian S16_19": 1069,
    "AISC CASTELLATED": 1102,
    "AISC N690 1994": 1202,
    "AISC N690 1984": 1204,
    "Japan AIJ 2005": 1210,
    "BS EN 1993-1-1:2005": 1220,
    "Russia SP 16.13330.2017": 1221,
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

'''
Map a Tekla profile name to the IDEA StatiCa (MPRL) cross-section name.
Cold-formed hollow sections CFRHS<h>X<b>X<t> become SHS<h>/<b>/<t> (square) or RHS<h>/<b>/<t>,
with the thickness to one decimal, e.g. CFRHS100X100X4 -> SHS100/100/4.0. Other profiles are returned unchanged.
'''
def tekla2ideastatica_profile(tekla_profile: str) -> str:
    cleaned_profile = "".join(tekla_profile.split())
    match = re.fullmatch(r"CFRHS(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", cleaned_profile, re.IGNORECASE)
    if not match:
        return cleaned_profile
    height, width, thickness = match.groups()
    prefix = "SHS" if float(height) == float(width) else "RHS"
    return f"{prefix}{height}/{width}/{float(thickness):.1f}"

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

def _create_combo_load_case(load: OSLoad, currentLoadCaseId: int, comboCaseTitle: str, comboLoadCases: List[int], primary_case_ids: List[int], load_factors: List[float]) -> int:
    comboCaseId = load.CreateNewLoadCombination(comboCaseTitle, currentLoadCaseId)
    comboLoadCases.append(comboCaseId)
    currentLoadCaseId += 1

    # Add the primary load cases with their corresponding factors
    for case_id, factor in zip(primary_case_ids, load_factors):
        if factor is not None and factor != 0:
            load.AddLoadAndFactorToCombination(comboCaseId, case_id, factor)
    return currentLoadCaseId

EMPTY_MODEL_PATH = Path(__file__).resolve().parents[2] / "sample_projects" / "empty_staad_project.std"

def _verify_open_model(staad, path: Path) -> None:
    # OpenSTAADFile returns before the GUI has finished switching models.
    deadline = time.monotonic() + 30
    counts = None
    while True:
        filename = staad.GetSTAADFile()
        if filename and Path(filename).resolve() == path.resolve():
            geometry = staad.Geometry
            counts = (
                geometry.GetNodeCount(), geometry.GetMemberCount(),
                geometry.GetPlateCount(), geometry.GetSolidCount(),
            )
            if all(count == 0 for count in counts):
                return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"STAAD did not open the requested empty model within 30 seconds: {path}. "
                f"Current file: {filename!r}; geometry counts: {counts}"
            )
        time.sleep(0.25)

def clean_staad() -> None:
    """Back up and reset the current .std file, leaving that same project open."""
    from openstaadpy import os_analytical

    empty_model = EMPTY_MODEL_PATH.read_bytes()
    staad = os_analytical.connect()
    if staad is None:
        raise RuntimeError("Could not connect to a running STAAD.Pro session.")
    filename = staad.GetSTAADFile()
    if not filename:
        raise RuntimeError("STAAD.Pro must have a saved analytical .std model open.")
    path = Path(filename)
    if not path.is_absolute() or path.suffix.lower() != ".std" or not path.is_file():
        raise RuntimeError(f"Not an existing STAAD analytical input file: {path}")
    if staad.IsPhysicalModel():
        raise RuntimeError("Physical models are not supported; open an analytical model.")
    if staad.IsAnalyzing():
        raise RuntimeError("Wait for STAAD analysis to finish before cleanup.")

    staad.SaveModel(True)
    # Exclusive creation keeps backups from earlier runs intact.
    with tempfile.NamedTemporaryFile(
        prefix=f"{path.stem}.before-cleanup-", suffix=".std.bak",
        dir=path.parent, delete=False,
    ) as backup:
        backup_path = Path(backup.name)
    shutil.copy2(path, backup_path)
    print(f"STAAD input backup: {backup_path}", flush=True)

    with tempfile.NamedTemporaryFile(
        prefix=f"{path.stem}.cleanup-stage-",
        suffix=".std", dir=path.parent, delete=False,
    ) as staging:
        staging_path = Path(staging.name)
    original_reopened = False
    try:
        staging_path.write_bytes(empty_model)
        # Switching to a different file proves the original model is no longer
        # active. GetSTAADFile can retain its path after CloseSTAADFile.
        staad.OpenSTAADFile(str(staging_path))
        _verify_open_model(staad, staging_path)

        with tempfile.NamedTemporaryFile(
            prefix=f"{path.stem}.cleanup-", suffix=".std",
            dir=path.parent, delete=False,
        ) as replacement:
            replacement_path = Path(replacement.name)
        try:
            replacement_path.write_bytes(empty_model)
            replacement_path.replace(path)
        finally:
            replacement_path.unlink(missing_ok=True)

        staad.OpenSTAADFile(str(path))
        _verify_open_model(staad, path)
        original_reopened = True
        staad.SaveModel(True)
        if path.read_bytes().split() != empty_model.split():
            raise RuntimeError(
                f"Saved STAAD input differs from {EMPTY_MODEL_PATH}. Inspect {path}."
            )
    finally:
        if original_reopened:
            staging_path.unlink(missing_ok=True)
        else:
            print(
                f"Cleanup interrupted; staging input retained: {staging_path}. "
                f"Original input backup: {backup_path}", flush=True,
            )
    print(f"STAAD cleanup saved: {path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Clean STAAD model, preserving the analytical model.")
    parser.add_argument("--clean", action="store_true", help="Clean the STAAD model.")
    args = parser.parse_args()
    if args.clean:
        clean_staad()
