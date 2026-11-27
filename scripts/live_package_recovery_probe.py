"""Remove one real result category and verify native Loop C recomputation."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "src"))
sys.coinit_flags=2
from agents.A4_staad import staad_com_helpers as staad_common
from config.settings import load_environment
load_environment()
from orchestration.state import WorkflowState
from services.workflow_store import SqliteWorkflowStore
import services.workflow_store as store_module
from agents.A5_idea_statica.package_validation import run_package_validator
from agents.A5_idea_statica import run_a5_ideastatica_analyzer, _connection_payload


def main():
    root=ROOT / "output_validation/backend_loop_test"
    baseline=json.loads((root / "baseline_report.json").read_text())
    source=WorkflowState.model_validate(json.loads((Path(baseline["artifact_folder"]) / "full_state.json").read_text()))
    state=source.model_copy(deep=True)
    state.run_id=f"package_recovery_{uuid.uuid4().hex[:6]}"
    state.status="RUNNING"
    state.error=None
    state.loop_c_count=0
    state.loop_c_history=[]
    # Only corrupt a disposable copy of real evidence, not the model or solver output.
    group=min(state.ideastatica_results, key=lambda key:len(_connection_payload(state,key)["load_effects"]))
    intact=deepcopy(state.ideastatica_results)
    checks=state.ideastatica_results[group]["check_summary"]["summary"]
    state.ideastatica_results[group]["check_summary"]["summary"]=[row for row in checks if "weld" not in row["name"].lower()]
    store_module.WORKFLOW_STORE=SqliteWorkflowStore(str(root / "package_recovery_fixture.db"))
    report={"kind":"REAL native IDEA recomputation after controlled removal of one category from a copy of genuine evidence",
            "source_run":source.run_id,"damaged_family":group,"removed_category":"Welds",
            "cached_pass_initially_retained":state.joint_grader_results[group].passed,
            "no_load_geometry_or_limit_change":True}
    state=run_package_validator(state)
    report["feedback"]=deepcopy(state.validation_report)
    report["a6_detected_missing_check"]=(not state.validation_report.get("passed") and
        state.validation_report.get("recovery_target")=="a5_ideastatica_analyzer")
    (root / "package_recovery_report.json").write_text(json.dumps(report,indent=2))
    state=run_a5_ideastatica_analyzer(state)
    report["a5_after_recovery"]=state.agent_statuses.get("A5")
    report["error"]=state.error
    report["restored_weld_check"]=any("weld" in row["name"].lower() for row in
        state.ideastatica_results[group]["check_summary"]["summary"])
    report["other_families_reused_unchanged"]={key:state.ideastatica_results[key]==value for key,value in intact.items() if key!=group}
    state=run_package_validator(state)
    report["regraded_package"]=state.validation_report
    report["final_code_revalidation"]={"status":state.agent_statuses.get("A5c"),"error":state.error}
    report["outcome"]="PASS" if (report["a6_detected_missing_check"] and report["restored_weld_check"]
        and all(report["other_families_reused_unchanged"].values()) and state.validation_report.get("passed") and state.status!="FAILED") else "FAIL"
    (root / "package_recovery_report.json").write_text(json.dumps(report,indent=2))
    (root / "package_recovery_final_state.json").write_text(state.model_dump_json(indent=2))
    print("RECOVERY",report["outcome"],flush=True)


if __name__=="__main__":main()
