# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A web app for designing standard steel civil structures. The code is organised so it can serve several structure types; the only one implemented so far is the **radiator support** frame. For each structure type, the app generates a parametric 3D grid of columns, beams and bracing, then sends that geometry to three Windows desktop engineering tools:
- **Tekla Structures** for the 3D model.
- **STAAD.Pro** for analysis and member design.
- **IDEA StatiCa Connection** for joint design.

There is no build step. It is plain Python plus static HTML/JS pages.

## Layout

```
src/
  server.py                    FastAPI app + entry point for all structure types
  common/                      tool-level code shared by every structure type
    tekla_common.py            Tekla connection, profile catalog lookups, grids, clean_tekla()
    staad_common.py            OpenSTAAD connection, node/beam/section helpers, load/code maps, clean_staad()
    idea_statica_integration.py  IDEA StatiCa joint design (design_joint())
  radiator_support/            one package per structure type
    generate_grid.py           generate_members(cfg) + CLI
    tekla_integration.py       send_to_tekla()
    staad_integration.py       send_to_staad()
    frame_specs.json           sample grid spec
  static/
    radiator_support/          web UI for this structure type
sample_projects/               IDEA StatiCa templates, the empty STAAD model, sample inputs
tekla_connection_attributes/   copied into the Tekla model's attributes folder on send
tekla_package_dbs/             copied into the Tekla model folder on send
tests/
```

**Import roots.** `src/` is the import root: modules import each other as `common.…` and `radiator_support.…`. Running `python src/server.py` puts `src/` on `sys.path` automatically. To run any other module standalone, set `PYTHONPATH=src` and use `python -m`. Running a module by its file path (`python src/radiator_support/x.py`) leaves `common` unimportable. Some modules swallow that `ImportError`, so the failure shows up later and looks unrelated.

**Working directory.** Run everything from the repo root. Several paths are relative to the current working directory, not to the module: the `sample_projects/<joint group>.ideaCon` template paths and the CLI defaults. `start_server.bat` does `cd` to the repo root for this reason. The repo-root folders `sample_projects/`, `tekla_connection_attributes/` and `tekla_package_dbs/` are located from `__file__` in `common/*_common.py`. If those modules move, update those paths.

## Commands

```bash
# Run the API server + web UI (UI at http://localhost:8080/radiator_support)
python src/server.py --port 8080          # or start_server.bat

# CLI: generate radiator-support members from a grid spec without the server
PYTHONPATH=src python -m radiator_support.generate_grid --input src/radiator_support/frame_specs.json --output out.json

# Python tests (unittest, no pytest config)
python -m unittest discover -s tests
python -m unittest tests.test_staad_radiator_installation_load            # one module
python -m unittest tests.test_staad_radiator_installation_load.RadiatorInstallationLoadTests.test_name  # one test

# JS tests for the results UI (src/static/radiator_support/results.js)
node --test tests/test_staad_results.cjs

# Integration self-tests / standalone runs (need the desktop app running)
PYTHONPATH=src python -m radiator_support.tekla_integration
PYTHONPATH=src python -m radiator_support.staad_integration --input <generated.json> --output analysis_output.json
python src/common/idea_statica_integration.py sample_projects/<template>.ideaCon   # read-only: lists connections, members, parameter keys
python src/common/idea_statica_integration.py --design inner_node_1_longitudinal_1_transverse_bracing.json

# Empty the open Tekla / STAAD model (destructive; see README.md)
python src/common/tekla_common.py --clean
python src/common/staad_common.py --clean

# Hit an endpoint directly
curl -X POST http://localhost:8080/api/v1/to_ideastatica -H "Content-Type: application/json" -d @inner_node_1_longitudinal_1_transverse_bracing.json
```

In PowerShell, set the variable first: `$env:PYTHONPATH = "src"`.

There is no linter config. Dependencies are listed in `requirements.txt`: `fastapi`, `uvicorn` and `pydantic` (server). Each integration also needs its own package: `pythonnet` for Tekla, `openstaadpy`/`pywin32` for STAAD, and `ideastatica-connection-api` for IDEA StatiCa. The SDK version must exactly match the installed IDEA StatiCa version.

