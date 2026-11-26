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


def evaluate_frame_parameters(
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
