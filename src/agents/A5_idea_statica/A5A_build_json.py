"""Build IDEA requests from verified STAAD connection evidence."""

import os

from config.settings import settings
from agents.A5_idea_statica.package_validation import reference_plate_material_policy
from agents.A5_idea_statica.idea_runtime import IdeaStaticaJointError
from config.run_policy import RunPolicy
from orchestration.state import WorkflowState


def connection_output_dir(state, joint_group, root):
    if joint_group in state.change_scope.get('groups', []) and not state.change_scope.get('reuse_calculations'):
        return os.path.join(root, 'output_exports', 'dynamic_changes', state.run_id, joint_group)
    return state.change_scope.get('artifact_dirs', {}).get(joint_group, os.path.join(root, f'output_{joint_group}'))


def _connection_payload(state: WorkflowState, joint_group: str) -> dict:
    """Use the exact template and all required strength envelopes, never sample loads."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if state.staad_results.get("results_local_or_global", 0) != 0:
        raise IdeaStaticaJointError("IDEA input requires local member forces; global forces need an explicit verified transformation.")
    from agents.A3_generate_structure.connection_scope import scope_for_state, template_for_group
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

from datetime import datetime
import hashlib
import json
from orchestration.state import AgentLogEntry, fail_workflow


def _connection_signature(payload: dict, generation: int) -> str:
    template = payload.get("template_project_path")
    template_hash = None
    if template and os.path.isfile(template):
        with open(template, "rb") as source:
            template_hash = hashlib.sha256(source.read()).hexdigest()
    return hashlib.sha256(json.dumps({"generation": generation, "payload": payload, "template_hash": template_hash}, sort_keys=True, allow_nan=False).encode()).hexdigest()


def begin_a5(state: WorkflowState) -> dict:
    """Prepare a per-visit context without keeping native clients in graph state."""
    state.current_agent = "A5"
    context = {"index": 0, "groups": [], "selected": set(), "scope": {}, "complete": False}
    if not state.frame_grader_result or not state.frame_grader_result.passed or state.staad_results.get("analysis_failed"):
        fail_workflow(state, "A5", "A verified passing STAAD analysis is required before connection design.")
        return context
    joint_groups = state.staad_results.get("joint_groups", {})
    if not joint_groups:
        fail_workflow(state, "A5", "STAAD returned no connection groups.")
        return context
    from agents.A3_generate_structure.connection_scope import scope_for_state
    try:
        scope = scope_for_state(state)
    except ValueError as exc:
        fail_workflow(state, "A5", str(exc))
        return context
    selected = set(state.change_scope.get("groups", []))
    context.update(scope=scope, selected=selected,
                   groups=sorted(scope["idea_required_groups"], key=lambda name: name in selected)
                   if selected else scope["idea_required_groups"])
    return context


def _fail_a5_step(state, context, exc):
    from utils.audit import exception_record
    exception_record(state, 'IDEA connection execution', exc)
    return fail_workflow(state, "A5", f"{context.get('joint_group', 'IDEA')}: connection execution failed: {exc}")


def prepare_a5(state: WorkflowState, context: dict) -> WorkflowState:
    """Choose one group and construct the exact IDEA request."""
    context["skip"] = False
    if context["index"] >= len(context["groups"]):
        context["complete"] = True
        scope = context["scope"]
        message = (f"All {len(scope['idea_required_groups'])} required IDEA connection groups passed the configured rubric; "
                   f"{len(scope['standard_detail_groups'])} families use the senior workflow's standard Tekla details.")
        state.agent_statuses["A5"] = {"status": "PASSED", "message": message}
        state.logs.append(AgentLogEntry(agent_id="A5", agent_name="Connection Designer", status="PASSED", message=message, timestamp=datetime.now().astimezone().isoformat()))
        return state
    joint_group = context["groups"][context["index"]]
    context["joint_group"] = joint_group
    try:
        payload = globals()['_connection_payload'](state, joint_group)
        signature = _connection_signature(payload, state.design_generation)
        context.update(payload=payload, signature=signature, generation=state.design_generation)
        previous = state.ideastatica_results.get(joint_group, {})
        grader = state.joint_grader_results.get(joint_group)
        if grader and grader.passed and previous.get("input_signature") == signature:
            try:
                __import__('agents.A5_idea_statica.A5C_evaluate_and_audit', fromlist=['validate_calculation_check_coverage']).validate_calculation_check_coverage(previous.get("check_summary", {}).get("summary", []))
                __import__('agents.A5_idea_statica.package_validation', fromlist=['verify_plate_material_evidence']).verify_plate_material_evidence(previous.get("plate_material_evidence"),
                    payload.get("plate_material_policy"), previous.get("iom_connection_data_file"))
                reusable = (previous.get("passed") is True and not previous.get("simulation")
                            and not previous.get("output_messages")
                            and __import__('agents.A5_idea_statica.A5C_evaluate_and_audit', fromlist=['grade_joint_check_summary']).grade_joint_check_summary(previous.get("check_summary", {}), state.run_policy).passed)
            except (IdeaStaticaJointError, ValueError):
                reusable = False
            if reusable:
                from utils.run_archive import guarded, record_idea
                guarded(state, record_idea, joint_group, payload, {**previous, 'archive_evidence_reused':True})
                context["index"] += 1
                context["skip"] = True
                return state
        if context["selected"] and joint_group not in context["selected"]:
            return fail_workflow(state, "A5", f"Unselected group {joint_group} lacks reusable verified evidence. Broader analysis requires a new approved plan.")
        return state
    except Exception as exc:
        return _fail_a5_step(state, context, exc)


def _check_a5_request(state, context):
    if (context["generation"] != state.design_generation
            or context["signature"] != _connection_signature(
                globals()['_connection_payload'](state, context["joint_group"]), state.design_generation)):
        raise IdeaStaticaJointError("IDEA inputs or template changed during connection design; the results cannot be used.")




#!/usr/bin/env python3
"""
build_idea_statica_input.py

