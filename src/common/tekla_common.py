"""Delete non-grid objects from the active Tekla model and save it.

Importing this module does not connect to Tekla. Calling main() is destructive.
Requires the same pythonnet/Tekla setup as tekla_integration.py.
"""

import getpass
import os
import sys
import shutil
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
from Tekla.Structures.Catalogs import LibraryProfileItem, ParametricProfileItem, ProfileItemParameter  # noqa: F401,E402
from Tekla.Structures.Model import ModelObject
from Tekla.Structures.Model.UI import Picker  # noqa: E402
from Tekla.Structures.Model.UI import ModelObjectSelector
from Tekla.Structures import AutoDirectionTypeEnum, DetailTypeEnum, DetailTypeEnum, PositionTypeEnum
from System.Collections import Hashtable, ArrayList

# ── Errors ────────────────────────────────────────────────────────────────

class TeklaConnectionError(RuntimeError):
    """Raised when no running Tekla Structures model can be reached."""


class TeklaMemberError(RuntimeError):
    """Raised when a single member fails to insert into the model."""


# ── Internals ────────────────────────────────────────────────────────────

# Repo root (this file is src/common/tekla_common.py); the Tekla attribute
# and package-DB folders live there, not next to this module.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _get_connected_model() -> Model:
    model = Model()
    if not model.GetConnectionStatus():
        raise TeklaConnectionError(
            "Could not connect to Tekla Structures. Make sure Tekla "
            "Structures is running with a model open."
        )
    return model


def _copy_missing_connection_attributes(model: Model) -> int:
    source_dir = os.path.join(_PROJECT_ROOT, "tekla_connection_attributes")
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

def _copy_missing_tekla_package_dbs(model: Model) -> int:
    source_dir = os.path.join(_PROJECT_ROOT, "tekla_package_dbs")
    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Tekla package DB source directory not found: {source_dir}")

    model_path = model.GetInfo().ModelPath
    packages_dir = model_path
    os.makedirs(packages_dir, exist_ok=True)

    copied_count = 0
    for current_dir, _, filenames in os.walk(source_dir):
        relative_dir = os.path.relpath(current_dir, source_dir)
        destination_dir = packages_dir if relative_dir == "." else os.path.join(packages_dir, relative_dir)
        os.makedirs(destination_dir, exist_ok=True)

        for filename in filenames:
            source_file = os.path.join(current_dir, filename)
            destination_file = os.path.join(destination_dir, filename)
            if os.path.exists(destination_file):
                continue
            shutil.copy2(source_file, destination_file)
            copied_count += 1
    print(f'Copied {copied_count} Tekla package DBs from {source_dir} to {packages_dir}.')
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

    is_new = grid is None
    if is_new:
        grid = Grid()

    print(f'Modifying grid with ID: {grid.Identifier.ID}...')
    grid.CoordinateX = X
    grid.CoordinateY = Y
    grid.CoordinateZ = Z
    grid.Origin = Point(0.0, 0.0, 0.0)
    grid.LabelX = labelX
    grid.LabelY = labelY
    grid.LabelZ = labelZ
    if not (grid.Insert() if is_new else grid.Modify()):
        raise TeklaMemberError("Tekla rejected the model grid.")
    if model.CommitChanges() is False:
        raise TeklaMemberError("Tekla rejected the grid commit.")

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

# For HEAXXX, the I shaped profiles, following properties are available:
# HEIGHT, WIDTH, WEB_THICKNESS, FLANGE_THICKNESS, ROUNDING_RADIUS_1, ROUNDING_RADIUS_2, FLANGE_SLOPE_RATIO
# For CFRHS120X120X5: HEIGHT, WIDTH, PLATE_THICKNESS, ROUNDING_RADIUS
def _get_profile_parameter(profile_name: str, property_name: str) -> Optional[float]:
    """Shared lookup used by get_profile_height() and get_profile_width()."""
    # CFRHS names such as CFRHS100X100X4 are parametric profiles. Preserve the
    # supplied name and ask both native catalogues instead of guessing dimensions.
    for profile_type in (LibraryProfileItem, ParametricProfileItem):
        profile_item = profile_type()
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

