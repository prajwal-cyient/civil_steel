"""
a5_ideastatica_analyzer_agent.py

Agent A5: Check the Joints (IDEA StatiCa FEA Analyzer & Joint Grader).
Contains the complete IDEA StatiCa Connection REST API client and design_joint() logic.
Executes the deterministic Joint Grader to verify plate plastic strain (<= 5%) and bolt/weld utilization (<= 100%).
Triggers Loop B corrective action when connection checks fail.
"""

from datetime import datetime
from copy import deepcopy
import json
import hashlib
from math import pi, isfinite
import os
import sys
import time
from agents.A5_idea_statica.idea_runtime import calculate_connection, calculation_budget
from typing import Any, Dict, List, Optional

try:
    from pydantic import StrictStr
except ImportError:
    StrictStr = str

try:
    from ideastatica_connection_api.connection_api_service_attacher import ConnectionApiServiceAttacher
    from ideastatica_connection_api import (
        ConConnectionLibrarySearchParameters,  # noqa: F401
        ConLoadEffect,
        ConLoadEffectMemberLoad,
        ConLoadEffectSectionLoad,
        ConMemberConnectedByEnum,
        ConMprlCrossSection,
        ConMprlElement,
    )
    from ideastatica_connection_api.models.con_operation_common_properties import ConOperationCommonProperties
    from ideastatica_connection_api.connection_api_client import ConnectionApiClient
    from ideastatica_connection_api.models.con_connection import ConConnection
    from ideastatica_connection_api.models.con_aligned_plate import ConAlignedPlate
    from ideastatica_connection_api.models.con_member import ConMember
    from ideastatica_connection_api.models.con_operation import ConOperation
    from ideastatica_connection_api.models.idea_parameter import IdeaParameter
    from ideastatica_connection_api.models.idea_parameter_update import IdeaParameterUpdate
    from ideastatica_connection_api.models.parameter_update_response import ParameterUpdateResponse
    from ideastatica_connection_api.models.connection_data import ConnectionData
    from ideastatica_connection_api.models.con_load_effect_position_enum import ConLoadEffectPositionEnum
except ImportError:
    ConnectionApiServiceAttacher = None
    ConnectionApiClient = Any
    ConConnection = Any
    ConMember = Any
    ConOperation = Any
    ConOperationCommonProperties = Any
    IdeaParameter = Any
    ParameterUpdateResponse = Any

from config.run_policy import RunPolicy
from orchestration.state import WorkflowState, AgentLogEntry, GraderResult, fail_workflow
from config.settings import settings
from services.correction_advisor import choose_correction
from agents.A5_idea_statica.package_validation import (
    material_only_plate_evidence, reference_plate_material_policy, verify_plate_material_evidence,
)
from config.ladders import (
    get_next_plate_thickness,
    get_next_bolt_diameter,
    get_next_weld_size,
    step_parameter_expression,
)
from agents.A5_idea_statica.idea_runtime import IdeaStaticaJointError
from agents.A5_idea_statica.A5A_build_json import connection_output_dir, _connection_payload
from agents.A5_idea_statica.A5C_evaluate_and_audit import grade_joint_check_summary
from agents.A5_idea_statica.joint_corrections import _correct_connection_parameters, apply_failed_joint_grade


BASE_URL = os.environ.get("IDEA_STATICA_BASE_URL", "http://localhost:5000")
SETUP_DIR = os.environ.get(
    "IDEA_STATICA_SETUP_DIR", r"C:\Program Files\IDEA StatiCa\StatiCa 26.0"
)


# ── Errors ────────────────────────────────────────────────────────────────



# ── Internals ────────────────────────────────────────────────────────────


from .idea_runtime import IdeaStaticaConnectionError, IdeaStaticaJointError, ensure_idea_solver_idle, calculation_budget, calculate_connection
from . import package_validation
from .A5C_evaluate_and_audit import _validate_template_limits, validate_calculation_check_coverage, grade_joint_check_summary
from .package_validation import material_only_plate_evidence, reference_plate_material_policy

