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
