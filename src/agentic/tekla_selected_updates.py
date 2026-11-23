"""Update verified existing components only. Never clean or rebuild the model."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import math
from agentic.state import AgentLogEntry, fail_workflow


def component_patch(manifest, changes, groups):
    from agentic.chat_changes import _length_mappings
    selected = set(groups)
    if not manifest or len({row['identifier'] for row in manifest}) != len(manifest):
        raise ValueError("A unique verified component manifest is required.")
    patches = {}
    for change in changes:
        matched = False
        for _, number, attribute, direction in _length_mappings(change["joint_group"], change["parameter"]):
            # These attributes also drive computed layout in the full publisher.
            # Until that dependency has its own validated adapter, do not patch it.
            if number == 144 and attribute in ("diameter", "l1", "l2"):
                raise ValueError("This parameter requires recomputed Tekla layout; selected publication is not validated. No rebuild was substituted.")
            rows = [row for row in manifest if change["joint_group"] in row["groups"]
                    and row["number"] == number and row.get("direction") == direction]
            if not rows:
                raise ValueError("No verified existing Tekla component matches the selected parameter.")
            for row in rows:
                if row.get('scope_basis') != 'joint_member_pair' or len(set(row.get('member_names', []))) != 2:
                    raise ValueError("Component ownership was not verified against a physical joint member pair.")
                if set(row["groups"]) - selected:
                    raise ValueError("A Tekla component is shared with an unselected group. Expanded approval is required.")
                old = row["attributes"].get(attribute)
                if old is None or not math.isclose(float(old), change["old_mm"], abs_tol=1e-3):
                    raise ValueError("Tekla manifest does not match the source parameter value.")
                values = patches.setdefault(row["identifier"], {})
                if attribute in values and values[attribute] != change["new_mm"]:
                    raise ValueError("Selected groups request conflicting values on a shared component.")
                values[attribute] = change["new_mm"]
                matched = True
        if not matched:
            raise ValueError("No verified Tekla mapping is available.")
    return patches


def verify_members(manifest, approved, select, profile_name):
    from agentic.chat_changes import fingerprint
    expected = {member['name']:member for member in approved}
    if len(manifest) != len(approved) or len(expected) != len(approved) or len({row['identifier'] for row in manifest}) != len(manifest):
        raise ValueError('A complete unique physical-member manifest is required.')
    normalize = lambda value:str(value).replace(' ','').upper()
    for row in manifest:
        member = row['member']
        if member.get('name') not in expected or fingerprint(member) != fingerprint(expected[member['name']]):
            raise ValueError('Physical member manifest does not match the approved geometry.')
        obj = select(row['identifier'])
        if obj is None:
            raise RuntimeError('A physical member is missing from the current Tekla model.')
        for point, coordinates in ((obj.StartPoint,member['p1']),(obj.EndPoint,member['p2'])):
            if any(abs(actual-target)>0.01 for actual,target in zip((point.X,point.Y,point.Z),coordinates)):
                raise RuntimeError('Tekla member geometry changed after source publication.')
        if normalize(obj.Profile.ProfileString) != normalize(profile_name(member['profile'])) or normalize(obj.Material.MaterialString) != normalize(member['material']):
            raise RuntimeError('Tekla member profile or material differs from the source publication.')
    return len(manifest)


def apply_components(model, manifest, patches, select, verify, set_attribute, verify_model=None):
    """Preflight all recorded components, write selected ones, read back all."""
    components = {}
    if verify_model:
        verify_model()
    identifiers = {row['identifier'] for row in manifest}
    if len(identifiers) != len(manifest) or set(patches) - identifiers:
        raise ValueError("Component identifiers are duplicated or outside the verified manifest.")
    for row in manifest:
        obj = select(row["identifier"])
        if obj is None or int(obj.Number) != row["number"] or str(obj.Name) != row["name"]:
            raise RuntimeError("Tekla component identity changed after the approved publication.")
        verify(model, obj, row["attributes"])
        components[row["identifier"]] = obj
    changed = []
    try:
        for row in manifest:
            ident = row["identifier"]
            if ident not in patches:
                continue
            obj = components[ident]
            changed.append(row)
            for key, value in patches[ident].items():
                set_attribute(obj, key, value)
            if obj.Modify() is False:
                raise RuntimeError("Tekla rejected the selected component update.")
        if model.CommitChanges() is False:
            raise RuntimeError("Tekla rejected the selected-update commit.")
        result = deepcopy(manifest)
        for row in result:
            row["attributes"].update(patches.get(row["identifier"], {}))
            verify(model, components[row["identifier"]], row["attributes"])
        if verify_model:
            verify_model()
        return result
    except Exception as exc:
        rollback_errors = []
        for row in changed:
            try:
                obj = components[row["identifier"]]
                for key in patches[row["identifier"]]:
                    set_attribute(obj, key, row["attributes"][key])
                if obj.Modify() is False:
                    raise RuntimeError("rollback rejected")
            except Exception:
                rollback_errors.append(row["identifier"])
        if changed:
            try:
                if model.CommitChanges() is False:
                    rollback_errors.append("commit")
                for row in changed:
                    verify(model, components[row["identifier"]], row["attributes"])
                if verify_model:
                    verify_model()
            except Exception:
                rollback_errors.append("readback")
        raise RuntimeError(f"Selected update failed; rollback {'needs manual inspection' if rollback_errors else 'verified for recorded attributes'}. {exc}") from exc


def run_selected_tekla(state):
    from agentic.agents import a7_tekla_modeler_agent as a7
    from agentic.chat_changes import verify_selected_values
    try:
        if not state.validation_report.get("passed"):
            raise ValueError("A passing package validation is required.")
        verify_selected_values(state)
        if not a7.tekla_common:
            raise ValueError('Tekla Open API is unavailable on this host; no model was changed.')
        baseline = state.change_scope["source_tekla"]
        manifest = baseline.get("connection_manifest")
        if not manifest or baseline.get('component_manifest_version') != 1:
            raise ValueError("The source publication has no verified component manifest.")
        model = a7.tekla_common._get_connected_model()
        if not baseline.get("model_path") or Path(model.GetInfo().ModelPath).resolve() != Path(baseline["model_path"]).resolve():
            raise ValueError("The active Tekla model differs from the approved source publication.")
        patches = component_patch(manifest, state.change_scope["changes"], state.change_scope["groups"])
        from Tekla.Structures import Identifier
        select = lambda ident:model.SelectModelObject(Identifier(ident))
        verify_model = lambda:verify_members(baseline.get('member_manifest',[]),state.frame_candidate.get('members',[]),
                                             select,a7.tekla_common.to_tekla_profile)
        updated = apply_components(model, manifest, patches,
            select, a7._verify_component, a7.set_component_attribute, verify_model)
        from Tekla.Structures.Model import ModelHandler
        import getpass
        if not ModelHandler().Save("Approved selected connection update", getpass.getuser()):
            raise RuntimeError("Updated components were committed but model save failed; inspect the model before retrying.")
        state.tekla_results = {**baseline, "connection_manifest": updated, "updated_connections": len(patches),
                               "verified_members":len(baseline['member_manifest']),
                               "publication_scope": "selected_connections", "failed": 0, "errors": []}
        message = f"Updated {len(patches)} existing Tekla connections; recorded unselected attributes verified unchanged. No model rebuild."
        state.agent_statuses["A7"] = {"status": "PASSED", "message": message}
        state.logs.append(AgentLogEntry(agent_id="A7", agent_name="Selected Tekla updater", status="PASSED",
                         message=message, timestamp=datetime.now().strftime("%H:%M:%S")))
        return state
    except Exception as exc:
        return fail_workflow(state, "A7", str(exc))