def _to_jsonable(obj: Any) -> Any:
    """
    Best-effort conversion of an SDK model object (OpenAPI-generated
    Pydantic-style model) into plain JSON-serializable data.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    for method_name in ("to_dict", "dict", "model_dump"):
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                return _to_jsonable(method())
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        return {k: _to_jsonable(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def _get_client(attacher_only: bool = True):
    """
    Connects to the Connection API service.
    """
    from agents.A5_idea_statica.idea_runtime import ensure_idea_solver_idle
    ensure_idea_solver_idle()
    if ConnectionApiServiceAttacher is None:
        raise IdeaStaticaConnectionError(
            "ideastatica-connection-api is not installed "
            "(`pip install ideastatica-connection-api`, matching your IDEA StatiCa version)."
        )
    if not attacher_only:
        raise NotImplementedError(
            "Only the attacher pattern is wired up here; start "
            "IdeaStatiCa.ConnectionRestApi.exe yourself."
        )
    try:
        return ConnectionApiServiceAttacher(BASE_URL).create_api_client()
    except Exception as exc:
        raise IdeaStaticaConnectionError(
            f"Could not attach to the IDEA StatiCa Connection API service at {BASE_URL}. "
            "Make sure IdeaStatiCa.ConnectionRestApi.exe is running.\n"
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc


def _resolve_connection(api_client, project_id: str, selector: Dict[str, Any]):
    connections = api_client.connection.get_connections(project_id)
    if not connections:
        raise IdeaStaticaJointError("The template project has no connections.")

    if "id" in selector:
        for c in connections:
            if c.id == selector["id"]:
                return c
        raise IdeaStaticaJointError(f"No connection with id={selector['id']!r}.")

    if "index" in selector:
        idx = selector["index"]
        if not (0 <= idx < len(connections)):
            raise IdeaStaticaJointError(f"connection index {idx} out of range (0..{len(connections)-1}).")
        return connections[idx]

    if "name" in selector:
        needle = selector["name"].lower()
        matches = [c for c in connections if needle == (c.name or "").lower()]
        if not matches:
            matches = [c for c in connections if needle in (c.name or "").lower()]
        if not matches:
            names = [c.name for c in connections]
            raise IdeaStaticaJointError(
                f"No connection name contains {selector['name']!r}. Available: {names}"
            )
        if len(matches) != 1:
            raise IdeaStaticaJointError(f"Ambiguous connection selector {selector['name']!r}.")
        return matches[0]

    raise IdeaStaticaJointError("connection_selector must have 'id', 'index', or 'name'.")


def _resolve_member(members: List[Any], member_name: str):
    needle = member_name.lower()
    matches = [m for m in members if needle == (m.name or "").lower()]
    if not matches:
        matches = [m for m in members if needle in (m.name or "").lower()]
    if not matches:
        names = [m.name for m in members]
        raise IdeaStaticaJointError(
            f"No member name contains {member_name!r}. Available: {names}"
        )
    if len(matches) != 1:
        raise IdeaStaticaJointError(f"Ambiguous member selector {member_name!r}.")
    return matches[0]


def _section_update_member(members, name):
    # Senior payloads name a transverse proxy, but the supplied templates
    # contain only the aligned structural beam. Resolve it explicitly and
    # permit read-only verification below; never silently drop the update.
    if name == "beam_transverse_placeholder" and not any(
            (member.name or "").lower() == name for member in members):
        return _resolve_member(members, "beam_transverse")
    return _resolve_member(members, name)


def _free_end_position(member: Any) -> Any:
    connected_by = getattr(member, "connected_by", None)
    if connected_by == getattr(ConMemberConnectedByEnum, "BEGIN", None):
        return getattr(ConLoadEffectPositionEnum, "END", "End")
    if connected_by == getattr(ConMemberConnectedByEnum, "END", None):
        return getattr(ConLoadEffectPositionEnum, "BEGIN", "Begin")
    raise IdeaStaticaJointError(
        f"Member {getattr(member, 'name', '')!r} has no connected_by set; can't determine its free end."
    )


def _resolve_cross_section_id(api_client, project_id: str, mprl_cross_section_name: str, mprl_material_name: str) -> int:
    def matching(section):
        section = _to_jsonable(section)
        if not isinstance(section, dict):
            return False
        material = section.get("material") or {}
        if not isinstance(material, dict):
            return False
        material = material.get("element") or material
        normalize = lambda value: "".join(str(value or "").upper().split())
        return (
            normalize(section.get("name") or section.get("mprl_name")) == normalize(mprl_cross_section_name)
            and normalize(material.get("name")) == normalize(mprl_material_name)
        )

    existing = api_client.material.get_cross_sections(project_id)
    for cs in existing:
        if matching(cs):
            cs_id = cs["id"] if isinstance(cs, dict) else getattr(cs, "id")
            return cs_id

    added = api_client.material.add_cross_section(
        project_id,
        ConMprlCrossSection(material_name=mprl_material_name, mprl_name=mprl_cross_section_name),
    )
    added_id = added.get("id", None) if isinstance(added, dict) else getattr(added, "id", None)
    if added_id is not None and matching(added):
        return added_id

    refreshed = api_client.material.get_cross_sections(project_id)
    for cs in refreshed:
        if matching(cs):
            cs_id = cs["id"] if isinstance(cs, dict) else getattr(cs, "id")
            return cs_id

    raise IdeaStaticaJointError(
        f"Could not verify cross-section '{mprl_cross_section_name}' with material '{mprl_material_name}'. "
        "Matching a profile name alone does not verify its steel grade."
    )


def _list_operations(api_client, project_id: str, connection_id: int):
    return api_client.operation.get_operations(project_id, connection_id)


def _list_operations_properties(api_client, project_id: str, connection_id: int):
    return api_client.operation.get_common_operation_properties(project_id, connection_id)


def _get_parameters(api_client, project_id: str, connection_id: int, include_hidden: bool = False):
    return api_client.parameter.get_parameters(project_id, connection_id, include_hidden)


def _find_parameters(api_client, project_id: str, connection_id: int, name_contains: str, include_hidden: bool = True):
    needle = name_contains.lower()
    return [
        p for p in _get_parameters(api_client, project_id, connection_id, include_hidden)
        if needle in (p.key or "").lower() or needle in (p.description or "").lower()
    ]


def _quote_expression_if_needed(expression: str) -> str:
    expression = expression.strip()
    if not expression:
        return expression
    if (expression[0] == expression[-1] == '"') or (expression[0] == expression[-1] == "'"):
        return expression
    try:
        float(expression)
        return expression
    except ValueError:
        return f'"{expression}"'


def _set_parameters(api_client, project_id: str, connection_id: int, updates: List[Dict[str, str]]):
    param_updates = [
        IdeaParameterUpdate(key=u["key"], expression=_quote_expression_if_needed(str(u["expression"])))
        for u in updates
    ]
    try:
        response = api_client.parameter.update(project_id, connection_id, param_updates)
        if getattr(response, "failed_validations", None):
            raise IdeaStaticaJointError(f"Parameter update failed validation: {response.failed_validations}")
        return response
    except Exception as e:
        raise IdeaStaticaJointError(f"Failed to set parameters: {e}") from e


def to_json_convention_string(obj: Any) -> str:
    return str(obj).replace('\'', '\"').replace('None', 'null').replace('True', 'true').replace('False', 'false')


# ── Public API ───────────────────────────────────────────────────────────


def _localize_failed_weld_parameters(api_client, project_id, connection_id, parameters, raw, progress):
    """Map failed solver weld IDs using reversible probes of the disposable project."""
    failed = set()
    for row in (raw.get("welds") or {}).values():
        if row.get("unityCheckWeld", 0) > 1:
            if row.get("unityCheckBaseMetal", 0) > 1 or row.get("isDetailingStatusOK") is False:
                raise IdeaStaticaJointError("Weld failure includes base-metal/detailing failure; weld-size-only correction is not justified.")
            failed.add(str(row["id"]))
    if not failed:
        raise IdeaStaticaJointError("Detailed failed weld IDs were not returned; no correction was localized.")

    def snapshot():
        data = _to_jsonable(api_client.export.export_iom_connection_data(project_id, connection_id))
        welds = {str(weld["id"]): float(weld["thickness"]) for weld in data.get("welds", [])}
        def canonical(value):
            if isinstance(value, float): return round(value, 9)
            if isinstance(value, dict): return {key: canonical(item) for key,item in value.items()}
            if isinstance(value, list): return [canonical(item) for item in value]
            return value
        geometry = canonical({key: value for key,value in data.items() if key != "welds"})
        return welds, geometry

    base, geometry = snapshot()
    if not failed.issubset(base):
        raise IdeaStaticaJointError("Failed weld IDs do not match exported connection geometry.")
    bindings = {}
    for parameter in parameters:
        key, expression = parameter["key"], str(parameter.get("expression", ""))
        if "weld" not in key.lower(): continue
        value = expression.strip("\"'")
        stepped = step_parameter_expression(key, value, "weld_size")
        if stepped == value: continue
        progress(f"Localizing weld feedback using parameter {key}")
        changed = set()
        try:
            _set_parameters(api_client, project_id, connection_id, [{"key": key, "expression": stepped}])
            after, new_geometry = snapshot()
            if set(after) != set(base) or new_geometry != geometry:
                raise IdeaStaticaJointError(f"Parameter {key} changes unrelated connection geometry; automatic binding rejected.")
            changed = {identifier for identifier in base if abs(after[identifier]-base[identifier]) > 1e-9}
        finally:
            _set_parameters(api_client, project_id, connection_id, [{"key": key, "expression": expression}])
            restored, restored_geometry = snapshot()
            if restored != base or restored_geometry != geometry:
                raise IdeaStaticaJointError(f"Could not restore parameter {key} after the disposable localization probe.")
        if changed:
            bindings[key] = sorted(changed)
    selected = set()
    for identifier in failed:
        matches = [key for key, identifiers in bindings.items() if identifier in identifiers]
        if len(matches) != 1:
            raise IdeaStaticaJointError(f"Failed weld {identifier} has {len(matches)} unambiguous exposed-parameter bindings; no blind correction applied.")
        selected.add(matches[0])
    touched = {identifier for key in selected for identifier in bindings[key]}
    if not touched.issubset(failed):
        raise IdeaStaticaJointError(
            f"Shared weld parameters would also change passing weld IDs {sorted(touched-failed)}; "
            "a separately validated local operation is required.")
    return {"failed_weld_ids": sorted(failed), "failed_parameter_keys": sorted(selected),
            "parameter_to_weld_ids": bindings, "unrelated_geometry_unchanged": True,
            "probe_expressions_restored": True}


def _apply_reference_plate_material(api_client, project_id, connection_id, plate_policy):
    if plate_policy != reference_plate_material_policy():
        raise IdeaStaticaJointError("Plate material policy differs from the verified Tekla detail files.")
    steels = _to_jsonable(api_client.material.get_steel_materials(project_id))
    matches = [m for m in steels if m.get("name") == plate_policy["idea_material_name"]
               and m.get("fy") == 355000000.0 and m.get("fu") == 490000000.0]
    if len(matches) != 1:
        raise IdeaStaticaJointError("A unique verified S355 plate catalogue material is required.")
    before = _to_jsonable(api_client.export.export_iom_connection_data(project_id, connection_id))
    properties = api_client.operation.get_common_operation_properties(project_id, connection_id)
    properties.plate_material_id = matches[0]["id"]
    api_client.operation.update_common_operation_properties(project_id, connection_id, properties)
    # This endpoint returns an aggregate editing selection, not a persisted
    # plate assignment. Verify the actual exported plates rather than its ID.
    after = _to_jsonable(api_client.export.export_iom_connection_data(project_id, connection_id))
    return material_only_plate_evidence(before, after, plate_policy)


def design_joint(data: dict) -> dict:
    """
    Opens a template .ideaCon project, updates member sections and applied loads,
    runs the CBFEM calculation, and extracts check results and IOM geometry.
    """
    template_path = data["template_project_path"]
    connection_selector = data["connection_selector"]
    member_updates = data.get("member_updates", [])
    load_effect_specs = data.get("load_effects", [])
    operation_parameter_updates = data.get("operation_parameters", [])
    output_dir = data["output_dir"]
    save_project_path = data.get("save_project_path")
    template_basename = os.path.splitext(os.path.basename(template_path))[0]
    joint_group_name = data.get("joint_group_name", template_basename)
    def progress(message):
        print(f"IDEA {joint_group_name}: {message}", flush=True)
        callback = data.get("progress_callback")
        if callback:
            callback(message)
    if not save_project_path:
        save_project_path = os.path.join(output_dir, 'out_' + os.path.basename(template_path))
    summary_file = os.path.join(output_dir, 'summary_' + template_basename + '.json')
    iom_connection_data_file = os.path.join(output_dir, 'iom_connection_data_' + template_basename + '.json')

    output_messages = []
    plate_material_evidence = None
    if not os.path.isfile(template_path):
        raise IdeaStaticaJointError(f"Template project not found: {template_path}")
    os.makedirs(output_dir, exist_ok=True)

    with _get_client() as api_client:
        progress("Opening connection template")
        try:
            api_client.project.open_project_from_filepath(template_path)
        except Exception as exc:
            api_client.project = None
            raise IdeaStaticaJointError(
                f"Could not open template project {template_path}: {type(exc).__name__}: {exc}"
            ) from exc

        project_id = api_client.project.active_project_id
        connection = _resolve_connection(api_client, project_id, connection_selector)
        connection_id = connection.id
        members = api_client.member.get_members(project_id, connection_id)
        if data.get("enforce_member_coverage"):
            expected_ids = {_section_update_member(members, u["member_name"]).id for u in member_updates}
            expected_ids.update(_resolve_member(members, loading["member_name"]).id
                                for effect in load_effect_specs for loading in effect.get("member_loadings", []))
            if expected_ids != {member.id for member in members}:
                raise IdeaStaticaJointError("Template topology contains members not covered by the current joint inputs.")

        # ---- Member cross-section/material updates ----
        progress("Verifying member sections, materials and brace angles")
        for update in member_updates:
            try:
                member = _section_update_member(members, update["member_name"])
            except Exception as e:
                raise IdeaStaticaJointError(f"Cannot apply section for {update['member_name']}: {e}") from e
            cross_section_id = _resolve_cross_section_id(
                api_client, project_id,
                update["cross_section_mprl_name"], update["material_mprl_name"],
            )
            if member.cross_section_id == cross_section_id and "gama_pitch" not in update:
                continue  # Preserve the template's aligned-member placement.
            if update["member_name"] == "beam_transverse_placeholder" and member.name == "beam_transverse":
                raise IdeaStaticaJointError("Aligned transverse beam has a different section/material; a compatible proxy or template is required.")
            member.cross_section_id = cross_section_id
            if "gama_pitch" in update:
                member.position.gama_pitch = float(update["gama_pitch"]) * pi / 180
            api_client.member.update_member(project_id, connection_id, member)
            saved = next((m for m in api_client.member.get_members(project_id, connection_id) if m.id == member.id), None)
            if saved is None or saved.cross_section_id != cross_section_id:
                raise IdeaStaticaJointError(f"Member {member.name} did not retain its section/material update.")
            if "gama_pitch" in update and abs(saved.position.gama_pitch - float(update["gama_pitch"]) * pi / 180) > 1e-8:
                raise IdeaStaticaJointError(f"Member {member.name} did not retain its brace angle.")

        # ---- Load effect ----
        progress(f"Applying {len(load_effect_specs)} STAAD load effects")
        if data.get("replace_template_loads"):
            for existing in api_client.load_effect.get_load_effects(project_id, connection_id):
                api_client.load_effect.delete_load_effect(project_id, connection_id, existing.id)
        for load_effect_spec in load_effect_specs:
            member_loadings = []
            for loading in load_effect_spec.get("member_loadings", []):
                try:
                    member = _resolve_member(members, loading["member_name"])
                except Exception as e:
                    raise IdeaStaticaJointError(f"Cannot apply load for {loading['member_name']}: {e}") from e
                specified_position = loading.get("position", "")
                if specified_position and specified_position.lower() in ["start", "begin"]:
                    position = ConLoadEffectPositionEnum.BEGIN
                elif specified_position and specified_position.lower() in ["end"]:
                    position = ConLoadEffectPositionEnum.END
                else:
                    position = _free_end_position(member)
                member_loadings.append(ConLoadEffectMemberLoad(
                    member_id=member.id,
                    position=position,
                    section_load=ConLoadEffectSectionLoad(
                        n=loading.get("n", 0) * 1000.0, vy=loading.get("vy", 0) * 1000.0, vz=loading.get("vz", 0) * 1000.0,
                        mx=loading.get("mx", 0) * 1000.0, my=loading.get("my", 0) * 1000.0, mz=loading.get("mz", 0) * 1000.0,
                    ),
                ))
            api_client.load_effect.add_load_effect(
                project_id, connection_id,
                ConLoadEffect(
                    name=load_effect_spec.get("name", "Generated load"),
                    active=True,
                    member_loadings=member_loadings,
                ),
            )

        # ---- Operation parameters (plate/bolt/weld dimensions) ----
        if operation_parameter_updates:
            exposed = {p.key for p in _get_parameters(api_client, project_id, connection_id)}
            unknown = {p["key"] for p in operation_parameter_updates} - exposed
            if unknown:
                raise IdeaStaticaJointError(f"Parameters not exposed by template: {sorted(unknown)}")
            try:
                _set_parameters(api_client, project_id, connection_id, operation_parameter_updates)
            except Exception as e:
                raise IdeaStaticaJointError(f"Operation parameter update rejected: {e}") from e

        operation_parameters = [
            {
                "key": p.key,
                "value": p.value,
                "unit": p.unit,
                "expression": p.expression,
                "description": p.description,
            }
            for p in _get_parameters(api_client, project_id, connection_id)
        ]
        plate_policy = data.get("plate_material_policy")
        if plate_policy:
            progress("Aligning plate materials with the declared standard Tekla details")
            plate_material_evidence = _apply_reference_plate_material(
                api_client, project_id, connection_id, plate_policy)
        by_key = {p["key"]: p for p in operation_parameters}
        for update in operation_parameter_updates:
            expected = str(update["expression"]).strip("\"'")
            actual = str(by_key.get(update["key"], {}).get("expression", "")).strip("\"'")
            try:
                matches = isfinite(float(expected)) and abs(float(expected) - float(actual)) <= 1e-9
            except ValueError:
                matches = expected == actual
            if not matches:
                raise IdeaStaticaJointError(f"Parameter {update['key']} did not retain the requested expression.")

        # IDEA summary check_value is utilization %, not physical strain %.
        # Confirm the physical limits and enabled checks before grading its summary.
        setup = api_client.settings.get_settings(project_id)
        physical_limits = _validate_template_limits(setup)

        # ---- Calculate ----
        progress(f"Running IDEA finite-element calculation ({len(load_effect_specs)} load effects)")
        results = calculate_connection(api_client, project_id, connection_id,
                                      calculation_budget(len(load_effect_specs)), progress)
        if not results:
            raise IdeaStaticaJointError("IDEA calculation returned no results.")
        result = results[0]
        progress("Reading calculation checks and saving connection evidence")
        if save_project_path:
            os.makedirs(os.path.dirname(save_project_path) or ".", exist_ok=True)
            api_client.project.download_project(project_id, save_project_path)
        check_summary = [
            {
                "name": c.name,
                "check_value": c.check_value,
                "check_status": c.check_status,
                "skipped": c.skipped,
                "message": getattr(c, "unity_check_message", None),
            }
            for c in (result.result_summary or [])
        ]
        check_summary = {"summary": check_summary, "operation_parameters": operation_parameters,
                         "physical_limits": physical_limits, "value_kind": "utilization_percent",
                         "run_policy": RunPolicy.model_validate(data.get("run_policy", {})).model_dump()}
        validate_calculation_check_coverage(check_summary["summary"])
        feedback_grade = grade_joint_check_summary(check_summary, data.get("run_policy"))
        if feedback_grade.corrective_action_needed and feedback_grade.corrective_action.get("fix_type") == "weld_size":
            progress("Reading detailed failed weld feedback")
            raw_feedback = json.loads(api_client.calculation.get_raw_json_results(project_id, [connection_id])[0])
            feedback_path = os.path.join(output_dir, "failed_weld_feedback.json")
            with open(feedback_path, "w") as feedback_file:
                json.dump(raw_feedback, feedback_file)
            try:
                localization = _localize_failed_weld_parameters(api_client, project_id, connection_id,
                                                               operation_parameters, raw_feedback, progress)
                check_summary.update(localization)
            except IdeaStaticaJointError as exc:
                check_summary["localization_error"] = str(exc)
                check_summary["failed_parameter_keys"] = []
        elif feedback_grade.corrective_action_needed:
            # One exposed plate/bolt variable can still control several
            # operations. A category name or a unique variable is not proof
            # that all of those operations failed.
            check_summary["failed_parameter_keys"] = []
            check_summary["localization_error"] = (
                "Detailed failed bolt/plate operation-to-parameter bindings are not validated for this template; "
                "category-wide or guessed changes are prohibited.")
        with open(summary_file, "w") as f:
            json.dump(check_summary, f, indent=2)

        connection_data = api_client.export.export_iom_connection_data(project_id, connection_id)
        with open(iom_connection_data_file, "w") as f:
            f.write(json.dumps(connection_data.to_dict() if hasattr(connection_data, "to_dict") else vars(connection_data), indent=2))
        if plate_material_evidence:
            with open(iom_connection_data_file, "rb") as source:
                plate_material_evidence["iom_sha256"] = hashlib.sha256(source.read()).hexdigest()
            package_validation.verify_plate_material_evidence(plate_material_evidence, plate_policy, iom_connection_data_file)

        extract_raw_results = data.get("extract_raw_results", False)
        raw_results_file = None
        raw_results_error = None
        if extract_raw_results or result.passed is not True or not feedback_grade.passed:
            try:
                raw_json_list = api_client.calculation.get_raw_json_results(
                    project_id, [connection_id], _request_timeout=(10.0, 60.0))
                raw_text = raw_json_list[0]
                json.loads(raw_text)  # Do not publish incomplete diagnostic JSON.
                raw_results_file = os.path.join(output_dir, "raw_results_" + template_basename + ".json")
                with open(raw_results_file, "w") as f:
                    f.write(raw_text)
            except Exception as exc:
                if extract_raw_results:
                    raise
                raw_results_file = None
                raw_results_error = f"{type(exc).__name__}: {exc}"

        extract_pdf_report = data.get("extract_pdf_report", False)
        report_pdf_file = None
        if extract_pdf_report:
            report_pdf_file = os.path.join(output_dir, "report_" + template_basename + ".pdf")
            api_client.report.save_report_pdf(project_id, connection_id, report_pdf_file)

    return {
        "connection_id": connection_id,
        "joint_group": joint_group_name,
        "passed": result.passed is True and feedback_grade.passed,
        "native_calculation_passed": result.passed,
        "validation_summary": feedback_grade.summary,
        "check_summary": check_summary,
        "output_messages": output_messages,
        "check_summary_file": summary_file,
        "raw_results_file": raw_results_file,
        "raw_results_error": raw_results_error,
        "iom_connection_data_file": iom_connection_data_file,
        "report_pdf_file": report_pdf_file,
        "saved_project_path": save_project_path,
        "plate_material_evidence": plate_material_evidence,
    }


# ── Grader Logic ─────────────────────────────────────────────────────────


def execute_a5(state: WorkflowState, context: dict) -> WorkflowState:
    """Call IDEA while retaining its native client lifecycle within design_joint."""
    joint_group, payload = context["joint_group"], context["payload"]
    try:
        __import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_check_a5_request'])._check_a5_request(state, context)
        from services.workflow_store import WORKFLOW_STORE
        state.agent_statuses["A5"] = {"status": "RUNNING", "message": f"Calculating {joint_group} (corrections used: {state.loop_b_count.get(joint_group, 0)}/{state.max_loop_b})."}
        WORKFLOW_STORE.save_state(state)
        def update_progress(message):
            from utils.audit import event
            event(state, 'progress', 'IDEA_StatiCa', joint_group=joint_group, message=message)
            timeout = settings().agent_timeout
            if message.startswith(("Running IDEA finite-element calculation", "IDEA job", "IDEA legacy", "IDEA service lacks")):
                timeout = calculation_budget(len(payload["load_effects"]))
            state.agent_statuses["A5"] = {"status": "RUNNING", "message": f"{joint_group}: {message}",
                                           "operation_timeout_seconds": timeout}
            WORKFLOW_STORE.save_state(state)
        from utils.run_archive import guarded, start_idea, record_idea
        archive_calculation = guarded(state, start_idea, joint_group, payload)
        try:
            from utils.audit import observe
            with observe(state, 'IDEA native connection calculation', 'IDEA_StatiCa',
                         joint_group=joint_group, load_effects=len(payload.get('load_effects', []))):
                result = design_joint({**payload, "progress_callback": update_progress})
        except Exception as exc:
            guarded(state, record_idea, joint_group, payload, {'error':str(exc)}, archive_calculation)
            raise
        guarded(state, record_idea, joint_group, payload, result, archive_calculation)
        __import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_check_a5_request'])._check_a5_request(state, context)
        if result.get("simulation"):
            raise IdeaStaticaJointError("Simulated results cannot verify a live connection.")
        if result.get("output_messages"):
            raise IdeaStaticaJointError(f"Unresolved integration warnings: {result['output_messages']}")
        result["input_signature"] = context["signature"]
        if payload.get('output_dir'):
            result['artifact_dir'] = payload['output_dir']
        state.ideastatica_results[joint_group] = result
        context["result"] = result
        return state
    except Exception as exc:
        return __import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_fail_a5_step'])._fail_a5_step(state, context, exc)


