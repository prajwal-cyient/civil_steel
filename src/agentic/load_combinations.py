"""Explicit project combinations, with checks against accidental load cancellation."""

import json
from math import isfinite
import os
from pathlib import Path
from agentic.config import workflow_mode


CASE_NAMES = {"DL", "INST", "LL", "W+X", "W-X", "W+Z", "W-Z", "EQ+X", "EQ+Z",
              "EQ+X_LOCAL", "EQ+Z_LOCAL", "SNOW", "T+", "T-"}


def reference_combinations() -> list:
    """Exact eight cases and three envelopes from civil-steel-standard-web."""
    cases = ["DL", "INST", "LL", "W+X", "W-X", "W+Z", "W-Z",
             "EQ+X", "EQ+Z", "EQ+X_LOCAL", "EQ+Z_LOCAL", "SNOW", "T+", "T-"]
    parameters = [
        (1, "SERVICEABILITY", 0.8, 0.75, 0.75, 1.2),
        (1, "SERVICEABILITY", 0.95, 1.2, 1.1, 1.5),
        (2, "STRENGTH", 0.95, 1.0, 1.2, 1.5),
        (2, "STRENGTH", 0.95, 1.2, 1.1, 1.5),
        (2, "STRENGTH", 0.95, 1.2, 1.1, 1.5),
        (3, "STRENGTH", 0.95, 1.3, 1.4, 1.5),
        (3, "STRENGTH", 0.95, 0.9, 0.9, 1.5),
        (3, "STRENGTH", 0.95, 1.5, 1.5, 1.5),
    ]
    return [{"name": f"Comb DL LL WL {index}", "envelope": kind, "envelope_id": envelope,
             "factors": dict(zip(cases, [1, 1, live] + [wind] * 4 + [seismic] * 5 + [temperature] * 2))}
            for index, (envelope, kind, live, wind, seismic, temperature) in enumerate(parameters, 1)]


def validate_combinations(combinations: list, *, reference_mode: bool = False) -> list:
    if not combinations:
        raise ValueError("Explicit engineer-reviewed load combinations are required; legacy opposing wind/temperature sums are not valid verification evidence.")
    names, envelopes = set(), set()
    for combination in combinations:
        if set(combination) - {"name", "envelope", "factors", "envelope_id"} or not {"name", "envelope", "factors"}.issubset(combination):
            raise ValueError("Each combination needs name, envelope and factors.")
        name, envelope, factors = combination["name"], combination["envelope"], combination["factors"]
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Combination names must be nonempty and unique.")
        if envelope not in ("SERVICEABILITY", "STRENGTH"):
            raise ValueError(f"Invalid combination envelope for {name}.")
        if not isinstance(factors, dict) or not factors or set(factors) - CASE_NAMES:
            raise ValueError(f"Unsupported or empty load-case factors for {name}.")
        if any(type(value) not in (int, float) or not isfinite(value) or value == 0 for value in factors.values()):
            raise ValueError(f"Nonfinite, zero or invalid load factor in {name}.")
        for first, second in (("W+X", "W-X"), ("W+Z", "W-Z"), ("T+", "T-")):
            if first in factors and second in factors and not reference_mode:
                raise ValueError(f"Combination {name} includes mutually exclusive {first} and {second}.")
        names.add(name)
        envelopes.add(envelope)
    if envelopes != {"SERVICEABILITY", "STRENGTH"}:
        raise ValueError("Both serviceability and strength combinations are required.")
    return combinations


def resolve_combinations(load_config: dict) -> list:
    combinations = load_config.get("load_combinations")
    path = os.getenv("PLANT_LOAD_COMBINATIONS_FILE")
    if combinations is None and path:
        content = json.loads(Path(path).read_text(encoding="utf-8"))
        combinations = content.get("combinations") if isinstance(content, dict) else content
    if combinations is None and workflow_mode() != "reviewed_engineering":
        combinations = reference_combinations()
    reference = workflow_mode() != "reviewed_engineering" and combinations == reference_combinations()
    return validate_combinations(combinations or [], reference_mode=reference)
