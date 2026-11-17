"""
a7_tekla_modeler_agent.py

Agent A7: Build the Model (Tekla Structures 3D Detailing Modeler).
Consolidated & Self-Contained. Connects directly to Tekla Structures via .NET Open API,
modifies the 3D grid, inserts physical members, and details base plates and joint connections
with attributes designed by IDEA StatiCa.
"""

from datetime import datetime
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

from agentic.state import WorkflowState, AgentLogEntry

# Ensure repo root is on sys.path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from common.tekla_common import *
    import common.tekla_common as tekla_common
except Exception:
    tekla_common = None


TEKLA_CONNECTION_PARAMETER_MAP = {
    "inner_node_1_longitudinal_1_transverse_bracing": [
        ("GRD_d", 30, "diameter", None),
        ("EP_d", 144, "diameter", None),
        ("EP_pl_th", 144, "tpl1", None),
        ("Stiff_th", 30, "tpl3", None),
        ("Stiff_th", 144, "l1", None),
        ("Stiff_th", 144, "l2", None),
        ("Stiff_sel", 30, "sel", None),
        ("Stiff_sel", 144, "sel", None),
        ("CPL_d", 20, "diameter", "longitudinal"),
        ("CPL_th", 20, "tpl1", "longitudinal"),
        ("CPL_th", 20, "tpl2", "longitudinal"),
        ("CPL_overlap_length", 20, "in", "longitudinal"),
        ("CPL2_d", 20, "diameter", "transverse"),
        ("CPL2_th", 20, "tpl1", "transverse"),
        ("CPL2_th", 20, "tpl2", "transverse"),
        ("CPL2_overlap_length", 20, "in", "transverse"),
    ],
    "inner_node_1_longitudinal_bracing": [
        ("GRD_d", 30, "diameter", None),
        ("EP_d", 144, "diameter", None),
        ("EP_pl_th", 144, "tpl1", None),
        ("Stiff_th", 30, "tpl3", None),
        ("Stiff_th", 144, "l1", None),
        ("Stiff_th", 144, "l2", None),
        ("Stiff_sel", 30, "sel", None),
        ("Stiff_sel", 144, "sel", None),
        ("CPL_d", 20, "diameter", "longitudinal"),
        ("CPL_th", 20, "tpl1", "longitudinal"),
        ("CPL_th", 20, "tpl2", "longitudinal"),
        ("CPL_overlap_length", 20, "in", "longitudinal"),
    ],
}

IDEA_TO_TEKLA_LENGTH_FACTOR = 1000.0

# The double-brace template keeps the original parameter namespaces; the
# evaluated values must also reach the corresponding Tekla components.
TEKLA_CONNECTION_PARAMETER_MAP["inner_node_2_longitudinal_bracings"] = list(
    TEKLA_CONNECTION_PARAMETER_MAP["inner_node_1_longitudinal_bracing"])
TEKLA_CONNECTION_PARAMETER_MAP["inner_node_2_longitudinal_1_transverse_bracing"] = list(
    TEKLA_CONNECTION_PARAMETER_MAP["inner_node_1_longitudinal_1_transverse_bracing"])
TEKLA_CONNECTION_PARAMETER_MAP["inner_node_2_longitudinal_1_transverse_bracing"] = [
    (f"D2_{key}" if key.startswith("CPL2_") else key, number, attribute, direction)
    for key, number, attribute, direction in TEKLA_CONNECTION_PARAMETER_MAP["inner_node_2_longitudinal_1_transverse_bracing"]
]


# ── Helpers & Fallbacks ──────────────────────────────────────────────────

def _index_to_letters(n: int) -> str:
    letters = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(0x41 + remainder) + letters
    return letters


def generate_y_axis_labels(n_trans_bays: int) -> list:
    return [_index_to_letters(i) for i in range(1, n_trans_bays + 2)]


