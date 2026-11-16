"""Explicit scope of the senior radiator workflow, separate from full joint FEA."""
from pathlib import Path
from copy import deepcopy

from agentic.config import settings, workflow_mode


REFERENCE_IDEA_TEMPLATES = {
    "inner_node_1_longitudinal_bracing": "inner_node_1_longitudinal_bracing.ideaCon",
    "inner_node_1_longitudinal_1_transverse_bracing": "inner_node_1_longitudinal_1_transverse_bracing.ideaCon",
    "inner_node_2_longitudinal_bracings": "two-longitudinal.ideaCon",
    "inner_node_2_longitudinal_1_transverse_bracing": "two-longitudinal-transverse.ideaCon",
}
STANDARD_DETAILS = {
    "base": {"component": 1004, "attribute_file": "baseplate_1004"},
    "column_beam": {"component": 144, "attribute_file": "column_longitudinal_beam"},
    "brace_column": {"component": 20, "attribute_file": "bracing_column"},
    "beam_beam": {"component": 30, "attribute_file": "longitudinal_beam_transverse_beam"},
}


def build_connection_scope(groups) -> dict:
    required = sorted(groups)
    reference = workflow_mode() != "reviewed_engineering"
    calculated = [name for name in required if name in REFERENCE_IDEA_TEMPLATES] if reference else required
    return {
        "mode": "reference_procedure" if reference else "all_joint_families",
        "idea_required_groups": calculated,
        "standard_detail_groups": sorted(set(required) - set(calculated)),
        "standard_details": deepcopy(STANDARD_DETAILS) if reference else {},
        "material_catalogue_mapping": {"S355JR": "S 355"} if reference else {},
        "basis": "Senior build_tekla_input.py and radiator_support/tekla_integration.py; standard details are not separate IDEA passes.",
    }


def scope_for_state(state) -> dict:
    groups = state.staad_results.get("joint_groups", {})
    scope = state.readiness.get("connection_scope")
    if scope is None:
        # Legacy persisted states and callers without an explicit plan retain
        # their full-coverage contract. Never infer a narrower scope here.
        return {"mode": "all_joint_families", "idea_required_groups": sorted(groups),
                "standard_detail_groups": [], "standard_details": {}}
    calculated = scope.get("idea_required_groups", [])
    standard = scope.get("standard_detail_groups", [])
    if (set(calculated) & set(standard) or set(calculated) | set(standard) != set(groups)
            or len(calculated) != len(set(calculated)) or len(standard) != len(set(standard))):
        raise ValueError("Connection scope does not cover the current joint families exactly once.")
    if scope.get("mode") == "all_joint_families" and (standard or scope.get("material_catalogue_mapping")):
        raise ValueError("Full joint verification cannot substitute standard details for IDEA calculations.")
    if scope.get("mode") == "reference_procedure":
        expected = set(groups) & set(REFERENCE_IDEA_TEMPLATES)
        if (set(calculated) != expected or scope.get("standard_details") != STANDARD_DETAILS
                or scope.get("material_catalogue_mapping") != {"S355JR": "S 355"}):
            raise ValueError("Connection scope differs from the declared senior reference procedure.")
    elif scope.get("mode") != "all_joint_families":
        raise ValueError("Unknown connection verification scope.")
    return scope


def template_for_group(group: str, reference: bool = False) -> Path:
    filename = REFERENCE_IDEA_TEMPLATES.get(group, f"{group}.ideaCon") if reference else f"{group}.ideaCon"
    return settings().templates / filename
