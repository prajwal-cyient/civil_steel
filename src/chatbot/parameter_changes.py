"""Immutable, approved change proposals using existing project names and IDEA keys."""
from copy import deepcopy
import hashlib
import json
import math
import re
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from chatbot.chat_store import now
from chatbot.tool_registry import execution_plan
from orchestration.state import workflow_stage


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, default=str).encode()).hexdigest()


class ParameterChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    joint_group: str = Field(min_length=1, max_length=160)
    parameter: str = Field(min_length=1, max_length=160)
    value: float = Field(gt=0)
    unit: Literal["mm", "m"]
    expected_old_value: float | None = None


def source_fingerprint(state):
    return fingerprint(state.model_dump())


def project_context_fingerprint(session):
    return fingerprint({"project_input":session["project_input"],
                        "next_run_policy":session.get("next_run_policy"),
                        "run_policy_error":session.get("run_policy_error",False)})


def template_fingerprints(state, groups):
    from agents.A3_generate_structure.connection_scope import scope_for_state, template_for_group
    reference = scope_for_state(state)['mode'] == 'reference_procedure'
    result = {}
    for group in groups:
        path = template_for_group(group, reference)
        if not path.is_file():
            raise ValueError(f'The connection template for {group} is unavailable on this host.')
        result[group] = {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def joint_parameters(state, groups=None):
    if state is None:
        raise ValueError("Select a saved engineering run first.")
    available = state.staad_results.get("joint_groups", {})
    selected = list(available) if groups is None else groups
    if not selected or any(group not in available for group in selected):
        raise ValueError("Use exact joint-group names from the selected run. Available: " + ", ".join(available))
    return {name: {"physical_joint_count": len(available[name].get("joints", [])),
                  "parameters": deepcopy(state.ideastatica_results.get(name, {}).get("check_summary", {}).get("operation_parameters", [])),
                  "notice": "Changes target this entire named group, not one physical joint."}
            for name in selected}


def _length_mappings(group, key):
    from agents.A6_tekla import TEKLA_CONNECTION_PARAMETER_MAP
    mappings = [row for row in TEKLA_CONNECTION_PARAMETER_MAP.get(group, []) if row[0] == key and row[2] != "sel"]
    if not mappings:
        raise ValueError(f"No validated numeric length/Tekla mapping for {group}.{key}. No parameter was guessed.")
    return mappings


def build_joint_proposal(session, state, changes, action="validate_selected_connections"):
    if state is None or state.status in ("RUNNING", "FAILED", "CANCELLED") or state.readiness.get("preview_only"):
        raise ValueError("Select a saved, verified engineering candidate before proposing connection changes.")
    from utils.evidence import frame_signature
    if not state.frame_grader_result or not state.frame_grader_result.passed or state.staad_results.get("candidate_signature") != frame_signature(state):
        raise ValueError("Current passing STAAD evidence is required; do not guess joint forces.")
    if action not in ("validate_selected_connections", "publish_selected_connections"):
        raise ValueError("Choose connection validation or selected connection publication.")
    if not changes or len(changes) > 100:
        raise ValueError("Supply between 1 and 100 explicit parameter changes.")
    parsed = [ParameterChange.model_validate(item) for item in changes]
    groups = list(dict.fromkeys(item.joint_group for item in parsed))
    metadata = joint_parameters(state, groups)
    from agents.A3_generate_structure.connection_scope import scope_for_state
    required = scope_for_state(state)["idea_required_groups"]
    if set(groups) - set(required):
        raise ValueError("Selected group uses standard details or lacks an IDEA template. A separate calculation mapping is required.")
    seen, patch, unchanged = set(), [], []
    for item in parsed:
        pair = (item.joint_group, item.parameter)
        if pair in seen:
            raise ValueError("The same group/parameter was supplied more than once.")
        seen.add(pair)
        exposed = {p["key"]: p for p in metadata[item.joint_group]["parameters"]}
        if item.parameter not in exposed:
            raise ValueError(f"Parameter {item.parameter} is not exposed by {item.joint_group}. Use an exact parameter key.")
        mappings = _length_mappings(item.joint_group, item.parameter)
        current = exposed[item.parameter]
        try:
            old = float(current["value"])
        except (TypeError, ValueError, KeyError):
            raise ValueError(f"No evaluated numeric value is available for {item.parameter}; formulas are not evaluated by chat.")
        if not math.isfinite(old) or old < 0:
            raise ValueError("Invalid current parameter evidence.")
        # Existing IDEA->Tekla maps explicitly convert these length keys by 1000.
        native = item.value / 1000 if item.unit == "mm" else item.value
        expected = item.expected_old_value
        if expected is not None:
            expected = expected / 1000 if item.unit == "mm" else expected
            if not math.isclose(old, expected, rel_tol=1e-8, abs_tol=1e-10):
                raise ValueError(f"Current value of {item.joint_group}.{item.parameter} differs from the requested old value. Refresh the proposal.")
        if math.isclose(old,native,rel_tol=1e-10,abs_tol=1e-12):
            unchanged.append({'joint_group':item.joint_group,'parameter':item.parameter,'value_mm':old*1000})
            continue
        patch.append({"joint_group": item.joint_group, "parameter": item.parameter,
                      "old_native": old, "new_native": native,
                      "old_mm": old * 1000, "new_mm": native * 1000,
                      "sdk_unit": current.get("unit"), "native_unit": "m",
                      "affected_mappings": [list(row[1:]) for row in mappings]})
    if not patch:
        raise ValueError('The selected parameters already have the requested values. No edit or recalculation is needed. To publish an existing validated change, request selected Tekla publication.')
    groups=list(dict.fromkeys(change['joint_group'] for change in patch))
    if action == "publish_selected_connections" and (not state.tekla_results.get("connection_manifest")
            or state.tekla_results.get("component_manifest_version") != 1
            or not state.tekla_results.get("member_manifest")
            or state.agent_statuses.get(workflow_stage(state, "tekla"), {}).get("status") != "PASSED"):
        raise ValueError("Selected Tekla publication requires a verified component manifest from a full publication. The legacy full-model rebuild will not be substituted.")
    if action == "publish_selected_connections":
        from agents.A6_tekla.selected_updates import component_patch
        component_patch(state.tekla_results["connection_manifest"], patch, groups)
    proposal = {"kind": "joint_parameters", "source_run_id": state.run_id,
                "source_fingerprint": source_fingerprint(state), "groups": groups,
                "changes": patch, "action": action, "steps": execution_plan(action, state.layout_version), "run_policy":state.run_policy,
                "unchanged_parameters":unchanged,
                "physical_joint_counts":{group:metadata[group]['physical_joint_count'] for group in groups},
                "template_fingerprints":template_fingerprints(state,groups),
                "notice": "Existing names are preserved. Only selected groups may change; failed checks stop publication."}
    return seal_proposal(session, proposal)


def seal_proposal(session, proposal):
    proposal = {**proposal, "id": "change_" + uuid.uuid4().hex, "session_id": session["id"],
                "project_fingerprint": project_context_fingerprint(session), "created_at": now()}
    return {**proposal, "digest": fingerprint(proposal), "status": "PENDING"}


def build_selected_publication(session, source):
    """Publish already validated edits without repeating their IDEA solves."""
    if source is None or source.status != 'READY' or not source.change_scope or not source.validation_report.get('passed'):
        raise ValueError('A selected-change candidate with a passing package validation is required.')
    previous = source.change_scope
    baseline = previous.get('source_tekla',{})
    if baseline.get('component_manifest_version') != 1 or not baseline.get('connection_manifest') or not baseline.get('member_manifest') or not baseline.get('model_path'):
        raise ValueError('The original publication has no verified Tekla manifests. A full-model rebuild will not be substituted.')
    verify_selected_values(source)
    from agents.A6_tekla.selected_updates import component_patch
    component_patch(baseline['connection_manifest'],previous['changes'],previous['groups'])
    proposal = {'kind':'joint_parameters','source_run_id':source.run_id,'source_fingerprint':source_fingerprint(source),
        'groups':previous['groups'],'changes':previous['changes'],'run_policy':source.run_policy,
        'action':'publish_selected_connections','reuse_calculations':True,
        'steps':[workflow_stage(source, 'package') + ':package', workflow_stage(source, 'tekla') + ':selected',
                 workflow_stage(source, 'report') + ':report'],
        'template_fingerprints':template_fingerprints(source,previous['groups']),
        'notice':'Publish previously calculated selected changes. Old/new values describe the recorded Tekla baseline and the validated target. The package audit rechecks saved evidence; IDEA is not recalculated. No model rebuild.'}
    return seal_proposal(session,proposal)


def build_project_proposal(session, grid_updates, load_updates, run_after=False):
    from chatbot.chatbot import project_input
    from chatbot.policy_requests import require_requested_policy
    require_requested_policy(session)
    candidate = deepcopy(session["project_input"])
    changes = []
    for section, updates in (("grid", grid_updates), ("load_configuration", load_updates)):
        if updates:
            original = candidate.get(section, {})
            changes.extend({"section": section, "parameter": key, "old": original.get(key), "new": value}
                           for key, value in updates.items() if original.get(key) != value)
            candidate[section] = {**original, **updates}
    candidate = project_input(candidate)
    action = run_after if isinstance(run_after, str) else "run_complete_workflow" if run_after else "configuration_only"
    if not changes and action == 'configuration_only':
        raise ValueError("No input changes were requested.")
    target = session.get('_target_grid')
    if target:
        from agents.A1_requirements import GridInputSchema
        grid = GridInputSchema(**candidate.get('grid',{}))
        if (grid.n_long_bays,grid.n_trans_bays) != tuple(target):
            raise ValueError('The proposed frame dimensions do not match the requested target.')
    if action != 'configuration_only' and session.get('run_policy_error'):
        raise ValueError('Resolve the rejected run policy before proposing engineering execution.')
    from config.run_policy import RunPolicy
    effective_policy = RunPolicy.model_validate(session.get('next_run_policy') or candidate.get('run_policy',{})).model_dump()
    return seal_proposal(session, {"kind": "project_inputs", "candidate": candidate, "changes": changes,
                         "action": action, "steps": execution_plan(action), "run_policy":effective_policy,
                         "requested_run_policy":session.get('_requested_run_policy', {}),
                         "notice": "Frame/load changes invalidate earlier analysis. Original saved runs remain unchanged."})


def validate_proposal(plan, session, source=None):
    signed = {k: v for k, v in plan.items() if k not in ("digest", "status", "run_id", "error", "completed_at", "approved_at", "approval_turn_id", "artifacts")}
    if plan["digest"] != fingerprint(signed):
        raise ValueError("Change proposal integrity check failed.")
    if plan["session_id"] != session["id"] or plan["project_fingerprint"] != project_context_fingerprint(session):
        raise ValueError("Project inputs or queued run rules changed after the preview. Create a new proposal.")
    if plan['kind'] == 'project_inputs':
        from chatbot.policy_requests import require_requested_policy
        require_requested_policy({'_requested_run_policy':plan.get('requested_run_policy',{}),
                                  'project_input':session['project_input']}, plan.get('run_policy',{}))
    if plan["kind"] == "joint_parameters":
        if session.get('run_id') != plan['source_run_id']:
            raise ValueError('The selected source run changed after the preview. Create a new proposal.')
        if source is None or source_fingerprint(source) != plan["source_fingerprint"]:
            raise ValueError("Source run changed after the preview. Create a new proposal.")
        if template_fingerprints(source,plan['groups']) != plan['template_fingerprints']:
            raise ValueError('Connection templates changed after the preview. Create a new proposal.')


def prepare_joint_candidate(plan, source):
    state = source.model_copy(deep=True)
    state.run_id = "run_" + uuid.uuid4().hex[:8]
    state.status, state.error = "RUNNING", None
    state.gate3_approved = False
    state.accepted_by = state.acceptance_timestamp = None
    state.validation_report, state.final_report = {}, {}
    state.change_scope = {"proposal_id": plan["id"], "proposal_digest": plan["digest"],
        "source_run_id": source.run_id, "groups": plan["groups"], "changes": plan["changes"],
        "action": plan["action"], "source_tekla": deepcopy(source.change_scope.get('source_tekla',{}) if plan.get('reuse_calculations') else source.tekla_results),
        "reuse_calculations":plan.get('reuse_calculations',False),
        "template_fingerprints":plan['template_fingerprints'],
        "unselected_fingerprint": unselected_fingerprint(source, plan["groups"])}
    from pathlib import Path
    state.change_scope['artifact_dirs'] = {
        group: result.get('artifact_dir') or str(Path(result['check_summary_file']).parent)
        for group, result in source.ideastatica_results.items()
        if result.get('artifact_dir') or result.get('check_summary_file')}
    state.tekla_results = {}
    for group in plan["groups"]:
        existing = state.active_operation_params.get(group) or [
            {"key": p["key"], "expression": str(p["expression"])}
            for p in source.ideastatica_results[group]["check_summary"]["operation_parameters"]]
        updates = {p["parameter"]: format(p["new_native"], ".12g") for p in plan["changes"] if p["joint_group"] == group}
        state.active_operation_params[group] = [{**p, "expression": updates.get(p["key"], p["expression"])} for p in existing]
        if not plan.get('reuse_calculations'):
            state.joint_grader_results.pop(group, None)
            state.ideastatica_results.pop(group, None)
    if plan.get('reuse_calculations'):
        state.readiness['publication_resume_source']=source.run_id
    stages = (workflow_stage(state, "package"), workflow_stage(state, "tekla"), workflow_stage(state, "report"))
    for stage in (stages if plan.get('reuse_calculations') else ("A5", *stages)):
        state.agent_statuses.pop(stage, None)
    return state


def unselected_fingerprint(state, groups):
    return fingerprint({"geometry": state.frame_candidate, "staad": state.staad_results,
        "results": {k: v for k, v in state.ideastatica_results.items() if k not in groups},
        "parameters": {k: v for k, v in state.active_operation_params.items() if k not in groups}})


def verify_selected_values(state):
    for change in state.change_scope["changes"]:
        params = state.ideastatica_results.get(change["joint_group"], {}).get("check_summary", {}).get("operation_parameters", [])
        actual = next((p.get("value") for p in params if p["key"] == change["parameter"]), None)
        if actual is None or not math.isclose(float(actual), change["new_native"], rel_tol=1e-8, abs_tol=1e-10):
            raise ValueError("Calculated parameter readback differs from the approved proposal.")
    if unselected_fingerprint(state, state.change_scope["groups"]) != state.change_scope["unselected_fingerprint"]:
        raise ValueError("Unselected project data changed; publication is blocked.")


def export_change_result(plan, result, project_input, database_path, state=None):
    """Save reviewable JSON without overwriting source inputs or legacy outputs."""
    from pathlib import Path
    from config.settings import ROOT
    default_store = (ROOT/'data'/'chat.db').resolve()
    store = Path(database_path).resolve()
    parent = ROOT/'output_exports'/'dynamic_changes' if store == default_store else store.parent/'change_exports'
    ident = result.get('run_id') or plan['id']
    if not re.fullmatch(r'(?:run_[a-f0-9]{8,32}|change_[a-f0-9]{32})', ident):
        raise ValueError('Invalid server-generated artifact identifier.')
    folder = parent/ident
    folder.mkdir(parents=True,exist_ok=True)
    documents = {'project_input.json':project_input,
                 'change_summary.json':{'proposal':plan,'result':result,'saved_at':now()}}
    if state is not None:
        documents['workflow_output.json'] = state.model_dump()
    for name, data in documents.items():
        temporary = folder/('.'+name+'.tmp')
        temporary.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
        temporary.replace(folder/name)
    return {'directory':str(folder),'files':list(documents)}
