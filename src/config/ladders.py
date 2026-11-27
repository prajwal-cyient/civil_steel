"""
ladders.py

Standard structural engineering profile and connection parameter ladders.
Provides deterministic stepping algorithms for Loop A (STAAD frame sizing)
and Loop B (IDEA StatiCa connection detailing).
"""

from typing import List, Optional
from math import isfinite

# Approved cold-formed hollow square section ladder for columns (increasing cross-section/moment capacity)
COLUMN_PROFILE_LADDER: List[str] = [
    "CFRHS100X100X4",
    "CFRHS100X100X5",
    "CFRHS120X120X5",
    "CFRHS120X120X6",
    "CFRHS140X140X5",
    "CFRHS140X140X6",
    "CFRHS150X150X6",
    "CFRHS160X160X6",
    "CFRHS160X160X8",
    "CFRHS180X180X8",
    "CFRHS200X200X8",
    "CFRHS200X200X10",
    "CFRHS220X220X10",
    "CFRHS250X250X10",
]

# Approved European wide-flange I-beam ladder for beams
BEAM_PROFILE_LADDER: List[str] = [
    "HEA100",
    "HEA120",
    "HEA140",
    "HEA160",
    "HEA180",
    "HEA200",
    "HEA220",
    "HEA240",
    "HEA260",
    "HEA280",
    "HEA300",
]

# Approved square hollow section ladder for diagonal bracings
BRACE_PROFILE_LADDER: List[str] = [
    "CFRHS80X80X3",
    "CFRHS90X90X3.6",
    "CFRHS100X100X4",
    "CFRHS120X120X5",
    "CFRHS140X140X5",
    "CFRHS150X150X6",
]

# Standard plate thicknesses (mm)
PLATE_THICKNESS_LADDER: List[float] = [
    8.0, 10.0, 12.0, 14.0, 15.0, 16.0, 18.0, 20.0, 22.0, 25.0, 30.0
]

# Standard bolt diameters (mm)
BOLT_DIAMETER_LADDER: List[float] = [
    12.0, 16.0, 20.0, 24.0, 27.0, 30.0, 36.0
]

# Standard fillet weld throat thicknesses (mm)
WELD_SIZE_LADDER: List[float] = [
    3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0
]


def get_next_profile(current_profile: str, member_type: str = "column") -> Optional[str]:
    """
    Finds the current profile in the approved ladder and returns the next stronger profile.
    Returns None if already at the top of the ladder.
    """
    curr = current_profile.strip().upper()
    ladder = COLUMN_PROFILE_LADDER
    if member_type.lower() == "beam":
        ladder = BEAM_PROFILE_LADDER
    elif "brace" in member_type.lower():
        ladder = BRACE_PROFILE_LADDER

    # Direct match or case-insensitive search
    match_index = -1
    for i, prof in enumerate(ladder):
        if prof.upper() == curr:
            match_index = i
            break

    if match_index != -1:
        if match_index + 1 < len(ladder):
            return ladder[match_index + 1]
        return None  # Reached ceiling

    # An unknown profile has no established ordering in this ladder.
    return None


def get_next_plate_thickness(current_t: float) -> float:
    """Returns the next standard plate thickness in mm."""
    for t in PLATE_THICKNESS_LADDER:
        if t > current_t:
            return t
    return PLATE_THICKNESS_LADDER[-1]


def get_next_bolt_diameter(current_d: float) -> float:
    """Returns the next standard metric bolt diameter in mm."""
    for d in BOLT_DIAMETER_LADDER:
        if d > current_d:
            return d
    return BOLT_DIAMETER_LADDER[-1]


def get_next_weld_size(current_w: float) -> float:
    """Returns the next standard weld throat size in mm."""
    for w in WELD_SIZE_LADDER:
        if w > current_w:
            return w
    return WELD_SIZE_LADDER[-1]


def step_parameter_expression(key: str, expression: str, fix_type: str = "auto") -> str:
    """
    Steps a connection parameter expression (supporting both meter and mm conventions)
    to the next approved standard engineering size.
    """
    key_lower = key.lower()
    try:
        val = float(expression)
    except (ValueError, TypeError):
        return expression
    if not isfinite(val) or val <= 0:
        return expression

    is_meters = (0.0 < val < 1.0)
    current_mm = val * 1000.0 if is_meters else val

    # Determine parameter category
    category = fix_type
    if category == "auto":
        if any(b in key_lower for b in ["_d", "dia", "bolt"]):
            category = "bolt_dia"
        elif any(w in key_lower for w in ["weld", "w1", "w2"]):
            category = "weld_size"
        else:
            category = "plate_thickness"

    if category == "bolt_dia":
        next_mm = get_next_bolt_diameter(current_mm)
    elif category == "weld_size":
        next_mm = get_next_weld_size(current_mm)
    else:
        next_mm = get_next_plate_thickness(current_mm)

    if next_mm <= current_mm:
        return expression

    if is_meters:
        # Format back to meters string (e.g. 0.020 for 20mm)
        return f"{next_mm / 1000.0:.4f}".rstrip("0").rstrip(".") if (next_mm / 1000.0) != round(next_mm / 1000.0, 3) else f"{next_mm / 1000.0:.3f}"
    else:
        return str(int(next_mm)) if next_mm == int(next_mm) else str(next_mm)


