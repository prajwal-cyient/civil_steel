"""
a1_requirements_agent.py

Agent A1: Understand the Request & Validate Requirements.
Validates input JSON and form data against strict Pydantic schemas,
clarifies missing defaults, and outputs a Confirmed Brief.
"""

from datetime import datetime
from typing import Any, Dict
from pydantic import BaseModel, ConfigDict, Field
from agentic.state import WorkflowState, AgentLogEntry
from agentic.load_combinations import resolve_combinations
from agentic.config import workflow_mode
from agentic.load_combinations import reference_combinations


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
    gusset_plate_thickness_mm: float = 15.0
    embedded_plate_thickness_mm: float = 10.0
    bracing_distance_from_column_mm: float = 20.0
    longitudinal_brace_bays: list[Dict[str, Any]] = Field(default_factory=list)
    transverse_brace_bays: list[Dict[str, Any]] = Field(default_factory=list)


def run_a1_requirements(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A1: Validates project specifications and establishes confirmed brief.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A1"

    raw = state.raw_input
    grid_raw = raw.get("grid", raw)
    load_raw = raw.get("load_configuration", {})

    try:
        validated_grid = GridInputSchema(**grid_raw).model_dump()
        state.grid_config = validated_grid
        state.load_config = {**load_raw, "load_combinations": resolve_combinations(load_raw)}
        state.readiness["workflow_mode"] = workflow_mode()
        state.readiness["combination_source"] = "reference_procedure" if state.load_config["load_combinations"] == reference_combinations() else "project_input"

        # Set initial active profiles
        state.active_column_profile = validated_grid["column_profile"]
        state.active_beam_profile = validated_grid["beam_profile"]
        state.active_long_brace_profile = validated_grid["longitudinal_brace_profile"]
        state.active_trans_brace_profile = validated_grid["transverse_brace_profile"]

        msg = (
            f"Requirements validated: {validated_grid['n_long_bays']}x{validated_grid['n_trans_bays']} bays, "
            f"Col: {state.active_column_profile}, Beam: {state.active_beam_profile}."
        )
        state.agent_statuses["A1"] = {"status": "PASSED", "message": msg}
        state.logs.append(AgentLogEntry(
            agent_id="A1",
            agent_name="Requirements Validator",
            status="PASSED",
            message=msg,
            timestamp=ts
        ))

    except Exception as exc:
        err_msg = f"A1 Validation Error: {exc}"
        state.status = "FAILED"
        state.error = err_msg
        state.agent_statuses["A1"] = {"status": "FAILED", "message": err_msg}
        state.logs.append(AgentLogEntry(
            agent_id="A1",
            agent_name="Requirements Validator",
            status="FAILED",
            message=err_msg,
            timestamp=ts
        ))

    return state
