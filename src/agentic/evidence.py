"""Bind calculation evidence to exactly the inputs that produced it."""

import hashlib
import json


def frame_signature(state) -> str:
    payload = {"grid": state.grid_config, "loads": state.load_config,
               "members": state.frame_candidate.get("members", []),
               "analytical_members": state.frame_candidate.get("members_split_beams", [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
