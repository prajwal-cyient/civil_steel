"""
tekla_integration.py

Sends generated structural members (columns, beams, and bracing) into a
running Tekla Structures model via the Tekla Open API, bridged through
pythonnet (clr).

This module only works on a Windows machine that has:
  - Tekla Structures installed, with a model open in the running instance
  - the "pythonnet" package installed (`pip install pythonnet`)

It is imported lazily by the API server (see generate_grid.py) only when
the POST /api/v1/to_tekla endpoint is actually called, so the rest of the
grid-generation tool works fine on machines without Tekla or pythonnet
installed.
"""

import argparse
import json
import math
from typing import Optional
import sys
import os

srcDir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if srcDir not in sys.path:
    sys.path.insert(0, srcDir)


from common.tekla_common import *
import common.tekla_common as tekla_common

# Maps IDEA StatiCa operation parameters of a joint group's template to Tekla connection attributes:
# joint group -> [(operation parameter key, Tekla connection number, Tekla attribute, bracing)], where
# bracing is "longitudinal"/"transverse" for connection 20 (column to bracing) and None otherwise.
# Joint groups not listed here keep the attribute-file values.
TEKLA_CONNECTION_PARAMETER_MAP = {
    "inner_node_1_longitudinal_1_transverse_bracing": [
        ("GRD_d", 30, "diameter", None),
        ("EP_d", 144, "diameter", None),
        ("EP_pl_th", 144, "tpl1", None),
        ("Stiff_th", 30, "tpl3", None),
        ("Stiff_th", 144, "l1", None),
        ("Stiff_th", 144, "l2", None),
        ("Stiff_sel", 30, "sel", None), # Stiffener selection for connection 30. If False, the stiffener is not used. Set tpl3 to 0.
        ("Stiff_sel", 144, "sel", None), # Stiffener selection for connection 144. If False, the stiffener is not used. Set l1 and l2 to 0.
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
        ("Stiff_sel", 30, "sel", None), # Stiffener selection for connection 30. If False, the stiffener is not used. Set tpl3 to 0.
        ("Stiff_sel", 144, "sel", None), # Stiffener selection for connection 144. If False, the stiffener is not used. Set l1 and l2 to 0.
        ("CPL_d", 20, "diameter", "longitudinal"),
        ("CPL_th", 20, "tpl1", "longitudinal"),
        ("CPL_th", 20, "tpl2", "longitudinal"),
        ("CPL_overlap_length", 20, "in", "longitudinal"),
    ],

}
# IDEA StatiCa parameter values are in m; Tekla connection attributes are in mm.
IDEA_TO_TEKLA_LENGTH_FACTOR = 1000.0

def connection_attributes(joint_groups: dict, joint_group_ids, connection_number: int, bracing: Optional[str] = None) -> dict:
    """
    Returns {Tekla attribute: value in mm} for one Tekla connection, from the IDEA StatiCa
    operation parameters of the given joint groups (see TEKLA_CONNECTION_PARAMETER_MAP).
    """
    attributes = {}
    for joint_group_id in joint_group_ids:
        # TODO: Should we consider value or expression for the operation parameters?
        values = {p["key"]: p.get("expression") for p in joint_groups[joint_group_id].get("operation_parameters", [])}
        for key, number, attribute, bracing_direction in TEKLA_CONNECTION_PARAMETER_MAP.get(joint_group_id, []):
            if number == connection_number and bracing_direction == bracing and values.get(key) is not None:
                if attribute == "sel" and values.get(key) in [False, '"False"', '"false"', "False", "false", 0, "0"]:
                    attributes["tpl3"] = 0
                    attributes["l1"] = 0
                    attributes["l2"] = 0
                elif attribute not in ["sel"] and str(values.get(key)).strip() != "":
                    try:
                        if attributes.get(attribute) != 0: # To take care of attributes that have been explicitly set to 0
                            attributes[attribute] = float(values[key]) * IDEA_TO_TEKLA_LENGTH_FACTOR
                    except ValueError:
                        raise ValueError(f"Invalid value for attribute {attribute} {key}: {values[key]}")

    return attributes

def joint_group_columns(joint_groups: dict) -> dict:
    """Returns {joint group: set of Tekla column names} for the columns at the group's joints."""
    return {
        joint_group_id: {member["tekla_name"] for joint in joint_group.get("joints", []) for member in joint["members"]
                         if member.get("member_type") == "column"}
        for joint_group_id, joint_group in joint_groups.items()
    }

