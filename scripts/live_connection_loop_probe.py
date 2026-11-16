"""Real IDEA Loop B fixture using a recorded actual STAAD governing load effect."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agentic.config import load_environment
load_environment()
from agentic.store import WORKFLOW_STORE, SqliteWorkflowStore
import agentic.store as store_module
from agentic.reference_procedure import build_connection_scope
from agentic.agents.a5_ideastatica_analyzer_agent import run_a5_ideastatica_analyzer


def main(multiplier=2.4, effect_index=0, senior_single=False):
    root = ROOT / "output_validation/backend_loop_test"
    group = "inner_node_2_longitudinal_bracings"
    original = WORKFLOW_STORE.get_state("run_45715b4c")
    state = original.model_copy(deep=True)
    state.run_id, state.status, state.error = f"verify_connection_{uuid.uuid4().hex[:6]}", "RUNNING", None
    run_root=root / state.run_id
    run_root.mkdir(parents=True)
    state.staad_results["joint_groups"] = {group: original.staad_results["joint_groups"][group]}
    env = next(env for env in original.staad_results["load_envelopes"] if env["envelopeId"] == 3)
    state.staad_results["load_envelopes"] = [env]
    data = deepcopy(original.staad_results["envelope_load_cases_by_joint_group"]["3"][group])
    data["load_effects"] = [data["load_effects"][effect_index]]
    if senior_single:
        group="inner_node_1_longitudinal_bracing"
        reference=Path(r"C:\Users\lb89380\projects\plant-engineering\civil-steel-standard-web") / "analysis_output_inner_node_1_longitudinal_bracing_envelope_3.json"
        data=json.loads(reference.read_text())
        data["load_effects"]=[data["load_effects"][effect_index]]
        state.staad_results["joint_groups"]={group:{"joints":[]}}
    # Explicit stress-test multiplier, never used by production combinations.
    for loading in data["load_effects"][0]["member_loadings"]:
        for component in ("n", "vy", "vz", "mx", "my", "mz"):
            loading[component] *= multiplier
    state.staad_results["envelope_load_cases_by_joint_group"] = {"3": {group: data}}
    state.readiness["connection_scope"] = build_connection_scope(state.staad_results["joint_groups"])
    state.ideastatica_results.clear(); state.joint_grader_results.clear()
    state.loop_b_count.clear(); state.loop_b_history.clear()
    state.active_operation_params = {}
    if senior_single:
        state.active_operation_params={group:[{"key":"Stiff_bot_weld(flange)","expression":"0.005"}]}
    state.max_loop_b = 2
    # This is a real isolated SQLite store, not a mocked solver or fake result.
    store_module.WORKFLOW_STORE = SqliteWorkflowStore(str(root / "connection_fixture_runs.db"))
    template = ROOT / ("sample_projects/inner_node_1_longitudinal_bracing.ideaCon" if senior_single else "sample_projects/two-longitudinal.ideaCon")
    template_hash = hashlib.sha256(template.read_bytes()).hexdigest()
    report = {"kind":"REAL IDEA solver + actual A5 correction loop", "source_frame_run":original.run_id,
              "scope":"One isolated governing ULS effect; not a full project publication", "group":group,
              "fixture_effect":data["load_effects"][0],
              "seed":f"Unchanged permitted template parameters; isolated actual effect index {effect_index} multiplied by {multiplier}. Production loads/combinations unchanged.",
              "attempts":[]}
    report['artifact_folder']=str(run_root)
    if senior_single:
        report['seed']=f"Archived senior connection input; multiplier {multiplier}; only bottom flange weld seeded at permitted 5 mm instead of template 8 mm. A connection algorithm fixture, not frame certification or project publication."
        report['source_input_file']=str(reference)
    try:
        for attempt in range(3):
            state = run_a5_ideastatica_analyzer(state)
            store_module.WORKFLOW_STORE.save_state(state)
            saved = run_root / f"connection_attempt_{attempt}"
            saved.mkdir(exist_ok=True)
            (saved / "state.json").write_text(state.model_dump_json(indent=2))
            result = state.ideastatica_results.get(group,{})
            copied = {}
            for key in ("check_summary_file","iom_connection_data_file","saved_project_path"):
                path = Path(result[key]) if result.get(key) else None
                if path and path.is_file():
                    destination = saved / path.name
                    shutil.copy2(path,destination)
                    copied[key] = str(destination)
            report["attempts"].append({"attempt":attempt,"status":state.agent_statuses.get("A5"),"error":state.error,
                "grade":state.joint_grader_results[group].model_dump() if group in state.joint_grader_results else None,
                "summary":result.get("check_summary"),"corrections":deepcopy(state.loop_b_history),"files":copied})
            (root / "live_connection_report.json").write_text(json.dumps(report,indent=2))
            (run_root / "report.json").write_text(json.dumps(report,indent=2))
            print("CONNECTION",json.dumps(report["attempts"][-1]["status"]),flush=True)
            if state.status == "FAILED" or state.agent_statuses.get("A5",{}).get("status") != "REVISING":break
    finally:
        report["template_file_unchanged"] = hashlib.sha256(template.read_bytes()).hexdigest() == template_hash
        report["final_stage_status"] = state.agent_statuses.get("A5")
        (root / "live_connection_report.json").write_text(json.dumps(report,indent=2))
        (run_root / "report.json").write_text(json.dumps(report,indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--multiplier",type=float,default=2.4)
    parser.add_argument("--effect-index",type=int,default=0)
    parser.add_argument("--senior-single",action="store_true")
    args=parser.parse_args()
    main(args.multiplier,args.effect_index,args.senior_single)
