"""Real OpenSTAAD fixture, isolated STD input; no simulated solver responses."""
import asyncio
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.coinit_flags = 2
from common import staad_common
from agentic.config import load_environment
load_environment()
from agentic.store import WORKFLOW_STORE
from agentic.state import WorkflowState
from agentic.agents.a1_requirements_agent import run_a1_requirements
from agentic.agents.a2_readiness_agent import run_a2_readiness
from agentic.agents.a3_frame_generator_agent import run_a3_frame_generator
from agentic.agents.a4_staad_analyzer_agent import run_a4_staad_analyzer


async def main(max_revisions=2, mixed=False):
    root = ROOT / "output_validation" / "backend_loop_test" / f"frame_probe_{uuid.uuid4().hex[:6]}"
    root.mkdir(parents=True, exist_ok=True)
    staad = staad_common._get_openstaad()
    if staad.IsAnalyzing():
        raise RuntimeError("An external STAAD analysis is running; no model switched.")
    original = staad_common.saved_model_path(staad)
    staad.SaveModel(True)
    original_bytes = original.read_bytes()
    (root / "original_before_probe.std.bak").write_bytes(original_bytes)
    fixture = root / f"frame_loop_fixture_{uuid.uuid4().hex[:6]}.std"
    fixture.write_bytes(original_bytes)
    state = None
    report = {"type": "REAL STAAD - isolated test input", "original_path": str(original), "attempts": [],
              "scenario": "mixed_member_safe_stop" if mixed else "revision_cap_stop" if max_revisions == 0 else "capacity_correction_success",
              "max_revisions": max_revisions}
    async def open_model(path):
        staad.OpenSTAADFile(str(path))
        await asyncio.sleep(2)
        deadline = time.monotonic() + 30
        stable = 0
        while time.monotonic() < deadline:
            raw = staad.GetSTAADFile()
            if raw and Path(raw).resolve() == path.resolve():
                stable += 1
                if stable >= 3:
                    return staad_common.saved_model_path(staad, path)
            else:
                stable = 0
            await asyncio.sleep(.2)
        raise RuntimeError(f"STAAD did not complete the intentional switch to {path}")
    try:
        await open_model(fixture)
        payload = deepcopy(WORKFLOW_STORE.get_state("run_5830ed43").raw_input)
        payload["grid"].update(n_long_bays=1, n_trans_bays=1, bay_long_mm=2000,
            bay_trans_mm=2000, col_height_mm=8000, total_structure_height=8300,
            column_profile="CFRHS100X100X4", beam_profile="HEA300",
            longitudinal_brace_profile="CFRHS150X150X6", transverse_brace_profile="CFRHS150X150X6",
            longitudinal_brace_bays=[{"bay": 1, "mirror": False}],
            transverse_brace_bays=[{"bay": 1, "mirror": False}])
        payload["load_configuration"]["total_radiator_weight_including_liquid_kg"] = 50000
        report["fixture_inputs"] = payload
        state = WorkflowState(run_id="live_frame_fixture", status="RUNNING", raw_input=payload, max_loop_a=max_revisions)
        state = run_a1_requirements(state)
        state = run_a2_readiness(state)
        if state.status == "FAILED":
            raise RuntimeError(state.error)
        for attempt in range(3):
            state = run_a3_frame_generator(state)
            if state.status == "FAILED":
                break
            if mixed:
                # Seed an isolated analytical negative test with three strong
                # columns and one weak column. This is never a production edit.
                for collection in ("members", "members_split_beams"):
                    for member in state.frame_candidate[collection]:
                        if member.get("type") == "column" and member["name"] != "column-0-0":
                            member["profile"] = "CFRHS250X250X10"
            before = deepcopy(state.frame_candidate)
            state = await run_a4_staad_analyzer(state)
            fixture_text = fixture.read_text(errors="replace")
            (root / f"frame_attempt_{attempt}.std").write_text(fixture_text)
            (root / f"frame_attempt_{attempt}.json").write_text(state.model_dump_json(indent=2))
            report["attempts"].append({"attempt": attempt, "grade": state.frame_grader_result.model_dump() if state.frame_grader_result else None,
                "error": state.error, "status": state.agent_statuses.get("A4"), "analyzed_column_profile": before["grid"]["column_profile"],
                "next_column_profile": state.active_column_profile,
                "actual_sections": {str(i): {"section": staad.Property.GetBeamSectionName(int(i)),
                                             "material": staad.Property.GetBeamMaterialName(int(i))}
                                    for i in state.staad_results.get("member_data_by_id",{})},
                "load_configuration_sha256": hashlib.sha256(json.dumps(state.load_config,sort_keys=True).encode()).hexdigest(),
                "model_call_events": state.model_events, "corrections": state.loop_a_history,
                "member_ratios": state.staad_results.get("member_ratios"),
                "input_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()})
            (root / "live_frame_report.json").write_text(json.dumps(report, indent=2))
            print("PROBE", json.dumps(report["attempts"][-1]["status"]), flush=True)
            if state.status == "FAILED" or state.agent_statuses.get("A4",{}).get("status") != "REVISING":
                break
    finally:
        await open_model(original)
        report["original_input_unchanged"] = original.read_bytes() == original_bytes
        report["restored_original"] = str(staad_common.saved_model_path(staad)) == str(original)
        report["finished_state"] = state.status if state else None
        report["finished_stage"] = state.agent_statuses.get("A4") if state else None
        (root / "live_frame_report.json").write_text(json.dumps(report, indent=2))
        (root.parent / "latest_frame_probe.json").write_text(json.dumps({"report":str(root / 'live_frame_report.json')}))
        print("RESTORED", report["original_input_unchanged"], flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--max-revisions",type=int,default=2)
    parser.add_argument("--mixed",action="store_true")
    args=parser.parse_args()
    asyncio.run(main(args.max_revisions,args.mixed))
