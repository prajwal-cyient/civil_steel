"""Read-only checks against installed STAAD section databases."""

import os
from pathlib import Path
import re
import sqlite3

from agentic.ladders import COLUMN_PROFILE_LADDER, BEAM_PROFILE_LADDER, BRACE_PROFILE_LADDER


def section_exists(profile: str, country: str = "EUROPE", directory: Path | None = None) -> bool:
    if country.upper() != "EUROPE":
        return False  # A different catalogue needs an explicit mapping, never a guess.
    if directory is None:
        explicit = os.getenv("STAAD_SECTION_DATABASE_PATH")
        if explicit:
            directory = Path(explicit)
        else:
            root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "Bentley" / "Engineering"
            candidates = sorted(root.glob("STAAD.Pro */Sections"))
            if not candidates:
                return False
            directory = candidates[-1]
    match = re.fullmatch(r"CFRHS([\d.]+)X([\d.]+)X([\d.]+)", profile, re.I)
    if match:
        h, b, t = match.groups()
        name = ("SHS" if float(h) == float(b) else "RHS") + f"{h}x{b}x{t}"
        database = directory / "EuropeanColdFormedSections.db3"
    else:
        name = "HE" + profile[3:] + "A" if profile.startswith("HEA") else profile
        database = directory / "EuropeanSections.db3"
    if not database.is_file():
        return False
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = '"' + table.replace('"', '""') + '"'
            if "StaadName" not in {row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")}:
                continue
            if connection.execute(f"SELECT 1 FROM {quoted} WHERE lower(StaadName)=lower(?) LIMIT 1", (name,)).fetchone():
                return True
        return False
    finally:
        connection.close()


def frame_options(current: str, target: str, country: str = "EUROPE") -> list[dict]:
    ladder = COLUMN_PROFILE_LADDER if target == "column" else BEAM_PROFILE_LADDER if target == "beam" else BRACE_PROFILE_LADDER
    if current not in ladder:
        return []
    from common.tekla_common import get_profile_height, get_profile_width
    options = []
    for profile in ladder[ladder.index(current) + 1:]:
        if not section_exists(profile, country):
            continue
        if not get_profile_height(profile) or not get_profile_width(profile):
            continue
        options.append({"id": profile, "profile": profile, "target": target})
    return options