def joint_group_beam_pairs(joint_groups: dict) -> dict:
    """Returns {joint group: set of (Tekla longitudinal beam name, Tekla transverse beam name)} meeting at the group's joints."""
    pairs = {}
    for joint_group_id, joint_group in joint_groups.items():
        pairs[joint_group_id] = set()
        for joint in joint_group.get("joints", []):
            longitudinal = {m["tekla_name"] for m in joint["members"] if m.get("member_type") == "beam_longitudinal"}
            transverse = {m["tekla_name"] for m in joint["members"] if m.get("member_type") == "beam_transverse"}
            pairs[joint_group_id].update((x, y) for x in longitudinal for y in transverse)
    return pairs

# Component attributes are typed in Tekla, and pythonnet picks the
# SetAttribute overload (string/int/double) from the Python type. Setting a
# string or int attribute through the double overload is silently ignored
# and the field shows blank in the dialog. Bolt counts are int; bolt
# spacings are strings because they may hold lists, e.g. "80 80" or "2*80".
TEKLA_INT_ATTRIBUTES = {"nb", "nw"}
TEKLA_STRING_ATTRIBUTES = {"lbd", "lwd"}

def set_component_attribute(component, param_name: str, param_value):
    if param_name in TEKLA_INT_ATTRIBUTES:
        component.SetAttribute(param_name, int(round(float(param_value))))
    elif param_name in TEKLA_STRING_ATTRIBUTES:
        if isinstance(param_value, (int, float)):
            param_value = f"{float(param_value):g}"
        component.SetAttribute(param_name, str(param_value))
    else:
        component.SetAttribute(param_name, float(param_value))

def add_base_connection(model: Model, column: Beam, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, attribute_file: str = "baseplate_1004", attributes: Optional[dict] = None):
    """
    Adds a base connection (base plate) to the given column in the currently running Tekla Structures model.

    Parameters
    ----------
    column : Beam
        The column to which the base connection will be added.
    connection_number : int
        The number to assign to the base connection detail.
    detail_name : str
        The name to assign to the base connection detail.
    design_code : str
        The design code to assign to the base connection detail.
    grid_specs : dict
        The grid specifications, including base plate offset above column base.
    attribute_file : str, optional
        The attribute file to load for the base connection detail, by default "baseplate_1004"
    attributes : dict, optional
        {attribute name: value} set on the detail after loading the attribute file.
    """

    detailObj = Detail()
    detailObj.Name = detail_name
    detailObj.Number = connection_number
    detailObj.LoadAttributesFromFile(attribute_file)
    for param_name, param_value in (attributes or {}).items():
        set_component_attribute(detailObj, param_name, param_value)
    detailObj.PositionType = PositionTypeEnum.BOX_PLANE
    detailObj.AutoDirectionType = AutoDirectionTypeEnum.AUTODIR_DETAIL
    detailObj.UpVector = Vector(1000, 0, 0) # OR 1000, this does not seem to matter for base plate
    detailObj.DetailType = DetailTypeEnum.END
    detailObj.ExactPositioning = False
    detailObj.Class = 5
    detailObj.Code = design_code
    detailObj.set_PrimaryObject(column)
    base_point = column.StartPoint
    base_point = base_point + Vector(0, 0, grid_specs.get('base_plate_offset_above_column_base_mm', 30))  # offset the base point slightly above the column base
    detailObj.SetReferencePoint(base_point)

    inserted = detailObj.Insert()
    if attributes:
        print(f"[debug] detail {detailObj.Number} id={detailObj.Identifier.ID} '{detail_name}': "
              f"column={column.Name} (id={column.Identifier.ID}), attributes={attributes}, inserted={inserted}")
    if inserted:
        #print("✅ Base plate created")
        pass
    else:
        print("❌ Failed to create base plate")
    #model.CommitChanges()

