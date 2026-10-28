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
import os
import shutil
import sys
from typing import Optional

# ── pythonnet / Tekla Open API ──────────────────────────────────────────
# 1. Path to your Tekla Structures bin folder (adjust version accordingly,
#    or set the TEKLA_BIN_PATH environment variable to override it).
tekla_bin_path = os.environ.get("TEKLA_BIN_PATH", r"C:\TeklaStructures\2025.0\bin")
sys.path.append(tekla_bin_path)

import clr  # noqa: E402  (pythonnet; must be imported before clr.AddReference)

dlls = [
    'Tekla.Structures.dll',
    'Tekla.Structures.Plugins.dll',
    'Tekla.Structures.Model.dll',
    'Tekla.Structures.DataType.dll',
    'Tekla.Structures.Geometry3d.Compatibility.dll',
    'Tekla.Structures.Dialog.dll',
    'Tekla.Structures.Analysis.dll',
    'Tekla.Structures.Catalogs.dll',
    'Tekla.Structures.Drawing.dll',
]

# 2. Load Tekla .NET Assemblies
for dll in dlls:
    clr.AddReference(os.path.join(tekla_bin_path, dll))
clr.AddReference("System")

from Tekla.Structures.Model import Detail, Model, Beam, Grid, Position, Connection  # noqa: E402
from Tekla.Structures.Model import ModelObjectEnumerator  # noqa: F401,E402
from Tekla.Structures.Geometry3d import Point, Vector  # noqa: E402
from Tekla.Structures.Catalogs import LibraryProfileItem, ProfileItemParameter  # noqa: F401,E402
from Tekla.Structures.Model import ModelObject
from Tekla.Structures.Model.UI import Picker  # noqa: E402
from Tekla.Structures.Model.UI import ModelObjectSelector
from Tekla.Structures import AutoDirectionTypeEnum, DetailTypeEnum, DetailTypeEnum, PositionTypeEnum
from System.Collections import Hashtable, ArrayList

from tekla_cleanup import main as clean_tekla

# ── Errors ────────────────────────────────────────────────────────────────

class TeklaConnectionError(RuntimeError):
    """Raised when no running Tekla Structures model can be reached."""


class TeklaMemberError(RuntimeError):
    """Raised when a single member fails to insert into the model."""


# ── Internals ────────────────────────────────────────────────────────────

def _get_connected_model() -> Model:
    model = Model()
    if not model.GetConnectionStatus():
        raise TeklaConnectionError(
            "Could not connect to Tekla Structures. Make sure Tekla "
            "Structures is running with a model open."
        )
    return model


def _copy_missing_connection_attributes(model: Model) -> int:
    source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tekla_connection_attributes")
    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Connection attribute source directory not found: {source_dir}")

    model_path = model.GetInfo().ModelPath
    attributes_dir = os.path.join(model_path, "attributes")
    os.makedirs(attributes_dir, exist_ok=True)

    copied_count = 0
    for current_dir, _, filenames in os.walk(source_dir):
        relative_dir = os.path.relpath(current_dir, source_dir)
        destination_dir = attributes_dir if relative_dir == "." else os.path.join(attributes_dir, relative_dir)
        os.makedirs(destination_dir, exist_ok=True)

        for filename in filenames:
            source_file = os.path.join(current_dir, filename)
            destination_file = os.path.join(destination_dir, filename)
            if os.path.exists(destination_file):
                continue
            shutil.copy2(source_file, destination_file)
            copied_count += 1
    print(f'Copied {copied_count} connection attributes from {source_dir} to {attributes_dir}.')
    return copied_count


# delete all grids
def delete_all_grids(model: Model):
    model_object_enumerator = model.GetModelObjectSelector().GetAllObjectsWithType(ModelObject.ModelObjectEnum.GRID)
    deleted_count = 0
    while model_object_enumerator.MoveNext():
        grid = model_object_enumerator.Current
        if isinstance(grid, Grid):
            grid_id = grid.Identifier.ID
            if grid.Delete():
                print(f'Deleted grid with ID: {grid_id}')
                deleted_count += 1
            else:
                print(f'Failed to delete grid with ID: {grid_id}')
    if deleted_count >0:
        model.CommitChanges()
    else:
        print(f'No grid deleted')