def get_bolt_parameters(bolt_dia: float) -> dict:
    tolerance = 2 if bolt_dia <= 27 else 3
    hole_dia = bolt_dia + tolerance
    edge_distance_table = {12: 25, 16: 30, 20: 35, 24: 40, 27: 45, 30: 50, 36: 60, 40: 65}
    return {
        "tolerance": tolerance,
        "edge_distance": edge_distance_table.get(bolt_dia, 1.5 * bolt_dia),
        "hole_dia": hole_dia,
    }


def _catalog_dimension(profile_name: str, property_name: str) -> float:
    if not tekla_common:
        raise RuntimeError("Tekla catalogue is unavailable.")
    getter = getattr(tekla_common, property_name)
    value = getter(profile_name)
    if value is None or not math.isfinite(float(value)) or value <= 0:
        raise RuntimeError(f"Missing Tekla catalogue dimension {property_name} for {profile_name}.")
    return float(value)


def get_profile_width(profile_name: str) -> float:
    return _catalog_dimension(profile_name, "get_profile_width")


def get_profile_height(profile_name: str) -> float:
    return _catalog_dimension(profile_name, "get_profile_height")


def get_profile_web_thickness(profile_name: str) -> float:
    return _catalog_dimension(profile_name, "get_profile_web_thickness")


def clean_tekla(progress=None):
    if tekla_common and hasattr(tekla_common, "clean_tekla"):
        tekla_common.clean_tekla(progress=progress)


def modify_grid(model, coord_x, coord_y, coord_z, label_x, label_y, label_z):
    if tekla_common and hasattr(tekla_common, "modify_grid"):
        tekla_common.modify_grid(model, coord_x, coord_y, coord_z, label_x, label_y, label_z)


def unmapped_connection_corrections(state):
    """A solved IDEA revision must have a validated physical-model handoff."""
    missing = []
    for entry in state.loop_b_history:
        group = entry["joint_group"]
        mapped = {row[0] for row in TEKLA_CONNECTION_PARAMETER_MAP.get(group, [])}
        missing.extend({"joint_group": group, "parameter": change["key"]}
                       for change in entry.get("changes", []) if change["key"] not in mapped)
    return missing


def connection_attributes(joint_groups: dict, joint_group_ids, connection_number: int, bracing: Optional[str] = None) -> dict:
    attributes = {}
    for joint_group_id in joint_group_ids:
        values = {p["key"]: p.get("expression") for p in joint_groups.get(joint_group_id, {}).get("operation_parameters", [])}
        for key, number, attribute, bracing_direction in TEKLA_CONNECTION_PARAMETER_MAP.get(joint_group_id, []):
            if number == connection_number and bracing_direction == bracing and values.get(key) is not None:
                if attribute == "sel" and values.get(key) in [False, '"False"', '"false"', "False", "false", 0, "0"]:
                    attributes["tpl3"] = 0
                    attributes["l1"] = 0
                    attributes["l2"] = 0
                elif attribute not in ["sel"] and str(values.get(key)).strip() != "":
                    try:
                        if attributes.get(attribute) != 0:
                            attributes[attribute] = float(values[key]) * IDEA_TO_TEKLA_LENGTH_FACTOR
                    except ValueError:
                        raise ValueError(f"Invalid evaluated IDEA parameter {key} for Tekla attribute {attribute}: {values[key]!r}")
    return attributes


def joint_group_columns(joint_groups: dict) -> dict:
    return {
        joint_group_id: {member["tekla_name"] for joint in joint_group.get("joints", []) for member in joint["members"]
                         if member.get("member_type") == "column"}
        for joint_group_id, joint_group in joint_groups.items()
    }


def joint_group_beam_pairs(joint_groups: dict) -> dict:
    pairs = {}
    for joint_group_id, joint_group in joint_groups.items():
        pairs[joint_group_id] = set()
        for joint in joint_group.get("joints", []):
            longitudinal = {m["tekla_name"] for m in joint["members"] if m.get("member_type") == "beam_longitudinal"}
            transverse = {m["tekla_name"] for m in joint["members"] if m.get("member_type") == "beam_transverse"}
            pairs[joint_group_id].update((x, y) for x in longitudinal for y in transverse)
    return pairs