def add_column2beam_connection(model: Model, secondary: Beam, primary: Beam, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, classCode: int, attribute_file: str = "column_longitudinal_beam", upVector: Vector = Vector(1000, 0, 0), attributes: Optional[dict] = None):
    """
    Adds a column-to-beam connection to the currently running Tekla Structures model.

    Parameters
    ----------
    model : Model
        The currently running Tekla Structures model.
    secondary : Beam
        The secondary beam involved in the connection.
    primary : Beam
        The primary beam involved in the connection.
    connection_number : int
        The number to assign to the connection detail.
    detail_name : str
        The name to assign to the connection detail.
    design_code : str
        The design code to assign to the connection detail.
    grid_specs : dict
        The grid specifications, including any necessary offsets.
    attribute_file : str, optional
        The attribute file to load for the connection detail, by default "column2beam_1004"
    upVector : Vector, optional
        The connection up vector, defaulting to the standard X direction.
    attributes : dict, optional
        {attribute name: value} set on the connection after loading the attribute file.
    """

    # ✅ Create Connection
    connectionObj = Connection()
    connectionObj.Name = detail_name
    connectionObj.Number = connection_number
    # ✅ Load attributes
    connectionObj.LoadAttributesFromFile(attribute_file)
    for param_name, param_value in (attributes or {}).items():
        set_component_attribute(connectionObj, param_name, param_value)
    #connectionObj.SetAttribute("cut_beam", 0)
    #connectionObj.SetAttribute("beam_cont", 1)
    connectionObj.Class = classCode
    connectionObj.Code = design_code

    # ✅ Direction
    connectionObj.UpVector = upVector

    # ✅ Positioning
    if connection_number in [144]:
        connectionObj.PositionType = PositionTypeEnum.COLLISION_PLANE

    # ✅ Auto direction
    #connectionObj.AutoDirectionType = AutoDirectionTypeEnum.AUTODIR_FROM_ATTRIBUTE_FILE
    #connectionObj.AutoDirectionType = AutoDirectionTypeEnum.AUTODIR_BASIC

    # ✅ Primary
    connectionObj.set_PrimaryObject(primary)

    #connectionObj.SetAttribute("stiffener_type", 1)
    #connectionObj.SetAttribute("t_stiffener", 10.0)
    #connectionObj.SetAttribute("plane_rotation", 1)
    # ✅ ✅ Secondary (ALWAYS ArrayList)
    secondaries = ArrayList()
    secondaries.Add(secondary)
    connectionObj.set_SecondaryObjects(secondaries)

    inserted = connectionObj.Insert()
    if attributes:
        print(f"[debug] connection {connectionObj.Number} id={connectionObj.Identifier.ID} '{detail_name}': "
              #f"primary={primary.Name} (id={primary.Identifier.ID}), secondary={secondary.Name} (id={secondary.Identifier.ID}), "
              f"attributes={attributes}, inserted={inserted}")
    if inserted:
        #print("✅ Column-to-beam connection created")
        pass
    else:
        print("❌ Failed to create column-to-beam connection")
    #model.CommitChanges()

