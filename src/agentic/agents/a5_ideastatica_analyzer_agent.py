"""
a5_ideastatica_analyzer_agent.py

Agent A5: Check the Joints (IDEA StatiCa FEA Analyzer & Joint Grader).
Contains the complete IDEA StatiCa Connection REST API client and design_joint() logic.
Executes the deterministic Joint Grader to verify plate plastic strain (<= 5%) and bolt/weld utilization (<= 100%).
Triggers Loop B corrective action when connection checks fail.
"""

from datetime import datetime
import json
import hashlib
from math import pi, isfinite
import os
import sys
import time
from agentic.idea_calculation import calculate_connection, calculation_budget
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

from agentic.run_policy import RunPolicy
from agentic.state import WorkflowState, AgentLogEntry, GraderResult, fail_workflow
from agentic.config import settings
from agentic.advisor import choose_correction
from agentic.connection_materials import (
    material_only_plate_evidence, reference_plate_material_policy, verify_plate_material_evidence,
)
from agentic.ladders import (
    get_next_plate_thickness,
    get_next_bolt_diameter,
    get_next_weld_size,
    step_parameter_expression,
)


BASE_URL = os.environ.get("IDEA_STATICA_BASE_URL", "http://localhost:5000")
SETUP_DIR = os.environ.get(
    "IDEA_STATICA_SETUP_DIR", r"C:\Program Files\IDEA StatiCa\StatiCa 26.0"
)


# ── Errors ────────────────────────────────────────────────────────────────

class IdeaStaticaConnectionError(RuntimeError):
    """Raised when the Connection API service can't be reached."""


class IdeaStaticaJointError(RuntimeError):
    """Raised when a specific joint (connection/member/load) can't be resolved or updated."""


# ── Internals ────────────────────────────────────────────────────────────

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
    from agentic.idea_runtime import ensure_idea_solver_idle
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

def _validate_template_limits(setup: dict) -> dict:
    """Check physical limits independently of summary utilization percentages."""
    prefix = "calculationCommon/Checks/Shared/"
    physical_limits = {"plastic_strain": setup.get(prefix + "LimitPlasticStrain"),
                       "local_deformation": setup.get(prefix + "LocalDeformationLimit")}
    for name, maximum in (("plastic_strain", 0.05), ("local_deformation", 0.03)):
        value = physical_limits[name]
        if type(value) not in (int, float) or not isfinite(value) or value <= 0 or value > maximum:
            raise IdeaStaticaJointError(f"Template {name} limit is missing or exceeds the configured rubric.")
    if setup.get("calculationCommon/Analysis/AnalysisGeneral/Shared/LocalDeformationCheck") is not True:
        raise IdeaStaticaJointError("Template local-deformation check is disabled.")
    return physical_limits


def validate_calculation_check_coverage(items):
    """The supported bolted/welded templates require every enabled check family."""
    names = [str(item.get("name", "")).lower() for item in items if item.get("skipped") is not True]
    required = {
        "analysis": lambda name: name == "analysis",
        "plates": lambda name: "plate" in name and "local" not in name,
        "local deformation": lambda name: "deformation" in name,
        "bolts": lambda name: "bolt" in name,
        "welds": lambda name: "weld" in name,
    }
    missing = [category for category, matches in required.items() if not any(matches(name) for name in names)]
    if missing:
        raise IdeaStaticaJointError(f"Missing/skipped required IDEA checks for the supported template: {missing}")


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
            verify_plate_material_evidence(plate_material_evidence, plate_policy, iom_connection_data_file)

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

