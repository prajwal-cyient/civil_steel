"""Profile and height evaluation for frame generation."""

from typing import Any, Callable, Dict, NamedTuple


class FrameParameters(NamedTuple):
    beam_profile: str
    column_profile: str
    col_height_mm: float
    total_structure_height: float
    beam_height_mm: float
    column_width_mm: float
    beam_width_mm: float
    column_section_height_mm: float
    effective_col_height_mm: float


def compute_frame_parameters(
    cfg: Dict[str, Any],
    lookup_height: Callable[[str], float],
    lookup_width: Callable[[str], float],
) -> FrameParameters:
    beam_profile = cfg.get("beam_profile", "HEA140")
    column_profile = cfg.get("column_profile", "CFRHS120X120X5")
    col_height = cfg["col_height_mm"]
    total_structure_height = cfg["total_structure_height"]

    beam_height = lookup_height(beam_profile)
    column_width = lookup_width(column_profile)
    beam_width = lookup_width(beam_profile)
    column_section_height = lookup_height(column_profile)

    max_col_height = total_structure_height - 2 * beam_height
    if max_col_height <= 0:
        raise ValueError(
            f"total_structure_height ({total_structure_height} mm) is too small to fit "
            f"two beam depths ({2 * beam_height} mm) plus any positive column height."
        )
    return FrameParameters(
        beam_profile, column_profile, col_height, total_structure_height,
        beam_height, column_width, beam_width, column_section_height,
        min(col_height, max_col_height),
    )

import math
import re
from typing import Tuple

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
        from agents.A6_tekla.tekla_open_api import get_profile_height
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
        from agents.A6_tekla.tekla_open_api import get_profile_width
        val = _lookup_profile_param(profile_name, get_profile_width)
    except Exception:
        val = 0.0
    if not val:
        val = _fallback_dimension(profile_name, 1)
    return val


def evaluate_frame_parameters(cfg: Dict[str, Any]) -> FrameParameters:
    """Evaluate profile dimensions and the existing column-height constraint."""
    return compute_frame_parameters(cfg, _lookup_profile_height, _lookup_profile_width)




def validate_profile_catalogue(cfg: dict) -> None:
    """Live workflows must use catalogue dimensions, never preview fallbacks."""
    from agents.A6_tekla.tekla_open_api import get_profile_height, get_profile_width
    keys = ("column_profile", "beam_profile", "longitudinal_brace_profile", "transverse_brace_profile")
    for profile in {cfg[key] for key in keys}:
        dimensions = (get_profile_height(profile), get_profile_width(profile))
        if any(value is None or not math.isfinite(float(value)) or value <= 0 for value in dimensions):
            raise ValueError(f"Profile {profile} is unavailable in the live Tekla catalogue.")
