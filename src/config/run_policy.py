"""Validated acceptance preferences belonging to one calculation run only."""
from pydantic import BaseModel, ConfigDict, Field


class RunPolicy(BaseModel):
    # Per-run preferences never replace native solver checks or human approval.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    max_member_ratio: float = Field(default=1.0, gt=0, le=1.0)
    drift_denominator: float = Field(default=200.0, ge=200.0)
    max_bolt_utilization_percent: float = Field(default=75.0, gt=0, le=100.0)
    max_weld_utilization_percent: float = Field(default=100.0, gt=0, le=100.0)
    max_plate_utilization_percent: float = Field(default=100.0, gt=0, le=100.0)
    max_deformation_utilization_percent: float = Field(default=100.0, gt=0, le=100.0)


def policy_for(state) -> RunPolicy:
    return RunPolicy.model_validate(state.run_policy)
