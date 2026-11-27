"""Render observed backend test artifacts to the requested plain-text report."""
from datetime import datetime
import difflib
import hashlib
import importlib.metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "output_validation" / "backend_loop_test"
REPORT = ROOT / "LOOP_ENGINEERING_TEST_REPORT.txt"


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def build_report():
    lines = ["BACKEND AUTOMATION AND LOOP ENGINEERING TEST REPORT", "="*76,
             f"Updated: {datetime.now().isoformat(timespec='seconds')} (development machine local time)",
             f"Project: {ROOT}",
             "Execution: development Python scripts, SDKs, backend API and pipeline; no frontend interaction.",
             "Evidence rule: live solver results and controlled/mock regression fixtures are labeled separately.",
             "No engineering limit, partial factor or reference load-combination factor was changed.", ""]
    def section(title):
        lines.extend(["",title,"-"*76])
    def item(value):
        lines.append(str(value))
    section("OBSERVED RESULTS AT A GLANCE")
    baseline_summary=read(ART / "baseline_report.json") or {}
    recovery_summary=read(ART / "package_recovery_report.json") or {}
    regression_summary=read(ART / "regression-cases.json") or []
    item("Original failing input, new complete native backend run: "+str(baseline_summary.get("run_id"))+" / "+str(baseline_summary.get("status")))
    item("A7 engineer sign-off is intentionally pending; A1-A6 passing does not constitute engineer acceptance.")
    item("Native Loop A: failed frame -> four localized column revisions -> fresh passing STAAD solve; see section 4.")
    item("Native negative frame tests: retry exhausted and mixed passing/failing columns -> explicit failure, no unsafe correction.")
    item("Native Loop C: missing check detected despite cached PASS -> only affected IDEA family recalculated -> package revalidated: "+str(recovery_summary.get("outcome")))
    item(f"Controlled Python regressions: {sum(row['status']=='PASS' for row in regression_summary)}/{len(regression_summary)} passing. Native and controlled cases are distinguished below.")
    item("Native IDEA failed-to-corrected Loop B convergence is NOT established by passing stress tests alone. See section 5 for the actual latest result.")
    item("Follow-up runtime fix: dedicated IDEA budget defaults to 600 seconds (bounded by the total run cap); see section 2 and follow-up cases in section 5.")
    item("Unsupported local changes or missing Tekla mappings stop publication; this implementation cannot guarantee convergence for every engineering input.")
    section("1. REPORTED STAAD PATH FAILURE AND FIX")
    item("Observed source run run_5830ed43 failed at A4: existing saved analytical input; returned path '.'.")
    item("Cause in code: a one-shot filename read converted the empty SDK string to Path('.').")
    item("Fix: saved_model_path retries transient empty/missing values for a bounded interval, pumps pending COM messages,")
    item("and requires an absolute existing .std path. A different nonblank expected model path is rejected.")
    item("The model is pinned from A2 through cleanup and analysis preparation; Tekla's A2 path is checked before cleanup.")
    item("Regression cases cover transient blanks, permanent blanks, unexpected model switches and the existing cleanup behavior.")
    item("No fallback guesses a model or fabricates successful analysis when no saved model is available.")
    section("2. CORRECTION SCOPE AUDIT / CODE CHANGES")
    item("Finding: the reference generator shares profiles by member category; changing that variable could resize passing members.")
    item("Fix: frame_correction_scope requires individual member evidence and records exact IDs/names/ratios.")
    item("A shared profile revision is allowed only if every affected member fails. Mixed failing/passing groups stop explicitly;")
    item("mixed per-member profiles and compatible connection templates are not a validated reference mode.")
    item("Finding: a category summary such as 'Welds failed' does not locate the actual weld among several exposed parameters.")
    item("Fix: ambiguous multi-parameter automatic changes are blocked. Only a unique exposed candidate or explicit localized keys may change.")
    item("Weld localization uses actual failed solver weld IDs and reversible exported-geometry probes in a disposable IDEA project.")
    item("The probes must restore expressions and geometry. A parameter touching passing welds or unrelated geometry is rejected.")
    item("A5c blocks publication if an IDEA correction has no validated Tekla attribute mapping; a solved analytical change may not be silently omitted.")
    item("Supported live templates must return Analysis, Plates, Local deformation, Bolts and Welds checks; missing/skipped families are rejected.")
    item("Cached IDEA PASS records are rechecked for full coverage and source validity before reuse, allowing Loop C to repair incomplete evidence.")
    item("The existing retry limits, numeric ladders, grades, loads and code-design settings remain unchanged.")
    item("FOLLOW-UP: calculation_budget separates IDEA's solve allowance from the generic 180-second operation timeout.")
    item("The new PLANT_IDEA_CALCULATION_TIMEOUT_SECONDS default is 600 seconds; 13-effect 2340-second and total 3600-second caps remain bounded.")
    item("Newer services use calculation-job polling with owned-job validation and cancellation requests on timeout/interruption.")
    item("Actual installed service rejects those routes (404); its native tests use the explicit bounded synchronous fallback. Native asynchronous cancellation was NOT exercised on this service.")
    item("Controlled tests verify completed, failed, empty, mismatched, expired and unsupported job responses without inventing engineering results.")
    item("Windows readiness and IDEA attachment now wait briefly for native cleanup, then reject another calculation while an IDEA-owned k2fem64.exe is active.")
    item("This guard reads process ownership through WMI, balances COM initialization and does not terminate shared desktop application processes.")
    hashes=read(ROOT / "output_validation/senior-reference-hashes.json")
    if hashes:
        reference=Path(r"C:\Users\lb89380\projects\plant-engineering\civil-steel-standard-web")
        item("Senior reference files compared with the earlier read-only inspection hashes:")
        for relative,digest in hashes.items():
            path=reference / relative
            item(relative+": "+("UNCHANGED" if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest else "CHANGED OR UNAVAILABLE - inspect before treating as same reference"))
    section("3. CONTROLLED BACKEND REGRESSION EXECUTION (NOT LIVE FEA)")
    records = read(ART / "regression-cases.json")
    if records is None:
        item("PENDING: per-case regression artifact not available.")
    else:
        item(f"Executed {len(records)} cases: {sum(r['status']=='PASS' for r in records)} PASS, "
             f"{sum(r['status']!='PASS' for r in records)} unsuccessful.")
        item("These cases intentionally mock adapters to test routing, failure guards and boundaries. They are not proof of model/solver correction.")
        for row in records:
            item(f"[{row['status']}] {row['test']} ({row['seconds']:.4f}s)")
            if row.get("error"):item(row["error"])
    jslog = ART / "javascript-regressions.txt"
    if jslog.exists():
        item("Additional Node logic regression output (no browser required):")
        data=jslog.read_bytes()
        item(data.decode("utf-16" if data.startswith((b"\xff\xfe",b"\xfe\xff")) else "utf-8-sig",errors="replace"))
    section("4. LIVE STAAD CAPACITY/DRIFT LOOP - ACTUAL SOFTWARE")
    reports = sorted(ART.glob("frame_probe_*/live_frame_report.json"))
    if not reports:
        item("PENDING: guarded native frame trial has not completed.")
    for path in reports:
        report = read(path)
        if not report:continue
        item(f"Artifact: {path}")
        item("Scenario: "+str(report.get("scenario", "capacity_correction_success")))
        item("Fixture differs from production inputs deliberately: 1x1 bays, 2 m bays, 8 m nominal columns,")
        item("initial CFRHS100X100X4 columns, HEA300 beams, CFRHS150X150X6 braces, 50,000 kg equipment mass.")
        item("These are test-input values, not changes to the approved production loads or the acceptance criteria.")
        for attempt in report.get("attempts",[]):
            item(f"Attempt {attempt['attempt']}: analyzed column profile {attempt.get('analyzed_column_profile')}")
            item("Actual grader: "+json.dumps(attempt.get("grade")))
            item("Individual member ratios: "+json.dumps(attempt.get("member_ratios")))
            item("Actual OpenSTAAD section/material read-back: "+json.dumps(attempt.get("actual_sections")))
            item("Feedback/corrections: "+json.dumps(attempt.get("corrections")))
            item("Advisor audit (if used): "+json.dumps(attempt.get("model_call_events")))
            item("Saved input SHA256: "+str(attempt.get("input_sha256")))
            item("Load configuration SHA256: "+str(attempt.get("load_configuration_sha256")))
            if attempt.get("error"):item("ERROR: "+attempt["error"])
        attempts = report.get("attempts",[])
        if len(attempts)>1:
            before,after=attempts[0],attempts[-1]
            changed={key for key,value in before.get("actual_sections",{}).items()
                     if after.get("actual_sections",{}).get(key)!=value}
            failed={str(key) for key,value in (before.get("member_ratios") or {}).items() if value.get("ratio",0)>1}
            unchanged_loads=len({row.get("load_configuration_sha256") for row in attempts})==1
            item("CHECK exact section changes: "+json.dumps(sorted(changed)))
            item("CHECK originally failed member IDs: "+json.dumps(sorted(failed)))
            item("CHECK no passing member section changed: "+str(changed.issubset(failed)))
            item("CHECK load inputs/combinations unchanged between iterations: "+str(unchanged_loads))
            before_std=path.parent / f"frame_attempt_{before['attempt']}.std"
            after_std=path.parent / f"frame_attempt_{after['attempt']}.std"
            if before_std.exists() and after_std.exists():
                item("Complete actual serialized STAAD input difference (all other lines unchanged):")
                item("".join(difflib.unified_diff(before_std.read_text().splitlines(True),after_std.read_text().splitlines(True),
                    fromfile=str(before_std),tofile=str(after_std))))
            result=bool(changed and changed.issubset(failed) and unchanged_loads and after.get('grade',{}).get('passed'))
            item("LIVE LOOP OUTCOME: "+("PASS" if result else "FAIL/UNRESOLVED"))
        elif len(attempts)==1 and report.get("scenario") in ("revision_cap_stop","mixed_member_safe_stop"):
            first=attempts[0]
            expected_message="retry limit" if report["scenario"]=="revision_cap_stop" else "passing column members"
            stopped=bool(not first.get("grade",{}).get("passed") and
                first.get("status",{}).get("status")=="FAILED" and not first.get("corrections")
                and expected_message in (first.get("error") or first.get("status",{}).get("message", "")))
            item("LIVE NEGATIVE TEST OUTCOME: "+("PASS - rejected unsafe/unresolved design without correction or publication" if stopped else "FAIL - expected stopping behavior not verified"))
        else:item("LIVE LOOP OUTCOME: INCOMPLETE - no completed changed-candidate rerun.")
        item("Original STD restored: "+str(report.get("restored_original")))
        item("Original STD bytes unchanged: "+str(report.get("original_input_unchanged")))
    section("5. LIVE IDEA CONNECTION CORRECTION / FAILURE TESTS")
    connection=read(ART / "live_connection_report.json")
    item(json.dumps(connection,indent=2) if connection else "PENDING: live connection correction/failure trial not completed.")
    if connection:
        attempts=connection.get("attempts",[])
        if len(attempts)>1:
            first,last=attempts[0],attempts[-1]
            item("Actual connection grader transition: "+str(first.get("grade",{}).get("details",{}).get("max_weld"))+"% -> "+str(last.get("grade",{}).get("details",{}).get("max_weld"))+"%")
            item("LIVE LOOP B OUTCOME: "+("PASS" if not first.get("grade",{}).get("passed") and last.get("grade",{}).get("passed") else "FAIL/UNRESOLVED"))
            paths=[row.get("files",{}).get("iom_connection_data_file") for row in (first,last)]
            if all(paths):
                a,b=map(lambda p:read(Path(p)),paths)
                before_welds={str(w['id']):w for w in a['welds']}
                after_welds={str(w['id']):w for w in b['welds']}
                changes={key:{"before":value,"after":after_welds.get(key)} for key,value in before_welds.items() if after_welds.get(key)!=value}
                item("Exact actual IOM weld changes: "+json.dumps(changes))
                item("Non-weld exported geometry unchanged: "+str({k:v for k,v in a.items() if k!='welds'}=={k:v for k,v in b.items() if k!='welds'}))
        else:item("LIVE LOOP B OUTCOME: no completed failed-to-corrected native rerun in this artifact.")
    for name in ("connection_rejected_below_bound.json","connection_permitted_success.json","connection_2_1_success.json","connection_2_4_success.json","connection_3_0_success.json", "connection_4_0_timeout.json", "connection_3_2_bolt_feedback.json", "connection_single_1_success.json", "connection_single_1_8_success.json"):
        extra=read(ART / name)
        if extra:item("Additional actual IDEA scenario: "+name+"\n"+json.dumps(extra,indent=2))
    item("FOLLOW-UP EXACT TIMEOUT RETEST: "+json.dumps(read(ART / "idea_runtime_retest_qa.json"),indent=2))
    item("The same 4x effect now returns genuine engineering failures: Analysis 94.140625% with check_status=False, bolts 89.41729971128721%, welds 100.6884587755102%.")
    item("Runtime handling improved; engineering convergence was NOT achieved. The analysis failure is not overridden to permit an unverified component change.")
    item("Legacy archive note: the original 4x timeout JSON was recovered verbatim from report_before_idea_followup.txt after a shared latest-result copy had captured a later 3.2x case.")
    item("Retest QA compares the actual old/new load effects and unchanged template hash. Future connection probes use unique run artifact folders.")
    item("The earlier shared connection_attempt_N files can be overwritten between cases; archived embedded summary JSON remains the record of those earlier results.")
    section("6. FULL A1-A7 BACKEND PIPELINE / REGRESSION OF THE USER INPUT")
    baseline=read(ART / "baseline_report.json")
    item(json.dumps(baseline,indent=2) if baseline else "PENDING: complete new pipeline result not yet captured.")
    if baseline and baseline.get("status"):
        statuses=baseline.get("agent_statuses",{})
        passed=all(statuses.get(f"A{i}",{}).get("status")=="PASSED" for i in range(1,7))
        reached=baseline.get("status")=="PAUSED_AT_GATE3" and statuses.get("A7",{}).get("status")=="AWAITING_APPROVAL"
        item("LIVE FULL BASELINE OUTCOME: "+("PASS - A1-A6 verified; A7 produced report awaiting genuine engineer sign-off" if passed and reached else "FAIL/UNRESOLVED"))
        item("No engineer identity/signature was fabricated to mark A7 accepted.")
        item("NOTE: this baseline worker began before the final check-coverage/cache guard additions. Native Loop C replay below revalidates that evidence with the final implementation.")
    api_guard=read(ART / "live_api_guard_report.json")
    item("Live concurrent-run ownership test: "+json.dumps(api_guard))
    section("7. LOOP C - DAMAGED EVIDENCE DETECTION AND REAL RECOMPUTATION")
    recovery=read(ART / "package_recovery_report.json")
    item(json.dumps(recovery,indent=2) if recovery else "PENDING: native evidence recovery trial not captured.")
    item("The missing-check case deliberately removes one result from a COPY of actual evidence. It does not invent substitute solver values.")
    section("8. ERRORS / EXCEPTIONS ENCOUNTERED")
    item("The reported original '.' path error is preserved in source run run_5830ed43.")
    item("Two early harness setup attempts exposed asynchronous OpenSTAAD file switching and a locked fixture file.")
    item("The harness now uses unique fixture names and waits for stable completion of intentional switches; it does not weaken the runtime ownership guard.")
    item("The STAAD SDK may report a general error for an optional supplementary max-ratio helper; actual per-member ratios used by the grader are captured separately.")
    item("A native IDEA negative fixture at 3 mm was rejected by the template's 5 mm lower bound. It was not used as an engineering correction.")
    if connection and any("timed out" in str(row.get("error", "")) for row in connection.get("attempts",[])):
        item("The latest overloaded IDEA fixture reached its actual HTTP calculation timeout; the exact timeout and exception are preserved in section 5.")
        item("It stopped FAILED with no successful grade or automatic correction. This proves bounded failure handling, not engineering convergence.")
    item("During development, stricter guards exposed incomplete mocked test fixtures and an obsolete expectation of publishing an unmapped weld correction.")
    item("Those test expectations/fixtures were corrected; the final per-case execution above records the actual latest results.")
    item("See console logs for exact exceptions, warning counts and complete stage traces; no exception is replaced with a fake pass.")
    section("9. REMAINING LIMITATIONS / RECOMMENDATIONS")
    item("A mixed failing/passing section group stops because validated mixed-profile generation/template partitioning is not implemented.")
    item("A multi-parameter connection failure requires a verified detailed failed-operation-to-parameter mapping; category-wide blind upsizing is prohibited.")
    item("Reference scope still uses scoped IDEA families and standard Tekla details for others. Standard details are not separate IDEA passes.")
    item("Final engineer approval remains separate. A completed software path does not certify every engineering assumption or every possible input.")
    item("Transient filename recovery is bounded; an actually unsaved, closed or externally switched model must still fail safely.")
    item("The API guard controls this server, not unrelated programs sharing the desktop applications. Isolated test files avoid overwriting original analytical inputs.")
    item("Do not increase limits, change load factors or suppress failed statuses to obtain success. Add validated detailed mappings/templates for unsupported corrections.")
    item("Weld corrections without approved Tekla attribute bindings can be solved and regraded in IDEA, but cannot currently publish a physical-model correction.")
    item("Native plate/bolt category failures stop until detailed failed-operation bindings are validated. A single exposed parameter alone does not establish a safe local correction.")
    item("Native failed-to-corrected IDEA Loop B convergence remains unverified by these trials. Controlled localization tests are not substituted for that missing native proof.")
    item("Follow-up native 3.2x test: actual bolt utilization 77.26749316102446% exceeded the unchanged 75% project limit; unsupported correction stopped with no parameter changes.")
    item("Template audit found Bolt_Type shared across operation property links 20-401, 1-401, 11-401. A global change is not an established local correction.")
    item("Audit artifact: output_validation/backend_loop_test/template_binding_audit.json. Export probes are geometry diagnostics, not proof of recalculated bolt sizing.")
    item("Needed to close the remaining correction gap: validate local template-operation/parameter/assembly bindings, supported corrective actions for each failure mode, and equivalent Tekla mappings; then demonstrate a genuine failed-to-corrected native rerun.")
    item("On the installed legacy service, a synchronous client timeout does not prove native computation stopped. Explicit async cancellation is implemented and controlled-tested, but requires a service exposing calculation-job routes.")
    item("The reference load combinations and load direction/temperature conventions are reproduced, not independently certified by these software tests.")
    item("STAAD's generic STEEL material label is interpreted together with the saved EC3 SGR 2 design parameter; the label alone is not a material-grade verification.")
    section("10. REPRODUCIBILITY / ARTIFACTS")
    for package in ("openstaadpy","ideastatica-connection-api","pythonnet","langgraph","pydantic"):
        try:item(package+"="+importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:item(package+": package metadata unavailable")
    item("Backend regression command: .venv\\Scripts\\python.exe scripts/run_backend_regressions.py")
    item("Native scripts: live_frame_loop_probe.py; live_connection_loop_probe.py; live_backend_baseline.py; live_package_recovery_probe.py")
    item("The native scripts operate live software; run sequentially with the applications idle. They deliberately preserve/reopen original models.")
    item("Native console logs:")
    for name in ("live-frame-loop-guarded.log","live-frame-loop-cap-stop.log","live-frame-loop-mixed-stop.log",
                 "live-connection-loop-console.log","live-backend-baseline-console.log","live-package-recovery-console.log"):
        item(str(ROOT / "output_validation" / name))
    item(str(ROOT / "output_validation/idea-timeout-retest-console.log"))
    item("Latest native idle observation: "+json.dumps(read(ART / "idea_native_busy_guard.json")))
    item("Final native readiness with the new idle guard: "+json.dumps(read(ART / "idea_final_readiness.json")))
    item("Final read-only runtime verification: "+json.dumps(read(ART / "final-runtime-state.json")))
    item("Artifact consistency checks: "+json.dumps(read(ART / "final-evidence-qa.json")))
    for path in sorted((ROOT / "src/agentic").rglob("*.py")):
        item(str(path.relative_to(ROOT))+" SHA256 "+hashlib.sha256(path.read_bytes()).hexdigest())
    for relative in ("src/agents/A4_staad/staad_com_helpers.py","src/agents/A4_staad/A4A_build_json.py","src/agents/A6_tekla/tekla_open_api.py","src/agents/A4_staad/staad_openstaad.py"):
        item(relative+" SHA256 "+hashlib.sha256((ROOT / relative).read_bytes()).hexdigest())
    REPORT.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    build_report()