# create a new grid
# Sample call
# model = Model()
# create_grid(model, '0.00 21*2600.00', '0.00 5*3300.00', '0.0 3600.0 7200.0', '1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22', 'A B C D E F', '+0 +3600 +7200 +8000 ', 2000, 2000, 2000, 2000, 2000, 2000, 0, 0, 0)
def create_grid(model: Model, X: str, Y: str, Z: str, labelX: str, labelY: str, labelZ: str, extensionLeftX: int, extensionLeftY: int, extensionLeftZ: int, extensionRightX: int, extensionRightY: int,extensionRightZ: int, originX: int, originY: int, originZ: int):
    grid = Grid()
    grid.CoordinateX = X
    grid.CoordinateY = Y
    grid.CoordinateZ = Z
    grid.LabelX = labelX
    grid.LabelY = labelY
    grid.labelZ = labelZ
    grid.ExtensionLeftX = extensionLeftX
    grid.ExtensionLeftY = extensionLeftY
    grid.ExtensionLeftZ = extensionLeftZ
    grid.ExtensionRightX = extensionRightX
    grid.ExtensionRightY = extensionRightY
    grid.ExtensionRightZ = extensionRightZ
    grid.Origin.X = originX
    grid.Origin.Y = originY
    grid.Origin.Z = originZ
    if grid.Insert():
        print(f'Grid created with ID: {grid.Identifier.ID}')
        model.CommitChanges()

# Find grid instances and modify the first grid found
# sample call
# modify_grid(model, '0.00 21*2600.00', '0.00 5*3300.00', '0.0 3600.0 7200.0', '1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22', 'A B C D E F', '+0 +3600 +7200 +8000 ')
def modify_grid(model: Model, X: str, Y: str, Z: str, labelX: str, labelY: str, labelZ: str):
    grid = None
    model_object_enumerator = model.GetModelObjectSelector().GetAllObjectsWithType(ModelObject.ModelObjectEnum.GRID)
    while model_object_enumerator.MoveNext():
        gridTemp = model_object_enumerator.Current
        if isinstance(gridTemp, Grid):
            grid = gridTemp
            print(f'Found grid: {grid.Identifier.ID}. It will be modified.')
            break

    if grid is None:
        print(f'No grid found to modify...')
        return

    print(f'Modifying grid with ID: {grid.Identifier.ID}...')
    grid.CoordinateX = X
    grid.CoordinateY = Y
    grid.CoordinateZ = Z
    grid.LabelX = labelX
    grid.LabelY = labelY
    grid.labelZ = labelZ
    if grid.Modify():
        print(f'Grid with ID {grid.Identifier.ID} modified.')
        model.CommitChanges()

def to_tekla_profile(name: str) -> str:
    """Convert UI section name to Tekla profile string.

    Replaces the Unicode multiplication sign (×, U+00D7) with an
    ASCII asterisk (*) as required by Tekla profile catalogue.

    Examples
    --------
    CFRHS120×120×5  →  CFRHS120*120*5
    HEA140          →  HEA140  (unchanged)
    """
    return name.replace("\u00d7", "X").replace("×", "X")

def _create_beam(member: dict) -> Beam:
    """
    Build and insert a single Tekla Beam from one member dict of the form:
        {"p1": [x, y, z], "p2": [x, y, z], "profile": str,
         "material": str, "class": str, "type": str, "name": str}

    Columns, beams, and bracing are all represented as Tekla Beam objects
    (a straight prismatic part between two points); "class" sets the
    Tekla part Class (e.g. used for numbering/reporting conventions), and
    "type" only affects the member's name/logging here.
    """
    beam = Beam()
    beam.StartPoint = Point(*member["p1"])
    beam.EndPoint = Point(*member["p2"])
    beam.Profile.ProfileString = to_tekla_profile(member["profile"])
    beam.Material.MaterialString = member["material"]
    beam.Class = member["class"]
    beam.Name = member.get("name").split('-')[0].upper()
    memberType = member.get("type", "unknown").lower()
    if memberType in ['column', "longitudinal_brace", "transverse_brace"]:
        beam.Position.Plane = Position.PlaneEnum.MIDDLE
        beam.Position.Depth = Position.DepthEnum.MIDDLE

    if memberType in ['longitudinal_brace', 'transverse_brace']:
        beam.Position.Rotation = Position.RotationEnum.BACK

    if memberType in ['column']:
        beam.Position.Rotation = Position.RotationEnum.BELOW
    if memberType in ['beam'] and member.get("name").startswith("beam-x-"):
        beam.Position.Plane = Position.PlaneEnum.MIDDLE
        beam.Position.Rotation = Position.RotationEnum.TOP
        beam.Position.Depth = Position.DepthEnum.MIDDLE
    if memberType in ['beam'] and member.get("name").startswith("beam-y-"):
        beam.Position.Plane = Position.PlaneEnum.MIDDLE
        beam.Position.Rotation = Position.RotationEnum.TOP
        beam.Position.Depth = Position.DepthEnum.FRONT
        #TODO: this should be height of the longitudinal beam on which this transverse beam sits
        # Does not matter as there is a single beam_profile in the frame specs of the model.
        beam.Position.DepthOffset = get_profile_height(member["profile"])/2.0

    if not beam.Insert():
        raise TeklaMemberError(f"Failed to insert member '{beam.Name}' into the model.")

    return beam


