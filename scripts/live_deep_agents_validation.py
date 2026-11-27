"""Run real A1-A8 Deep Agents on isolated 8x5 models, then restore open models.

Requires the desktop software and configured GPT credentials. No mocks or gate
acceptance. Results, native models and per-agent evidence stay in output_exports.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.coinit_flags = 2
from config.settings import load_environment, deep_agents_enabled
load_environment()
from agents.A4_staad import staad_com_helpers as staad_common  # Initialize STA before pythonnet.
from agents.A6_tekla import tekla_open_api as tekla_common
from Tekla.Structures.Model import ModelHandler
from orchestration.execution import RunController


def check_idle():
    sources = [(ROOT / "data/runs.db", "workflow_runs"),
               (Path(r"C:\Users\lb89380\projects\siddharth\siddharth agentic poc2\civil-steel-standard-agentic\data\plant.db"), "runs")]
    for path, table in sources:
        if path.is_file():
            con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                if con.execute(f"select count(*) from {table} where status in ('RUNNING','QUEUED')").fetchone()[0]:
                    raise RuntimeError("Another workflow is active; native models not switched.")
                if table == "runs" and con.execute("select count(*) from model_leases").fetchone()[0]:
                    raise RuntimeError("Another application holds a native model lease.")
            finally:
                con.close()


def open_staad(staad, path):
    staad.OpenSTAADFile(str(path))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if staad_common.saved_model_path(staad).resolve() == path.resolve():
                return
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError("STAAD did not attach the requested isolated model.")


def open_tekla(handler, path):
    if not handler.Open(str(path), False):
        raise RuntimeError("Tekla did not open the requested model.")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        model = tekla_common.Model()
        if model.GetConnectionStatus() and Path(model.GetInfo().ModelPath).resolve() == path.resolve():
            return
        time.sleep(.5)
    raise RuntimeError("Tekla attachment timed out.")


def export(state, folder):
    data = json.loads(state.model_dump_json())
    (folder / "full_output.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (folder / "final_report.json").write_text(json.dumps(state.final_report, indent=2), encoding="utf-8")
    (folder / "generated_grid.json").write_text(json.dumps(state.frame_candidate, indent=2), encoding="utf-8")
    fields = {"A1": ["grid_config", "load_config"], "A2": ["readiness"], "A3": ["frame_candidate"],
              "A4": ["staad_results", "frame_grader_result", "loop_a_history"],
              "A5": ["ideastatica_results", "joint_grader_results", "loop_b_history"],
              "A6": ["validation_report", "loop_c_history"], "A7": ["tekla_results"], "A8": ["final_report"]}
    for agent, keys in fields.items():
        target = folder / "agents" / agent;target.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": state.run_id, "status": data["agent_statuses"].get(agent),
                   "deep_agent_executions": [e for e in state.deep_agent_events if e["stage"] == agent],
                   "note": "Final stage evidence; execution audit includes all revisits.",
                   **{key: data[key] for key in keys}}
        (target / "output.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for group, result in state.ideastatica_results.items():
        project = Path(result.get("saved_project_path", ""))
        if project.is_file():
            shutil.copytree(project.parent, folder / "software/IDEA" / group, dirs_exist_ok=True)


def main():
    if not deep_agents_enabled():
        raise RuntimeError("Enable PLANT_DEEP_AGENTS_ENABLED before this live validation.")
    check_idle()
    run_id = "run_" + uuid.uuid4().hex[:8]
    folder = ROOT / "output_exports/deep_agents_live_20261117" / run_id
    folder.mkdir(parents=True)
    root = ROOT / "output_exports/deep_agents_live_20261117"
    (root / "latest_run.json").write_text(json.dumps({"run_id": run_id, "folder": str(folder)}))
    original = json.loads((ROOT / "output_exports/run_8x5_material_recovery_20261116/full_output.json").read_text())
    payload = original["raw_input"]
    (folder / "input.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    staad = staad_common._get_openstaad()
    if staad.IsAnalyzing():
        raise RuntimeError("STAAD is busy; no models switched.")
    original_staad = staad_common.saved_model_path(staad)
    staad.SaveModel(True)
    staad_bytes = original_staad.read_bytes()
    original_tekla = Path(tekla_common._get_connected_model().GetInfo().ModelPath).resolve()
    handler = ModelHandler()
    if not handler.Save("Save before isolated Deep Agents test", "Deep Agents validation"):
        raise RuntimeError("Could not save original Tekla model.")
    handler.Close()
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in original_tekla.glob("*.db1")}
    report = {"run_id": run_id, "kind": "LIVE GPT Deep Agents + native STAAD/IDEA/Tekla; no mocks",
              "original_staad": str(original_staad), "original_tekla": str(original_tekla),
              "models_restored": False, "source_tekla_hashes_after_save": hashes}
    try:
        target_tekla = folder / "software/Tekla/model";target_tekla.mkdir(parents=True)
        prefix = "run_b363d21f/software/Tekla/model/"
        with zipfile.ZipFile(ROOT / "output_exports/run_b363d21f_agentic_output.zip") as archive:
            for item in archive.infolist():
                if not item.filename.startswith(prefix) or item.is_dir():continue
                relative = Path(item.filename[len(prefix):])
                if relative.name in (".locked", ".TeklaStructures") or relative.suffix.lower() == ".lck":continue
                target = (target_tekla / relative).resolve()
                if not target.is_relative_to(target_tekla.resolve()):raise RuntimeError("Unsafe archive member.")
                target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(archive.read(item))
        target_staad = folder / "software/STAAD/model.std";target_staad.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "output_exports/run_8x5_material_recovery_20261116/software/STAAD/model.std", target_staad)
        open_tekla(handler, target_tekla)
        open_staad(staad, target_staad)
        print("LIVE_RUN", run_id, str(folder), flush=True)
        state = asyncio.run(RunController().execute(payload, run_id))
        export(state, folder)
        report.update(status=state.status, error=state.error, deep_agent_model_calls=state.deep_agent_model_call_count,
                      agents=state.agent_statuses, deep_agent_events=state.deep_agent_events)
        print("LIVE_RESULT", json.dumps({k: report[k] for k in ("run_id", "status", "error", "deep_agent_model_calls")}), flush=True)
    finally:
        if not staad.IsAnalyzing():
            open_staad(staad, original_staad)
            handler.Close()
            report["source_tekla_databases_unchanged"] = all(hashlib.sha256((original_tekla / name).read_bytes()).hexdigest() == h for name, h in hashes.items())
            open_tekla(handler, original_tekla)
            report["source_staad_unchanged"] = original_staad.read_bytes() == staad_bytes
            report["models_restored"] = True
        else:
            report["restore_deferred"] = "Isolated STAAD calculation still active; source models were not overwritten."
        (folder / "live_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("RESTORATION", report["models_restored"], flush=True)


if __name__ == "__main__":
    main()