TEKLA_INT_ATTRIBUTES = {"nb", "nw", "assembly_type"}
TEKLA_STRING_ATTRIBUTES = {"lbd", "lwd"}


def _is_string_attribute(key):
    return key in TEKLA_STRING_ATTRIBUTES or key == "mat" or (key.startswith("mat") and key[3:].isdigit())


def _component_integer(value):
    # pythonnet can choose GetAttribute(string, ref double) for Python int.
    # Explicit Int32 is required for persisted integer attribute read-back.
    try:
        from System import Int32
        return Int32(int(value))
    except ImportError:  # Hardware-independent unit tests.
        return int(value)


def set_component_attribute(component, param_name: str, param_value):
    if param_name in TEKLA_INT_ATTRIBUTES:
        accepted = component.SetAttribute(param_name, _component_integer(round(float(param_value))))
    elif _is_string_attribute(param_name):
        accepted = component.SetAttribute(param_name, str(param_value))
    else:
        accepted = component.SetAttribute(param_name, float(param_value))
    if accepted is False:
        raise RuntimeError(f"Tekla rejected component attribute {param_name}.")


def _verify_component(model, component, attributes):
    persisted = model.SelectModelObject(component.Identifier)
    if persisted is None:
        raise RuntimeError("Inserted Tekla connection could not be read back.")
    for key, expected in (attributes or {}).items():
        default = "" if _is_string_attribute(key) else _component_integer(0) if key in TEKLA_INT_ATTRIBUTES else 0.0
        returned = persisted.GetAttribute(key, default)
        if not isinstance(returned, tuple) or len(returned) != 2 or not returned[0]:
            raise RuntimeError(f"Cannot verify persisted Tekla attribute {key}.")
        actual = returned[1]
        if _is_string_attribute(key):
            matches = str(actual).strip() == str(expected).strip()
        else:
            matches = math.isclose(float(actual), float(expected), rel_tol=1e-6, abs_tol=1e-3)
        if not matches:
            raise RuntimeError(f"Tekla attribute {key} differs from the approved value: {actual} versus {expected}.")
    return persisted


def add_base_connection(model, column, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, attribute_file: str = "baseplate_1004", attributes: Optional[dict] = None):
    try:
        from Tekla.Structures.Model import Detail
        from Tekla.Structures.Geometry3d import Vector
        from Tekla.Structures import PositionTypeEnum, AutoDirectionTypeEnum, DetailTypeEnum
        detailObj = Detail()
        detailObj.Name = detail_name
        detailObj.Number = connection_number
        if not detailObj.LoadAttributesFromFile(attribute_file):
            raise RuntimeError(f"Tekla attribute file could not be loaded: {attribute_file}")
        for param_name, param_value in (attributes or {}).items():
            set_component_attribute(detailObj, param_name, param_value)
        detailObj.PositionType = PositionTypeEnum.BOX_PLANE
        detailObj.AutoDirectionType = AutoDirectionTypeEnum.AUTODIR_DETAIL
        detailObj.UpVector = Vector(1000, 0, 0)
        detailObj.DetailType = DetailTypeEnum.END
        detailObj.ExactPositioning = False
        detailObj.Class = 5
        detailObj.Code = design_code
        detailObj.set_PrimaryObject(column)
        base_point = column.StartPoint
        base_point = base_point + Vector(0, 0, grid_specs.get('base_plate_offset_above_column_base_mm', 30))
        detailObj.SetReferencePoint(base_point)
        if not detailObj.Insert():
            raise RuntimeError(f"Tekla rejected base connection {detail_name}.")
        return _verify_component(model, detailObj, attributes)
    except Exception as exc:
        raise RuntimeError(f"Base connection {detail_name} failed: {exc}") from exc


