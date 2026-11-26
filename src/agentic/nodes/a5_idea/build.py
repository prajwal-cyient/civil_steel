"""Build IDEA requests from verified STAAD connection evidence."""

import os

from agentic.config import settings
from agentic.connection_materials import reference_plate_material_policy
from agentic.nodes.a5_idea.errors import IdeaStaticaJointError
from agentic.run_policy import RunPolicy
from agentic.state import WorkflowState


def connection_output_dir(state, joint_group, root):
    if joint_group in state.change_scope.get('groups', []) and not state.change_scope.get('reuse_calculations'):
        return os.path.join(root, 'output_exports', 'dynamic_changes', state.run_id, joint_group)
    return state.change_scope.get('artifact_dirs', {}).get(joint_group, os.path.join(root, f'output_{joint_group}'))


def _connection_payload(state: WorkflowState, joint_group: str) -> dict:
    """Use the exact template and all required strength envelopes, never sample loads."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    if state.staad_results.get("results_local_or_global", 0) != 0:
        raise IdeaStaticaJointError("IDEA input requires local member forces; global forces need an explicit verified transformation.")
    from agentic.reference_procedure import scope_for_state, template_for_group
    scope = scope_for_state(state)
    reference = scope["mode"] == "reference_procedure"
    template = str(template_for_group(joint_group, reference))
    if not os.path.isfile(template):
        missing = sorted(group for group in state.staad_results.get("joint_groups", {joint_group: {}})
                         if not (settings().templates / f"{group}.ideaCon").is_file())
        raise IdeaStaticaJointError(
            f"No matching connection template for {joint_group}. "
            f"Missing template mappings ({len(missing)}): {', '.join(missing)}. "
            "Provide compatible connection templates and verified member/end mappings; "
            "unrelated templates cannot verify these joints."
        )
    envelopes = [e for e in state.staad_results.get("load_envelopes", []) if e.get("envelopeType") == "STRENGTH"]
    if not envelopes:
        raise IdeaStaticaJointError("STAAD returned no strength envelopes.")
    by_envelope = state.staad_results.get("envelope_load_cases_by_joint_group", {})
    loads, updates = [], None
    for envelope in envelopes:
        env_id = envelope["envelopeId"]
        groups = by_envelope.get(env_id, by_envelope.get(str(env_id), {}))
        data = groups.get(joint_group, {})
        if not data.get("member_updates") or not data.get("load_effects"):
            raise IdeaStaticaJointError(f"Missing member properties or loads for {joint_group}, strength envelope {env_id}.")
        if updates is not None and updates != data["member_updates"]:
            raise IdeaStaticaJointError(f"Inconsistent member properties across envelopes for {joint_group}.")
        updates = data["member_updates"]
        for effect in data["load_effects"]:
            if not effect.get("member_loadings"):
                raise IdeaStaticaJointError(f"Empty load effect in envelope {env_id}.")
            loads.append({**effect, "name": f"Envelope {env_id}: {effect.get('name', 'ULS')}"})
    material_map = scope.get("material_catalogue_mapping", {})
    updates = [{**update, "material_mprl_name": material_map.get(update["material_mprl_name"], update["material_mprl_name"])}
               if "material_mprl_name" in update else update for update in updates]
    return {
        **({"run_policy": state.run_policy} if state.run_policy and state.run_policy != RunPolicy().model_dump() else {}),
        "joint_group_name": joint_group,
        "template_project_path": template, "connection_selector": {"name": "CON1"},
        "member_updates": updates, "load_effects": loads,
        "replace_template_loads": True,
        "enforce_member_coverage": True,
        "operation_parameters": state.active_operation_params.get(joint_group, []),
        "plate_material_policy": reference_plate_material_policy() if scope["mode"] == "reference_procedure" else None,
        "output_dir": connection_output_dir(state, joint_group, root),
        "extract_raw_results": False,
    }