# ── Public API ───────────────────────────────────────────────────────────

def _get_profile_parameter(profile_name: str, property_name: str) -> Optional[float]:
    """Shared lookup used by get_profile_height() and get_profile_width()."""
    profile_item = LibraryProfileItem()

    if profile_item.Select(profile_name):
        for param in profile_item.aProfileItemParameters:
            if param.Property.upper() == property_name:
                return param.Value

    return None


def get_profile_height(profile_name: str) -> Optional[float]:
    """
    Look up a profile's cross-section height (mm) from the Tekla catalog.

    Used by generate_grid.py to figure out how much clearance a beam's
    own depth needs when stacking beams on top of one another (and when
    clamping the total structure height), since that depth isn't known
    until the actual catalog profile is resolved.

    Parameters
    ----------
    profile_name : str
        The Tekla profile string (e.g. "HEA140", "CFRHS120X120X5").

    Returns
    -------
    Optional[float]
        The profile's HEIGHT catalog parameter, or None if the profile
        couldn't be found or has no HEIGHT parameter.
    """
    return _get_profile_parameter(profile_name, "HEIGHT")


def get_profile_width(profile_name: str) -> Optional[float]:
    """
    Look up a profile's cross-section width (mm) from the Tekla catalog.

    Used by generate_grid.py to extend continuous beam runs past the
    grid lines by the width of whatever they land on/against (e.g. a
    continuous longitudinal beam extended by the column width at each
    end), since that width isn't known until the actual catalog profile
    is resolved.

    Parameters
    ----------
    profile_name : str
        The Tekla profile string (e.g. "HEA140", "CFRHS120X120X5").

    Returns
    -------
    Optional[float]
        The profile's WIDTH catalog parameter, or None if the profile
        couldn't be found or has no WIDTH parameter.
    """
    return _get_profile_parameter(profile_name, "WIDTH")

def _index_to_letters(n: int) -> str:
    """
    Converts a positive 1-based integer to spreadsheet-style base-26
    letters: 1->A, 2->B, ..., 26->Z, 27->AA, 28->AB, ..., 52->AZ,
    53->BA, ... This is "bijective base-26" (digits are 1-26 mapped to
    A-Z, with no zero), which is what Excel/Tekla-style column/grid
    labeling actually uses — a naive base-26 conversion breaks at the
    rollover point (e.g. would produce "A@" instead of "AA"), which is
    exactly why chr(a + 0x40) alone fails past n=26.
    """
    letters = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(0x41 + remainder) + letters
    return letters


def generate_y_axis_labels(n_trans_bays: int) -> list:
    """
    Generates Tekla-style grid line labels for the transverse (Y) axis:
    A, B, C, ..., Z, AA, AB, ... Returns one label per grid line
    (n_trans_bays + 1 labels total, matching n_trans_bays + 1 grid
    lines for n_trans_bays bays).
    """
    return [_index_to_letters(i) for i in range(1, n_trans_bays + 2)]