Build an IDEA StatiCa joint-design input file from a STAAD analysis output.

Reads the STAAD output JSON written by send_to_staad() (e.g.
analysis_output_staad.json) and selects
envelope_load_cases_by_joint_group[<envelope id>][<joint group id>], which
holds the member_updates and load_effects for that joint group under that
envelope. Combines them with operation parameters from a separate JSON file
and writes a file that can be passed to:

    python src/common/idea_statica_integration.py --design <output file>

This is the same payload the web UI builds when you click "Send to IdeaStatica".

The operation parameters file may be either a JSON list of
{"key": ..., "expression": ...} entries, or an object with an
"operation_parameters" list (e.g. an earlier design input file such as
inner_node_1_longitudinal_1_transverse_bracing.json).

Usage:
    python src/common/build_idea_statica_input.py analysis_output_staad.json \\
        inner_node_1_longitudinal_1_transverse_bracing.json 1 \\
        inner_node_1_longitudinal_1_transverse_bracing \\
        --output inner_node_1_longitudinal_1_transverse_bracing_design.json
"""

import argparse
import json
import sys
from typing import Any, Dict, List


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_operation_parameters(path: str) -> List[Dict[str, Any]]:
    data = load_json(path)
    if isinstance(data, dict):
        data = data.get("operation_parameters")
    if not isinstance(data, list):
        raise ValueError(
            f"{path}: expected a list of operation parameters, or an object "
            'with an "operation_parameters" list.'
        )
    for index, param in enumerate(data):
        if not isinstance(param, dict) or "key" not in param or "expression" not in param:
            raise ValueError(
                f'{path}: operation parameter {index} needs "key" and "expression".'
            )
    return data


def build_design_input(
    staad_output: Dict[str, Any],
    operation_parameters: List[Dict[str, Any]],
    envelope_id: str,
    joint_group_id: str,
) -> Dict[str, Any]:
    by_envelope = staad_output.get("envelope_load_cases_by_joint_group")
    if not by_envelope:
        raise ValueError('STAAD output has no "envelope_load_cases_by_joint_group".')

    # Envelope ids are ints in send_to_staad() but become string keys in JSON.
    envelope_key = str(envelope_id)
    if envelope_key not in by_envelope:
        raise ValueError(
            f"Envelope {envelope_key} not found. Available envelopes: "
            f"{', '.join(by_envelope)}"
        )
    by_joint_group = by_envelope[envelope_key]
    if joint_group_id not in by_joint_group:
        raise ValueError(
            f"Joint group {joint_group_id} not found in envelope {envelope_key}. "
            f"Available joint groups: {', '.join(by_joint_group)}"
        )
    joint_payload = by_joint_group[joint_group_id]

    load_effects = joint_payload.get("load_effects") or []
    if not load_effects:
        raise ValueError(
            f"No load effects from STAAD for joint group {joint_group_id} "
            f"in envelope {envelope_key}."
        )

    return {
        "template_project_path": f"sample_projects/{joint_group_id}.ideaCon",
        "connection_selector": {"name": "CON1"},
        "member_updates": joint_payload.get("member_updates") or [],
        "load_effects": load_effects,
        "output_dir": "output_CON1",
        "save_project_path": "",
        "operation_parameters": operation_parameters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an idea_statica_integration.py --design input file "
        "from a STAAD analysis output."
    )
    parser.add_argument("-s", "--staad_output", help="STAAD analysis output JSON (from send_to_staad())")
    parser.add_argument("-o", "--operation_parameters", help="JSON file with the operation parameters")
    parser.add_argument("-e", "--envelope_id", help="Envelope id, e.g. 1")
    parser.add_argument("-j", "--joint_group_id", help="Joint group id, e.g. inner_node_1_longitudinal_1_transverse_bracing")
    parser.add_argument(
        "--output",
        help="Path to write the design input JSON (default: analysis_output_<joint_group_id>_envelope_<envelope_id>.json)",
    )
    args = parser.parse_args()

    output_path = args.output or f"analysis_output_{args.joint_group_id}_envelope_{args.envelope_id}.json"

    try:
        design_input = build_design_input(
            load_json(args.staad_output),
            load_operation_parameters(args.operation_parameters),
            args.envelope_id,
            args.joint_group_id,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(design_input, f, indent=2)

    print(
        f"Wrote {output_path}: {len(design_input['member_updates'])} member updates, "
        f"{len(design_input['load_effects'])} load effects, "
        f"{len(design_input['operation_parameters'])} operation parameters."
    )


if __name__ == "__main__":
    main()