def grade_joint_check_summary(check_summary: Dict[str, Any], run_policy=None) -> GraderResult:
    """Grade valid checks; distinguish exceeded limits from SDK status failures."""
    policy = RunPolicy.model_validate(run_policy or {})
    items = check_summary if isinstance(check_summary, list) else check_summary.get("summary", [])
    failures, fix_types, advisories = [], [], []
    maxima = {"max_plate_utilization": 0.0, "max_deformation_utilization": 0.0, "max_bolt": 0.0, "max_weld": 0.0}
    active_checks = 0
    unresolved = False
    for item in items:
        if item.get("skipped") is True:
            continue
        name = str(item.get("name", "")).lower()
        val, status = item.get("check_value"), item.get("check_status")
        if not isinstance(status, bool) or isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val):
            failures.append(f"Incomplete or nonfinite IDEA check: {name}")
            unresolved = True
            continue
        if "deformation" in name or "local" in name or "loc." in name:
            key, limit, fix = "max_deformation_utilization", policy.max_deformation_utilization_percent, "plate_thickness"
        elif "plate" in name or "strain" in name:
            key, limit, fix = "max_plate_utilization", policy.max_plate_utilization_percent, "plate_thickness"
        elif "bolt" in name:
            key, limit, fix = "max_bolt", policy.max_bolt_utilization_percent, "bolt_dia"
        elif "weld" in name:
            key, limit, fix = "max_weld", policy.max_weld_utilization_percent, "weld_size"
        else:
            if status is not True:
                failures.append(f"IDEA {name} reports check_status=False (value={val}); inspect calculation details.")
                unresolved = True
            continue
        active_checks += 1
        if val < 0:
            failures.append(f"Invalid negative result for {name}: {val}")
            unresolved = True
            continue
        maxima[key] = max(maxima[key], val)
        if val > limit:
            failures.append(f"{item['name']} value {val}% exceeds {limit}% rubric limit")
            fix_types.append(fix)
        elif status is False:
            failures.append(f"IDEA {name} reports check_status=False despite utilization {val}% within {limit}%; {item.get('message') or 'inspect calculation details before correction.'}")
            unresolved = True
    if not active_checks:
        failures.append("No active connection component checks returned.")
        unresolved = True
    if 0 < maxima["max_bolt"] < 50 and maxima["max_plate_utilization"] < 20:
        advisories.append("Joint underutilized; downsizing is not permitted by this rubric.")
    fix_type = fix_types[0] if fix_types and not unresolved else None
    return GraderResult(
        passed=not failures, score=0.0 if failures else 1.0,
        summary="; ".join(failures) if failures else "All returned connection checks passed the configured rubric.",
        details={**maxima, "failures": failures, "fix_type": fix_type,
                 "advisories": advisories, "downsizing_permitted": False},
        corrective_action_needed=fix_type is not None,
        corrective_action={"fix_type": fix_type} if fix_type else None,
    )


def connection_output_dir(state, joint_group, root):
    if joint_group in state.change_scope.get('groups', []) and not state.change_scope.get('reuse_calculations'):
        return os.path.join(root, 'output_exports', 'dynamic_changes', state.run_id, joint_group)
    return state.change_scope.get('artifact_dirs', {}).get(joint_group, os.path.join(root, f'output_{joint_group}'))


