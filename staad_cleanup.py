"""Reset the active STAAD analytical input, reopen it, and save.

Importing this module does not connect to STAAD. Calling main() is destructive.
Requires openstaadpy, as used by staad_integration.py.
"""

from pathlib import Path
import shutil
import tempfile
import time


EMPTY_MODEL_PATH = Path(__file__).resolve().parent / "sample_projects" / "empty_staad_project.std"


def _verify_open_model(staad, path: Path) -> None:
    # OpenSTAADFile returns before the GUI has finished switching models.
    deadline = time.monotonic() + 30
    counts = None
    while True:
        filename = staad.GetSTAADFile()
        if filename and Path(filename).resolve() == path.resolve():
            geometry = staad.Geometry
            counts = (
                geometry.GetNodeCount(), geometry.GetMemberCount(),
                geometry.GetPlateCount(), geometry.GetSolidCount(),
            )
            if all(count == 0 for count in counts):
                return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"STAAD did not open the requested empty model within 30 seconds: {path}. "
                f"Current file: {filename!r}; geometry counts: {counts}"
            )
        time.sleep(0.25)


def main() -> None:
    """Back up and reset the current .std file, leaving that same project open."""
    from openstaadpy import os_analytical

    empty_model = EMPTY_MODEL_PATH.read_bytes()
    staad = os_analytical.connect()
    if staad is None:
        raise RuntimeError("Could not connect to a running STAAD.Pro session.")
    filename = staad.GetSTAADFile()
    if not filename:
        raise RuntimeError("STAAD.Pro must have a saved analytical .std model open.")
    path = Path(filename)
    if not path.is_absolute() or path.suffix.lower() != ".std" or not path.is_file():
        raise RuntimeError(f"Not an existing STAAD analytical input file: {path}")
    if staad.IsPhysicalModel():
        raise RuntimeError("Physical models are not supported; open an analytical model.")
    if staad.IsAnalyzing():
        raise RuntimeError("Wait for STAAD analysis to finish before cleanup.")

    staad.SaveModel(True)
    # Exclusive creation keeps backups from earlier runs intact.
    with tempfile.NamedTemporaryFile(
        prefix=f"{path.stem}.before-cleanup-", suffix=".std.bak",
        dir=path.parent, delete=False,
    ) as backup:
        backup_path = Path(backup.name)
    shutil.copy2(path, backup_path)
    print(f"STAAD input backup: {backup_path}", flush=True)

    with tempfile.NamedTemporaryFile(
        prefix=f"{path.stem}.cleanup-stage-",
        suffix=".std", dir=path.parent, delete=False,
    ) as staging:
        staging_path = Path(staging.name)
    original_reopened = False
    try:
        staging_path.write_bytes(empty_model)
        # Switching to a different file proves the original model is no longer
        # active. GetSTAADFile can retain its path after CloseSTAADFile.
        staad.OpenSTAADFile(str(staging_path))
        _verify_open_model(staad, staging_path)

        with tempfile.NamedTemporaryFile(
            prefix=f"{path.stem}.cleanup-", suffix=".std",
            dir=path.parent, delete=False,
        ) as replacement:
            replacement_path = Path(replacement.name)
        try:
            replacement_path.write_bytes(empty_model)
            replacement_path.replace(path)
        finally:
            replacement_path.unlink(missing_ok=True)

        staad.OpenSTAADFile(str(path))
        _verify_open_model(staad, path)
        original_reopened = True
        staad.SaveModel(True)
        if path.read_bytes().split() != empty_model.split():
            raise RuntimeError(
                f"Saved STAAD input differs from {EMPTY_MODEL_PATH}. Inspect {path}."
            )
    finally:
        if original_reopened:
            staging_path.unlink(missing_ok=True)
        else:
            print(
                f"Cleanup interrupted; staging input retained: {staging_path}. "
                f"Original input backup: {backup_path}", flush=True,
            )
    print(f"STAAD cleanup saved: {path}")


if __name__ == "__main__":
    main()
