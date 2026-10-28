"""Delete non-grid objects from the active Tekla model and save it.

Importing this module does not connect to Tekla. Calling main() is destructive.
Requires the same pythonnet/Tekla setup as tekla_integration.py.
"""

import getpass


def main() -> None:
    """Empty the active model except for grids and grid surfaces, then save."""
    from tekla_integration import _get_connected_model
    from Tekla.Structures.Model import (
        BaseComponent, GridBase, GridSurface, LoadGroup, ModelHandler,
    )

    model = _get_connected_model()

    def snapshot():
        objects = []
        grids = set()
        default_groups = []
        enumerator = model.GetModelObjectSelector().GetAllObjects()
        while enumerator.MoveNext():
            obj = enumerator.Current
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
        for obj in objects:
            # Deleting a parent can also delete objects from our snapshot.
            current = model.SelectModelObject(obj.Identifier)
            if current is not None:
                current.Delete()
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
    main()