### Tests

Python tests mostly avoid importing the integration modules, because those modules load Windows/COM/.NET dependencies at import time. Instead, a test `ast`-parses the source file, pulls out named functions and constants, and `exec`s them in isolation:
- `test_staad_joint_group_load_effects.py` extracts from `src/radiator_support/staad_integration.py`.
- `test_staad_radiator_installation_load.py` extracts from both `src/radiator_support/staad_integration.py` and `src/common/staad_common.py`. The radiator module star-imports the common one, and some helpers it calls (`_node_key`, `searchDictByKeyStart`, the `*_MAP` constants) live in the common module. Calls written as `staad_common.<name>` are resolved through a `SimpleNamespace` over the extracted names.
- `test_project_cleanup.py` imports `common.staad_common` directly; its `openstaadpy` import is guarded. It `ast`-extracts `clean_tekla()` from `src/common/tekla_common.py`, because that module loads pythonnet and the Tekla DLLs at import. `_get_connected_model` is injected as a mock.

If you rename a helper, change its signature, or move it between `common/` and a structure package, update the test's extraction list, source path or call sites too.

## Architecture

**`src/server.py`**: the single FastAPI server for all structure types.
- `create_app()` builds the app. FastAPI and pydantic are imported lazily inside it. The pydantic request models live here, including `RadiatorSupportGridConfig`, the authoritative input schema for the radiator-support grid.
- Integration modules are imported lazily *inside* each endpoint, so the server starts on machines without Tekla, STAAD or IDEA StatiCa installed. Only the endpoint that needs a missing tool fails.
- Endpoints that depend on the structure type are prefixed with the type name. IDEA StatiCa joint design applies to every type, so it has no prefix:
  - `GET  /radiator_support`: the web UI
  - `POST /api/v1/radiator_support/generate`
  - `POST /api/v1/radiator_support/to_tekla`
  - `POST /api/v1/radiator_support/to_staad`
  - `POST /api/v1/to_ideastatica`
- To add a structure type: add a `src/<type>/` package (generator plus Tekla/STAAD integrations built on `common/`), its request models and `/api/v1/<type>/…` endpoints in `server.py`, and a UI under `src/static/<type>/`.

**`src/radiator_support/generate_grid.py`**: `generate_members(cfg)` builds the geometry and returns two representations:
- `members`: continuous beams, sent to Tekla.
- `members_split_beams`: beams split per bay, sent to STAAD.

Profile dimensions are looked up through Tekla's catalog (`common.tekla_common`). If Tekla isn't reachable, the lookups return 0, so geometry still generates but beam stacking and lengths are not physically correct.

**Integration modules.** Each one drives a *live, running GUI session* of its tool (not headless) and has a long module docstring. Read that docstring before changing the module: it records what has been verified against the real API and what is still best-effort. Code that any structure type could use belongs in `common/`. Code specific to one structure belongs in that structure's package. The structure modules pull in the common ones with `from common.<tool>_common import *` and also `import common.<tool>_common as <tool>_common`.
- `common/tekla_common.py`: Tekla Open API via pythonnet. The bin path comes from `TEKLA_BIN_PATH`. Python stubs for IntelliSense live in `typings/`.
- `radiator_support/tekla_integration.py`: `send_to_tekla()` creates grids, members and Tekla connections, using the attribute files in `tekla_connection_attributes/`.
- `common/staad_common.py`: OpenSTAAD connection, ID allocation, node/beam creation, section assignment, `tekla2staad()`, and the load-type, direction and design-code maps.
- `radiator_support/staad_integration.py`: `send_to_staad()` is async and is the bulk of the domain logic:
  - It creates nodes and beams with caller-allocated IDs, assigns sections, and builds load cases, combinations and envelopes from `load_configuration` (radiator, wind, seismic).
  - It runs the analysis, then post-processes the results. It groups joints by position and bracing count, and computes min/max forces per envelope, joint group and member type.
  - Joint group names follow the pattern `{support|inner|corner|longitudinal_edge|transverse_edge}_node_<n>_longitudinal_<m>_transverse_bracing...` (see `group_name_suffix_from_bracings`).