def add_base_connection(model: Model, column: Beam, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, attribute_file: str = "baseplate_1004"):
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
    """

    detailObj = Detail()
    detailObj.Name = detail_name
    detailObj.Number = connection_number
    detailObj.LoadAttributesFromFile(attribute_file)
    detailObj.PositionType = PositionTypeEnum.MIDDLE_PLANE
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

    if detailObj.Insert():
        #print("✅ Base plate created")
        pass
    else:
        print("❌ Failed to create base plate")
    #model.CommitChanges()

def add_column2beam_connection(model: Model, secondary: Beam, primary: Beam, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, classCode: int, attribute_file: str = "column_longitudinal_beam", upVector: Vector = Vector(1000, 0, 0)):
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
    """

    # ✅ Create Connection
    connectionObj = Connection()
    connectionObj.Name = detail_name
    connectionObj.Number = connection_number
    # ✅ Load attributes
    connectionObj.LoadAttributesFromFile(attribute_file)
    #connectionObj.SetAttribute("cut_beam", 0)
    #connectionObj.SetAttribute("beam_cont", 1)
    connectionObj.Class = classCode
    connectionObj.Code = design_code

    # ✅ Direction
    connectionObj.UpVector = upVector

    # ✅ Positioning
    #connectionObj.PositionType = PositionTypeEnum.COLLISION_PLANE

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

    if connectionObj.Insert():
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
    model = _get_connected_model()
    _copy_missing_connection_attributes(model)

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
            beam = _create_beam(member)
            created += 1
            member_type = member.get("type", "").lower()
            member_name = member.get("name", "")
            if member_type == "column":
                columns.append((member, beam))
            elif member_type == "beam" and member_name.startswith("beam-x-"):
                longitudinal_beams[member_name] = beam
            elif member_type == "beam" and member_name.startswith("beam-y-"):
                transverse_beams[member_name] = beam
            elif member_type in ("longitudinal_brace", "transverse_brace"):
                braces[member_name] = beam
        except Exception as exc:  # keep going; report this member and move on
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

    model.CommitChanges()
    # Add base connections and column-to-beam connections for the columns
    for member, column in columns:
        try:
            # Base connection for the column (BN)
            add_base_connection(
                model,
                column,
                1004,
                f"{member.get('name', 'Column')} base plate",
                "1004",
                grid,
            )
        except Exception as exc:
            errors.append({"name": member.get("name", "<unnamed>"), "error": str(exc)})

        member_name = member.get("name", "")
        name_parts = member_name.split("-")
        if len(name_parts) >= 2:
            longitudinal_beam = longitudinal_beams.get(f"beam-x-{name_parts[1]}")
            if longitudinal_beam is not None:
                try:
                    # Add connection between column and longitudinal beam
                    add_column2beam_connection(
                        model,
                        column,
                        longitudinal_beam,
                        144,
                        f"{member_name} longitudinal beam connection",
                        "144",
                        grid,
                        6
                    )
                except Exception as exc:
                    errors.append({"name": member_name, "error": str(exc)})

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
                try:
                    # Add connection between column and adjacent brace
                    add_column2beam_connection(
                        model,
                        brace,
                        column,
                        20,
                        f"{member_name} {brace_name} connection",
                        "20",
                        grid,
                        1,
                        "bracing_column",
                    )
                except Exception as exc:
                    errors.append({"name": member_name, "error": str(exc)})
    model.CommitChanges()
    # Add connections between longitudinal and transverse beams
    for longitudinal_index in range(grid["n_long_bays"] + 1):
        transverse_beam = transverse_beams.get(f"beam-y-{longitudinal_index}")
        if transverse_beam is None:
            continue
        for transverse_index in range(grid["n_trans_bays"] + 1):
            longitudinal_beam = longitudinal_beams.get(f"beam-x-{transverse_index}")
            if longitudinal_beam is None:
                continue
            try:
                add_column2beam_connection(
                    model,
                    transverse_beam,
                    longitudinal_beam,
                    30,
                    f"beam-x-{transverse_index} beam-y-{longitudinal_index} connection",
                    "30",
                    grid,
                    1,
                    "longitudinal_beam_transverse_beam",
                )
            except Exception as exc:
                errors.append({
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
            model = _get_connected_model()
            pick_and_print_attributes()
        except Exception as exc:
            print(f"   FAIL: {exc}\n")
        return

    input_json_path = args.input
    if not input_json_path:
        print("Error: --input argument is required.")
        return
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