def add_column2beam_connection(model, secondary, primary, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, classCode: int, attribute_file: str = "column_longitudinal_beam", upVector=None, attributes: Optional[dict] = None):
    try:
        from Tekla.Structures.Model import Connection
        from Tekla.Structures.Geometry3d import Vector
        from Tekla.Structures import PositionTypeEnum
        from System.Collections import ArrayList

        connectionObj = Connection()
        connectionObj.Name = detail_name
        connectionObj.Number = connection_number
        if not connectionObj.LoadAttributesFromFile(attribute_file):
            raise RuntimeError(f"Tekla attribute file could not be loaded: {attribute_file}")
        for param_name, param_value in (attributes or {}).items():
            set_component_attribute(connectionObj, param_name, param_value)
        connectionObj.Class = classCode
        connectionObj.Code = design_code
        if upVector is None:
            upVector = Vector(1000, 0, 0)
        connectionObj.UpVector = upVector
        if connection_number in [144]:
            connectionObj.PositionType = PositionTypeEnum.COLLISION_PLANE
        connectionObj.set_PrimaryObject(primary)
        secondaries = ArrayList()
        secondaries.Add(secondary)
        connectionObj.set_SecondaryObjects(secondaries)
        if not connectionObj.Insert():
            raise RuntimeError(f"Tekla rejected connection {detail_name}.")
        return _verify_component(model, connectionObj, attributes)
    except Exception as exc:
        raise RuntimeError(f"Connection {detail_name} failed: {exc}") from exc


# ── Inspection & Diagnostics ─────────────────────────────────────────────

def _stringify(value) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return str(value)
    except Exception as exc:
        return f"<unprintable: {exc}>"


def dump_attributes(obj) -> dict:
    try:
        from System.Collections import Hashtable
        net_type = obj.GetType()
        attributes = {}
        for prop in net_type.GetProperties():
            if prop.GetIndexParameters():
                continue
            try:
                value = prop.GetValue(obj, None)
            except Exception as exc:
                value = f"<error reading property: {exc}>"
            attributes[prop.Name] = _stringify(value)

        uda = Hashtable()
        if obj.GetAllUserProperties(uda):
            for key in sorted([str(x) for x in uda.Keys]):
                attributes[key] = uda[key]

        return {"type_name": net_type.FullName, "attributes": attributes}
    except Exception as exc:
        return {"error": str(exc)}


def print_attributes(obj) -> None:
    result = dump_attributes(obj)
    print(f"=== {result.get('type_name', 'Object')} ===")
    for name, val in sorted(result.get("attributes", {}).items()):
        print(f"{name}: {val}")


def pick_and_print_attributes() -> None:
    try:
        from Tekla.Structures.Model.UI import ModelObjectSelector
        print("Select an object in Tekla to retrieve its attributes ...")
        selector = ModelObjectSelector()
        selected = selector.GetSelectedObjects()
        while selected.MoveNext():
            picked = selected.Current
            if picked is not None:
                print_attributes(picked)
    except Exception as exc:
        print(f"pick_and_print_attributes failed: {exc}")


# ── Core Modeler Engine ──────────────────────────────────────────────────