- `common/idea_statica_integration.py`: IDEA StatiCa Connection REST API. It attaches to an already-running `IdeaStatiCa.ConnectionRestApi.exe`, at `IDEA_STATICA_BASE_URL` (default `http://localhost:5000`).
  - The API cannot create plates, bolts or welds. `design_joint()` instead opens a hand-built template `.ideaCon`, swaps in member cross-sections and loads, updates exposed operation parameters, recalculates, and exports the results.
  - Parameter keys exist only if the template author exposed them in the desktop app's Developer Mode.
  - Forces are sent in kN/kNm and scaled to N/Nm inside the module.

**Cross-tool naming contract.** These names must stay consistent across Python, JS and the templates:
- **Member names → IDEA StatiCa member names.** Generated member names (`beam-x…`, `beam-y…`, `bracing-long…`, `bracing-trans…`, otherwise column) map to the IDEA StatiCa template's member names (`beam_longitudinal`, `beam_transverse`, `bracing_longitudinal`, `bracing_transverse`, `column`). The mapping is done by `memberName2type`, which is duplicated in `src/radiator_support/staad_integration.py` and `src/static/radiator_support/results.js`; change both together.
- **Joint group → template file.** The UI sends `template_project_path = sample_projects/<joint group name>.ideaCon`. Each STAAD joint group therefore needs a template file with that exact name.
- **Section and material names.** Tekla, STAAD and IDEA StatiCa (MPRL) each use their own catalog naming (e.g. `CFRHS100X100X4` vs `SHS100/100/4.0`). `tekla2staad()` in `common/staad_common.py` handles the STAAD mapping.

**Web UI** (`src/static/radiator_support/`, served at `/radiator_support`; `src/static/` is also mounted at `/static`): a single `index.html` with an inline ES-module script and Three.js loaded from unpkg, plus two classic scripts:
- `results.js`: renders the results pane; tested by the `.cjs` test through a fake DOM.
- `idea_statica_defaults.js`: the hardcoded IDEA StatiCa payload.

The flow is Generate → Send to STAAD (replaces the preview with STAAD geometry and fills the joint-group and STRENGTH-envelope dropdowns) → Send to IdeaStatica. The UI calls the `/api/v1/radiator_support/…` endpoints and `/api/v1/to_ideastatica`; if you rename an endpoint, update the `fetch` calls in `index.html` too. `README.md` describes the STAAD results-viewer behaviour.

## Work in progress

- **IDEA StatiCa member updates and load effects** are still the hardcoded copy in `idea_statica_defaults.js`. They are meant to come from the STAAD response's `envelope_load_cases_by_joint_group[envelopeId][jointGroup]`, which currently yields load effects only. Only the operation parameters come from the UI.
- **SDK client close bug.** The SDK's `ConnectionApiClient.__exit__` closes the project whenever `api_client.project` is set, even if nothing was opened. Clear `api_client.project` when an open fails (as `design_joint()` already does), or the close error masks the real one.
- **`patch/con_member.py`** is an experimental patched SDK model for `aligned_plate`/`related_plate`. It doesn't work yet; those `member_updates` fields are a known TODO in `design_joint()`.
- **Known failing tests.** These fail at HEAD as well; the reorganisation did not cause them. `test_staad_joint_force_maxima.py` tests `_joint_force_maxima`, which was removed from `staad_integration.py` in commit 28ab134. `RadiatorInstallationLoadTests.test_configuration_separates_installation_and_edge_dead_loads` calls `_apply_load_configuration` without the newer `grid_spec` argument.
- **Leftover pre-move references.** Some module docstrings and help strings still name `generate_grid.py` as the server, or use the old endpoint paths `/api/v1/generate_radiator_support` and `/api/v1/to_tekla`. Trust `src/server.py`.
- **`skill.md`** describes an older input schema (`col_section`, `long_bracings`, …). The authoritative schema is `RadiatorSupportGridConfig` in `src/server.py`, together with the module docstring of `src/radiator_support/generate_grid.py`.