def send_to_tekla(data: dict) -> dict:
    """
    Send every member in `data["members"]` to the currently running Tekla
    Structures model.

    Parameters
    ----------
    data : dict
        The same JSON structure produced by generate_members(): must
        contain a "members" list, where each entry has p1, p2, profile,
        material, class, type, and name.

    Returns
    -------
    dict
        {
          "created": <int>,           # members successfully inserted
          "failed": <int>,            # members that raised an error
          "errors": [                 # one entry per failed member
            {"name": <str>, "error": <str>},
            ...
          ]
        }

    Raises
    ------
    TeklaConnectionError
        If Tekla Structures isn't running / reachable. This aborts before
        any members are created, since nothing could be inserted anyway.
    """
    # Clean the Tekla model before adding new members
    clean_tekla()
    members = data.get("members", [])
    grid = data.get('grid', {})
    # Joint groups designed in IDEA StatiCa: {<joint group>: {"joints": [...], "operation_parameters": [...]}}
    # Their operation parameters set the attributes of the connections at their joints.
    joint_groups = data.get("joint_groups", {})
    print(f"joint_groups: {json.dumps(joint_groups, indent=2)}")
    columns_by_joint_group = joint_group_columns(joint_groups)
    beam_pairs_by_joint_group = joint_group_beam_pairs(joint_groups)
    model = tekla_common._get_connected_model()
    tekla_common._copy_missing_connection_attributes(model)
    tekla_common._copy_missing_tekla_package_dbs(model)

    coordinateX = f'0.00 {grid["n_long_bays"]}*{grid["bay_long_mm"]}'
    coordinateY = f'0.00 {grid["n_trans_bays"]}*{grid["bay_trans_mm"]}'
    coordinateZ = f'0.00 3600.00 7200.00'
    labelX = ' '.join(map(lambda a: str(a), list(range(1, grid["n_long_bays"]+2))))
    labelY = ' '.join(generate_y_axis_labels(grid["n_trans_bays"]+2))
    #TODO: How to compute labelZ
    labelZ = '0.00 3600.00 7200.00'
    modify_grid(model, coordinateX, coordinateY, coordinateZ, labelX, labelY, labelZ)

    created = 0
    errors = []
    columns = []
    longitudinal_beams = {}
    transverse_beams = {}
    braces = {}

    # Add members to the Tekla model
    for member in members:
        try:
            beam = tekla_common._create_beam(member)
            created += 1
            member_type = member.get("type", "").lower()
            member_name = member.get("name", "")
            if member_type == "column":
                columns.append((member, beam))
            elif member_type == "beam" and member_name.startswith("beam-x-"):
                longitudinal_beams[member_name] = (member, beam)
            elif member_type == "beam" and member_name.startswith("beam-y-"):
                transverse_beams[member_name] = (member, beam)
            elif member_type in ("longitudinal_brace", "transverse_brace"):
                braces[member_name] = (member, beam)
        except Exception as exc:  # keep going; report this member and move on
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

    model.CommitChanges()
    # Add base connections and column-to-beam connections for the columns
    for member, column in columns:
        try:
            # Base connection for the column (BN)
            connection_name = f"{member.get('name', 'Column')} base plate"
            connection_number = 1004
            #TODO: Compute connection attributes for the base connection
            # TODO: Obtain idea statica connection attributes for the base connection
            connection_attributes_from_idea_statica = {}
            add_base_connection(
                model,
                column,
                connection_number,
                connection_name,
                str(connection_number),
                grid,
                attributes=connection_attributes_from_idea_statica,
            )
        except Exception as exc:
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

        member_name = member.get("name", "")
        column_joint_groups = [joint_group_id for joint_group_id, column_names in columns_by_joint_group.items() if member_name in column_names]
        name_parts = member_name.split("-")
        if len(name_parts) >= 2:
            longitudinal_beam = longitudinal_beams.get(f"beam-x-{name_parts[1]}")
            if longitudinal_beam is not None:
                longitudinal_beam_member, longitudinal_beam = longitudinal_beam
                try:
                    # Add connection between column and longitudinal beam
                    connection_name = f"{member_name} beam-x-{name_parts[1]} longitudinal beam connection"
                    connection_number = 144
                    connection_attributes_from_idea_statica = connection_attributes(joint_groups, column_joint_groups, connection_number)
                    # set computed connection parameters
                    column_profile = member.get('profile')
                    longitudinal_beam_profile = longitudinal_beam_member.get('profile')
                    column_profile_width = get_profile_width(column_profile)
                    column_profile_height = get_profile_height(column_profile)
                    longitudinal_beam_profile_width = get_profile_width(longitudinal_beam_profile)
                    longitudinal_beam_profile_height = get_profile_height(longitudinal_beam_profile)
                    # bpl6 is negative half of the difference between the column profile width and the stiffner thickness (l1 or l2 of connection 144)
                    assembly_type = 1
                    l1 = connection_attributes_from_idea_statica.get("l1", 10)
                    bpl6 = -0.5*(column_profile_width - l1)
                    bpl7 = bpl6
                    # lbd is floor of (column profile height + 2*1.5*bolt_dia, 5)
                    # bolt_dia is EP_d ==> diameter in connection 144
                    bolt_dia = connection_attributes_from_idea_statica.get("diameter", 16)
                    bolt_parameters = get_bolt_parameters(bolt_dia)
                    rb1 = bolt_parameters.get("edge_distance")
                    rb2 = bolt_parameters.get("edge_distance")
                    hole_dia = bolt_parameters.get("hole_dia")
                    lbd = math.floor((column_profile_height + 2*1.5*bolt_dia)/5)*5
                    # lwd is floor longitudinal beam profile width- rb1 - rb2/(nw-1), 5)
                    # rb1, rb2 are edge distances for the bolts along longitudinal beam
                    # nb is the number of bolts along the longitudinal direction
                    # nw is the number of bolts along transverse direction
                    # TODO: nb, nw are supposed to come from Idea Statica connection attributes
                    # For now, we will assume default values for nb and nw to be 2
                    nb = 2
                    nw = 2
                    lwd = math.floor((longitudinal_beam_profile_width - rb1 - rb2)/(nw-1)/5)*5
                    tolerance = bolt_parameters.get("tolerance")
                    # w1, w2: 0.5*(profile width - profile web thickness) - 10
                    profile_web_thickness = get_profile_web_thickness(longitudinal_beam_profile)
                    w1 = 0.5 * (longitudinal_beam_profile_width - profile_web_thickness) - 10
                    w2 = w1
                    computed_attributes = {"assembly_type": assembly_type, "bpl6": bpl6, "bpl7": bpl7, 
                                           "lbd": lbd, "lwd": lwd, "nb": nb, "nw": nw,
                                           "rb1": rb1, "rb2": rb2,
                                           "rw1": rb1, "rw2": rb2, "tolerance": tolerance, "w1": w1, "w2": w2, }
                    # merge computed_attributes into connection_attributes_from_idea_statica
                    connection_attributes_from_idea_statica.update(computed_attributes)
                    add_column2beam_connection(
                        model,
                        column,
                        longitudinal_beam,
                        connection_number,
                        connection_name,
                        str(connection_number),
                        grid,
                        6,
                        attributes=connection_attributes_from_idea_statica,
                    )
                except Exception as exc:
                    errors.append({"connection": f"{connection_number} {connection_name}", "name": member_name, "error": str(exc)})
                    import traceback
                    traceback.print_exc()
                    print(f"Connection attribues: {connection_attributes_from_idea_statica}")

        if len(name_parts) >= 3:
            column_j, column_i = map(int, name_parts[1:3])
            adjacent_brace_names = (
                f"bracing-long-{column_j}-{column_i}",
                f"bracing-long-{column_j}-{column_i + 1}",
                f"bracing-trans-{column_i}-{column_j}",
                f"bracing-trans-{column_i}-{column_j + 1}",
            )
            for brace_name in adjacent_brace_names:
                brace = braces.get(brace_name)
                if brace is None:
                    continue
                brace_member, brace = brace
                try:
                    # Add connection between column and adjacent brace
                    connection_name = f"{member_name} {brace_name} connection"
                    connection_number = 20
                    connection_attributes_from_idea_statica = connection_attributes(joint_groups, column_joint_groups, connection_number,
                                                         "longitudinal" if brace_name.startswith("bracing-long") else "transverse")
                    # TODO: Computed attribues
                    add_column2beam_connection(
                        model,
                        brace,
                        column,
                        connection_number,
                        connection_name,
                        str(connection_number),
                        grid,
                        1,
                        "bracing_column",
                        attributes=connection_attributes_from_idea_statica,
                    )
                except Exception as exc:
                    errors.append({"connection": f"{connection_number} {connection_name}", "name": member_name, "error": str(exc)})
    model.CommitChanges()
    # Add connections between longitudinal and transverse beams
    for longitudinal_index in range(grid["n_long_bays"] + 1):
        transverse_beam = transverse_beams.get(f"beam-y-{longitudinal_index}")
        if transverse_beam is None:
            continue
        transverse_beam_member, transverse_beam = transverse_beam
        for transverse_index in range(grid["n_trans_bays"] + 1):
            longitudinal_beam = longitudinal_beams.get(f"beam-x-{transverse_index}")
            if longitudinal_beam is None:
                continue
            longitudinal_beam_member, longitudinal_beam = longitudinal_beam
            beam_pair = (f"beam-x-{transverse_index}", f"beam-y-{longitudinal_index}")
            beam_pair_joint_groups = [joint_group_id for joint_group_id, pairs in beam_pairs_by_joint_group.items() if beam_pair in pairs]
            try:
                connection_name = f"beam-x-{transverse_index} beam-y-{longitudinal_index} connection"
                connection_number = 30
                connection_attributes_from_idea_statica = connection_attributes(joint_groups, beam_pair_joint_groups, connection_number)
                # TODO: Computed attribues
                add_column2beam_connection(
                    model,
                    transverse_beam,
                    longitudinal_beam,
                    connection_number,
                    connection_name,
                    str(connection_number),
                    grid,
                    1,
                    "longitudinal_beam_transverse_beam",
                    attributes=connection_attributes_from_idea_statica,
                )
            except Exception as exc:
                errors.append({
                    "connection": f"{connection_number} {connection_name}",
                    "name": f"beam-x-{transverse_index} beam-y-{longitudinal_index}",
                    "error": str(exc),
                })

    model.CommitChanges()

    return {
        "created": created,
        "failed": len(errors),
        "errors": errors,
    }


