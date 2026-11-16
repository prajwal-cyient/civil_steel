"""Build the source-verified project handover PDF without executing engineering code.

Documentation tooling only. Requires reportlab and PyMuPDF for optional QA.
Never reads credentials, executes workbook macros, or contacts desktop software.
"""
from __future__ import annotations

import ast
from collections import defaultdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
PDF = OUT / "Agentic_Steel_Workflow_Technical_Guide.pdf"
DATE = "15 November 2026"
NAVY = colors.HexColor("#102A43")
TEAL = colors.HexColor("#007F82")
PALE = colors.HexColor("#EEF5F8")
GREY = colors.HexColor("#526475")


# Each production Python function is given an explanation. Qualified names are
# used when an otherwise identical leaf name has a different role.
DESCRIPTIONS = {
    "create_app": "Constructs the FastAPI application, request schemas, static UI, manual integration endpoints and agentic router. It defines the HTTP boundary; it does not itself perform a structural calculation.",
    "reserve_desktop_tools": "Rejects manual model-changing requests with HTTP 409 while this server's agentic controller owns the desktop tools. This protects against conflicting actions through this API, not against another unrelated application or server.",
    "radiator_support_ui": "Serves the radiator support HTML page. Rendering or refreshing this page is not an instruction to start a calculation.",
    "favicon": "Returns the application's icon asset to the browser.",
    "generate_radiator_support": "Validates the manual generation request and returns generated geometry and its summary. This keeps the senior application's manual generation route available.",
    "to_tekla": "Converts the manual Tekla request into the integration payload and calls the modeler. Actual adapter exceptions become HTTP errors rather than fabricated success.",
    "to_staad": "Converts the manual STAAD request into the analytical payload and awaits the live STAAD integration. It returns the actual analysis/result dictionary.",
    "to_ideastatica": "Passes a manual connection request to the IDEA adapter, excluding absent optional fields. It preserves the manual integration route alongside automation.",
    "start_api_server": "Builds the API application and starts Uvicorn using configured host and port. Development defaults bind to loopback.",
    "choose_correction": "Asks the optional GPT advisor to select only one supplied candidate ID, validates its structured response, and returns the corresponding existing candidate. It caps model calls, records an audit event, and falls back to the deterministic default on refusal, invalid output or service failure.",
    "section_exists": "Reads the installed European STAAD section databases in read-only mode and checks the translated section name. Unsupported countries or unavailable catalogue entries return False; a guessed section is not accepted.",
    "frame_options": "Filters the larger entries in the configured column, beam or brace ladder against STAAD availability and positive Tekla dimensions. It returns named candidates for the correction selector; availability is not an independent engineering approval.",
    "preview_joint_groups": "Builds a temporary node/member connectivity dictionary from generated analytical members, then uses the same grouping logic as STAAD. It plans connection families without calling model-changing OpenSTAAD methods.",
    "template_coverage": "Compares previewed joint-family names with available IDEA filenames and returns required, available and missing families plus the preview. Reference-scope planning subsequently distinguishes calculated families from standard details.",
    "workflow_mode": "Reads and validates the workflow mode. Only poc1_automation, poc2_automation and reviewed_engineering are accepted.",
    "load_environment": "Reads simple KEY=value settings from the repository .env without evaluating shell commands or interpolation. Existing process environment values win; credentials are not copied into workflow state.",
    "_integer": "Parses an integer setting and enforces a minimum. Invalid revision, timeout or call-limit values fail configuration rather than silently becoming usable defaults.",
    "settings": "Returns an immutable Settings instance containing the configuration actually consumed by this application. A setting present in .env is not necessarily implemented unless code reads it.",
    "frame_signature": "Serializes the current grid, loads, physical members and analytical members in a stable order and computes SHA-256. A6 compares this signature with the one attached to the STAAD result.",
    "RunController.__init__": "Initializes the active run ID, owned worker handle and cancellation flag. One controller instance tracks one worker at a time.",
    "RunController._terminate": "Stops only the owned worker process tree and awaits its exit. On Windows it invokes taskkill for that worker; an external IDEA REST service is not necessarily part of this tree.",
    "RunController.cancel": "Checks that the requested run belongs to this controller, marks cancellation and terminates its worker. Cancellation during process startup is also handled.",
    "RunController.execute": "Persists initial input, starts the disposable STA worker, captures its log, polls durable progress and enforces time budgets. It records worker crashes, cancellations and timeouts as terminal outcomes, and supports a prepared publication-only retry.",
    "get_next_profile": "Returns the next configured profile for a recognized member category. Unknown profiles and ladder ceilings do not justify inventing a new section.",
    "get_next_plate_thickness": "Returns the next configured plate thickness in millimetres, or the ceiling. The caller must check that a real increase occurred.",
    "get_next_bolt_diameter": "Returns the next configured bolt diameter in millimetres. It changes only a proposed parameter; IDEA must still rerun and pass.",
    "get_next_weld_size": "Returns the next configured weld size in millimetres. This is a bounded candidate generator, not a weld-capacity calculation.",
    "step_parameter_expression": "Steps a finite positive numeric expression through the appropriate sizing ladder while preserving its metre/mm convention. Formula strings, invalid values and ceiling values are not arbitrarily rewritten.",
    "reference_combinations": "Creates the senior procedure's exact eight combinations and three envelope IDs. The original opposing-direction factors are preserved only as the declared reference procedure, not relabeled as independently approved project criteria.",
    "validate_combinations": "Checks required keys, unique names, known load cases, finite nonzero factors and both serviceability/strength coverage. Custom combinations reject opposing wind and temperature cases; the exact declared reference set has an explicit compatibility exception.",
    "resolve_combinations": "Resolves combinations from the run payload, then the configured JSON file, then automation-mode reference defaults. Reviewed mode requires explicit input; the resolved combinations are validated before model changes.",
    "_sync_state": "Saves graph progress to the durable store and updates the server cache when available. Its wrapper catches synchronization exceptions; persistence is not a distributed transaction with the external engineering applications.",
    "_start_agent": "Sets the current agent and RUNNING message, then synchronizes progress before a node begins its work.",
    "route_after_a4": "Ends on failure, returns to A3 for a permitted frame revision, or advances a passing frame to A5. Routing uses explicit stage status rather than trusting a progress label.",
    "route_after_a5": "Ends on terminal connection failure, reruns A5 after an actual parameter revision, or advances a passing scoped connection package to A6.",
    "route_after_a6": "Routes a bounded validation recovery to A3 or A5, advances a valid package to A7, or ends a failed audit.",
    "build_workflow_graph": "Registers the eight LangGraph nodes, sequential edges and conditional correction/recovery edges. Failed early stages terminate; the workflow does not continue merely to color every stage green.",
    "run_agentic_workflow": "Creates typed initial state with configured limits, compiles the graph and invokes it with a bounded recursion setting. On graph exceptions it records failure against the latest saved stage.",
    "prepare_publication_retry": "Requires a failed A7 run with a validated calculation package, deep-copies it into a new run, records lineage and clears old publication/acceptance data. It cannot be used to skip a failed A4 or A5.",
    "run_publication_retry": "Runs A6 again against retained source evidence, stops if evidence changed or failed, and only then reruns A7 and A8. It avoids repeating unchanged FEA solely to repair a Tekla handoff.",
    "build_connection_scope": "Constructs the explicit split between required IDEA families and standard-detail families. Automation follows the senior procedure; reviewed mode requires all families. It records component presets and the reference material-catalogue mapping.",
    "scope_for_state": "Audits exact, nonoverlapping family coverage and rejects stale plans, dropped required inner calculations or altered reference presets/material mappings. A legacy state with no declared plan retains full coverage.",
    "template_for_group": "Resolves an exact filename or the explicit inner double-brace alias in reference mode. It does not choose an unrelated template by similarity.",
    "start_agentic_run": "Acquires the API's run lock, assigns a new run ID and waits for the managed worker's final state. Concurrent runs through this server receive HTTP 409.",
    "get_active_run": "Returns the durable snapshot of the controller's current run, or null when idle. Browser progress polling uses this read-only endpoint.",
    "retry_publication": "Looks up a failed source run, prepares an eligible publication retry and executes it under the same tool lock. It preserves the failed source and returns the new tracked run.",
    "cancel_run": "Delegates a stop request to the controller and rejects requests for runs it does not own. The eventual durable state records the cancellation outcome.",
    "list_recent_runs": "Returns recent durable run metadata for history and page-refresh restoration.",
    "get_run_status": "Loads one saved run and serializes it for the browser. Missing run IDs return HTTP 404; integer result keys survive JSON conversion.",
    "accept_run": "Loads a package, invokes the final acceptance guard, persists its accepted state and returns it. It does not make failed or cancelled runs acceptable.",
    "fail_workflow": "Sets FAILED, the current agent and an explanatory error, updates the stage status and appends a timestamped log. It keeps ended failures from appearing to run forever.",
    "monotonic_now_iso": "Produces strictly increasing UTC timestamps for saved run ordering, including rapid writes in the same process. This is a storage-order helper, not an engineering clock.",
    "SqliteWorkflowStore.__init__": "Creates the database directory, synchronization lock/cache and required SQLite tables/indexes.",
    "SqliteWorkflowStore._connection": "Provides a context-managed SQLite connection with named rows and a lock-wait timeout, closing it afterward.",
    "SqliteWorkflowStore._init_db": "Creates workflow_runs, workflow_events and the status index if absent. This implementation uses local SQLite, not a PostgreSQL migration framework.",
    "SqliteWorkflowStore.save_state": "Upserts a complete serialized state snapshot and commits it atomically in SQLite. It preserves the original created_at on updates and advances updated_at.",
    "SqliteWorkflowStore.get_state": "Reads state from SQLite on every call so another process's changes are visible. It then refreshes the in-process cache and validates the stored model.",
    "SqliteWorkflowStore.list_runs": "Returns run metadata ordered by most recent update, bounded by the requested limit.",
    "SqliteWorkflowStore.record_log": "Inserts a structured event row and commits it. Snapshot logs and event rows should not be confused with a LangGraph checkpointer.",
    "load_json": "Reads a UTF-8 JSON file for the command-line handoff builder and returns its decoded data.",
    "load_operation_parameters": "Accepts a parameter list or an object containing operation_parameters and checks each entry has key and expression.",
    "build_design_input": "Builds the senior-style manual IDEA payload for one explicit envelope and family, using actual STAAD loads and properties. It rejects missing families and empty effects.",
    "_warn": "Writes a command-line handoff-builder diagnostic to stderr. These legacy warnings are not the automated A6 acceptance policy.",
    "build_joint_groups": "Associates supplied IDEA output files with STAAD joint groups and Tekla member names. This manual CLI utility warns about failed outputs; automated A6 is the stricter publication gate.",
    "searchDictByKeyStart": "Returns the value of the first dictionary entry whose key starts with the requested prefix, or None if no entry matches. It does not return a filtered dictionary.",
    "get_ubc97_ce": "Interpolates the existing UBC97 exposure coefficient table by height, with explicit low/high-height handling and invalid-exposure rejection. Its table is a project/reference input, not a newly derived standard.",
    "calculate_wind_pressure_profile": "Calculates the reference pressure profile from exposure, speed, frame coefficient and importance factor, returning heights and pressures. The PDF explains the implementation rather than independently certifying the load model.",
    "tekla2staad": "Normalizes profile/material strings and translates them to STAAD conventions. The assignment path separately handles European cold-formed tables and verifies actual section/material read-back.",
    "tekla2ideastatica_profile": "Converts CFRHS dimensions to IDEA SHS/RHS catalogue syntax, such as CFRHS120X120X5 to SHS120/120/5.0. Other cleaned profile names pass through unchanged.",
    "country2code": "Maps a country label to the configured STAAD section-table code, returning an invalid sentinel for an unknown label.",
    "_SequentialIdAllocator.__init__": "Initializes a deterministic noncolliding node/member ID sequence for newly generated STAAD entities.",
    "_SequentialIdAllocator.next": "Returns the current entity ID and advances the sequence.",
    "_get_openstaad": "Connects to the running OpenSTAAD analytical session and enables silent mode. Connection problems propagate as an adapter error.",
    "_format_com_error": "Formats COM exceptions into an actionable diagnostic without pretending a failed call succeeded.",
    "_node_key": "Rounds coordinates into a consistent node-cache key so coincident beam endpoints can share one analytical node.",
    "_get_or_create_node": "Reuses a cached STAAD node or creates a new one with the allocator, converting generated millimetres to metres for the model API.",
    "_create_beam": "Creates one member in the relevant live application. In STAAD it resolves shared nodes and creates the analytical beam; in Tekla it sets physical endpoints, profile, material, class and positioning before insertion.",
    "_resolve_profile_name": "Looks up the configured STAAD profile-name translation instead of synthesizing an arbitrary replacement.",
    "_assign_property": "Creates/reuses a STAAD section property in the proper table, assigns it and its material, and reads both identities back. An SDK boolean alone is not accepted as evidence of assignment.",
    "_create_combo_load_case": "Creates one STAAD combination and adds each nonzero primary-load factor. It advances the combination ID used by the caller.",
    "_verify_open_model": "Waits until STAAD reports the requested path and zero geometry counts. It proves the cleanup staging/original model actually opened before proceeding.",
    "prepare_saved_analysis": "Saves the generated STD, backs it up if correction is needed, inserts PerformAnalysis(0), saves again and checks serialized command order. It refuses to solve if the command is still absent.",
    "clean_staad": "Saves and backs up the current analytical STD, switches through an empty staging model, replaces/reopens the original path and verifies its saved tokens. It rejects physical models and active analysis; cleanup is not a rollback transaction.",
    "prepare_analysis_input": "Purely examines serialized command order and returns an idempotently prepared string with PERFORM ANALYSIS before DEFINE ENVELOPE. Ambiguous ordering or missing envelopes raise a validation error.",
    "analysis_errors": "Extracts solver ERROR lines and wrapped explanations from an ANL report so the user sees the root solver error instead of only missing downstream outputs.",
    "_get_connected_model": "Connects to Tekla's running model and verifies connectivity. The model must already be open and saved for the live workflow.",
    "_copy_missing_connection_attributes": "Copies missing reference attribute assets into the active model's attributes folder. Existing destination files are left untouched; this is not a silent overwrite of model-specific settings.",
    "_copy_missing_tekla_package_dbs": "Copies missing packaged Tekla database assets needed by the standard model. Existing files are preserved.",
    "delete_all_grids": "Legacy manual utility that explicitly deletes model grids. The automated cleanup path does not call it because it preserves grids.",
    "create_grid": "Creates a Tekla grid with coordinate, label, extension and origin settings. This is an explicit model-changing helper.",
    "modify_grid": "Updates existing grid coordinates/labels or delegates to the shared grid helper. A7 derives levels from the actual generated geometry instead of fixed sample heights.",
    "to_tekla_profile": "Normalizes profile strings to the installed Tekla naming convention before member creation and verification.",
    "_get_profile_parameter": "Reads a named property from the Tekla profile catalogue for geometric calculations.",
    "get_profile_height": "Returns the profile height from the installed Tekla catalogue. A7's wrapper requires a finite positive result rather than guessing.",
    "get_profile_width": "Returns the profile width from the installed Tekla catalogue; A7 validates that it is finite and positive.",
    "get_profile_web_thickness": "Reads the profile web/wall thickness used by the reference connection-geometry calculations.",
    "_index_to_letters": "Converts a positive grid index to spreadsheet-style labels, including rollover beyond Z.",
    "generate_y_axis_labels": "Creates the transverse grid-label sequence for the configured number of bays.",
    "get_bolt_parameters": "Returns the existing bolt-hole tolerance, hole diameter and edge-distance rule for the selected diameter. It supports reference detailing geometry; it does not replace IDEA bolt checks.",
    "clean_tekla": "Enumerates the active model, preserves grids/grid surfaces and DefaultGroup, removes components before remaining parts, verifies cleanup and saves. Optional callbacks report long scans/deletions; failures propagate.",
    "snapshot": "Nested Tekla cleanup helper that snapshots deletable objects and preserved grid/default-group IDs, sorting components ahead of their generated parts.",
    "memberData2type": "Classifies a member-data record for load/design/grouping logic.",
    "memberName2type": "Classifies generated member names, including split beam and brace names, into consistent member categories.",
    "_get_top_node_coordinates": "Selects the coordinate records associated with the model's top-node IDs.",
    "_get_boundary_nodes": "Classifies top nodes by boundary count for corner/edge/inner load distribution.",
    "_get_corner_nodes": "Returns top nodes at plan corners using the shared boundary classifier.",
    "_get_edge_nodes": "Returns noncorner top nodes on the plan perimeter for load distribution.",
    "_get_inner_nodes": "Returns top nodes inside the plan boundary for load distribution.",
    "_get_edge_members": "Finds perimeter members from geometry/top-node information for the reference loading rules.",
    "_apply_distributed_nodal_load": "Converts input mass to force where required and distributes nodal load among corner, edge and inner nodes according to the existing reference method.",
    "_apply_distributed_seismic_joint_weight": "Adds distributed seismic joint weight through the reference nodal-distribution method.",
    "_apply_radiator_installation_load": "Applies the radiator installation mass/load using the corresponding reference distribution, keeping it separate from unrelated load types.",
    "_apply_radiator_wind_nodal_loads": "Computes equipment wind force and its nodal effects from geometry, exposure and direction. Bay/radiator/fan dimensions are converted to metres before areas and moment arms are formed.",
    "_create_directional_wind_cases": "Creates directional frame-wind cases from the model extents, wind definition and open/closed structure setting.",
    "create_design_parameters": "Creates code-design parameters and assigns effective/unrestrained lengths to the appropriate member categories from grid geometry.",
    "_apply_load_configuration": "Builds the reference primary loads, combinations, envelopes, strength load list and design parameters. It returns application/error metadata; failed load application cannot be treated as verified results.",
    "is_node_on_beams": "Tests whether a node is an endpoint of any member in the supplied beam set.",
    "add_to_group": "Adds a node's connected member/end records to a named joint family, preserving Tekla identities and member roles.",
    "find_num_bracings": "Counts longitudinal and transverse braces meeting a node to distinguish joint topologies.",
    "group_name_suffix_from_bracings": "Builds the family-name suffix from longitudinal/transverse brace counts.",
    "_create_joint_groups": "Classifies supports and top joints into corner, edge and inner families with their brace counts. A3 planning and A4 result extraction share this topology logic.",
    "summarize_forces_displacements": "Summarizes component minima/maxima and the corresponding entity IDs from a result dictionary.",
    "find_connected_members_forces": "Collects the force vector at each member end attached to one node, not at the remote end.",
    "find_joint_group_governing_forces": "Finds component extremes across load cases and joints while retaining governing case, node, member and end. Current selection includes columns and all six force/moment components.",
    "joint_load_effect": "Builds one coherent IDEA effect from all members at one joint in one case. Through beams use opposite positions; distinct braces receive distinct names matching template slots.",
    "build_joint_group_load_effects": "Deduplicates governing case/node pairs and creates their complete joint load effects. This is component-extreme screening, not a mathematical proof of every nonlinear multiaxial interaction.",
    "find_extreme_forces": "Updates an envelope's force extrema and attached governing case/node/member evidence.",
    "find_extreme_member_displacements": "Updates extreme member displacement records and governing evidence.",
    "find_extreme_node_displacements": "Updates extreme nodal displacement records used by the serviceability summary.",
    "find_extreme_support_reactions": "Updates extreme support-reaction records without substituting arbitrary sample reactions.",
    "initialize_summary_results": "Creates the nested summary structure for forces, reactions and displacements before scanning actual results.",
    "initialize_forces_summary_results": "Initializes per-component force-extreme tracking for the requested categories.",
    "compute_summary_results": "Applies the extrema helpers to one load case and updates the combined summary dictionary.",
    "send_to_staad": "A4's thin adapter delegates to the live implementation; the radiator implementation resets/builds STAAD, assigns verified properties/loads, runs unattended analysis and extracts forces, design ratios and normalized displacements. Live errors are not replaced by simulation.",
    "_lookup_profile_param": "Safely probes a profile-parameter getter for geometry generation, returning zero when the lookup fails.",
    "_fallback_dimension": "Provides legacy dimension lookup/parsing for generation helpers. The live A3 path separately validates installed catalogue dimensions before generation, so a fallback alone does not establish live readiness.",
    "_lookup_profile_height": "Looks up Tekla height and retains the legacy generation-only fallback if lookup is unavailable. Live catalogue validation remains a separate prerequisite.",
    "_lookup_profile_width": "Looks up Tekla width with the legacy generation helper fallback; it is not a substitute for live catalogue verification.",
    "generate_members": "Generates physical CAD members, split analytical members and a summary from the same grid. It accounts for section dimensions, offsets and mirrored bracing while preserving cross-tool names.",
    "point": "Nested generator helper that produces a grid-space physical point from bay indices and elevation.",
    "transform_to_staad_co_ordinates": "Converts a generated physical point to the analytical STAAD coordinate convention, maintaining the explicit difference between CAD and analysis axes.",
    "check_geometry": "Rejects missing, invalid, nonfinite, zero-length, duplicate or disconnected analytical geometry before sending it to the desktop solver.",
    "validate_profile_catalogue": "Requires positive dimensions from the actual installed Tekla catalogue for all selected profiles before a live frame is generated.",
    "run_a1_requirements": "Validates the input grid, resolves declared load combinations, initializes active profiles and records the workflow/reference basis. Invalid input becomes an A1 failure before model modification.",
    "run_a2_readiness": "Checks live enablement, a saved idle analytical STAAD model, IDEA service/SDK product-release compatibility, a saved Tekla model and template availability. It records actual readiness instead of assuming that an open window means an API is ready.",
    "run_a3_frame_generator": "Applies active correction profiles to the grid, validates catalogues and geometry, generates the frame and declares its connection plan. It increments design generation and rejects missing templates required by that plan.",
    "grade_staad_results": "First rejects invalid/incomplete live evidence, then checks all returned member-type ratios and serviceability drift. It returns a boolean grade plus an allowed correction target only for a supported engineering failure.",
    "run_a4_staad_analyzer": "Clears downstream evidence, analyzes the current frame and grades it. A permitted failed frame selects a larger catalogue candidate, records an actual profile change and routes through A3 again; unrecoverable or invalid evidence stops.",
    "_to_jsonable": "Converts SDK enums/models and nested objects to ordinary JSON-compatible data for exported evidence and API handling.",
    "_get_client": "Attaches to the already-running IDEA Connection REST API service. It does not silently start a substitute offline solver.",
    "_resolve_connection": "Selects the intended connection by ID, index or unique name. Missing and ambiguous matches are errors.",
    "_resolve_member": "Resolves an exact or unique member-name match and rejects ambiguous/missing members instead of dropping their loads or properties.",
    "_section_update_member": "Handles the senior transverse-placeholder name explicitly when the template contains only the actual aligned transverse beam. Subsequent verification permits a no-op only when its section/material already match.",
    "_free_end_position": "Infers a member's free end from connected_by when an effect does not specify a position. Missing connection-end information fails rather than guessing.",
    "_resolve_cross_section_id": "Reuses or adds a section only when both section name and embedded material name match the requested catalogue identity. Added material/section evidence is checked before its ID is accepted.",
    "matching": "Nested section/material matcher that normalizes whitespace/case and examines the material embedded in each IDEA cross-section record.",
    "_list_operations": "Returns the selected connection's IDEA operations for diagnostics and supported parameter inspection.",
    "_list_operations_properties": "Returns common operation properties from the SDK for connection inspection.",
    "_get_parameters": "Reads exposed IDEA parameters, optionally including hidden fields for diagnostics. Corrections may only update supported exposed keys.",
    "_find_parameters": "Filters IDEA parameters by key/description text for diagnostic discovery.",
    "_quote_expression_if_needed": "Leaves numeric or already quoted expressions intact and quotes textual expressions for IDEA's expression interface.",
    "_set_parameters": "Sends typed SDK parameter-update objects and propagates failures, rather than recording a proposed correction as though it had been applied.",
    "to_json_convention_string": "Formats SDK-derived data as JSON for inspection through the shared converter.",
    "_validate_template_limits": "Checks the template's enabled local-deformation check and physical plastic-strain/deformation limits before summary utilization is graded.",
    "design_joint": "Opens the exact template; checks topology, sections/materials and brace angles; replaces template loads with actual effects; applies/reads back parameters; validates physical settings; calculates and exports a saved project and checks. Every integration failure remains visible.",
    "progress": "Nested adapter callback that prints the current IDEA/Tekla phase and reports it to the worker when supplied. It reports activity, not engineering acceptance.",
    "grade_joint_check_summary": "Interprets IDEA check_value as utilization percent, checks finite values and SDK statuses, and identifies a permitted size category for a numeric exceedance. A false SDK status within the numeric limit is unresolved evidence, not permission for a blind retry.",
    "_connection_payload": "Builds one scoped IDEA request from all strength envelopes, explicit template aliases and the declared reference material mapping. It refuses global-force input, empty loads, inconsistent properties and missing templates.",
    "_connection_signature": "Hashes the complete calculational payload, design generation and template content so only unchanged passing connection evidence can be reused.",
    "_correct_connection_parameters": "Steps only exposed numeric parameters that match the failing size category and returns both proposed overrides and an explicit old/new change list.",
    "run_a5_ideastatica_analyzer": "Calculates required IDEA families, reuses only matching passing evidence, grades actual checks and runs bounded per-family detailing corrections. No-op changes, repeated unchanged checks, unsupported parameters and exhausted retries stop the run.",
    "update_progress": "Nested A5/A7 callback that saves phase/status messages for UI polling and the watchdog. A5 declares a bounded multi-effect calculation budget when it starts FEA.",
    "run_a6_package_validator": "Independently regrades source evidence, checks frame/connection signatures and scope, then builds the evaluated Tekla handoff and package hash. Failed evidence triggers bounded recovery or terminal failure; cached PASS flags alone are insufficient.",
    "_catalog_dimension": "Requires a finite positive installed Tekla dimension for the modeler's connection-geometry calculations.",
    "connection_attributes": "Maps evaluated IDEA parameter keys to component attributes and converts metres to millimetres. It handles the double-brace transverse D2 namespace and rejects invalid numeric mapped values.",
    "joint_group_columns": "Builds the column-name set for each calculated family so connection parameters reach the relevant physical columns.",
    "joint_group_beam_pairs": "Builds longitudinal/transverse physical beam pairs at the family joints for component-30 parameter assignment.",
    "_component_integer": "Creates an explicit .NET Int32 for integer component attributes. This avoids pythonnet choosing the ref-double overload during read-back.",
    "set_component_attribute": "Selects integer, string or floating-point SetAttribute handling according to the component field. Explicit rejection propagates to the publication result.",
    "_verify_component": "Selects the inserted component again and checks requested attributes using the proper typed GetAttribute overload and numeric/string comparisons.",
    "add_base_connection": "Loads the existing 1004 attribute preset, applies requested overrides, assigns the column/reference point and inserts the detail. It verifies the inserted component rather than assuming Insert or loading succeeded.",
    "add_column2beam_connection": "Loads the chosen standard component preset, assigns primary/secondary members, applies attributes and positioning, inserts it and verifies its persisted attributes.",
    "_stringify": "Safely turns diagnostic property values into displayable strings.",
    "dump_attributes": "Collects .NET object properties and user properties for diagnostic inspection, reporting read errors as diagnostics.",
    "print_attributes": "Prints a structured object's diagnostic attributes; it is not an acceptance checker.",
    "pick_and_print_attributes": "Interactive diagnostic utility for objects selected in Tekla. The automatic graph does not require this manual picker.",
    "send_to_tekla": "Resets the target model, supplies missing reference assets, sets the actual grid, inserts physical members and standard/calculated component attributes, verifies geometry/profile/materials and saves on success. It returns counts and concrete errors; it cannot return a simulated live pass.",
    "connect": "Nested modeler wrapper that records each successfully inserted/verified component ID and reports progress in batches.",
    "run_a7_tekla_modeler": "Requires a passing A6 package, sends only its validated handoff to Tekla and checks member counts, verification counts, simulation flags and errors. Incomplete publication is terminal and described concisely while full diagnostics remain in state.",
    "run_a8_reporting": "Builds the calculation summary, scope, correction history, connection results and source-run lineage. It ends automation at PAUSED_AT_GATE3, awaiting the responsible engineer's acceptance.",
    "accept_gate3": "Requires a validated package already awaiting review with A7 PASSED, then records the named engineer and timestamp and marks COMPLETED. It cannot approve an arbitrary failed run.",
}

