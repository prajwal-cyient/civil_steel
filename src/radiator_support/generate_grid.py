#!/usr/bin/env python3
"""
generate_grid.py

Backward-compatibility interface.
The full 3D geometry engine has been absorbed into Agent A3 (a3_frame_generator_agent.py).
"""

from agentic.agents.a3_frame_generator_agent import (
    generate_members,
    _lookup_profile_height,
    _lookup_profile_width,
    _lookup_profile_param,
    _fallback_dimension,
    STANDARD_PROFILE_DIMENSIONS
)

__all__ = [
    "generate_members",
    "_lookup_profile_height",
    "_lookup_profile_width",
    "_lookup_profile_param",
    "_fallback_dimension",
    "STANDARD_PROFILE_DIMENSIONS"
]

if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Generate 3D steel grid")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="output.json")
    args = parser.parse_args()
    with open(args.input) as f:
        cfg = json.load(f)
    grid_cfg = cfg.get("grid", cfg)
    result = generate_members(grid_cfg)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Generated {result['summary']['total_members']} members to {args.output}")