# ── Diagnostics: dump all attributes of any picked object ────────────────
#
# Works for any Tekla model object -- Beam, a vertical Beam acting as a
# column, Connection/BaseComponent, Plate, BoltArray, Weld, etc. -- since
# it uses .NET reflection on the object's actual runtime type rather than
# a type-specific property list. Useful for exploring what a given
# component actually exposes when you don't already know its exact class.
#
# NOTE ON USER-DEFINED ATTRIBUTES (UDAs): Tekla's classic Open API has no
# generic "get all UDAs" call -- user-defined/report properties are only
# readable by name via GetUserProperty()/GetReportProperty(), so they are
# NOT included here unless you already know their names ahead of time.
# This dumps the object's regular .NET properties only.

def _stringify(value) -> object:
    """Best-effort conversion of a .NET property value into something printable/JSON-safe."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return str(value)
    except Exception as exc:
        return f"<unprintable: {exc}>"


def dump_attributes(obj) -> dict:
    """
    Generic .NET reflection dump of every readable, non-indexer property
    on `obj`'s actual runtime type.

    Returns
    -------
    dict
        {"type_name": <full .NET type name>, "attributes": {name: value, ...}}
    """
    net_type = obj.GetType()
    attributes = {}
    for prop in net_type.GetProperties():
        if prop.GetIndexParameters():
            continue  # skip indexer properties (e.g. "Item[key]"); GetValue(obj) with no args would raise
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


def print_attributes(obj) -> None:
    """Pretty-prints every attribute from dump_attributes(obj), sorted by name."""
    result = dump_attributes(obj)
    print(f"=== {result['type_name']} ===")
    for name in sorted(result["attributes"]):
        print(f"{name}: {result['attributes'][name]}")


def pick_and_print_attributes() -> None:
    """
    Prompts the user to click one object in the Tekla Structures UI
    (beam, column, connection, plate, bolt, weld, ...) and prints every
    readable attribute on it. Returns the same dict print_attributes()
    prints, in case you want it programmatically too.

    Must be run with the Tekla Structures desktop UI visible and focused
    -- this blocks waiting for a click in the 3D view, the same as
    clicking "Pick" in any Tekla dialog.
    """
    print("Select an object in Tekla to retrieve it's attribues ...")
    # ✅ Get selection
    selector = ModelObjectSelector()
    selected = selector.GetSelectedObjects()

    while selected.MoveNext():
        picked = selected.Current
        if picked is not None:
            print_attributes(picked)
            dump_attributes(picked)


# ── Standalone self-test ─────────────────────────────────────────────────
#
# Run this file directly (`python tekla_integration.py`) on the machine
# that has Tekla Structures + pythonnet installed, with a model open, to
# check that the whole chain — pythonnet import, DLL loading, live model
# connection, catalog access, and member insertion — actually works,
# independently of generate_grid.py or the web UI.
#
# This creates one real, visible dummy column and prints a pass/fail
# summary for each step. It does NOT undo the insertion afterward (Tekla
# Open API has no simple "delete a specific part" call from here), so
# expect one extra "SELF_TEST_DUMMY_COLUMN" member left in your model —
# delete it manually once you've confirmed the test passed.

def _self_test() -> None:
    print("=== tekla_integration.py self-test ===\n")

    parser = argparse.ArgumentParser(
        description="Generate a beam/column/bracing grid from a JSON spec."
    )
    parser.add_argument("--input", help="Path to input JSON file", default="./sample_projects/generated_grid_8x5_no_mirror_bracing.json")
    parser.add_argument("--output", help="Path to output JSON file", default="analysis_output_tekla.json")
    parser.add_argument("--tekla-bin-path", help="Path to Tekla Structures bin directory", default="C:\\TeklaStructures\\2025.0\\bin")
    parser.add_argument("--pick-object", help="Prompt to pick an object in Tekla", action="store_true")
    args = parser.parse_args()

    if args.pick_object:
        print("Pick an object in Tekla to retrieve its attributes...")
        try:
            model = tekla_common._get_connected_model()
            pick_and_print_attributes()
        except Exception as exc:
            print(f"   FAIL: {exc}\n")
        return

    input_json_path = args.input
    if not input_json_path:
        print("Error: --input argument is required.")
        return
    print(f"Loading input from {args.input}")
    with open(input_json_path, "r") as f:
        input_data = json.load(f)
    print(f"Loaded input JSON from {input_json_path}:")
    #print(json.dumps(input_data, indent=2))
    print("Proceeding with Tekla self-test...")
    analysis_output = send_to_tekla(input_data)
    if args.output:
        with open(args.output, "w") as f:
            json.dump(analysis_output, f, indent=2)
        print(f"Analysis output written to {args.output}")

    print("=== self-test complete ===")

if __name__ == "__main__":
    _self_test()