for index in range(1, 9):
    DESCRIPTIONS[f"node_a{index}"] = f"LangGraph wrapper for A{index}: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration."

MAIN_DESCRIPTIONS = {
    "src/server.py": "Parses the server port, loads environment configuration and starts the local API. It is the application entry point used by start_server.bat.",
    "src/agentic/worker.py": "Loads a saved run, initializes a Windows STA COM apartment before pythonnet imports, executes the full graph or publication-only recovery, and persists the terminal result.",
    "src/common/build_idea_statica_input.py": "Parses CLI arguments, loads STAAD/parameter JSON, builds one manual IDEA input and writes it to disk.",
    "src/common/build_tekla_input.py": "Parses CLI arguments, loads generated geometry plus selected IDEA outputs and writes the manual Tekla handoff JSON.",
    "src/radiator_support/staad_integration.py": "Standalone adapter diagnostic entry point. It is separate from the managed A1-A8 server route and should not be confused with the normal workflow launcher.",
}

JS_DESCRIPTIONS = {
    "addBraceRow": "Adds an editable bay/mirror row to a brace list; its remove handler deletes that UI row.",
    "readBraceList": "Reads the configured brace rows into bay/mirror input records.",
    "num": "Reads a form control as a floating-point number.",
    "int": "Reads a form control as a base-10 integer.",
    "str": "Reads a form control as text.",
    "buildPayload": "Builds the grid and geometry-generation request from the form. It does not calculate structural acceptance in the browser.",
    "resizeRenderer": "Resizes the Three.js renderer/camera for the current canvas area.",
    "animate": "Schedules rendering and updates the 3D scene controls.",
    "indexToLetters": "Builds spreadsheet-style grid labels, including indices beyond Z.",
    "makeIndexLabel": "Creates a visual grid-index label for the 3D viewer.",
    "toThree": "Transforms stored geometry coordinates into the viewer convention.",
    "clearStructureView": "Removes scene geometry/labels and clears old STAAD selection state, disposing graphics resources.",
    "staadClassColor": "Chooses display colors for STAAD member classes; colors are not design grades.",
    "renderStructure": "Draws physical or returned analytical members, grid indices and support/joint data in the viewer.",
    "renderStaadJoints": "Creates joint markers from returned analytical connectivity.",
    "renderStaadViewSummary": "Shows metadata for the analytical structure currently displayed.",
    "staadHit": "Raycasts pointer coordinates against displayed members/joints for selection.",
    "highlightStaadJoints": "Highlights exactly the requested joint/member IDs and restores others' normal colors.",
    "selectStaadJoint": "Selects a joint and shows its connected-member information.",
    "selectStaadJointGroup": "Highlights all joints/members belonging to the chosen family.",
    "renderSummary": "Displays generated geometry counts and summary fields.",
    "buildLoadConfiguration": "Reads the load form into the backend request, preserving the manual workflow's field names.",
    "isBooleanExpression": "Recognizes boolean-like parameter text so the UI can render an appropriate control.",
    "renderIdeaOperationParameters": "Builds the manual parameter editor for the selected family using stored defaults.",
    "buildOperationParameters": "Reads edited key/expression pairs for a manual IDEA request.",
    "updateAgentSteps": "Updates A1-A8 stage colors and revision badges from saved statuses and actual loop counts.",
    "renderAgenticRunState": "Renders one saved/final state into geometry, software-result panels, statuses and Gate 3. It explicitly distinguishes IDEA-checked families from standard details.",
    "restoreSavedAgenticRun": "Loads an explicit or latest saved run using GET only, follows a currently owned active run, and restores its final display after refresh. It never starts a replacement calculation; orphaned progress does not disable the button forever.",
    "resultElement": "Creates a result-display element with text and optional CSS class.",
    "resultLabel": "Converts a result key to a readable label.",
    "resultValue": "Formats a result value for display.",
    "resultTable": "Builds a table from result records and selected columns.",
    "resultSection": "Creates an expandable result section.",
    "resultData": "Recursively renders structured result data.",
    "openResultPanel": "Opens the requested integration-results drawer/panel.",
    "renderIntegrationStatus": "Displays counts, analysis status and reported integration failures.",
    "memberName2type": "Infers display categories from generated/split member names.",
    "staadLoadCases": "Combines and deduplicates load-case IDs across envelopes while retaining their titles.",
    "renderStaadLoadCases": "Populates the analytical load-case selector.",
    "renderStaadJointGroups": "Populates family selection from the actual response and removes stale choices.",
    "renderIdeaEnvelopes": "Populates the manual IDEA envelope selector from returned analysis data.",
    "staadForceTable": "Displays member force components and units in a table.",
    "renderStaadTooltip": "Shows member metadata, both member ends, selected case forces and support reactions, or explicit missing data.",
    "renderStaadResults": "Renders the actual STAAD result hierarchy and associated selectors.",
    "renderTeklaResults": "Shows Tekla insertion/verification counts and errors.",
    "renderIdeaStaticaResults": "Shows returned IDEA checks and operation parameters for a calculated connection.",
    "initializePaneDividers": "Registers resizable-pane drag behavior for the viewer and results drawer.",
}


