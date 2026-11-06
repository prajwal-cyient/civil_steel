# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A web app for designing standard steel radiator-support frames. It generates a parametric 3D grid of columns, beams and bracing, then sends that geometry to three Windows desktop engineering tools:
- **Tekla Structures** for the 3D model.
- **STAAD.Pro** for analysis and member design.
- **IDEA StatiCa Connection** for joint design.

There is no build step. It is plain Python plus one static HTML/JS page.

## Commands

```bash
# Run the API server + web UI (UI at http://localhost:8080/radiator_support)
python generate_grid.py --port 8080

# CLI: generate members from a grid spec without the server
python generate_grid.py --input frame_specs.json --output out.json

# Python tests (unittest, no pytest config)
python -m unittest discover -s tests
python -m unittest tests.test_staad_radiator_installation_load            # one module
python -m unittest tests.test_staad_radiator_installation_load.RadiatorInstallationLoadTests.test_name  # one test

# JS tests for the results UI (static/radiator_support/results.js)
node --test tests/test_staad_results.cjs

# Integration self-tests / standalone runs (need the desktop app running)
python tekla_integration.py
python staad_integration.py --input <generated.json> --output analysis_output.json
python idea_statica_integration.py sample_projects/<template>.ideaCon   # read-only: lists connections, members, parameter keys
python idea_statica_integration.py --design inner_node_1_longitudinal_1_transverse_bracing.json

# Hit an endpoint directly
curl -X POST http://localhost:8080/api/v1/to_ideastatica -H "Content-Type: application/json" -d @inner_node_1_longitudinal_1_transverse_bracing.json
```

There is no linter config. Dependencies are listed in `requirements.txt`: `fastapi`, `uvicorn` and `pydantic` (server). Each integration also needs its own package: `pythonnet` for Tekla, `openstaadpy`/`pywin32` for STAAD, and `ideastatica-connection-api` for IDEA StatiCa. The SDK version must exactly match the installed IDEA StatiCa version.

Python tests don't import the integration modules, because those modules load Windows/COM/.NET dependencies at import time. Instead, each test `ast`-parses `staad_integration.py`, pulls out the named functions and constants, and `exec`s them in isolation. If a test helper is renamed, or its signature changes, the test's extraction list or call sites must be updated too.

## Architecture

**`generate_grid.py`**: the entry point.
- `generate_members(cfg)` builds the geometry and returns two representations:
  - `members`: continuous beams, sent to Tekla.
  - `members_split_beams`: beams split per bay, sent to STAAD.
- `create_app()` builds the FastAPI app. FastAPI and pydantic are imported lazily so CLI mode works without them. Endpoints:
  - `POST /api/v1/generate_radiator_support`
  - `POST /api/v1/to_tekla`
  - `POST /api/v1/to_staad`
  - `POST /api/v1/to_ideastatica`
- Each integration module is imported in a module-level `try/except ImportError`, so the server still starts on machines without that tool installed.
- Profile dimensions are looked up through Tekla's catalog. If Tekla isn't reachable, the lookups return 0, so geometry still generates but beam stacking and lengths are not physically correct.

**Integration modules.** Each one drives a *live, running GUI session* of its tool (not headless) and has a long module docstring. Read that docstring before changing the module: it records what has been verified against the real API and what is still best-effort.
- `tekla_integration.py`: Tekla Open API via pythonnet. The bin path comes from `TEKLA_BIN_PATH`. Python stubs for IntelliSense live in `typings/`.
- `staad_integration.py`: OpenSTAAD. `send_to_staad()` is async and is the bulk of the domain logic:
  - It creates nodes and beams with caller-allocated IDs, assigns sections, and builds load cases, combinations and envelopes from `load_configuration` (radiator, wind, seismic).
  - It runs the analysis, then post-processes the results. It groups joints by position and bracing count, and computes min/max forces per envelope, joint group and member type.
  - Joint group names follow the pattern `{support|inner|corner|longitudinal_edge|transverse_edge}_node_<n>_longitudinal_<m>_transverse_bracing...` (see `group_name_suffix_from_bracings`).
- `idea_statica_integration.py`: IDEA StatiCa Connection REST API. It attaches to an already-running `IdeaStatiCa.ConnectionRestApi.exe`, at `IDEA_STATICA_BASE_URL` (default `http://localhost:5000`).
  - The API cannot create plates, bolts or welds. `design_joint()` instead opens a hand-built template `.ideaCon`, swaps in member cross-sections and loads, updates exposed operation parameters, recalculates, and exports the results.
  - Parameter keys exist only if the template author exposed them in the desktop app's Developer Mode.
  - Forces are sent in kN/kNm and scaled to N/Nm inside the module.

**Cross-tool naming contract.** These names must stay consistent across Python, JS and the templates:
- **Member names → IDEA StatiCa member names.** Generated member names (`beam-x…`, `beam-y…`, `bracing-long…`, `bracing-trans…`, otherwise column) map to the IDEA StatiCa template's member names (`beam_longitudinal`, `beam_transverse`, `bracing_longitudinal`, `bracing_transverse`, `column`). The mapping is done by `memberName2type`, which is duplicated in `staad_integration.py` and `static/radiator_support/results.js`; change both together.
- **Joint group → template file.** The UI sends `template_project_path = sample_projects/<joint group name>.ideaCon`. Each STAAD joint group therefore needs a template file with that exact name.
- **Section and material names.** Tekla, STAAD and IDEA StatiCa (MPRL) each use their own catalog naming (e.g. `CFRHS100X100X4` vs `SHS100/100/4.0`). `tekla2staad()` handles the STAAD mapping.

**Web UI** (`static/radiator_support/`): a single `index.html` with an inline ES-module script and Three.js loaded from unpkg, plus two classic scripts:
- `results.js`: renders the results pane; tested by the `.cjs` test through a fake DOM.
- `idea_statica_defaults.js`: the hardcoded IDEA StatiCa payload.

The flow is Generate → Send to STAAD (replaces the preview with STAAD geometry and fills the joint-group and STRENGTH-envelope dropdowns) → Send to IdeaStatica. `README.md` describes the STAAD results-viewer behaviour.

## Work in progress

- **IDEA StatiCa member updates and load effects** are still the hardcoded copy in `idea_statica_defaults.js`. They are meant to come from the STAAD response's `envelope_load_cases_by_joint_group[envelopeId][jointGroup]`, which currently yields load effects only. Only the operation parameters come from the UI.
- **SDK client close bug.** The SDK's `ConnectionApiClient.__exit__` closes the project whenever `api_client.project` is set, even if nothing was opened. Clear `api_client.project` when an open fails (as `design_joint()` already does), or the close error masks the real one.
- **`patch/con_member.py`** is an experimental patched SDK model for `aligned_plate`/`related_plate`. It doesn't work yet; those `member_updates` fields are a known TODO in `design_joint()`.
- **`skill.md`** describes an older input schema (`col_section`, `long_bracings`, …). The authoritative schema is the `GridConfig` model in `generate_grid.py` and its module docstring.