def send_to_tekla(data: dict) -> dict:
    """
    Direct Tekla Structures Modeler Engine.
    Inserts all physical frame members and detailed connections into the open model.
    """
    members = data.get("members", [])
    grid = data.get("grid", {})
    joint_groups = data.get("joint_groups", {})
    plate_policy = data.get("plate_material_policy")
    if plate_policy:
        from agentic.connection_materials import reference_plate_material_policy
        if plate_policy != reference_plate_material_policy():
            raise RuntimeError("Standard Tekla material files changed after package validation.")
    def material_attributes(number):
        return dict((plate_policy or {}).get("component_attributes", {}).get(str(number), {}))
    def progress(message):
        print(f"Tekla: {message}", flush=True)
        callback = data.get("progress_callback")
        if callback:
            callback(message)

    if not tekla_common:
        raise RuntimeError("Tekla .NET environment not available on this host: Tekla.Structures.dll not found.")

    expected_path = data.get("expected_tekla_model_path")
    if expected_path:
        from pathlib import Path
        actual_path = Path(tekla_common._get_connected_model().GetInfo().ModelPath).resolve()
        if actual_path != Path(expected_path).resolve():
            raise RuntimeError(f"Tekla active model changed during the run: {actual_path}; expected {expected_path}.")
    progress("Preparing the target model and reference attributes")
    clean_tekla(progress=progress)
    columns_by_joint_group = joint_group_columns(joint_groups)
    beam_pairs_by_joint_group = joint_group_beam_pairs(joint_groups)
    model = tekla_common._get_connected_model()
    tekla_common._copy_missing_connection_attributes(model)
    tekla_common._copy_missing_tekla_package_dbs(model)

    origin = grid.get("starting_point", [0.0, 0.0, 0.0])
    coord_x = f'{origin[0]:g} {grid.get("n_long_bays", 1)}*{grid.get("bay_long_mm", 2750)}'
    coord_y = f'{origin[1]:g} {grid.get("n_trans_bays", 1)}*{grid.get("bay_trans_mm", 1620)}'
    levels = sorted({point[2] for member in members for point in (member["p1"], member["p2"])})
    coord_z = ' '.join(f'{level:g}' for level in levels)
    label_x = ' '.join(str(i) for i in range(1, grid.get("n_long_bays", 1) + 2))
    label_y = ' '.join(generate_y_axis_labels(grid.get("n_trans_bays", 1)))
    label_z = coord_z
    modify_grid(model, coord_x, coord_y, coord_z, label_x, label_y, label_z)

    created = 0
    errors = []
    inserted_members = []
    connection_ids = []

    def connect(factory, *args, **kwargs):
        connection = factory(*args, **kwargs)
        connection_ids.append(int(connection.Identifier.ID))
        if len(connection_ids) % 20 == 0:
            progress(f"Inserted and verified {len(connection_ids)} connections")
        return connection
    columns = []
    longitudinal_beams = {}
    transverse_beams = {}
    braces = {}

    for member in members:
        try:
            beam = tekla_common._create_beam(member)
            created += 1
            if created % 25 == 0:
                progress(f"Inserted {created}/{len(members)} frame members")
            inserted_members.append((member, beam.Identifier))
            m_type = member.get("type", "").lower()
            m_name = member.get("name", "")
            if m_type == "column":
                columns.append((member, beam))
            elif m_type == "beam" and m_name.startswith("beam-x-"):
                longitudinal_beams[m_name] = (member, beam)
            elif m_type == "beam" and m_name.startswith("beam-y-"):
                transverse_beams[m_name] = (member, beam)
            elif m_type in ("longitudinal_brace", "transverse_brace"):
                braces[m_name] = (member, beam)
        except Exception as exc:
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

    if model.CommitChanges() is False:
        raise RuntimeError("Tekla rejected the member commit.")
    progress(f"Committed {created} frame members; inserting connections")

    # 1. Base connections
    for member, col in columns:
        try:
            connect(add_base_connection, model, col, 1004, f"{member.get('name')} base plate", "1004", grid,
                    attributes=material_attributes(1004))
        except Exception as exc:
            errors.append({"name": member.get("name"), "error": str(exc)})

        # 2. Column to longitudinal beam connections (144)
        member_name = member.get("name", "")
        column_joint_groups = [jg for jg, col_names in columns_by_joint_group.items() if member_name in col_names]
        name_parts = member_name.split("-")
        if len(name_parts) >= 2:
            longitudinal_beam = longitudinal_beams.get(f"beam-x-{name_parts[1]}")
            if longitudinal_beam is not None:
                longitudinal_beam_member, longitudinal_beam_obj = longitudinal_beam
                try:
                    conn_name = f"{member_name} beam-x-{name_parts[1]} longitudinal beam connection"
                    conn_num = 144
                    idea_attrs = {**material_attributes(conn_num),
                                  **connection_attributes(joint_groups, column_joint_groups, conn_num)}

                    col_prof = member.get('profile')
                    beam_prof = longitudinal_beam_member.get('profile')
                    col_w = get_profile_width(col_prof)
                    col_h = get_profile_height(col_prof)
                    beam_w = get_profile_width(beam_prof)

                    l1 = idea_attrs.get("l1", 10)
                    bpl6 = -0.5 * (col_w - l1)
                    bolt_dia = idea_attrs.get("diameter", 16)
                    bolt_params = get_bolt_parameters(bolt_dia)
                    rb1 = bolt_params.get("edge_distance", 30)
                    lbd = math.floor((col_h + 3.0 * bolt_dia) / 5) * 5
                    nb, nw = 2, 2
                    lwd = math.floor((beam_w - 2 * rb1) / (nw - 1) / 5) * 5
                    web_t = get_profile_web_thickness(beam_prof)
                    w1 = 0.5 * (beam_w - web_t) - 10

                    computed_attrs = {
                        "assembly_type": 1, "bpl6": bpl6, "bpl7": bpl6,
                        "lbd": lbd, "lwd": lwd, "nb": nb, "nw": nw,
                        "rb1": rb1, "rb2": rb1, "rw1": rb1, "rw2": rb1,
                        "tolerance": bolt_params.get("tolerance", 2),
                        "w1": w1, "w2": w1,
                    }
                    idea_attrs.update(computed_attrs)
                    connect(add_column2beam_connection,
                        model, col, longitudinal_beam_obj, conn_num, conn_name,
                        str(conn_num), grid, 6, attributes=idea_attrs
                    )
                except Exception as exc:
                    errors.append({"connection": f"144 {member_name}", "error": str(exc)})

        # 3. Column to adjacent brace connections (20)
        if len(name_parts) >= 3:
            try:
                col_j, col_i = map(int, name_parts[1:3])
                adjacent_brace_names = (
                    f"bracing-long-{col_j}-{col_i}",
                    f"bracing-long-{col_j}-{col_i + 1}",
                    f"bracing-trans-{col_i}-{col_j}",
                    f"bracing-trans-{col_i}-{col_j + 1}",
                )
                for brace_name in adjacent_brace_names:
                    brace_entry = braces.get(brace_name)
                    if brace_entry:
                        _, brace_obj = brace_entry
                        dir_str = "longitudinal" if brace_name.startswith("bracing-long") else "transverse"
                        brace_attrs = {**material_attributes(20),
                                       **connection_attributes(joint_groups, column_joint_groups, 20, dir_str)}
                        connect(add_column2beam_connection,
                            model, brace_obj, col, 20, f"{member_name} {brace_name} connection",
                            "20", grid, 1, "bracing_column", attributes=brace_attrs
                        )
            except Exception as exc:
                errors.append({"connection": f"20 {member_name}", "error": str(exc)})

    # 4. Longitudinal to transverse beam connections (30)
    for l_idx in range(grid.get("n_long_bays", 1) + 1):
        t_beam = transverse_beams.get(f"beam-y-{l_idx}")
        if not t_beam:
            continue
        _, t_beam_obj = t_beam
        for t_idx in range(grid.get("n_trans_bays", 1) + 1):
            l_beam = longitudinal_beams.get(f"beam-x-{t_idx}")
            if not l_beam:
                continue
            _, l_beam_obj = l_beam
            beam_pair = (f"beam-x-{t_idx}", f"beam-y-{l_idx}")
            pair_joint_groups = [jg for jg, pairs in beam_pairs_by_joint_group.items() if beam_pair in pairs]
            try:
                beam_attrs = {**material_attributes(30),
                              **connection_attributes(joint_groups, pair_joint_groups, 30)}
                connect(add_column2beam_connection,
                    model, t_beam_obj, l_beam_obj, 30,
                    f"beam-x-{t_idx} beam-y-{l_idx} connection",
                    "30", grid, 1, "longitudinal_beam_transverse_beam", attributes=beam_attrs
                )
            except Exception as exc:
                errors.append({"connection": f"30 {beam_pair}", "error": str(exc)})

    if model.CommitChanges() is False:
        raise RuntimeError("Tekla rejected the connection commit.")

    verified_members = 0
    progress("Checking final member geometry, profiles and materials")
    for expected, identifier in inserted_members:
        actual = model.SelectModelObject(identifier)
        try:
            if actual is None:
                raise RuntimeError("Member is absent from the persisted model.")
            for point, value in ((actual.StartPoint, expected["p1"]), (actual.EndPoint, expected["p2"])):
                if any(abs(a - b) > 0.01 for a, b in zip((point.X, point.Y, point.Z), value)):
                    raise RuntimeError("Persisted endpoints differ from the approved geometry.")
            normalize = lambda text: str(text).replace(" ", "").upper()
            if normalize(actual.Profile.ProfileString) != normalize(tekla_common.to_tekla_profile(expected["profile"])):
                raise RuntimeError("Persisted profile differs from the approved section.")
            if normalize(actual.Material.MaterialString) != normalize(expected["material"]):
                raise RuntimeError("Persisted material differs from the approved material.")
            verified_members += 1
        except Exception as exc:
            errors.append({"name": expected["name"], "error": str(exc)})

    if not errors:
        progress("Saving the verified Tekla model")
        from Tekla.Structures.Model import ModelHandler
        import getpass
        if not ModelHandler().Save("Verified automated radiator support publication", getpass.getuser()):
            raise RuntimeError("Tekla model was committed but could not be saved.")

    return {
        "created": created,
        "verified_members": verified_members,
        "verified_connections": len(connection_ids),
        "plate_materials_verified": bool(plate_policy) and bool(connection_ids) and not errors,
        "connection_ids": connection_ids,
        "failed": len(errors),
        "errors": errors
    }