def get_profile_web_thickness(profile_name: str) -> Optional[float]:
    """
    Look up a profile's web thickness (mm) from the Tekla catalog.

    Parameters
    ----------
    profile_name : str
        The Tekla profile string (e.g. "HEA140", "CFRHS120X120X5").

    Returns
    -------
    Optional[float]
        The profile's WEB_THICK catalog parameter, or None if the profile
        couldn't be found or has no WEB_THICK parameter.
    """
    return _get_profile_parameter(profile_name, "WEB_THICKNESS")

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

# Get bolt parameters from bolt diameter
def get_bolt_parameters(bolt_dia: float) -> dict:
    """
    Returns standard bolt parameters based on the bolt diameter.

    Parameters
    ----------
    bolt_dia : float
        The bolt diameter.

    Returns
    -------
    dict
        A dictionary containing standard bolt parameters such as pitch, edge distance, etc.
    """
    tolerance = 2 if bolt_dia <= 27 else 3
    hole_dia = bolt_dia + tolerance
    edge_distance_table = {12: 25, 16: 30, 20: 35, 24: 40, 27: 45, 30: 50, 36: 60, 40: 65}
    return {
        "tolerance": tolerance,
        "edge_distance": edge_distance_table.get(bolt_dia, 1.5 * bolt_dia),
        "hole_dia": hole_dia,
    }

def clean_tekla(progress=None) -> None:
    """Empty the active model except for grids and grid surfaces, then save."""
    from Tekla.Structures.Model import (
        BaseComponent, GridBase, GridSurface, LoadGroup, ModelHandler,
    )

    model = _get_connected_model()

    def snapshot():
        objects = []
        grids = set()
        default_groups = []
        enumerator = model.GetModelObjectSelector().GetAllObjects()
        inspected = 0
        while enumerator.MoveNext():
            obj = enumerator.Current
            inspected += 1
            if progress and inspected % 1000 == 0:
                progress(f"Inspecting existing Tekla objects: {inspected}")
            if isinstance(obj, (GridBase, GridSurface)):
                grids.add(obj.Identifier.ID)
            elif isinstance(obj, LoadGroup) and obj.GroupName == "DefaultGroup":
                default_groups.append(obj.Identifier.ID)
            else:
                objects.append(obj)
        # Remove components before their generated parts so they cannot recreate them.
        objects.sort(key=lambda obj: not isinstance(obj, BaseComponent))
        return objects, grids, default_groups

    objects, original_grids, default_groups = snapshot()
    print(f"Cleaning Tekla model: {model.GetInfo().ModelPath}")
    if default_groups:
        print(f"Preserving Tekla DefaultGroup load group IDs: {default_groups}")
    while objects:
        previous_count = len(objects)
        for index, obj in enumerate(objects, 1):
            # Deleting a parent can also delete objects from our snapshot.
            current = model.SelectModelObject(obj.Identifier)
            if current is not None:
                current.Delete()
            if progress and index % 1000 == 0:
                progress(f"Clearing existing Tekla objects: {index}/{previous_count}")
        objects, grids, _ = snapshot()
        if grids != original_grids:
            raise RuntimeError("Tekla grids changed during cleanup; model was not saved.")
        if objects and len(objects) >= previous_count:
            remaining = ", ".join(
                f"{obj.Identifier.ID} ({obj.GetType().FullName})" for obj in objects
            )
            raise RuntimeError(
                f"Tekla cleanup incomplete. Cannot delete object IDs: {remaining}. "
                "Some objects may already be deleted; model was not saved."
            )

    if not model.CommitChanges():
        raise RuntimeError("Tekla cleanup could not be committed; model was not saved.")
    if not ModelHandler().Save("Clean model, preserving grids", getpass.getuser()):
        raise RuntimeError("Tekla cleanup was committed, but saving the model failed.")
    print("Tekla cleanup saved; grids and DefaultGroup preserved.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Clean Tekla model, preserving grids and DefaultGroup.")
    parser.add_argument("--clean", action="store_true", help="Clean the Tekla model.")
    args = parser.parse_args()
    if args.clean:
        clean_tekla()
