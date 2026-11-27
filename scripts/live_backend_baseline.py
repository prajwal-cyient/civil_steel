"""Run the original failing input through the real backend on isolated models."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.coinit_flags = 2
from agents.A4_staad import staad_com_helpers as staad_common
from config.settings import load_environment
load_environment()
from agents.A6_tekla import tekla_open_api as tekla_common
from Tekla.Structures.Model import ModelHandler
from services.workflow_store import WORKFLOW_STORE


def api(path, data=None, timeout=30):
    request = urllib.request.Request("http://127.0.0.1:8080/api/v2/agentic/"+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def open_staad(staad, path):
    staad.OpenSTAADFile(str(path))
    time.sleep(2)
    deadline, stable = time.monotonic()+30, 0
    while time.monotonic()<deadline:
        name = staad.GetSTAADFile()
        stable = stable+1 if name and Path(name).resolve()==path.resolve() else 0
        if stable>=3:return staad_common.saved_model_path(staad,path)
        time.sleep(.2)
    raise RuntimeError(f"STAAD switch did not complete: {path}")


def open_tekla(handler, path):
    if not handler.Open(str(path), False):raise RuntimeError(f"Tekla failed to open {path}")
    deadline = time.monotonic()+45
    while time.monotonic()<deadline:
        model = tekla_common.Model()
        if model.GetConnectionStatus() and Path(model.GetInfo().ModelPath).resolve()==path.resolve():return
        time.sleep(.5)
    raise RuntimeError(f"Tekla switch did not complete: {path}")


def main():
    root = ROOT / "output_validation/backend_loop_test"
    folder = root / f"baseline_{uuid.uuid4().hex[:6]}"
    folder.mkdir()
    report = {"kind":"REAL backend HTTP A1-A7, native STAAD/IDEA/Tekla; no solver mocks",
              "source_input":"run_5830ed43", "artifact_folder":str(folder)}
    def save():
        (root / "baseline_report.json").write_text(json.dumps(report,indent=2))
    if api("active") is not None:raise RuntimeError("An API run is active; models not switched.")
    staad = staad_common._get_openstaad()
    if staad.IsAnalyzing():raise RuntimeError("External STAAD analysis active; models not switched.")
    original = staad_common.saved_model_path(staad)
    staad.SaveModel(True)
    original_bytes = original.read_bytes()
    original_tekla = Path(tekla_common._get_connected_model().GetInfo().ModelPath)
    handler = ModelHandler()
    if not handler.Save("Save before isolated backend validation", "Backend validation"):
        raise RuntimeError("Could not save original Tekla model; test aborted.")
    # Copy a closed database. Opening a copy preserves the source database.
    handler.Close()
    copied_tekla = folder / "tekla" / original_tekla.name
    try:
        shutil.copytree(original_tekla,copied_tekla)
        source_dbs = {str(p.relative_to(original_tekla)):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in original_tekla.glob("*.db1")}
        report["source_tekla_databases_before"] = source_dbs
        open_tekla(handler,copied_tekla)
        copied_staad = folder / "analytical.std"
        copied_staad.write_bytes(original_bytes)
        open_staad(staad,copied_staad)
        payload = WORKFLOW_STORE.get_state("run_5830ed43").raw_input
        report["original_staad"],report["original_tekla"] = str(original),str(original_tekla)
        report["input_sha256"] = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        save()
        result = api("run",payload,timeout=3700)
        (folder / "full_state.json").write_text(json.dumps(result,indent=2))
        for key in ("run_id","status","error","agent_statuses","loop_a_history","loop_b_history",
                    "loop_c_history","validation_report","tekla_results","final_report"):
            report[key] = result.get(key)
        report["frame_grade"] = result.get("frame_grader_result")
        report["joint_grades"] = result.get("joint_grader_results")
        print("BASELINE",json.dumps({k:result.get(k) for k in ("run_id","status","error","agent_statuses")}),flush=True)
    except Exception as exc:
        report["harness_exception"] = repr(exc)
        raise
    finally:
        # A bounded API request must finish before restoring shared applications.
        if api("active") is None:
            open_staad(staad,original)
            handler.Close()
            report["source_tekla_databases_unchanged"] = all(
                hashlib.sha256((original_tekla/name).read_bytes()).hexdigest()==digest
                for name,digest in report.get("source_tekla_databases_before",{}).items())
            open_tekla(handler,original_tekla)
            report["original_staad_bytes_unchanged"] = original.read_bytes()==original_bytes
            report["models_restored"] = True
        else:
            report["models_restored"] = False
            report["restore_deferred"] = "Backend still active; do not switch its models."
        save()


if __name__ == "__main__":main()
