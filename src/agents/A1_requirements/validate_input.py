"""Validate grid, profile, and base geometry inputs for Agent A1."""

from typing import Any, Dict
from pydantic import BaseModel, ConfigDict, Field


class GridInputSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    n_long_bays: int = Field(default=8, gt=0)
    n_trans_bays: int = Field(default=5, gt=0)
    bay_long_mm: float = Field(default=2750.0, gt=0)
    bay_trans_mm: float = Field(default=1620.0, gt=0)
    col_height_mm: float = Field(default=5000.0, gt=0)
    total_structure_height: float = Field(default=5300.0, gt=0)
    starting_point: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])

    beam_material: str = "S355JR"
    column_material: str = "S355JR"
    longitudinal_brace_material: str = "S355JR"
    transverse_brace_material: str = "S355JR"

    beam_profile: str = "HEA140"
    column_profile: str = "CFRHS120X120X5"
    longitudinal_brace_profile: str = "CFRHS100X100X4"
    transverse_brace_profile: str = "CFRHS100X100X4"

    beamClass: str = "3"
    columnClass: str = "11"
    longitudinalClass: str = "2"
    transverseClass: str = "7"

    longitudinal_beam_extra_mm: float = 140.0
    vertical_base_eccentricity_mm: float = 0.0
    base_plate_offset_above_column_base_mm: float = Field(default=30.0, allow_inf_nan=False)
    gusset_plate_thickness_mm: float = 15.0
    embedded_plate_thickness_mm: float = 10.0
    bracing_distance_from_column_mm: float = 20.0
    longitudinal_brace_bays: list[Dict[str, Any]] = Field(default_factory=list)
    transverse_brace_bays: list[Dict[str, Any]] = Field(default_factory=list)


