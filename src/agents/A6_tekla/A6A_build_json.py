"""Build a Tekla publication request from audited workflow evidence."""

from orchestration.state import WorkflowState


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

from copy import deepcopy
from orchestration.state import workflow_stage

def prepare_a7_tekla(state: WorkflowState) -> WorkflowState:
    from orchestration.state import fail_workflow
    # local build_tekla_payload
    state.current_agent = workflow_stage(state, "tekla")
    state.tekla_request.clear()
    state.tekla_publication_passed = None
    state.tekla_prepublication_error = None
    state.tekla_grader_result = None
    state.tekla_publication_passed = None
    if state.change_scope:
        return fail_workflow(state, workflow_stage(state, "tekla"), "Selected changes must use the scoped Tekla adapter; full-model cleanup is blocked.")
    if not state.validation_report.get("passed"):
        return fail_workflow(state, workflow_stage(state, "tekla"), "A passing package validation is required before Tekla publication.")
    if state.tekla_retry_count:
        from agents.A5_idea_statica.package_validation import run_a6_package_validator
        state = run_a6_package_validator(state, agent_id="A5c")
        if (state.status == "FAILED" or not state.validation_report.get("passed")
                or state.agent_statuses.get("A5c", {}).get("status") != "PASSED"):
            return fail_workflow(state, workflow_stage(state, "tekla"), "Calculation evidence failed revalidation before Tekla retry.")
        state.current_agent = workflow_stage(state, "tekla")
    state.tekla_request = deepcopy(build_tekla_payload(state))
    return state


def _tekla_request_matches(state: WorkflowState) -> bool:
    # local build_tekla_payload
    return bool(state.tekla_request) and state.tekla_request == build_tekla_payload(state)




#!/usr/bin/env python3
"""
build_tekla_input.py

Build a tekla_integration.py input file from a generated grid, a STAAD
analysis output and one or more IDEA StatiCa design outputs.

The output contains every field of the generated grid JSON (grid, members,
members_split_beams, ...) plus a "joint_groups" object, built as follows:

    "joint_groups": {
        "<joint_group>": {
            "joints": <STAAD output["joint_groups"][<joint_group>]["joints"]>,
            "operation_parameters": <IDEA output["check_summary"]["operation_parameters"]>
        },
        ...
    }

Each IDEA StatiCa output is the JSON returned by design_joint() (the
/api/v1/to_ideastatica response). Its "joint_group" field names the joint
group. send_to_tekla() uses the joints' member "tekla_name" values to find
the Tekla columns and beams at each joint, and maps the operation parameters
onto the Tekla connection attributes.

This is the same "joint_groups" payload the web UI collects after each
"Send to IdeaStatica" and sends with "Send to Tekla".

Usage:
    python src/radiator_support/build_tekla_input.py -s analysis_output_staad.json -g generated_grid.json \\
        -i idea_output_inner_node_1_longitudinal_bracing.json \\
           idea_output_inner_node_1_longitudinal_1_transverse_bracing.json \\
        --output tekla_input.json

    python src/radiator_support/tekla_integration.py --input tekla_input.json
"""

import argparse
import json
import sys
from typing import Any, Dict, List


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def build_joint_groups(
    staad_output: Dict[str, Any],
    idea_outputs: List[Dict[str, Any]],
    idea_output_paths: List[str],
) -> Dict[str, Any]:
    staad_joint_groups = staad_output.get("joint_groups")
    if not staad_joint_groups:
        raise ValueError('STAAD output has no "joint_groups".')

    joint_groups: Dict[str, Any] = {}
    for path, idea_output in zip(idea_output_paths, idea_outputs):
        if not isinstance(idea_output, dict):
            raise ValueError(f"{path}: expected a JSON object (the design_joint() result).")

        joint_group_id = idea_output.get("joint_group")
        if not joint_group_id:
            raise ValueError(f'{path}: no "joint_group" field.')

        check_summary = idea_output.get("check_summary")
        operation_parameters = (
            check_summary.get("operation_parameters") if isinstance(check_summary, dict) else None
        )
        if operation_parameters is None:
            raise ValueError(f'{path}: no "check_summary" -> "operation_parameters".')

        if joint_group_id not in staad_joint_groups:
            raise ValueError(
                f"{path}: joint group {joint_group_id} is not in the STAAD output. "
                f"Available joint groups: {', '.join(staad_joint_groups)}"
            )
        joints = staad_joint_groups[joint_group_id].get("joints") or []

        # send_to_tekla() reads member["tekla_name"] to find the Tekla parts at each
        # joint. Older STAAD outputs don't include it.
        missing = sorted({
            member.get("name") or str(member.get("beam"))
            for joint in joints for member in joint.get("members", [])
            if not member.get("tekla_name")
        })
        if missing:
            raise ValueError(
                f"STAAD output joint group {joint_group_id} has members without "
                f"\"tekla_name\" ({', '.join(missing[:5])}{', ...' if len(missing) > 5 else ''}). "
                "Re-run Send to STAAD with members_split_beams generated by the current generate_grid.py."
            )

        if joint_group_id in joint_groups:
            _warn(f"joint group {joint_group_id} appears in more than one IDEA StatiCa output; using {path}.")
        if idea_output.get("passed") is False:
            _warn(f"{path}: joint group {joint_group_id} failed its IDEA StatiCa checks.")

        joint_groups[joint_group_id] = {
            "joints": joints,
            "operation_parameters": operation_parameters,
        }
    return joint_groups


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a tekla_integration.py input file from a generated grid, "
        "a STAAD output and IDEA StatiCa design outputs."
    )
    parser.add_argument("-s", "--staad_output", required=True, help="STAAD analysis output JSON (from send_to_staad())")
    parser.add_argument("-g", "--generated_grid", required=True, help="Generated grid JSON (from generate_grid.py)")
    parser.add_argument("-i", "--idea_outputs", required=True, nargs="+", help="IDEA StatiCa design output JSON files (design_joint() results)")
    parser.add_argument("--output", default="analysis_output_tekla_input.json", help="Path to write the Tekla input JSON (default: tekla_input.json)")
    args = parser.parse_args()

    try:
        generated_grid = load_json(args.generated_grid)
        if not isinstance(generated_grid, dict) or "members" not in generated_grid:
            raise ValueError(f'{args.generated_grid}: expected a generated grid JSON with "members".')
        joint_groups = build_joint_groups(
            load_json(args.staad_output),
            [load_json(path) for path in args.idea_outputs],
            args.idea_outputs,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if "joint_groups" in generated_grid:
        _warn(f'{args.generated_grid} already has "joint_groups"; replacing them.')
    output = {**generated_grid, "joint_groups": joint_groups}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    breakdown = ", ".join(
        f"{joint_group_id} ({len(group['joints'])} joints, {len(group['operation_parameters'])} parameters)"
        for joint_group_id, group in joint_groups.items()
    )
    print(f"Wrote {args.output}: {len(output['members'])} members; joint groups: {breakdown}")


if __name__ == "__main__":
    main()
