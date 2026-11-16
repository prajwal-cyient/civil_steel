"""Validate serialized STAAD command order before starting the solver."""

import re


def prepare_analysis_input(source: str) -> str:
    """Ensure analysis precedes result envelopes; preserve loads and design data."""
    lines = source.splitlines(keepends=True)
    envelope = next((i for i, line in enumerate(lines)
                     if re.match(r"^\s*DEFINE\s+ENVELOPE\b", line, re.I)), None)
    if envelope is None:
        raise ValueError("Generated STAAD input has no DEFINE ENVELOPE command.")
    analyses = [i for i, line in enumerate(lines)
                if re.match(r"^\s*PERFORM\s+ANALYSIS\b", line, re.I)]
    if any(i < envelope for i in analyses):
        return source
    if analyses:
        raise ValueError("PERFORM ANALYSIS occurs after result envelopes; refusing ambiguous command order.")
    newline = "\r\n" if "\r\n" in source else "\n"
    lines.insert(envelope, f"PERFORM ANALYSIS{newline}")
    return "".join(lines)


def analysis_errors(report: str) -> list[str]:
    """Read solver errors, including wrapped explanations, from an ANL report."""
    lines = report.splitlines()
    errors = []
    for i, line in enumerate(lines):
        if re.search(r"\*\*\*\s*ERROR\b", line, re.I):
            parts = [line.strip()]
            for continuation in lines[i + 1:i + 4]:
                if not continuation.strip() or "***" in continuation:
                    break
                parts.append(continuation.strip())
            errors.append(" ".join(parts))
    return errors
