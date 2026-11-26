"""Build a Tekla publication request from audited workflow evidence."""

from agentic.state import WorkflowState


def build_tekla_payload(state: WorkflowState) -> dict:
    if state.change_scope:
        raise ValueError("Selected changes must use the scoped Tekla adapter; full-model cleanup is blocked.")
    if not state.validation_report.get("passed"):
        raise ValueError("A passing package validation is required before Tekla publication.")
    return {
        "members": state.frame_candidate.get("members", []),
        "grid": state.grid_config,
        "joint_groups": state.validation_report.get("validated_joint_groups_payload", {}),
        "expected_tekla_model_path": state.readiness.get("tekla_model_path"),
        "plate_material_policy": state.validation_report.get("plate_material_policy"),
    }