def run_a7_tekla_modeler(state: WorkflowState) -> WorkflowState:
    """
    Executes Agent A7: Connects to Tekla Structures via .NET and inserts physical 3D elements and details.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    state.current_agent = "A7"
    from agentic.state import fail_workflow
    if not state.validation_report.get("passed"):
        return fail_workflow(state, "A7", "A passing package validation is required before Tekla publication.")

    members = state.frame_candidate.get("members", [])
    grid = state.grid_config
    joint_groups = state.validation_report.get("validated_joint_groups_payload", {})

    payload = {
        "members": members,
        "grid": grid,
        "joint_groups": joint_groups,
        "expected_tekla_model_path": state.readiness.get("tekla_model_path"),
        "plate_material_policy": state.validation_report.get("plate_material_policy"),
    }

    tekla_res = None
    try:
        def update_progress(message):
            from agentic.store import WORKFLOW_STORE
            state.agent_statuses["A7"] = {"status": "RUNNING", "message": message}
            WORKFLOW_STORE.save_state(state)
        tekla_res = send_to_tekla({**payload, "progress_callback": update_progress})
    except Exception as exc:
        return fail_workflow(state, "A7", f"Tekla publication failed: {exc}")

    state.tekla_results = tekla_res
    if tekla_res.get("simulation") or tekla_res.get("failed") or tekla_res.get("errors") or tekla_res.get("created") != len(members) or tekla_res.get("verified_members") != len(members) or (payload["plate_material_policy"] and not tekla_res.get("plate_materials_verified")):
        examples = "; ".join(str(error.get("error", error)) if isinstance(error, dict) else str(error)
                             for error in tekla_res.get("errors", [])[:3])
        return fail_workflow(state, "A7", f"Tekla publication incomplete: {tekla_res.get('verified_members', 0)}/{len(members)} members verified, "
                             f"{tekla_res.get('verified_connections', 0)} connections verified, {tekla_res.get('failed', 0)} failures. {examples}")
    created_count = tekla_res.get("created", len(members))

    msg = f"Published {created_count} members to Tekla Structures with verified connection attributes (0 failed)."
    state.agent_statuses["A7"] = {"status": "PASSED", "message": msg, "created": created_count}
    state.logs.append(AgentLogEntry(
        agent_id="A7",
        agent_name="Tekla 3D Modeler",
        status="PASSED",
        message=msg,
        timestamp=ts
    ))

    return state