def inventory():
    rows, classes, manifest = [], [], {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        def visit(node, prefix=""):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + child.name
                    if isinstance(child, ast.ClassDef):
                        fields = [(field.target.id, ast.unparse(field.annotation))
                                  for field in child.body if isinstance(field, ast.AnnAssign)
                                  and isinstance(field.target, ast.Name)]
                        classes.append({"file": relative, "name": name, "line": child.lineno, "fields": fields,
                                        "bases": [ast.unparse(base) for base in child.bases]})
                    else:
                        description = DESCRIPTIONS.get(name) or DESCRIPTIONS.get(child.name)
                        if child.name == "main":
                            description = MAIN_DESCRIPTIONS.get(relative)
                        if not description:
                            raise ValueError(f"Missing curated function explanation: {relative}:{name}")
                        rows.append({"file": relative, "name": name, "line": child.lineno,
                                     "end_line": child.end_lineno, "signature": f"{child.name}({ast.unparse(child.args)})",
                                     "returns": ast.unparse(child.returns) if child.returns else None,
                                     "async": isinstance(child, ast.AsyncFunctionDef), "purpose": description,
                                     "calls": list(dict.fromkeys(ast.unparse(call.func) for call in ast.walk(child)
                                                                 if isinstance(call, ast.Call)))})
                    visit(child, name + ".")
                else:
                    visit(child, prefix)
        visit(tree)
    js = []
    for path in [ROOT / "src/static/radiator_support/index.html", ROOT / "src/static/radiator_support/results.js"]:
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        for match in re.finditer(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", source):
            name = match.group(1)
            if name not in JS_DESCRIPTIONS:
                raise ValueError(f"Missing frontend explanation: {name}")
            js.append({"file": relative, "name": name, "line": source[:match.start()].count("\n") + 1,
                       "signature": f"{name}({match.group(2)})", "purpose": JS_DESCRIPTIONS[name]})
    tests = []
    default_js = ROOT / "src/static/radiator_support/idea_statica_defaults.js"
    manifest[default_js.relative_to(ROOT).as_posix()] = hashlib.sha256(default_js.read_bytes()).hexdigest()
    for path in sorted((ROOT / "tests").glob("test*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        cases = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith("test_")]
        tests.append({"file": path.relative_to(ROOT).as_posix(), "cases": cases})
    return rows, js, classes, tests, manifest


def font_setup():
    directory = Path("C:/Windows/Fonts")
    for name, filename in (("Body", "arial.ttf"), ("BodyBold", "arialbd.ttf"), ("BodyItalic", "ariali.ttf"), ("Mono", "consola.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(directory / filename)))
    pdfmetrics.registerFontFamily("Body", normal="Body", bold="BodyBold", italic="BodyItalic", boldItalic="BodyBold")


class WorkflowDiagram(Flowable):
    def __init__(self, hybrid=False):
        super().__init__()
        self.width, self.height = 493, (144 if hybrid else 210)
        self.hybrid = hybrid

    def draw(self):
        c = self.canv
        c.setLineWidth(1.2)
        def box(x, y, w, h, title, subtitle, accent=False):
            c.setFillColor(TEAL if accent else PALE)
            c.setStrokeColor(TEAL if accent else colors.HexColor("#CAD8E1"))
            c.roundRect(x, y, w, h, 6, fill=1, stroke=1)
            c.setFillColor(colors.white if accent else NAVY)
            c.setFont("BodyBold", 9)
            c.drawCentredString(x+w/2, y+h-17, title)
            c.setFont("Body", 7.5)
            c.drawCentredString(x+w/2, y+12, subtitle)
        def arrow(x1, y1, x2, y2):
            import math
            c.setStrokeColor(TEAL)
            c.line(x1, y1, x2, y2)
            a = math.atan2(y2-y1, x2-x1)
            for d in (-0.5, 0.5):
                c.line(x2, y2, x2-6*math.cos(a+d), y2-6*math.sin(a+d))
        if self.hybrid:
            nodes = [(0, 78, "Tool evidence", "Numerical results"), (170, 78, "Checker + grader", "Fixed acceptance rules"),
                     (340, 78, "Correction options", "Permitted candidates"), (340, 6, "Optional GPT", "Candidate ID only"),
                     (170, 6, "Deterministic validator", "Identity, bounds, budget"), (0, 6, "Tool rerun + regrade", "Actual new evidence")]
            for x,y,t,s in nodes: box(x,y,151,52,t,s)
            for x in (151,321): arrow(x,104,x+19,104)
            arrow(415,78,415,58);arrow(340,32,321,32);arrow(170,32,151,32);arrow(75,58,75,78)
        else:
            labels = [("A1", "Requirements"), ("A2", "Readiness"), ("A3", "Generate frame"), ("A4", "STAAD + grade"),
                      ("A8", "Report / review"), ("A7", "Tekla publication"), ("A6", "Evidence audit"), ("A5", "IDEA + grade")]
            for index,(title,subtitle) in enumerate(labels):
                col,row=index%4,index//4
                box(col*127,135-row*85,112,55,title,subtitle,title in ("A4","A5"))
            for x in (112,239,366): arrow(x,162,x+15,162)
            arrow(437,135,437,105)
            for x in (381,254,127): arrow(x,77,x-15,77)
            c.setFont("Body", 8);c.setFillColor(TEAL)
            c.drawString(0,25,"Loop A: A4 > A3     Loop B: A5 > A5     Loop C: A6 > A3 / A5")
            c.setFillColor(GREY);c.drawString(0,10,"An invalid result or exhausted correction limit ends the run with an explanation.")


class GuideDoc(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, pagesize=A4, leftMargin=48, rightMargin=48, topMargin=52, bottomMargin=48, **kwargs)
        self.addPageTemplates(PageTemplate(id="guide", frames=[Frame(48,48,A4[0]-96,A4[1]-100,id="body")], onPage=self.decorate))
        self.heading_count = 0

    def beforeDocument(self):
        # TOC passes must generate the same bookmark keys.
        self.heading_count = 0

    def decorate(self, canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setFont("Body", 7.5);canvas.setFillColor(GREY)
        canvas.drawString(48,A4[1]-30,"AGENTIC STEEL WORKFLOW  |  TECHNICAL HANDOVER")
        canvas.setStrokeColor(colors.HexColor("#DCE5EC"));canvas.line(48,37,A4[0]-48,37)
        canvas.drawString(48,24,"Source snapshot: 15 November 2026  |  Reference-scope validation")
        canvas.drawRightString(A4[0]-48,24,str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        level = getattr(flowable, "toc_level", None)
        if level is not None:
            text = flowable.getPlainText()
            key = "heading-" + str(self.heading_count)
            self.heading_count += 1
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text,key,level=level,closed=level>0)
            self.notify("TOCEntry",(level,text,self.page,key))


def build():
    OUT.mkdir(exist_ok=True)
    rows, js, classes, tests, manifest = inventory()
    font_setup()
    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle("GuideBody",fontName="Body",fontSize=9.2,leading=13.2,spaceAfter=7,textColor=NAVY),
        "small": ParagraphStyle("GuideSmall",fontName="Body",fontSize=8,leading=11,spaceAfter=5,textColor=GREY),
        "code": ParagraphStyle("GuideCode",fontName="Mono",fontSize=7.6,leading=10.6,spaceAfter=5,textColor=TEAL,splitLongWords=True),
        "h1": ParagraphStyle("GuideH1",fontName="BodyBold",fontSize=21,leading=26,spaceBefore=12,spaceAfter=15,textColor=NAVY,keepWithNext=True),
        "h2": ParagraphStyle("GuideH2",fontName="BodyBold",fontSize=12.5,leading=16,spaceBefore=13,spaceAfter=7,textColor=TEAL,keepWithNext=True),
        "h3": ParagraphStyle("GuideH3",fontName="BodyBold",fontSize=9.4,leading=13,spaceBefore=8,spaceAfter=4,textColor=NAVY,keepWithNext=True),
        "cell": ParagraphStyle("GuideCell",fontName="Body",fontSize=8.2,leading=11,textColor=NAVY),
        "headcell": ParagraphStyle("GuideHeadCell",fontName="BodyBold",fontSize=8.2,leading=11,textColor=colors.white),
    }
    story, markdown = [], []
    def p(text, kind="body"):
        story.append(Paragraph(escape(text).replace("\n","<br/>"),styles[kind]));markdown.append(text+"\n")
    def heading(text, level=0, page=False):
        if page:story.append(PageBreak())
        element=Paragraph(escape(text),styles["h1" if level==0 else "h2"]);element.toc_level=level
        story.append(element);markdown.append(("#"*(level+1))+" "+text+"\n")
    def bullet(text):
        story.append(Paragraph("<b>•</b> "+escape(text),styles["body"]));markdown.append("- "+text+"\n")
    def table(headers, data, widths=None):
        values=[[Paragraph(escape(str(cell)),styles["headcell"]) for cell in headers]]
        values += [[Paragraph(escape(str(cell)).replace("\n","<br/>"),styles["cell"]) for cell in row] for row in data]
        grid=Table(values,colWidths=widths or [(A4[0]-108)/len(headers)]*len(headers),repeatRows=1,hAlign="LEFT")
        grid.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),NAVY),("VALIGN",(0,0),(-1,-1),"TOP"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,PALE]),("LEFTPADDING",(0,0),(-1,-1),7),
            ("RIGHTPADDING",(0,0),(-1,-1),7),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
            ("LINEBELOW",(0,0),(-1,0),0.6,TEAL)]))
        story.extend([grid,Spacer(1,10)])
        markdown.append("| "+" | ".join(headers)+" |\n| "+" | ".join("---" for _ in headers)+" |\n")
        for row in data:markdown.append("| "+" | ".join(str(v).replace("\n"," ").replace("|","/") for v in row)+" |\n")
    def code(text):p(text,"code")
    def source_link(label, url):
        story.append(Paragraph(f'<link href="{escape(url)}" color="#007F82"><u>{escape(label)}</u></link>',styles["small"]))
        markdown.append(f"[{label}]({url})\n")

    story.append(Spacer(1,43))
    p("ENGINEERING AUTOMATION / TECHNICAL HANDBOOK","small")
    story.append(Paragraph("How the agentic<br/>steel workflow<br/>was built",ParagraphStyle("Cover",fontName="BodyBold",fontSize=34,leading=40,textColor=NAVY,spaceAfter=18)))
    p("A1–A8 • Checkers • Graders • Analyzers • Correction loops • Optional GPT • STAAD / IDEA / Tekla")
    story.append(Spacer(1,15));story.append(WorkflowDiagram());story.append(Spacer(1,12))
    p("A source-verified implementation guide, function reference and presentation Q&A.")
    p(f"Prepared {DATE}. Repository: agentic-flow. Branch: agentic-workflow.")
    p(f"Coverage: {len(rows)} Python functions/methods (including nested helpers) and {len(js)} named frontend functions. Anonymous UI handlers, data models, configuration, tests and operational limits are explained separately.")
    p("Verified outcome: A1–A7 passed and A8 generated the package; final engineer acceptance is pending. The reference procedure checks two IDEA families and uses standard Tekla details for eleven others. This is not a claim of separate IDEA verification for every family.","small")
    story.append(PageBreak())
    story.append(Paragraph("Contents",styles["h1"]))
    toc=TableOfContents();toc.levelStyles=[ParagraphStyle("Toc0",fontName="BodyBold",fontSize=9.4,leading=14,spaceBefore=6),ParagraphStyle("Toc1",fontName="Body",fontSize=8.1,leading=11,leftIndent=13,firstLineIndent=0)]
    story.append(toc)

    heading("1. Explain the project in one minute",page=True)
    p("The application automates the senior engineer's existing radiator-support workflow. An engineer supplies a grid, sections, materials, bracing and load data. Eight sequential LangGraph stages validate that input, generate compatible geometry, run STAAD and scoped IDEA calculations, audit the evidence, publish the Tekla model and prepare a review package.")
    p("The main design decision is separation of responsibilities: software adapters obtain numerical evidence; deterministic checkers verify completeness and identity; deterministic graders apply configured limits; bounded correction loops change permitted design variables and require a new calculation. An optional GPT advisor may choose among supplied candidates, but cannot set acceptance limits or declare a design safe.")
    p("A successful automated run means the declared scope passed its implemented checks and the handoff was verified. It does not mean every possible input is solvable, every joint was independently analyzed, or the responsible engineer has signed the package.")
    heading("Terminology you should use accurately",1)
    table(["Term","Meaning in this code"],[
        ("Analyzer","Runs or interprets the real engineering software: STAAD for the frame, IDEA for scoped connections."),
        ("Checker","Checks input validity, topology, units, material/section identity, result completeness, signatures and applied-value read-back."),
        ("Grader","Returns passed/failed evidence against the fixed frame/connection rubric and identifies a permitted correction category."),
        ("Engineering loop","A failure causes a real permitted parameter/profile change, tool rerun and regrading; repeated execution alone is not a correction."),
        ("Agent","A named state-processing responsibility in LangGraph. Agents here are functions, not eight independently reasoning GPT sessions."),
        ("Deep agentic workflow","An informal description of multi-stage stateful orchestration and feedback. The repository does not import the DeepAgents framework."),
        ("Acceptance","A8 prepares the report; accept_gate3 records final engineer sign-off separately.")],[105,382])

    heading("2. Architecture and source map",page=True)
    story.append(WorkflowDiagram())
    p("The graph is deliberately sequential because the workflow modifies shared desktop models. A separate worker process owns COM/.NET integration calls while the FastAPI process remains available to serve progress and cancellation requests. The graph is not a parallel swarm.")
    table(["Layer","Key files","Responsibility"],[
        ("UI","src/static/radiator_support/index.html; results.js","Input form, viewer, progress, result panels and Gate 3."),
        ("API","src/server.py; src/agentic/router.py","Manual compatibility routes, agentic routes and in-server ownership guards."),
        ("Worker/runtime","execution.py; worker.py","Disposable process, STA import ordering, logs, stop and time budgets."),
        ("State/control","state.py; store.py; orchestrator.py","Pydantic state, SQLite snapshots and LangGraph routing."),
        ("Engineering stages","src/agentic/agents/a1_... through a8_...","Validation, model generation, analyses, grading, audit, CAD and report."),
        ("Shared evidence/policy","checks.py; evidence.py; catalogue.py; reference_procedure.py; load_combinations.py; ladders.py; advisor.py","Scope, hashes, allowed candidates, existing load procedure and optional constrained GPT."),
        ("Desktop adapters","src/radiator_support/staad_integration.py; src/common/staad_common.py; src/common/tekla_common.py; A5/A7 adapters","Actual OpenSTAAD, IDEA REST and Tekla Open API calls.")],[78,175,234])
    heading("Normal request sequence",1)
    code("Browser POST /api/v2/agentic/run\n  -> router lock -> RunController.execute\n  -> python -m agentic.worker --run-id <id>\n  -> run_agentic_workflow -> LangGraph A1 ... A8\n  -> SQLite snapshots + worker log\nBrowser GET /active or /runs/<id> -> live/saved state")
    p("The database is a durable application store. The compiled LangGraph is not configured with a LangGraph database checkpointer; automatic resume from any arbitrary interrupted node is not implemented. A specific guarded publication retry exists for failed A7 runs.")

    heading("3. What was added or hardened",page=True)
    table(["Feature","What it does","Why it matters"],[
        ("Typed shared state","Keeps inputs, tool results, grades, corrections and audit data together.","A later stage can verify exactly which candidate it is using."),
        ("Integer load-case keys","Allows native integer IDs alongside named result sections.","Prevents Pydantic string-key errors during handoffs."),
        ("Real readiness","Checks saved analytical/CAD models, installed catalogues and IDEA service release.","Open windows alone do not prove usable APIs."),
        ("Real-result failure guards","Reject missing ratios/units/envelopes, simulation and API errors.","Missing results cannot become zero-demand passes."),
        ("Frame/detail feedback","Changes only configured supported profiles or exposed numeric sizes.","Retries have an explicit engineering action and bounded audit trail."),
        ("Reference-scope plan","Separates IDEA families from standard-detail families.","Reproduces the senior workflow without inventing missing baseplate calculations."),
        ("Optional GPT selector","Constrained candidate-ID selection with schema and budget.","Adds diagnosis/selection support without free-form design changes."),
        ("Evidence hashes","Binds frame inputs and connection payload/template content.","Rejects stale cached results after changes."),
        ("Applied-value read-back","Checks actual sections, materials, brace angles, parameters and CAD attributes.","An API call returning is not proof the requested value was retained."),
        ("Worker isolation","Moves desktop calls into an owned disposable process.","The UI/API can remain responsive and stop an owned worker."),
        ("Durable progress","SQLite snapshots and logs record phases and correction history.","Progress and final results survive browser refresh."),
        ("Publication recovery","Re-audits retained results before retrying failed A7.","A CAD interop repair need not repeat unchanged FEA."),
        ("Unattended STAAD","Verifies command order and uses silent AnalyzeEx.","Avoids omitted analysis commands and the manual Done-dialog hang."),
        ("Typed Tekla attributes","Uses explicit Int32 and verifies persisted values.","Prevents pythonnet from selecting the wrong attribute overload."),
        ("Saved result restoration","Uses GET requests to load a saved run or follow an owned active run.","Refreshing the UI does not start another engineering calculation.")],[100,199,188])

    heading("4. A1–A8, step by step",page=True)
    stages = [
        ("A1 — Requirements", "run_a1_requirements", "Receives raw_input. Pydantic validates grid shape, positive sizes, profile/material fields and defaults. resolve_combinations establishes the declared load basis. Active profiles are initialized for future corrections.", "Outputs grid_config, load_config, active profiles and basis metadata. Invalid input stops before desktop model changes."),
        ("A2 — Readiness", "run_a2_readiness", "Checks live engineering enablement; a saved idle analytical STAAD model; IDEA API release matching the SDK's first two version components; a saved Tekla model; and template presence. It records versions and readiness evidence.", "An unavailable tool or unsupported current model stops at A2. There is no production simulation fallback."),
        ("A3 — Frame generation and planning", "run_a3_frame_generator", "Validates installed Tekla dimensions, generates physical members and split analytical members, checks geometry connectivity/finite coordinates, previews families and declares their calculation/standard-detail scope.", "Outputs frame_candidate and connection_scope, increments design_generation, and checks templates required by that plan. A Loop A revision regenerates the frame from changed active profiles."),
        ("A4 — STAAD analyzer and frame grader", "run_a4_staad_analyzer", "Invalidates dependent connection/publication evidence, sends a copy of the candidate to STAAD, extracts actual results and checks member ratios plus serviceability drift. Solver/data errors stop; supported numerical failures may generate a profile revision.", "Outputs staad_results, frame_grader_result and frame signature. A permitted failure records an old/new profile and routes A4 -> A3 -> A4."),
        ("A5 — IDEA analyzer and connection grader", "run_a5_ideastatica_analyzer", "Iterates only declared IDEA families, combines actual strength-envelope effects, resolves the exact template/members/materials, applies and reads back changes, calculates and grades the returned summary. Unchanged passing signatures can be reused.", "Outputs scoped ideastatica_results and joint_grader_results. A permitted detailing correction updates exposed numeric overrides and returns A5 -> A5; unresolved evidence or exhausted limits stops."),
        ("A6 — Independent package audit", "run_a6_package_validator", "Regrades raw source evidence instead of trusting cached booleans. Checks the current frame signature, each required connection signature/template hash and exact scope. It turns evaluated IDEA values into Tekla-ready parameter records.", "Outputs validation_report, package_hash and validated_joint_groups_payload. Failed evidence routes through bounded Loop C or ends the run."),
        ("A7 — Tekla publication", "run_a7_tekla_modeler", "Requires passing A6. Cleans the target while preserving grids, supplies missing reference assets, derives actual grid levels, inserts physical members and reference components, applies evaluated dimensions, verifies persisted identities/attributes and saves.", "Outputs created/verified member and connection counts plus detailed errors. A missing member, rejected attribute or failed save cannot reach a passing publication grade."),
        ("A8 — Report and final acceptance", "run_a8_reporting / accept_gate3", "Assembles profiles, engineering grades, scope, hashes, source lineage and correction histories. It ends the automated calculation/publication flow at the existing engineer-review gate.", "PAUSED_AT_GATE3 means the report is ready, not that an engineer has signed it. accept_gate3 checks eligibility and records the named acceptance separately."),
    ]
    for title, function, detail, outcome in stages:
        heading(title,1);code(function);p(detail);p(outcome)

    heading("5. Checkers and graders: exact decisions",page=True)
    table(["Check","Current implemented decision"],[
        ("STAAD solve","ReturnValue must be 2 or 3; analysis_failed and error counts/messages invalidate evidence. Warnings are retained and must be interpreted."),
        ("Member ratios","Finite, nonnegative design ratios must cover generated member categories. Maximum ratio <= 1.0 is required by the current frame rubric."),
        ("Drift","Translations must be normalized to mm, serviceability envelope evidence must exist, and lateral X/Z displacement must be <= column height / 200."),
        ("Consistency","Reported drift booleans must agree with actual numeric displacements. Missing evidence is not a low-demand result."),
        ("IDEA physical settings","Plastic-strain limit must be no greater than 0.05; local-deformation limit no greater than 0.03; the local-deformation check must be enabled."),
        ("IDEA plate/deformation summaries","check_value is utilization percent relative to the software check; limit 100%, with successful check_status."),
        ("IDEA bolts","The current project grader uses a 75% utilization threshold, even though a software summary may print a 100% software limit."),
        ("IDEA welds","The current grader uses 100% utilization and successful status."),
        ("Unexpected SDK status","A false check_status inside an apparently acceptable numeric limit is unresolved evidence. The code stops instead of blindly increasing a size."),
        ("Tekla evidence","All expected physical members must be created and verified; API errors/simulation/failed attribute read-back invalidate publication."),
        ("Final approval","A failed/cancelled run cannot be signed off. A8 approval requires a previously validated, successfully published package.")],[125,362])
    p("The plate utilization is not raw physical strain. Example: a 50% plate utilization under a 5% strain limit is not a 50% strain result. The physical template limit and summary utilization are checked separately.")
    source_link("Primary reference: IDEA result-summary percentage and status semantics", "https://github.com/idea-statica/ideastatica-public/blob/main/docs/docs/api/connection-api/connection_api_concepts.md")
    p("The workbook's possible 0.9 member target and L/300 checks were not silently substituted for the current 1.0 and H/200 rubric. Different project criteria require reconciliation and validation. See WORKBOOK_REVIEW.md.")

    heading("6. Automatic correction loops",page=True)
    table(["Loop","Trigger and allowed action","Rerun / stop rule"],[
        ("A — frame sizing","A4 has valid results but a supported ratio or drift failure. Select a larger configured section present in both relevant catalogues.","Regenerate A3 and rerun A4; clear downstream evidence. Stop for unsupported profiles, no larger candidate, repeated candidate or exhausted limit."),
        ("B — connection detailing","A5 has a numeric plate/deformation, bolt or weld exceedance that maps to an exposed numeric parameter.","Apply/read back a permitted size increase and rerun A5. Stop for missing parameters, no real size change, unchanged check signature, invalid evidence or per-family retry exhaustion."),
        ("C — evidence recovery","A6 detects missing/failed or stale source evidence.","Route to A5 for connection-related failures, otherwise A3. Recovery is bounded and does not bypass a grader."),
        ("Publication retry","An A7 integration failure occurred after A6 passed.","Create a new tracked run; re-audit A6; rerun A7/A8 only if evidence is unchanged and passing. This is an operational retry, not a new structural correction.")],[80,208,199])
    code("valid tool evidence -> deterministic grade\n  if PASS: next stage\n  elif supported correction and budget remains:\n      choose permitted candidate -> confirm applied change\n      rerun tool -> obtain new evidence -> grade again\n  else: FAILED with reason and saved diagnostics")
    heading("Worked explanation: frame correction",1)
    p("Illustrative example, not the latest live run: column utilization is 1.20. A4 identifies the column target, filters larger catalogue sections and chooses the next permitted candidate. It records the old/new profile, increments loop_a_count and returns REVISING. A3 regenerates the member geometry/metadata; A4 performs a new solve. The previous connection evidence cannot be retained after that frame change.")
    heading("Worked explanation: connection correction",1)
    p("Illustrative example: bolts return 82% against the configured 75% rubric. A5 may increase an exposed numeric diameter parameter from 16 mm to 20 mm, confirm IDEA retained the expression, rerun and grade the new result. It may not reduce loads or change the rubric to obtain a pass. Passing an IDEA correction does not by itself prove every possible changed parameter has an appropriate physical Tekla mapping; that mapping must exist and be validated.")
    heading("What does retry 3 mean?",1)
    p("Separate the initial attempt from revisions. With two permitted revisions, a family can have its initial calculation plus two changed-candidate calculations. Different joint families and multiple load effects are not automatically retries. loop_a_history and loop_b_history identify actual changes; loop_b_count is per family. Class defaults are three frame/connection revisions, but the runner overrides them with settings, and the supplied development configuration used two each.")
    p("Automatic correction is supported and regression-tested. The latest live reference run needed no engineering correction; it therefore does not establish live success for every possible correction branch or future input. No-op and unchanged-check guards prevent meaningless infinite retries, not every possible non-improving candidate.")

    heading("7. Hybrid GPT and deterministic control",page=True)
    story.append(WorkflowDiagram(hybrid=True));story.append(Spacer(1,10))
    p("Most of the workflow is deterministic. The optional advisor is used in A4 for severe/repeated failures and in A5 on a later correction when multiple change candidates exist. The model receives evidence and already constructed candidate options. It returns option_id and rationale under a strict JSON schema.")
    table(["GPT may","GPT may not"],[
        ("Select an existing candidate ID or decline.","Invent a section, alter supports/loads/materials, change acceptance limits or declare a design safe."),
        ("Provide an auditable explanation.","Replace a STAAD/IDEA calculation or independently sign the package."),
        ("Help choose among supplied options.","Execute arbitrary model-generated code or edit the source/configuration from its response.")],[243,244])
    p("The response is validated by Proposal, candidate IDs are checked against the supplied map, and every chosen change still requires numerical rerun and grading. A refusal/invalid response/network error falls back to the deterministic default. A null proposal logs DECLINED and also returns that default. The model-call count is capped.")
    p("Credentials remain backend-only. Requests use the configured endpoint and Azure api-key or standard bearer header as appropriate. The audit stores stage, model, candidate/rationale and sanitized failure metadata; it avoids response bodies, headers and credential text.")
    p("Accurate presentation answer: 'We built a LangGraph stateful engineering workflow with deterministic verification and an optional constrained GPT correction selector.' Do not say 'eight GPT engineers independently prove the design' or claim the DeepAgents package is installed.")

    heading("8. Engineering handoffs and units",page=True)
    table(["Boundary","Implementation and check"],[
        ("Geometry -> STAAD","Generated coordinates are converted from mm to m and transformed into the analytical axis convention. Physical CAD members and split analytical members have different counts."),
        ("Sections","HEA140 maps to STAAD HE140A; European CFRHS sections use the cold-formed section table. Actual section and material identities are read back."),
        ("STAAD -> stored forces","Base-system force conversion was benchmarked locally; the code uses base-unit identity rather than blindly trusting an output-unit label. English force uses 4.4482216152605; moment additionally uses 0.0254."),
        ("Displacements","Translate only displacement components to mm; rotations are not multiplied as lengths. The grader explicitly requires the mm label."),
        ("Equipment wind","Radiator/fan/bay dimensions are converted to metres before area and lever-arm calculations. This fixes the prior mm-to-m mapping error."),
        ("STAAD -> IDEA","The connection payload requires local member forces, actual strength envelopes and coherent case/node effects. kN and kN.m values are multiplied by 1000 for the IDEA N and N.m interface values."),
        ("IDEA parameters -> Tekla","A6 transfers evaluated values, avoiding formula strings. Mapped lengths are converted m -> mm by 1000. The double-brace transverse branch uses D2_CPL2_* keys."),
        ("Typed CAD attributes","Integer attributes such as nb, nw and assembly_type use Int32; spacing-list fields use strings; length values use floating point. GetAttribute reads use the corresponding overload.")],[115,372])
    p("The source contains some historical comments describing tentative SDK behavior or old unit terminology. This guide follows the current executable branches and recorded probes, not an assumption that every old docstring is authoritative.")
    source_link("Primary reference: IDEA SI units (m, N, N.m, rad, Pa)", "https://developer.ideastatica.com/docs/iom/iom_units.html")
    heading("Steel-design parameters are separate from material names",1)
    p("The STAAD adapter maps the requested steel label to its configured material name, commonly STEEL. create_design_parameters separately assigns SGR = 2 for the default EN 1993-1-1:2005 design code. Bentley documents this grade selector as S355. A generic material label alone does not establish the effective design strength, and choosing a different UI steel grade does not automatically derive a different SGR in this implementation.")
    source_link("Primary reference: Bentley EC3 design parameters and SGR", "https://prd-aws-docs.bentley.com/LiveContent/web/STAAD.Pro%20Help-v2024/en/topics/Design_Codes/European_Codes/r-stpst_EC3_EN-1993-1_Design_Parameters.html")
    table(["Parameter assigned in code","Role in the existing procedure"],[
        ("BEAM = 3; NA = 0","Member design-check sampling setting and selected National Annex identifier."),
        ("SGR = 2","Default EC3 design grade selector; it is assigned independently of the API material-name read-back."),
        ("SBLT = 0 / 2","Rolled-beam versus cold-formed column/brace category."),
        ("GM0 = 1; GM1 = 1; GM2 = 1.25","Existing design partial factors; not modified by GPT or correction loops."),
        ("LY / LZ / UNL; KY / KZ","Lengths derived from the member categories/grid and configured column factors."),
        ("TRACK = 2 on columns","Detailed design output setting in the current generator.")],[205,282])
    p("Treat grade/section-code applicability, constitutive material constants and project-specific overrides as engineering inputs requiring review. This guide does not claim a complete cross-tool mechanical-property audit.")
    heading("How governing connection effects are selected",1)
    p("The selector scans each joint's own member end across envelope cases, tracks max/min of six components and deduplicates case/node pairs. Each chosen effect includes the complete connected-member force set for that one real case and node. It does not combine unrelated component maxima into a fictitious load case. Component-extreme screening still does not prove that every possible nonlinear multiaxial interaction is governing; broader checking needs an explicit verification scope.")

    heading("9. Matching the senior workflow",page=True)
    p("The senior application's saved analysis_output_tekla_input.json carries IDEA outputs for two inner families. Its Tekla code creates other connections from standard details. Requiring an IDEA template for every support/edge group was therefore a scope mismatch, not evidence that the manual application had already calculated those missing families.")
    table(["Item","Reference procedure used by automation"],[
        ("Single longitudinal inner family","inner_node_1_longitudinal_bracing.ideaCon"),
        ("Single longitudinal + transverse inner family","inner_node_1_longitudinal_1_transverse_bracing.ideaCon"),
        ("Double longitudinal inner family","Explicit alias: two-longitudinal.ideaCon"),
        ("Double longitudinal + transverse inner family","Explicit alias: two-longitudinal-transverse.ideaCon"),
        ("Base details","Tekla 1004 / baseplate_1004"),
        ("Column-to-beam details","Tekla 144 / column_longitudinal_beam"),
        ("Brace details","Tekla 20 / bracing_column"),
        ("Beam crossing details","Tekla 30 / longitudinal_beam_transverse_beam")],[199,288])
    p("The user explicitly selected the senior scope. The final recorded project has two calculated IDEA families and eleven standard-detail families. Standard details are not inserted into joint_grader_results as artificial IDEA passes. The final report and UI show that distinction.")
    p("The reference mode records S355JR -> S 355 as the static-analysis catalogue mapping inherited from the senior template basis. Tekla retains S355JR. This label mapping should not be presented as a general proof of toughness or every material property; a different material basis requires validation.")
    p("Custom combinations and full reviewed-engineering mode remain distinct. The automation's exact legacy eight-combination compatibility does not turn opposing wind/temperature sums into independently reviewed engineering criteria.")

    heading("10. State, evidence and durability",page=True)
    table(["State group","Main fields and purpose"],[
        ("Identity/progress","run_id, status, current_agent, logs, agent_statuses, error"),
        ("Inputs/readiness","raw_input, grid_config, load_config, readiness, model_call_count, model_events"),
        ("Frame candidate","frame_candidate, active section fields, design_generation, candidate_history"),
        ("Frame verification","staad_results, frame_grader_result, loop_a_count/history"),
        ("Connections","ideastatica_results, joint_grader_results, active_operation_params, per-family loop_b_count/history"),
        ("Package/publication","validation_report, package_hash, loop_c_count/history, tekla_results"),
        ("Final acceptance","final_report, gate3_approved, accepted_by, acceptance_timestamp")],[125,362])
    p("GraderResult carries passed, score, summary, details and optional corrective_action. Routing depends on explicit pass/failure/status evidence; the numerical score is informational, not a universally calibrated safety factor. AgentLogEntry stores agent identity, status, message, timestamp and optional loop information.")
    p("Frame signature = SHA-256 of current grid + loads + physical/analytical members. Connection signature = SHA-256 of its payload + design generation + template content hash. A6 reconstructs these inputs before accepting reuse. Package hash summarizes the validated handoff and scope. Hash equality detects input/content changes; it is not a digital engineer signature or proof of correct physics.")
    p("SqliteWorkflowStore writes full snapshots transactionally and reads the database on each fetch so a parent process can see worker updates. The controller stores worker stdout/stderr under data/worker_logs. Snapshots and external application modifications are not one atomic transaction. A failed integration may leave a partially changed desktop model that requires inspection/cleanup.")

    heading("11. Runtime, UI and recovery",page=True)
    p("worker.py sets sys.coinit_flags = 2 and imports the STAAD helper before Tekla/pythonnet, preventing conflicting COM-apartment initialization. Blocking desktop operations stay inside this worker. The parent watchdog polls saved state and tracks changing stage messages.")
    p("The run has an overall time cap and a stalled-operation cap. A5's multi-effect calculation budget is min(run cap, per-effect allowance x effect count); the HTTP read timeout is bounded similarly. Phase and Tekla batch progress refresh the watchdog's progress timestamp. This is progress reporting, not continuous independent measurement of solver health.")
    p("Stop requests terminate the owned worker tree and persist CANCELLED. They do not universally guarantee that a previously submitted external IDEA REST calculation is cancelled, because that solver may belong to another service process. The server lock also does not control another application that modifies the same desktop model.")
    p("The browser polls /active during a run, restores saved states via /runs on refresh and renders returned numerical results. Restoration uses GET only. If an old state says RUNNING but this server has no corresponding active worker, the UI releases the run button and leaves the saved evidence available for inspection.")
    heading("Guarded A7 recovery",1)
    code("POST /api/v2/agentic/retry-publication/<failed-A7-run>\n  -> require failed A7 + previously valid A6\n  -> clone into new run ID; retain source lineage\n  -> clear old CAD/report/acceptance data\n  -> fresh A6 source revalidation\n  -> A7 publication -> A8 report\n  -> stop if evidence is changed or no longer passing")
    p("A publication retry is an explicit endpoint, not an automatic promise to fix arbitrary CAD failures. It is useful after an integration issue is corrected, as demonstrated by the latest recorded run. It cannot bypass a missing engineering analysis.")

    heading("12. Configuration and operations",page=True)
    table(["Consumed setting","Meaning / code default"],[
        ("PLANT_HOST / PLANT_PORT","Local API bind address and port; 127.0.0.1 / 8080."),
        ("PLANT_WORKFLOW_MODE","poc2_automation default; poc1_automation and reviewed_engineering also validated."),
        ("PLANT_LIVE_ENGINEERING_ENABLED","True by default; false prevents a live run."),
        ("PLANT_RUN_TIMEOUT_SECONDS","3600 default overall cap."),
        ("PLANT_AGENT_TIMEOUT_SECONDS","180 default per-operation/per-effect allowance."),
        ("PLANT_MAX_FRAME_REVISIONS / PLANT_MAX_CONNECTION_REVISIONS","Code defaults 3/3; development configuration supplied for the live run used 2/2."),
        ("PLANT_LLM_ENABLED / PLANT_MODEL_CALL_LIMIT","Optional selector enabled by default when credentials/model exist; call cap 6."),
        ("PLANT_AGENT_MODEL / OPENAI_API_KEY / OPENAI_BASE_URL","Backend model/deployment and credential/endpoint settings. Values of credentials are intentionally absent from this guide."),
        ("PLANT_TEMPLATE_DIRECTORY","Defaults to repository sample_projects."),
        ("PLANT_LOAD_COMBINATIONS_FILE","Optional explicit project JSON override."),
        ("STAAD_SECTION_DATABASE_PATH","Optional section-database directory override."),
        ("TEKLA_BIN_PATH / IDEA_STATICA_BASE_URL","Installed Tekla assemblies / running IDEA service endpoint.")],[232,255])
    p("Important implementation boundary: source inspection found no application wiring for the supplied PLANT_AUTH_ENABLED/tokens, PLANT_DATABASE_URL, PLANT_CHECKPOINT_URL, approval-mode flags or custom LangSmith setup. Adding those names to .env does not implement authentication, PostgreSQL, migrations, automatic engineering sign-off or tracing. SQLite is the implemented store. Do not present this development app as a production-secured deployment.")
    heading("Safe operating sequence",1)
    for text in ["Open saved analytical STAAD and Tekla models intended for automation, and start the compatible IDEA Connection REST API service.","Start the local API with start_server.bat (or the venv Python entry point) and open /radiator_support.","Supply the project form; use Run Agentic Design once. Inspect actual stage messages rather than repeatedly starting concurrent jobs.","After completion, review the declared calculation/standard-detail scope, warnings, grades, model and report before the responsible engineer accepts Gate 3.","For an actual failure, use its worker log and saved error. A missing/unresolved input is not solved by increasing retry limits or bypassing a grade."]:
        bullet(text)

    heading("13. API and data contracts",page=True)
    table(["Method / route","Purpose"],[
        ("GET /radiator_support","UI and query-based saved-run view."),
        ("POST /api/v2/agentic/run","Starts a new managed A1-A8 run using grid + load_configuration."),
        ("GET /api/v2/agentic/active","Current owned run snapshot or null."),
        ("GET /api/v2/agentic/runs","Recent run metadata; optional limit."),
        ("GET /api/v2/agentic/runs/{run_id}","Full saved state and diagnostics."),
        ("POST /api/v2/agentic/cancel/{run_id}","Stops the worker owned by this controller."),
        ("POST /api/v2/agentic/retry-publication/{run_id}","New tracked A7 retry after A6 source revalidation."),
        ("POST /api/v2/agentic/accept/{run_id}","Final eligible engineer acceptance."),
        ("POST /api/v1/radiator_support/generate","Manual generation compatibility route."),
        ("POST /api/v1/radiator_support/to_staad","Manual live STAAD handoff."),
        ("POST /api/v1/radiator_support/to_tekla","Manual live Tekla handoff."),
        ("POST /api/v1/to_ideastatica","Manual live IDEA handoff.")],[245,242])
    code('{"grid": {"n_long_bays": 8, "n_trans_bays": 5, "bay_long_mm": 2750,\n "bay_trans_mm": 1620, "col_height_mm": 5000, "column_profile": "CFRHS120X120X5",\n "beam_profile": "HEA140"}, "load_configuration": {"...": "project load fields"}}')
    p("This compact example explains shape only; it is not a ready-to-analyze complete engineering input. Real load fields, selected bracing, declared combinations and the compatible scope must be supplied. The form and Pydantic schemas provide the actual complete interface.")

    heading("14. Recorded evidence and tests",page=True)
    p("Evidence snapshot: source run run_45715b4c completed A1-A6 using actual STAAD and IDEA calculations. Its first Tekla attempt exposed an integer read-back error. After the fix, run_80c61cca independently revalidated those unchanged calculations, successfully published/saved Tekla and reached A8 with no error.")
    table(["Evidence","Recorded result"],[
        ("Analytical members","402"),("STAAD maximum ratio","0.3234004; drift passed H/200"),
        ("STAAD completion","Zero errors, one warning"),("Combined inner double-brace family","Bolts 21.25%; welds 56.48%; checks passed"),
        ("Longitudinal inner double-brace family","Bolts 23.83%; welds 65.78%; checks passed"),
        ("Tekla physical model","194 members verified; 464 connections verified; zero failures; model saved"),
        ("Final package","a17410b76665; PAUSED_AT_GATE3; final engineer acceptance pending"),
        ("Regression validation","117 Python tests and 12 JavaScript tests recorded passing; inline JS syntax and whitespace checks passed"),
        ("Live correction coverage","No engineering correction was needed in this particular live run. Correction branches have regression tests; not every branch has a live engineering demonstration.")],[195,292])
    p("The live STAAD warning concerns additional UBC/IBC analysis/change sequencing if tension/compression, repeat loads, reanalysis or optimization are used. It was not converted into zero warnings or hidden from the evidence.")
    heading("Independent calibration evidence",1)
    p("The local cantilever probe used a 5 m member with 10 kN tip load. Reaction and bending moment matched approximately 10 kN and 50 kN.m; the measured 15.262557 mm deflection was within the stated 0.1 mm tolerance of 15.243902 mm bending-only theory, with shear deformation explaining the difference. This validates that probe's unit/result handling, not the entire radiator design or every code case.")
    p("Regression tests use mocked adapters to exercise fail/pass routing, revisions and data guards without running destructive desktop operations. Source inspection found the following test inventory; test names document the behavior under test, while WORKFLOW_VALIDATION.md records execution results.")
    table(["Python test module","Cases"],[(item["file"].split("/")[-1],len(item["cases"])) for item in tests],[420,67])
    p("JavaScript tests cover result/viewer handling and saved-run restoration, including the guarantee that refresh uses read-only requests and does not start another calculation.")

    heading("15. Troubleshooting and important limits",page=True)
    table(["Symptom","Meaning / correct response"],[
        ("Pydantic integer-key error","Older state schema required string result keys. Current state allows native integer load-case IDs."),
        ("DEFINE ENVELOPE solver error","Check the actual saved STD. PERFORM ANALYSIS must precede result envelopes; SDK option 6 was silently omitted on the installed build."),
        ("STAAD appears finished but A4 waits","An Analyze() completion dialog can keep the engine status active. The unattended path uses AnalyzeEx."),
        ("A5 stays on one family","A family may have several serial FEA load effects. Read the phase, load count and budget; a stable stage label alone does not prove a hang."),
        ("Missing support/edge IDEA file","Check the declared scope. Standard-detail families are not required IDEA calculations in the user-selected senior procedure; full reviewed mode is different."),
        ("Missing transverse placeholder","Use the verified no-op on the real aligned beam only when section/material match. A different beam requires a compatible template/proxy; do not ignore the mismatch."),
        ("False SDK status inside numeric limit","Treat as unresolved analysis evidence, not a simple size exceedance. Inspect IDEA details instead of blindly repeating parameter increases."),
        ("Tekla assembly_type/nb read-back fails","Use explicit Int32 for integer Set/GetAttribute calls; do not switch off verification."),
        ("Old PASS after input change","Reconstruct frame/connection signatures and invalidate dependent results. Do not edit stored pass flags."),
        ("A7 failed but calculations passed","Correct the handoff issue, then use the guarded publication retry. A6 must revalidate unchanged evidence first."),
        ("A8 is awaiting approval","Automation produced the report. The final engineer acceptance is an explicit remaining gate, not another solver error.")],[164,323])
    for text in ["A passing reference-scope run does not establish all-family IDEA verification. Standard CAD details are a declared different basis.","Component-extreme load selection does not prove exhaustive nonlinear combination coverage. Template geometry, member ends and force conventions remain engineering-sensitive mappings.","Connection revisions are bounded and limited to supported exposed parameters; no-op/repeated-check failures stop. Future profile/detail changes can require new compatible templates or verified Tekla parameter mappings.","The code does not automatically create or approve missing engineering templates, invent foundation details, resolve every solver instability or guarantee that every valid-looking input has a feasible design.","The API is a local development service. Production authentication, authorization, deployment hardening, PostgreSQL migrations, distributed job ownership and complete external-solver cancellation are not established by this implementation.","Local hashes and successful API read-back demonstrate data consistency; they are not independent certification of structural assumptions, standard-detail suitability or construction safety."]:
        bullet(text)

    heading("16. Presentation and interview Q&A",page=True)
    questions = [
        ("What did you automate?","The existing input-to-STAAD-to-selected-IDEA-to-Tekla handoffs, plus input checking, deterministic grading, bounded corrections, evidence auditing and report generation."),
        ("Why eight agents?","They separate requirements, readiness, generation, frame analysis, connection analysis, evidence validation, CAD publication and reporting. Each has a clear state contract and failure boundary."),
        ("Are these eight GPT models?","No. They are LangGraph node functions. GPT is an optional constrained selector used only in certain correction situations."),
        ("Are you using the DeepAgents library?","No such import/dependency is present. Describe this as a LangGraph stateful agentic engineering workflow; 'deep' is an informal project description."),
        ("What is the difference between checker and grader?","A checker asks whether evidence is complete, consistent and correctly applied. A grader asks whether valid evidence meets the configured acceptance limits."),
        ("What actually performs the calculations?","STAAD's solver/design engine evaluates the frame. IDEA's connection solver evaluates declared connection families. Tekla constructs the detailed physical model; GPT does not replace these numerical engines."),
        ("How does a loop differ from a retry?","A correction loop records a real permitted old-to-new profile/parameter change, reruns the tool and grades new evidence. Repeating the same unmodified request is not engineering correction."),
        ("What does the LLM see?","Sanitized stage evidence and a supplied candidate list. Its output is a strict candidate ID and rationale. Backend credentials are used for transport and are not copied into workflow state."),
        ("Can GPT change loads to make a design pass?","No. The advisor contract forbids altering loads, supports, materials or acceptance criteria. The code validates candidate membership and requires tool rerun."),
        ("What happens if GPT fails?","The implementation records sanitized failure metadata and falls back to the deterministic candidate. Call limits and timeouts bound its use."),
        ("Does every failed check get fixed automatically?","Only failures with supported correction mappings, exposed numeric parameters and remaining budget. Unsupported, unstable, incomplete or uncorrectable evidence stops with a reason."),
        ("How do you stop endless loops?","Revision caps, permitted candidate ladders, candidate-history checks, no-op detection and unchanged-check signatures, plus the overall runtime limit."),
        ("Why can you see three attempts?","An initial calculation plus two permitted corrections makes three calculations. Several families or load effects are separate work, not necessarily correction retries."),
        ("Does a bigger section always improve the grade?","No universal monotonic improvement is guaranteed. The new candidate must be rerun and regraded; limits can still end in a reported failure."),
        ("Why were support templates blocking before?","Automation initially required all families while the senior manual handoff calculated only selected inner families and used standard Tekla details elsewhere. The user confirmed that reference scope."),
        ("Did you certify all thirteen families with IDEA?","No. The recorded run separately calculated two required families and declared eleven standard-detail families. The report explicitly preserves that distinction."),
        ("How do you avoid stale results?","A4 clears dependent evidence; frame and connection signatures bind inputs/generation/template content; A6 reconstructs those signatures and independently regrades source results."),
        ("Why separate the API and worker?","Desktop COM/.NET calls can block and require STA initialization. A disposable worker keeps API progress/stop requests responsive and gives the controller an owned process to terminate."),
        ("Why does importing order matter?","STAAD expects a compatible STA COM apartment. Initializing pythonnet/CLR first can select a conflicting apartment; the worker imports the STAAD helper before Tekla."),
        ("How do you know an API change was applied?","Read back section/material identity, brace angle, parameter expressions, and persisted Tekla attributes. Returned booleans or a completed request alone are insufficient."),
        ("What was the STAAD command bug?","PerformAnalysis(6) silently omitted the input command on the installed build. Option 0 was verified to insert it; the saved STD order is checked before solving."),
        ("What was the Tekla integer bug?","pythonnet could choose a ref-double read overload for integer fields. Explicit Int32 made persisted integer attribute checks work correctly."),
        ("Can a browser refresh rerun the design?","The restoration path uses GET requests only and renders saved results or follows the owned active run. It does not POST another calculation."),
        ("Can a failed design be signed off?","The acceptance endpoint rejects failed/cancelled or unvalidated states. It requires a validated package awaiting review with A7 passed."),
        ("What is proven by the live run?","Recorded evidence shows frame analysis, both scoped IDEA grades, package validation and saved Tekla publication reaching A8, including a corrected publication retry. It is not universal verification of all inputs."),
        ("Were correction loops exercised in that live run?","No engineering correction was needed in that run. Regression tests exercise frame and connection correction routing; broader live correction coverage remains work to demonstrate."),
        ("Is SQLite a LangGraph checkpointer?","Not in this code. It is an application snapshot/event store. The graph is compiled without a database checkpointer; only the explicit A7 recovery path provides selective reuse."),
        ("Do .env auth/PostgreSQL flags enable those features?","No. Those supplied names are not wired into the inspected application. The implementation uses SQLite and local API defaults; production controls need separate engineering/software work."),
        ("Did Excel values replace the calculation basis?","No. Workbooks were reviewed statically for useful checklist/reference evidence. Conflicting limits, fixed counts, external links and macros were not blindly adopted."),
        ("What is the most honest final claim?","We automated the declared senior workflow, verified its live tool handoffs and implemented bounded checked correction loops. The final engineer reviews the scope and evidence before acceptance; unsupported cases fail explicitly.")
    ]
    for question,answer in questions:
        markdown.append("### "+question+"\n")
        story.append(Paragraph(escape(question),styles["h3"]));p(answer)

    heading("Appendix A. Every production Python function",page=True)
    p("This catalogue covers every production Python def/async def declaration found by the source AST, including nested helpers and methods. Each entry has a curated purpose plus its exact signature and source line. Inline sorting/filtering/normalization lambdas are explained with their containing function. Identical short names are disambiguated by module/qualified name. Line numbers apply to this snapshot and will move after edits.")
    grouped=defaultdict(list)
    for row in rows:grouped[row["file"]].append(row)
    for module, entries in grouped.items():
        heading(module,1)
        for entry in entries:
            label=f"{entry['name']}  ·  line {entry['line']}" + ("  ·  async" if entry["async"] else "")
            story.append(Paragraph(escape(label),styles["h3"]));markdown.append("### "+label+"\n")
            code(entry["signature"] + (" -> "+entry["returns"] if entry["returns"] else ""))
            p(entry["purpose"])
            calls=[call for call in entry["calls"] if any(token in call for token in ("run_a", "grade_", "signature", "save_state", "send_to_", "PerformAnalysis", "AnalyzeEx", "update_member", "calculate", "SetAttribute", "GetAttribute"))]
            if calls:p("Key calls: "+", ".join(calls[:6]),"small")
    heading("Compatibility modules with no local function definitions",1)
    p("src/common/idea_statica_integration.py re-exports the A5 implementation; src/radiator_support/tekla_integration.py re-exports A7; src/radiator_support/generate_grid.py re-exports A3 generation. These preserve import paths used by the senior/manual workflow. Empty __init__.py files mark packages. The default-parameter JavaScript is data, not an independent engineering solver.")

    heading("Appendix B. Frontend functions and event handlers",page=True)
    p("The following covers every named JavaScript function in the production UI and results renderer. Anonymous event callbacks are grouped afterward by user action, with source locations.")
    grouped_js=defaultdict(list)
    for row in js:grouped_js[row["file"]].append(row)
    for module,entries in grouped_js.items():
        heading(module,1)
        for entry in entries:
            story.append(Paragraph(escape(f"{entry['name']} · line {entry['line']}"),styles["h3"]));code(entry["signature"]);p(entry["purpose"])
    table(["Event / callback group","Behavior"],[
        ("Brace add/remove controls","Edit form rows only; later request building reads bay and mirror values."),
        ("Window resize / animation","Updates the camera and renderer; no engineering acceptance change."),
        ("Pointer down/up/move/cancel/leave","Distinguishes click/drag, selects or highlights joints/members and shows/hides tooltips."),
        ("Joint-group / load-case change","Updates highlight/selection and prevents stale tooltip data."),
        ("Generate Structure button","POSTs the manual generation payload and renders the returned geometry."),
        ("Manual Tekla / STAAD / IDEA buttons","Build the existing manual payloads, call their compatibility routes and render the actual responses."),
        ("Run Agentic Design button","Starts one managed POST, polls /active, renders its final state and resets button/cancel controls."),
        ("Stop button","Calls the cancel endpoint for currentAgenticRunId and reports stop errors without inventing success."),
        ("Gate 3 acceptance button","Submits explicit final acceptance for the currently displayed eligible run."),
        ("Pane divider callbacks","Resize the results/viewer layout; they do not modify the engineering model.")],[184,303])

    heading("Appendix C. Data models and test-case index",page=True)
    p("Classes below are defined in production source. Pydantic models describe accepted request/state shape; plain exception classes identify adapter failures; the settings dataclass stores immutable runtime configuration. A schema declaration is not itself a structural calculation.")
    for item in classes:
        if not item["fields"]:continue
        heading(f"{item['name']} ({item['file']}:{item['line']})",1)
        p("Base types: "+(", ".join(item["bases"]) or "plain Python class"),"small")
        table(["Field","Declared type"],item["fields"],[196,291])
    heading("Python test cases by module",1)
    for item in tests:
        story.append(Paragraph(escape(item["file"]),styles["h3"]))
        for name in item["cases"]:p(name,"code")
    heading("JavaScript test modules",1)
    p("tests/test_staad_results.cjs: viewer categories, envelopes, result labels, tooltips, highlighting, resource disposal and grid labeling. tests/test_agentic_restore.cjs: read-only final restoration, active-run following and orphaned-state button release.")

    heading("Appendix D. Evidence sources and reproducibility",page=True)
    p("The installed project uses the IDEA 26.0/API 4 SDK/service path checked in A2. Online documentation can describe newer API 5 endpoints. Use the installed matching SDK and local service contract rather than copying a different release's routes blindly.")
    source_link("Primary reference: IDEA API version history and service architecture", "https://developer.ideastatica.com/docs/api/connection-api/connection_api_overview.html")
    for file,reason in [
        ("WORKFLOW_VALIDATION.md","Latest live results and historical diagnoses; latest section supersedes earlier scope blockers."),
        ("SENIOR_WORKFLOW_REVIEW.md","Manual/automatic source comparison and the user's scope decision."),
        ("WORKBOOK_REVIEW.md","Read-only spreadsheet review and reasons conflicting inputs were not blindly adopted."),
        ("output_validation/reference-end-to-end-summary.json","Recorded passing package and explicit family scope."),
        ("output_validation/reference-publication-retry.json","Full saved final retry state, including lineage."),
        ("output_validation/reference-full-run.json","Original calculation run and the initial failed A7 diagnostic."),
        ("output_validation/senior-reference-hashes.json","Fingerprints of inspected senior source/input files."),
        ("data/worker_logs/run_45715b4c.log / run_80c61cca.log","Actual analysis and publication progress logs."),
        ("output_validation/cantilever_probe_report.json","Independent local force/moment/displacement calibration evidence.")]:
        code(file);p(reason)
    p("This PDF was created by static source inspection plus recorded artifacts. The documentation build does not execute application functions, contact engineering software, invoke GPT, read credential values or execute workbook macros. It does not rerun the live engineering test.")
    p("Rebuild command: .venv\\Scripts\\python.exe docs\\build_technical_guide.py. Documentation tooling requires reportlab; PDF QA uses the already available PyMuPDF. These are documentation tools, not new application runtime requirements.")
    p("Generated companion files: Agentic_Steel_Workflow_Technical_Guide.md (editable text), technical_guide_inventory.json (all documented symbols/models/tests), and technical_guide_manifest.json (source SHA-256 fingerprints). The complete source fingerprint list stays in the manifest to keep this PDF readable.")
    code("Review rule: implementation fact != live evidence != engineering approval.\nAlways state the declared scope and the limit of the evidence when presenting the project.")

    inventory_data={"date":DATE,"python_functions":rows,"frontend_functions":js,"classes":classes,"tests":tests}
    (OUT/"technical_guide_inventory.json").write_text(json.dumps(inventory_data,indent=2),encoding="utf-8")
    (OUT/"technical_guide_manifest.json").write_text(json.dumps({"date":DATE,"source_sha256":manifest},indent=2),encoding="utf-8")
    (OUT/"Agentic_Steel_Workflow_Technical_Guide.md").write_text("\n".join(markdown),encoding="utf-8")
    document=GuideDoc(str(PDF),title="Agentic Steel Workflow — Technical Guide and Function Reference",author="Project technical handover",subject="Source-verified A1–A8 workflow, checks, grading, loops, integrations and recorded evidence")
    document.multiBuild(story)
    print(json.dumps({"pdf":str(PDF),"python_functions":len(rows),"frontend_functions":len(js),"test_cases":sum(len(item['cases']) for item in tests)},indent=2))


if __name__ == "__main__":
    build()
