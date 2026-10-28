# civil-steel-standard-web
Web App to design Steel Standard structures in Tekla, Staad.Pro and Idea Statica

## Cleaning open models

These utilities are **destructive** and save changes automatically. Back up
important models first. Importing either module has no application side effects;
calling its synchronous `main()` performs cleanup without prompting.

```python
from tekla_cleanup import main as clean_tekla
from staad_cleanup import main as clean_staad

clean_tekla()
clean_staad()
```

They can also be run separately with `python tekla_cleanup.py` or
`python staad_cleanup.py`.

- [tekla_cleanup.py](./tekla_cleanup.py) uses the existing Tekla connection setup
  (`pythonnet`, `TEKLA_BIN_PATH`). It deletes all enumerated model objects except
  grids (including radial grids), grid surfaces/planes and Tekla's `DefaultGroup`
  load group (reported explicitly, since Tekla does not allow deleting it).
  Components are
  connections/details that can generate parts, bolts and welds; these are removed
  along with the structural objects. It commits and saves the open model.
  It does not remove drawings, catalogs, attributes or project-directory files.
- [staad_cleanup.py](./staad_cleanup.py) uses `openstaadpy` and requires an open,
  saved analytical `.std` model with no analysis running. It saves current edits,
  creates a unique `*.before-cleanup-*.std.bak` backup beside the input, opens
  a temporary copy of [empty_staad_project.std](./sample_projects/empty_staad_project.std)
  and waits up to 30 seconds for STAAD's asynchronous file opening to make it
  active and empty. It then replaces the original input
  with that template, reopens the original path, verifies it and saves. The saved
  input must match the template's tokens (including its title and job information).
  This clears geometry, loads, supports,
  materials/properties, groups, units, original job information, and analysis/design
  commands. Physical models are rejected. External result files and other
  project-directory files are not deleted; the backup contains the input only.

Connection, deletion, verification and save errors propagate to the caller.
Cleanup is not transactional: on failure, inspect the open model before retrying;
for STAAD, the printed backup path holds the saved pre-cleanup input. If switching
back to the original file fails, the staging `.std` is retained and its path printed.

## STAAD results viewer

After **Send to STAAD**, the 3D preview is replaced with the returned
`member_data_by_id` geometry in STAAD's Y-up coordinate system. Member colours
represent their `class`; pyramids mark `support_nodes`. The load-case selector
combines the cases from all load envelopes and displays their `load_case_title`.
The STAAD view starts rotated 180 degrees horizontally, with column indices
shown along all four edges of the ground grid, offset beyond the support icons.
Longitudinal indices are numeric; transverse (Y) indices use spreadsheet-style
letters: A through Z, then AA, AB, and so on, in both preview and STAAD views.

Hover over a member for its metadata, name-derived type, start/end forces and
any support reactions for the selected case. Hover over a support joint for its
reactions. Force and moment units are taken from the response. Click a joint to
highlight the member ends reported by `joint_member_ends`; click empty space to
clear the selection. Generating a new structure resets the STAAD view.

In the left pane, **Idea Statica Connection Design** lists the returned
`joint_groups`. Selecting a group highlights all its joints and connected
members. Selecting the placeholder clears the highlighting; clicking a joint
replaces the group selection with that individual joint. The dropdown resets
when a new structure is generated and is disabled when no groups are available.

Run the results UI unit tests with `node --test tests\test_staad_results.cjs`.