def _connection_payload(state: WorkflowState, joint_group: str) -> dict:
    """Use the exact template and all required strength envelopes, never sample loads."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if state.staad_results.get("results_local_or_global", 0) != 0:
        raise IdeaStaticaJointError("IDEA input requires local member forces; global forces need an explicit verified transformation.")
    from agentic.reference_procedure import scope_for_state, template_for_group
    scope = scope_for_state(state)
    reference = scope["mode"] == "reference_procedure"
    template = str(template_for_group(joint_group, reference))
    if not os.path.isfile(template):
        missing = sorted(group for group in state.staad_results.get("joint_groups", {joint_group: {}})
                         if not (settings().templates / f"{group}.ideaCon").is_file())
        raise IdeaStaticaJointError(
            f"No matching connection template for {joint_group}. "
            f"Missing template mappings ({len(missing)}): {', '.join(missing)}. "
            "Provide compatible connection templates and verified member/end mappings; "
            "unrelated templates cannot verify these joints."
        )
    envelopes = [e for e in state.staad_results.get("load_envelopes", []) if e.get("envelopeType") == "STRENGTH"]
    if not envelopes:
        raise IdeaStaticaJointError("STAAD returned no strength envelopes.")
    by_envelope = state.staad_results.get("envelope_load_cases_by_joint_group", {})
    loads, updates = [], None
    for envelope in envelopes:
        env_id = envelope["envelopeId"]
        groups = by_envelope.get(env_id, by_envelope.get(str(env_id), {}))
        data = groups.get(joint_group, {})
        if not data.get("member_updates") or not data.get("load_effects"):
            raise IdeaStaticaJointError(f"Missing member properties or loads for {joint_group}, strength envelope {env_id}.")
        if updates is not None and updates != data["member_updates"]:
            raise IdeaStaticaJointError(f"Inconsistent member properties across envelopes for {joint_group}.")
        updates = data["member_updates"]
        for effect in data["load_effects"]:
            if not effect.get("member_loadings"):
                raise IdeaStaticaJointError(f"Empty load effect in envelope {env_id}.")
            loads.append({**effect, "name": f"Envelope {env_id}: {effect.get('name', 'ULS')}"})
    material_map = scope.get("material_catalogue_mapping", {})
    updates = [{**update, "material_mprl_name": material_map.get(update["material_mprl_name"], update["material_mprl_name"])}
               if "material_mprl_name" in update else update for update in updates]
    return {
        **({"run_policy": state.run_policy} if state.run_policy and state.run_policy != RunPolicy().model_dump() else {}),
        "joint_group_name": joint_group,
        "template_project_path": template, "connection_selector": {"name": "CON1"},
        "member_updates": updates, "load_effects": loads,
        "replace_template_loads": True,
        "enforce_member_coverage": True,
        "operation_parameters": state.active_operation_params.get(joint_group, []),
        "plate_material_policy": reference_plate_material_policy() if scope["mode"] == "reference_procedure" else None,
        "output_dir": connection_output_dir(state, joint_group, root),
        "extract_raw_results": False,
    }


def _connection_signature(payload: dict, generation: int) -> str:
    template = payload.get("template_project_path")
    template_hash = None
    if template and os.path.isfile(template):
        with open(template, "rb") as source:
            template_hash = hashlib.sha256(source.read()).hexdigest()
    return hashlib.sha256(json.dumps({"generation": generation, "payload": payload, "template_hash": template_hash}, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _correct_connection_parameters(parameters: list, fix_type: str, parameter_keys=None) -> tuple:
    """Change only localized exposed parameters; never upsize every weld/plate."""
    updated, changes = [], []
    for parameter in parameters:
        key = parameter["key"]
        lower = key.lower()
        matches = (
            fix_type == "weld_size" and "weld" in lower
            or fix_type == "bolt_dia" and (lower.endswith("_d") or "diameter" in lower)
            or fix_type == "plate_thickness" and (lower.endswith("_th") or "thickness" in lower)
        )
        if parameter_keys is not None:
            matches = matches and key in parameter_keys
        expression = str(parameter.get("expression", ""))
        numeric = expression.strip("\"'")
        new_expression = step_parameter_expression(key, numeric, fix_type) if matches else numeric
        if matches and new_expression != numeric:
            if expression[:1] in ("'", '\"'):
                new_expression = expression[0] + new_expression + expression[0]
            changes.append({"key": key, "old_value": expression, "new_value": new_expression})
            expression = new_expression
        updated.append({"key": key, "expression": expression})
    if parameter_keys is None and len(changes) > 1:
        # A category summary such as "Welds failed" does not identify which
        # weld failed. Preserve already-correct details until the detailed
        # result is mapped to an exact exposed parameter.
        return [{"key": p["key"], "expression": str(p.get("expression", ""))} for p in parameters], []
    return updated, changes


def run_a5_ideastatica_analyzer(state: WorkflowState) -> WorkflowState:
    """Solve affected joints only, with bounded corrections and terminal failures."""
    state.current_agent = "A5"
    ts = datetime.now().strftime("%H:%M:%S")
    if not state.frame_grader_result or not state.frame_grader_result.passed or state.staad_results.get("analysis_failed"):
        return fail_workflow(state, "A5", "A verified passing STAAD analysis is required before connection design.")
    joint_groups = state.staad_results.get("joint_groups", {})
    if not joint_groups:
        return fail_workflow(state, "A5", "STAAD returned no connection groups.")
    from agentic.reference_procedure import scope_for_state
    try:
        scope = scope_for_state(state)
    except ValueError as exc:
        return fail_workflow(state, "A5", str(exc))
    selected = set(state.change_scope.get("groups", []))
    groups = sorted(scope["idea_required_groups"], key=lambda name: name in selected) if selected else scope["idea_required_groups"]
    for joint_group in groups:
        try:
            payload = _connection_payload(state, joint_group)
            signature = _connection_signature(payload, state.design_generation)
            previous = state.ideastatica_results.get(joint_group, {})
            grader = state.joint_grader_results.get(joint_group)
            if grader and grader.passed and previous.get("input_signature") == signature:
                # A cached PASS is only a shortcut when its source checks are
                # still complete and valid. Loop C must be able to repair a
                # damaged/missing result, not repeatedly reuse that stale flag.
                try:
                    validate_calculation_check_coverage(previous.get("check_summary", {}).get("summary", []))
                    verify_plate_material_evidence(previous.get("plate_material_evidence"),
                        payload.get("plate_material_policy"), previous.get("iom_connection_data_file"))
                    reusable = (previous.get("passed") is True and not previous.get("simulation")
                                and not previous.get("output_messages")
                                and grade_joint_check_summary(previous.get("check_summary", {}), state.run_policy).passed)
                except (IdeaStaticaJointError, ValueError):
                    reusable = False
                if reusable:
                    from agentic.run_archive import guarded, record_idea
                    guarded(state, record_idea, joint_group, payload, {**previous, 'archive_evidence_reused':True})
                    continue
            if selected and joint_group not in selected:
                return fail_workflow(state, "A5", f"Unselected group {joint_group} lacks reusable verified evidence. Broader analysis requires a new approved plan.")
            from agentic.store import WORKFLOW_STORE
            state.agent_statuses["A5"] = {"status": "RUNNING", "message": f"Calculating {joint_group} (corrections used: {state.loop_b_count.get(joint_group, 0)}/{state.max_loop_b})."}
            WORKFLOW_STORE.save_state(state)
            def update_progress(message):
                from agentic.audit import event
                event(state, 'progress', 'IDEA_StatiCa', joint_group=joint_group, message=message)
                timeout = settings().agent_timeout
                if message.startswith("Running IDEA finite-element calculation"):
                    timeout = calculation_budget(len(payload["load_effects"]))
                elif message.startswith(("IDEA job", "IDEA legacy", "IDEA service lacks")):
                    timeout = calculation_budget(len(payload["load_effects"]))
                state.agent_statuses["A5"] = {"status": "RUNNING", "message": f"{joint_group}: {message}",
                                               "operation_timeout_seconds": timeout}
                WORKFLOW_STORE.save_state(state)
            from agentic.run_archive import guarded, start_idea, record_idea
            archive_calculation = guarded(state, start_idea, joint_group, payload)
            try:
                from agentic.audit import observe
                with observe(state, 'IDEA native connection calculation', 'IDEA_StatiCa',
                             joint_group=joint_group, load_effects=len(payload.get('load_effects', []))):
                    result = design_joint({**payload, "progress_callback": update_progress})
            except Exception as exc:
                guarded(state, record_idea, joint_group, payload, {'error':str(exc)}, archive_calculation)
                raise
            guarded(state, record_idea, joint_group, payload, result, archive_calculation)
            if result.get("simulation"):
                raise IdeaStaticaJointError("Simulated results cannot verify a live connection.")
            if result.get("output_messages"):
                raise IdeaStaticaJointError(f"Unresolved integration warnings: {result['output_messages']}")
            # Use the parameters actually exposed by this template, not a fabricated default.
            parameters = result.get("check_summary", {}).get("operation_parameters", [])
            result["input_signature"] = _connection_signature(payload, state.design_generation)
            if payload.get('output_dir'):
                result['artifact_dir'] = payload['output_dir']
            state.ideastatica_results[joint_group] = result
            grader = grade_joint_check_summary(result.get("check_summary", {}), state.run_policy)
            if result.get("passed") is not True and grader.passed:
                grader = GraderResult(passed=False, score=0, summary="IDEA did not report overall calculation success; inspect calculation details.")
            state.joint_grader_results[joint_group] = grader
            from agentic.audit import evaluation
            evaluation(state, 'IDEA_StatiCa', grader, joint_group=joint_group)
            if grader.passed:
                continue
            if selected:
                return fail_workflow(state, "A5", f"{joint_group}: approved values failed engineering checks. Further corrections require a new change preview and approval. {grader.summary}")
            if not grader.corrective_action_needed:
                return fail_workflow(state, "A5", f"{joint_group}: {grader.summary}")
            count = state.loop_b_count.get(joint_group, 0)
            if count >= state.max_loop_b:
                return fail_workflow(state, "A5", f"{joint_group}: retry limit reached. {grader.summary}")
            fix_type = grader.corrective_action["fix_type"]
            localized = result.get("check_summary", {}).get("failed_parameter_keys")
            updated, changes = _correct_connection_parameters(parameters, fix_type, localized)
            if not changes:
                localization_error = result.get("check_summary", {}).get("localization_error")
                return fail_workflow(state, "A5", f"{joint_group}: no uniquely localized exposed numeric {fix_type} correction is available. "
                                     "Detailed failed-operation-to-parameter mapping is required when several parameters match; "
                                     f"already-correct details were preserved. {grader.summary}"
                                     + (f" Localization: {localization_error}" if localization_error else ""))
            metric_signature = hashlib.sha256(json.dumps(result["check_summary"]["summary"], sort_keys=True, allow_nan=False).encode()).hexdigest()
            history = [entry for entry in state.loop_b_history if entry["joint_group"] == joint_group]
            if history and history[-1].get("result_signature") == metric_signature:
                return fail_workflow(state, "A5", f"{joint_group}: checks did not change after the confirmed parameter update; inspect calculation details instead of repeating the same correction.")
            options = [{"id": "all", "changes": changes}]
            if len(changes) > 1:
                options += [{"id": change["key"], "changes": [change]} for change in changes]
            selected = options[0]
            if count and len(options) > 1:
                selected = choose_correction(state, "connection", grader.model_dump(), options, "all")
            changes = selected["changes"]
            overrides = {p["key"]: p for p in state.active_operation_params.get(joint_group, [])}
            changed_keys = {p["key"] for p in changes}
            overrides.update({p["key"]: p for p in updated if p["key"] in changed_keys})
            state.active_operation_params[joint_group] = list(overrides.values())
            state.loop_b_count[joint_group] = count + 1
            state.loop_b_history.append({"joint_group": joint_group, "iteration": count + 1,
                "fix_type": fix_type, "changes": changes, "reason": grader.summary, "result_signature": metric_signature,
                "localization": {key: result["check_summary"][key] for key in
                    ("failed_weld_ids", "failed_parameter_keys", "parameter_to_weld_ids", "unrelated_geometry_unchanged", "probe_expressions_restored")
                    if key in result["check_summary"]}})
            message = f"Loop B ({count + 1}/{state.max_loop_b}): {joint_group}: {changes}"
            state.agent_statuses["A5"] = {"status": "REVISING", "message": message}
            state.logs.append(AgentLogEntry(agent_id="A5", agent_name="Connection Designer", status="REVISING", message=message, timestamp=datetime.now().astimezone().isoformat()))
            return state
        except Exception as exc:
            from agentic.audit import exception_record
            exception_record(state, 'IDEA connection execution', exc)
            return fail_workflow(state, "A5", f"{joint_group}: connection execution failed: {exc}")
    message = (f"All {len(scope['idea_required_groups'])} required IDEA connection groups passed the configured rubric; "
               f"{len(scope['standard_detail_groups'])} families use the senior workflow's standard Tekla details.")
    state.agent_statuses["A5"] = {"status": "PASSED", "message": message}
    state.logs.append(AgentLogEntry(agent_id="A5", agent_name="Connection Designer", status="PASSED", message=message, timestamp=datetime.now().astimezone().isoformat()))
    return state
