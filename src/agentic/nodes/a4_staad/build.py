"""Build the STAAD request from the current frame."""

from copy import deepcopy

from agentic.state import WorkflowState


def build_staad_payload(state: WorkflowState) -> dict:
    """Build the existing STAAD request without modifying workflow evidence."""
    return {
        "grid": state.grid_config,
        "members_split_beams": deepcopy(state.frame_candidate.get("members_split_beams", [])),
        "load_configuration": state.load_config,
        "expected_staad_model_path": state.readiness.get("staad_model_path"),
    }
