ENGINEERING AUTOMATION / TECHNICAL HANDBOOK

A1–A8 • Checkers • Graders • Analyzers • Correction loops • Optional GPT • STAAD / IDEA / Tekla

A source-verified implementation guide, function reference and presentation Q&A.

Prepared 15 November 2026. Repository: agentic-flow. Branch: agentic-workflow.

Coverage: 217 Python functions/methods (including nested helpers) and 47 named frontend functions. Anonymous UI handlers, data models, configuration, tests and operational limits are explained separately.

Verified outcome: A1–A7 passed and A8 generated the package; final engineer acceptance is pending. The reference procedure checks two IDEA families and uses standard Tekla details for eleven others. This is not a claim of separate IDEA verification for every family.

# 1. Explain the project in one minute

The application automates the senior engineer's existing radiator-support workflow. An engineer supplies a grid, sections, materials, bracing and load data. Eight sequential LangGraph stages validate that input, generate compatible geometry, run STAAD and scoped IDEA calculations, audit the evidence, publish the Tekla model and prepare a review package.

The main design decision is separation of responsibilities: software adapters obtain numerical evidence; deterministic checkers verify completeness and identity; deterministic graders apply configured limits; bounded correction loops change permitted design variables and require a new calculation. An optional GPT advisor may choose among supplied candidates, but cannot set acceptance limits or declare a design safe.

A successful automated run means the declared scope passed its implemented checks and the handoff was verified. It does not mean every possible input is solvable, every joint was independently analyzed, or the responsible engineer has signed the package.

## Terminology you should use accurately

| Term | Meaning in this code |
| --- | --- |

| Analyzer | Runs or interprets the real engineering software: STAAD for the frame, IDEA for scoped connections. |

| Checker | Checks input validity, topology, units, material/section identity, result completeness, signatures and applied-value read-back. |

| Grader | Returns passed/failed evidence against the fixed frame/connection rubric and identifies a permitted correction category. |

| Engineering loop | A failure causes a real permitted parameter/profile change, tool rerun and regrading; repeated execution alone is not a correction. |

| Agent | A named state-processing responsibility in LangGraph. Agents here are functions, not eight independently reasoning GPT sessions. |

| Deep agentic workflow | An informal description of multi-stage stateful orchestration and feedback. The repository does not import the DeepAgents framework. |

| Acceptance | A8 prepares the report; accept_gate3 records final engineer sign-off separately. |

# 2. Architecture and source map

The graph is deliberately sequential because the workflow modifies shared desktop models. A separate worker process owns COM/.NET integration calls while the FastAPI process remains available to serve progress and cancellation requests. The graph is not a parallel swarm.

| Layer | Key files | Responsibility |
| --- | --- | --- |

| UI | src/static/radiator_support/index.html; results.js | Input form, viewer, progress, result panels and Gate 3. |

| API | src/server.py; src/agentic/router.py | Manual compatibility routes, agentic routes and in-server ownership guards. |

| Worker/runtime | execution.py; worker.py | Disposable process, STA import ordering, logs, stop and time budgets. |

| State/control | state.py; store.py; orchestrator.py | Pydantic state, SQLite snapshots and LangGraph routing. |

| Engineering stages | src/agentic/agents/a1_... through a8_... | Validation, model generation, analyses, grading, audit, CAD and report. |

| Shared evidence/policy | checks.py; evidence.py; catalogue.py; reference_procedure.py; load_combinations.py; ladders.py; advisor.py | Scope, hashes, allowed candidates, existing load procedure and optional constrained GPT. |

| Desktop adapters | src/radiator_support/staad_integration.py; src/common/staad_common.py; src/common/tekla_common.py; A5/A7 adapters | Actual OpenSTAAD, IDEA REST and Tekla Open API calls. |

## Normal request sequence

Browser POST /api/v2/agentic/run
  -> router lock -> RunController.execute
  -> python -m agentic.worker --run-id <id>
  -> run_agentic_workflow -> LangGraph A1 ... A8
  -> SQLite snapshots + worker log
Browser GET /active or /runs/<id> -> live/saved state

The database is a durable application store. The compiled LangGraph is not configured with a LangGraph database checkpointer; automatic resume from any arbitrary interrupted node is not implemented. A specific guarded publication retry exists for failed A7 runs.

# 3. What was added or hardened

| Feature | What it does | Why it matters |
| --- | --- | --- |

| Typed shared state | Keeps inputs, tool results, grades, corrections and audit data together. | A later stage can verify exactly which candidate it is using. |

| Integer load-case keys | Allows native integer IDs alongside named result sections. | Prevents Pydantic string-key errors during handoffs. |

| Real readiness | Checks saved analytical/CAD models, installed catalogues and IDEA service release. | Open windows alone do not prove usable APIs. |

| Real-result failure guards | Reject missing ratios/units/envelopes, simulation and API errors. | Missing results cannot become zero-demand passes. |

| Frame/detail feedback | Changes only configured supported profiles or exposed numeric sizes. | Retries have an explicit engineering action and bounded audit trail. |

| Reference-scope plan | Separates IDEA families from standard-detail families. | Reproduces the senior workflow without inventing missing baseplate calculations. |

| Optional GPT selector | Constrained candidate-ID selection with schema and budget. | Adds diagnosis/selection support without free-form design changes. |

| Evidence hashes | Binds frame inputs and connection payload/template content. | Rejects stale cached results after changes. |

| Applied-value read-back | Checks actual sections, materials, brace angles, parameters and CAD attributes. | An API call returning is not proof the requested value was retained. |

| Worker isolation | Moves desktop calls into an owned disposable process. | The UI/API can remain responsive and stop an owned worker. |

| Durable progress | SQLite snapshots and logs record phases and correction history. | Progress and final results survive browser refresh. |

| Publication recovery | Re-audits retained results before retrying failed A7. | A CAD interop repair need not repeat unchanged FEA. |

| Unattended STAAD | Verifies command order and uses silent AnalyzeEx. | Avoids omitted analysis commands and the manual Done-dialog hang. |

| Typed Tekla attributes | Uses explicit Int32 and verifies persisted values. | Prevents pythonnet from selecting the wrong attribute overload. |

| Saved result restoration | Uses GET requests to load a saved run or follow an owned active run. | Refreshing the UI does not start another engineering calculation. |

# 4. A1–A8, step by step

## A1 — Requirements

run_a1_requirements

Receives raw_input. Pydantic validates grid shape, positive sizes, profile/material fields and defaults. resolve_combinations establishes the declared load basis. Active profiles are initialized for future corrections.

Outputs grid_config, load_config, active profiles and basis metadata. Invalid input stops before desktop model changes.

## A2 — Readiness

run_a2_readiness

Checks live engineering enablement; a saved idle analytical STAAD model; IDEA API release matching the SDK's first two version components; a saved Tekla model; and template presence. It records versions and readiness evidence.

An unavailable tool or unsupported current model stops at A2. There is no production simulation fallback.

## A3 — Frame generation and planning

run_a3_frame_generator

Validates installed Tekla dimensions, generates physical members and split analytical members, checks geometry connectivity/finite coordinates, previews families and declares their calculation/standard-detail scope.

Outputs frame_candidate and connection_scope, increments design_generation, and checks templates required by that plan. A Loop A revision regenerates the frame from changed active profiles.

## A4 — STAAD analyzer and frame grader

run_a4_staad_analyzer

Invalidates dependent connection/publication evidence, sends a copy of the candidate to STAAD, extracts actual results and checks member ratios plus serviceability drift. Solver/data errors stop; supported numerical failures may generate a profile revision.

Outputs staad_results, frame_grader_result and frame signature. A permitted failure records an old/new profile and routes A4 -> A3 -> A4.

## A5 — IDEA analyzer and connection grader

run_a5_ideastatica_analyzer

Iterates only declared IDEA families, combines actual strength-envelope effects, resolves the exact template/members/materials, applies and reads back changes, calculates and grades the returned summary. Unchanged passing signatures can be reused.

Outputs scoped ideastatica_results and joint_grader_results. A permitted detailing correction updates exposed numeric overrides and returns A5 -> A5; unresolved evidence or exhausted limits stops.

## A6 — Independent package audit

run_a6_package_validator

Regrades raw source evidence instead of trusting cached booleans. Checks the current frame signature, each required connection signature/template hash and exact scope. It turns evaluated IDEA values into Tekla-ready parameter records.

Outputs validation_report, package_hash and validated_joint_groups_payload. Failed evidence routes through bounded Loop C or ends the run.

## A7 — Tekla publication

run_a7_tekla_modeler

Requires passing A6. Cleans the target while preserving grids, supplies missing reference assets, derives actual grid levels, inserts physical members and reference components, applies evaluated dimensions, verifies persisted identities/attributes and saves.

Outputs created/verified member and connection counts plus detailed errors. A missing member, rejected attribute or failed save cannot reach a passing publication grade.

## A8 — Report and final acceptance

run_a8_reporting / accept_gate3

Assembles profiles, engineering grades, scope, hashes, source lineage and correction histories. It ends the automated calculation/publication flow at the existing engineer-review gate.

PAUSED_AT_GATE3 means the report is ready, not that an engineer has signed it. accept_gate3 checks eligibility and records the named acceptance separately.

# 5. Checkers and graders: exact decisions

| Check | Current implemented decision |
| --- | --- |

| STAAD solve | ReturnValue must be 2 or 3; analysis_failed and error counts/messages invalidate evidence. Warnings are retained and must be interpreted. |

| Member ratios | Finite, nonnegative design ratios must cover generated member categories. Maximum ratio <= 1.0 is required by the current frame rubric. |

| Drift | Translations must be normalized to mm, serviceability envelope evidence must exist, and lateral X/Z displacement must be <= column height / 200. |

| Consistency | Reported drift booleans must agree with actual numeric displacements. Missing evidence is not a low-demand result. |

| IDEA physical settings | Plastic-strain limit must be no greater than 0.05; local-deformation limit no greater than 0.03; the local-deformation check must be enabled. |

| IDEA plate/deformation summaries | check_value is utilization percent relative to the software check; limit 100%, with successful check_status. |

| IDEA bolts | The current project grader uses a 75% utilization threshold, even though a software summary may print a 100% software limit. |

| IDEA welds | The current grader uses 100% utilization and successful status. |

| Unexpected SDK status | A false check_status inside an apparently acceptable numeric limit is unresolved evidence. The code stops instead of blindly increasing a size. |

| Tekla evidence | All expected physical members must be created and verified; API errors/simulation/failed attribute read-back invalidate publication. |

| Final approval | A failed/cancelled run cannot be signed off. A8 approval requires a previously validated, successfully published package. |

The plate utilization is not raw physical strain. Example: a 50% plate utilization under a 5% strain limit is not a 50% strain result. The physical template limit and summary utilization are checked separately.

[Primary reference: IDEA result-summary percentage and status semantics](https://github.com/idea-statica/ideastatica-public/blob/main/docs/docs/api/connection-api/connection_api_concepts.md)

The workbook's possible 0.9 member target and L/300 checks were not silently substituted for the current 1.0 and H/200 rubric. Different project criteria require reconciliation and validation. See WORKBOOK_REVIEW.md.

# 6. Automatic correction loops

| Loop | Trigger and allowed action | Rerun / stop rule |
| --- | --- | --- |

| A — frame sizing | A4 has valid results but a supported ratio or drift failure. Select a larger configured section present in both relevant catalogues. | Regenerate A3 and rerun A4; clear downstream evidence. Stop for unsupported profiles, no larger candidate, repeated candidate or exhausted limit. |

| B — connection detailing | A5 has a numeric plate/deformation, bolt or weld exceedance that maps to an exposed numeric parameter. | Apply/read back a permitted size increase and rerun A5. Stop for missing parameters, no real size change, unchanged check signature, invalid evidence or per-family retry exhaustion. |

| C — evidence recovery | A6 detects missing/failed or stale source evidence. | Route to A5 for connection-related failures, otherwise A3. Recovery is bounded and does not bypass a grader. |

| Publication retry | An A7 integration failure occurred after A6 passed. | Create a new tracked run; re-audit A6; rerun A7/A8 only if evidence is unchanged and passing. This is an operational retry, not a new structural correction. |

valid tool evidence -> deterministic grade
  if PASS: next stage
  elif supported correction and budget remains:
      choose permitted candidate -> confirm applied change
      rerun tool -> obtain new evidence -> grade again
  else: FAILED with reason and saved diagnostics

## Worked explanation: frame correction

Illustrative example, not the latest live run: column utilization is 1.20. A4 identifies the column target, filters larger catalogue sections and chooses the next permitted candidate. It records the old/new profile, increments loop_a_count and returns REVISING. A3 regenerates the member geometry/metadata; A4 performs a new solve. The previous connection evidence cannot be retained after that frame change.

## Worked explanation: connection correction

Illustrative example: bolts return 82% against the configured 75% rubric. A5 may increase an exposed numeric diameter parameter from 16 mm to 20 mm, confirm IDEA retained the expression, rerun and grade the new result. It may not reduce loads or change the rubric to obtain a pass. Passing an IDEA correction does not by itself prove every possible changed parameter has an appropriate physical Tekla mapping; that mapping must exist and be validated.

## What does retry 3 mean?

Separate the initial attempt from revisions. With two permitted revisions, a family can have its initial calculation plus two changed-candidate calculations. Different joint families and multiple load effects are not automatically retries. loop_a_history and loop_b_history identify actual changes; loop_b_count is per family. Class defaults are three frame/connection revisions, but the runner overrides them with settings, and the supplied development configuration used two each.

Automatic correction is supported and regression-tested. The latest live reference run needed no engineering correction; it therefore does not establish live success for every possible correction branch or future input. No-op and unchanged-check guards prevent meaningless infinite retries, not every possible non-improving candidate.

# 7. Hybrid GPT and deterministic control

Most of the workflow is deterministic. The optional advisor is used in A4 for severe/repeated failures and in A5 on a later correction when multiple change candidates exist. The model receives evidence and already constructed candidate options. It returns option_id and rationale under a strict JSON schema.

| GPT may | GPT may not |
| --- | --- |

| Select an existing candidate ID or decline. | Invent a section, alter supports/loads/materials, change acceptance limits or declare a design safe. |

| Provide an auditable explanation. | Replace a STAAD/IDEA calculation or independently sign the package. |

| Help choose among supplied options. | Execute arbitrary model-generated code or edit the source/configuration from its response. |

The response is validated by Proposal, candidate IDs are checked against the supplied map, and every chosen change still requires numerical rerun and grading. A refusal/invalid response/network error falls back to the deterministic default. A null proposal logs DECLINED and also returns that default. The model-call count is capped.

Credentials remain backend-only. Requests use the configured endpoint and Azure api-key or standard bearer header as appropriate. The audit stores stage, model, candidate/rationale and sanitized failure metadata; it avoids response bodies, headers and credential text.

Accurate presentation answer: 'We built a LangGraph stateful engineering workflow with deterministic verification and an optional constrained GPT correction selector.' Do not say 'eight GPT engineers independently prove the design' or claim the DeepAgents package is installed.

# 8. Engineering handoffs and units

| Boundary | Implementation and check |
| --- | --- |

| Geometry -> STAAD | Generated coordinates are converted from mm to m and transformed into the analytical axis convention. Physical CAD members and split analytical members have different counts. |

| Sections | HEA140 maps to STAAD HE140A; European CFRHS sections use the cold-formed section table. Actual section and material identities are read back. |

| STAAD -> stored forces | Base-system force conversion was benchmarked locally; the code uses base-unit identity rather than blindly trusting an output-unit label. English force uses 4.4482216152605; moment additionally uses 0.0254. |

| Displacements | Translate only displacement components to mm; rotations are not multiplied as lengths. The grader explicitly requires the mm label. |

| Equipment wind | Radiator/fan/bay dimensions are converted to metres before area and lever-arm calculations. This fixes the prior mm-to-m mapping error. |

| STAAD -> IDEA | The connection payload requires local member forces, actual strength envelopes and coherent case/node effects. kN and kN.m values are multiplied by 1000 for the IDEA N and N.m interface values. |

| IDEA parameters -> Tekla | A6 transfers evaluated values, avoiding formula strings. Mapped lengths are converted m -> mm by 1000. The double-brace transverse branch uses D2_CPL2_* keys. |

| Typed CAD attributes | Integer attributes such as nb, nw and assembly_type use Int32; spacing-list fields use strings; length values use floating point. GetAttribute reads use the corresponding overload. |

The source contains some historical comments describing tentative SDK behavior or old unit terminology. This guide follows the current executable branches and recorded probes, not an assumption that every old docstring is authoritative.

[Primary reference: IDEA SI units (m, N, N.m, rad, Pa)](https://developer.ideastatica.com/docs/iom/iom_units.html)

## Steel-design parameters are separate from material names

The STAAD adapter maps the requested steel label to its configured material name, commonly STEEL. create_design_parameters separately assigns SGR = 2 for the default EN 1993-1-1:2005 design code. Bentley documents this grade selector as S355. A generic material label alone does not establish the effective design strength, and choosing a different UI steel grade does not automatically derive a different SGR in this implementation.

[Primary reference: Bentley EC3 design parameters and SGR](https://prd-aws-docs.bentley.com/LiveContent/web/STAAD.Pro%20Help-v2024/en/topics/Design_Codes/European_Codes/r-stpst_EC3_EN-1993-1_Design_Parameters.html)

| Parameter assigned in code | Role in the existing procedure |
| --- | --- |

| BEAM = 3; NA = 0 | Member design-check sampling setting and selected National Annex identifier. |

| SGR = 2 | Default EC3 design grade selector; it is assigned independently of the API material-name read-back. |

| SBLT = 0 / 2 | Rolled-beam versus cold-formed column/brace category. |

| GM0 = 1; GM1 = 1; GM2 = 1.25 | Existing design partial factors; not modified by GPT or correction loops. |

| LY / LZ / UNL; KY / KZ | Lengths derived from the member categories/grid and configured column factors. |

| TRACK = 2 on columns | Detailed design output setting in the current generator. |

Treat grade/section-code applicability, constitutive material constants and project-specific overrides as engineering inputs requiring review. This guide does not claim a complete cross-tool mechanical-property audit.

## How governing connection effects are selected

The selector scans each joint's own member end across envelope cases, tracks max/min of six components and deduplicates case/node pairs. Each chosen effect includes the complete connected-member force set for that one real case and node. It does not combine unrelated component maxima into a fictitious load case. Component-extreme screening still does not prove that every possible nonlinear multiaxial interaction is governing; broader checking needs an explicit verification scope.

# 9. Matching the senior workflow

The senior application's saved analysis_output_tekla_input.json carries IDEA outputs for two inner families. Its Tekla code creates other connections from standard details. Requiring an IDEA template for every support/edge group was therefore a scope mismatch, not evidence that the manual application had already calculated those missing families.

| Item | Reference procedure used by automation |
| --- | --- |

| Single longitudinal inner family | inner_node_1_longitudinal_bracing.ideaCon |

| Single longitudinal + transverse inner family | inner_node_1_longitudinal_1_transverse_bracing.ideaCon |

| Double longitudinal inner family | Explicit alias: two-longitudinal.ideaCon |

| Double longitudinal + transverse inner family | Explicit alias: two-longitudinal-transverse.ideaCon |

| Base details | Tekla 1004 / baseplate_1004 |

| Column-to-beam details | Tekla 144 / column_longitudinal_beam |

| Brace details | Tekla 20 / bracing_column |

| Beam crossing details | Tekla 30 / longitudinal_beam_transverse_beam |

The user explicitly selected the senior scope. The final recorded project has two calculated IDEA families and eleven standard-detail families. Standard details are not inserted into joint_grader_results as artificial IDEA passes. The final report and UI show that distinction.

The reference mode records S355JR -> S 355 as the static-analysis catalogue mapping inherited from the senior template basis. Tekla retains S355JR. This label mapping should not be presented as a general proof of toughness or every material property; a different material basis requires validation.

Custom combinations and full reviewed-engineering mode remain distinct. The automation's exact legacy eight-combination compatibility does not turn opposing wind/temperature sums into independently reviewed engineering criteria.

# 10. State, evidence and durability

| State group | Main fields and purpose |
| --- | --- |

| Identity/progress | run_id, status, current_agent, logs, agent_statuses, error |

| Inputs/readiness | raw_input, grid_config, load_config, readiness, model_call_count, model_events |

| Frame candidate | frame_candidate, active section fields, design_generation, candidate_history |

| Frame verification | staad_results, frame_grader_result, loop_a_count/history |

| Connections | ideastatica_results, joint_grader_results, active_operation_params, per-family loop_b_count/history |

| Package/publication | validation_report, package_hash, loop_c_count/history, tekla_results |

| Final acceptance | final_report, gate3_approved, accepted_by, acceptance_timestamp |

GraderResult carries passed, score, summary, details and optional corrective_action. Routing depends on explicit pass/failure/status evidence; the numerical score is informational, not a universally calibrated safety factor. AgentLogEntry stores agent identity, status, message, timestamp and optional loop information.

Frame signature = SHA-256 of current grid + loads + physical/analytical members. Connection signature = SHA-256 of its payload + design generation + template content hash. A6 reconstructs these inputs before accepting reuse. Package hash summarizes the validated handoff and scope. Hash equality detects input/content changes; it is not a digital engineer signature or proof of correct physics.

SqliteWorkflowStore writes full snapshots transactionally and reads the database on each fetch so a parent process can see worker updates. The controller stores worker stdout/stderr under data/worker_logs. Snapshots and external application modifications are not one atomic transaction. A failed integration may leave a partially changed desktop model that requires inspection/cleanup.

# 11. Runtime, UI and recovery

worker.py sets sys.coinit_flags = 2 and imports the STAAD helper before Tekla/pythonnet, preventing conflicting COM-apartment initialization. Blocking desktop operations stay inside this worker. The parent watchdog polls saved state and tracks changing stage messages.

The run has an overall time cap and a stalled-operation cap. A5's multi-effect calculation budget is min(run cap, per-effect allowance x effect count); the HTTP read timeout is bounded similarly. Phase and Tekla batch progress refresh the watchdog's progress timestamp. This is progress reporting, not continuous independent measurement of solver health.

Stop requests terminate the owned worker tree and persist CANCELLED. They do not universally guarantee that a previously submitted external IDEA REST calculation is cancelled, because that solver may belong to another service process. The server lock also does not control another application that modifies the same desktop model.

The browser polls /active during a run, restores saved states via /runs on refresh and renders returned numerical results. Restoration uses GET only. If an old state says RUNNING but this server has no corresponding active worker, the UI releases the run button and leaves the saved evidence available for inspection.

## Guarded A7 recovery

POST /api/v2/agentic/retry-publication/<failed-A7-run>
  -> require failed A7 + previously valid A6
  -> clone into new run ID; retain source lineage
  -> clear old CAD/report/acceptance data
  -> fresh A6 source revalidation
  -> A7 publication -> A8 report
  -> stop if evidence is changed or no longer passing

A publication retry is an explicit endpoint, not an automatic promise to fix arbitrary CAD failures. It is useful after an integration issue is corrected, as demonstrated by the latest recorded run. It cannot bypass a missing engineering analysis.

# 12. Configuration and operations

| Consumed setting | Meaning / code default |
| --- | --- |

| PLANT_HOST / PLANT_PORT | Local API bind address and port; 127.0.0.1 / 8080. |

| PLANT_WORKFLOW_MODE | poc2_automation default; poc1_automation and reviewed_engineering also validated. |

| PLANT_LIVE_ENGINEERING_ENABLED | True by default; false prevents a live run. |

| PLANT_RUN_TIMEOUT_SECONDS | 3600 default overall cap. |

| PLANT_AGENT_TIMEOUT_SECONDS | 180 default per-operation/per-effect allowance. |

| PLANT_MAX_FRAME_REVISIONS / PLANT_MAX_CONNECTION_REVISIONS | Code defaults 3/3; development configuration supplied for the live run used 2/2. |

| PLANT_LLM_ENABLED / PLANT_MODEL_CALL_LIMIT | Optional selector enabled by default when credentials/model exist; call cap 6. |

| PLANT_AGENT_MODEL / OPENAI_API_KEY / OPENAI_BASE_URL | Backend model/deployment and credential/endpoint settings. Values of credentials are intentionally absent from this guide. |

| PLANT_TEMPLATE_DIRECTORY | Defaults to repository sample_projects. |

| PLANT_LOAD_COMBINATIONS_FILE | Optional explicit project JSON override. |

| STAAD_SECTION_DATABASE_PATH | Optional section-database directory override. |

| TEKLA_BIN_PATH / IDEA_STATICA_BASE_URL | Installed Tekla assemblies / running IDEA service endpoint. |

Important implementation boundary: source inspection found no application wiring for the supplied PLANT_AUTH_ENABLED/tokens, PLANT_DATABASE_URL, PLANT_CHECKPOINT_URL, approval-mode flags or custom LangSmith setup. Adding those names to .env does not implement authentication, PostgreSQL, migrations, automatic engineering sign-off or tracing. SQLite is the implemented store. Do not present this development app as a production-secured deployment.

## Safe operating sequence

- Open saved analytical STAAD and Tekla models intended for automation, and start the compatible IDEA Connection REST API service.

- Start the local API with start_server.bat (or the venv Python entry point) and open /radiator_support.

- Supply the project form; use Run Agentic Design once. Inspect actual stage messages rather than repeatedly starting concurrent jobs.

- After completion, review the declared calculation/standard-detail scope, warnings, grades, model and report before the responsible engineer accepts Gate 3.

- For an actual failure, use its worker log and saved error. A missing/unresolved input is not solved by increasing retry limits or bypassing a grade.

# 13. API and data contracts

| Method / route | Purpose |
| --- | --- |

| GET /radiator_support | UI and query-based saved-run view. |

| POST /api/v2/agentic/run | Starts a new managed A1-A8 run using grid + load_configuration. |

| GET /api/v2/agentic/active | Current owned run snapshot or null. |

| GET /api/v2/agentic/runs | Recent run metadata; optional limit. |

| GET /api/v2/agentic/runs/{run_id} | Full saved state and diagnostics. |

| POST /api/v2/agentic/cancel/{run_id} | Stops the worker owned by this controller. |

| POST /api/v2/agentic/retry-publication/{run_id} | New tracked A7 retry after A6 source revalidation. |

| POST /api/v2/agentic/accept/{run_id} | Final eligible engineer acceptance. |

| POST /api/v1/radiator_support/generate | Manual generation compatibility route. |

| POST /api/v1/radiator_support/to_staad | Manual live STAAD handoff. |

| POST /api/v1/radiator_support/to_tekla | Manual live Tekla handoff. |

| POST /api/v1/to_ideastatica | Manual live IDEA handoff. |

{"grid": {"n_long_bays": 8, "n_trans_bays": 5, "bay_long_mm": 2750,
 "bay_trans_mm": 1620, "col_height_mm": 5000, "column_profile": "CFRHS120X120X5",
 "beam_profile": "HEA140"}, "load_configuration": {"...": "project load fields"}}

This compact example explains shape only; it is not a ready-to-analyze complete engineering input. Real load fields, selected bracing, declared combinations and the compatible scope must be supplied. The form and Pydantic schemas provide the actual complete interface.

# 14. Recorded evidence and tests

Evidence snapshot: source run run_45715b4c completed A1-A6 using actual STAAD and IDEA calculations. Its first Tekla attempt exposed an integer read-back error. After the fix, run_80c61cca independently revalidated those unchanged calculations, successfully published/saved Tekla and reached A8 with no error.

| Evidence | Recorded result |
| --- | --- |

| Analytical members | 402 |

| STAAD maximum ratio | 0.3234004; drift passed H/200 |

| STAAD completion | Zero errors, one warning |

| Combined inner double-brace family | Bolts 21.25%; welds 56.48%; checks passed |

| Longitudinal inner double-brace family | Bolts 23.83%; welds 65.78%; checks passed |

| Tekla physical model | 194 members verified; 464 connections verified; zero failures; model saved |

| Final package | a17410b76665; PAUSED_AT_GATE3; final engineer acceptance pending |

| Regression validation | 117 Python tests and 12 JavaScript tests recorded passing; inline JS syntax and whitespace checks passed |

| Live correction coverage | No engineering correction was needed in this particular live run. Correction branches have regression tests; not every branch has a live engineering demonstration. |

The live STAAD warning concerns additional UBC/IBC analysis/change sequencing if tension/compression, repeat loads, reanalysis or optimization are used. It was not converted into zero warnings or hidden from the evidence.

## Independent calibration evidence

The local cantilever probe used a 5 m member with 10 kN tip load. Reaction and bending moment matched approximately 10 kN and 50 kN.m; the measured 15.262557 mm deflection was within the stated 0.1 mm tolerance of 15.243902 mm bending-only theory, with shear deformation explaining the difference. This validates that probe's unit/result handling, not the entire radiator design or every code case.

Regression tests use mocked adapters to exercise fail/pass routing, revisions and data guards without running destructive desktop operations. Source inspection found the following test inventory; test names document the behavior under test, while WORKFLOW_VALIDATION.md records execution results.

| Python test module | Cases |
| --- | --- |

| test_agentic_workflow.py | 7 |

| test_engineering_guards.py | 27 |

| test_idea_material_identity.py | 3 |

| test_project_cleanup.py | 26 |

| test_publication_recovery.py | 3 |

| test_reference_automation.py | 4 |

| test_reference_scope.py | 9 |

| test_runtime_and_advisor.py | 11 |

| test_staad_input.py | 6 |

| test_staad_joint_force_maxima.py | 3 |

| test_staad_joint_group_load_effects.py | 5 |

| test_staad_radiator_installation_load.py | 10 |

| test_staad_workflow_state.py | 3 |

JavaScript tests cover result/viewer handling and saved-run restoration, including the guarantee that refresh uses read-only requests and does not start another calculation.

# 15. Troubleshooting and important limits

| Symptom | Meaning / correct response |
| --- | --- |

| Pydantic integer-key error | Older state schema required string result keys. Current state allows native integer load-case IDs. |

| DEFINE ENVELOPE solver error | Check the actual saved STD. PERFORM ANALYSIS must precede result envelopes; SDK option 6 was silently omitted on the installed build. |

| STAAD appears finished but A4 waits | An Analyze() completion dialog can keep the engine status active. The unattended path uses AnalyzeEx. |

| A5 stays on one family | A family may have several serial FEA load effects. Read the phase, load count and budget; a stable stage label alone does not prove a hang. |

| Missing support/edge IDEA file | Check the declared scope. Standard-detail families are not required IDEA calculations in the user-selected senior procedure; full reviewed mode is different. |

| Missing transverse placeholder | Use the verified no-op on the real aligned beam only when section/material match. A different beam requires a compatible template/proxy; do not ignore the mismatch. |

| False SDK status inside numeric limit | Treat as unresolved analysis evidence, not a simple size exceedance. Inspect IDEA details instead of blindly repeating parameter increases. |

| Tekla assembly_type/nb read-back fails | Use explicit Int32 for integer Set/GetAttribute calls; do not switch off verification. |

| Old PASS after input change | Reconstruct frame/connection signatures and invalidate dependent results. Do not edit stored pass flags. |

| A7 failed but calculations passed | Correct the handoff issue, then use the guarded publication retry. A6 must revalidate unchanged evidence first. |

| A8 is awaiting approval | Automation produced the report. The final engineer acceptance is an explicit remaining gate, not another solver error. |

- A passing reference-scope run does not establish all-family IDEA verification. Standard CAD details are a declared different basis.

- Component-extreme load selection does not prove exhaustive nonlinear combination coverage. Template geometry, member ends and force conventions remain engineering-sensitive mappings.

- Connection revisions are bounded and limited to supported exposed parameters; no-op/repeated-check failures stop. Future profile/detail changes can require new compatible templates or verified Tekla parameter mappings.

- The code does not automatically create or approve missing engineering templates, invent foundation details, resolve every solver instability or guarantee that every valid-looking input has a feasible design.

- The API is a local development service. Production authentication, authorization, deployment hardening, PostgreSQL migrations, distributed job ownership and complete external-solver cancellation are not established by this implementation.

- Local hashes and successful API read-back demonstrate data consistency; they are not independent certification of structural assumptions, standard-detail suitability or construction safety.

# 16. Presentation and interview Q&A

### What did you automate?

The existing input-to-STAAD-to-selected-IDEA-to-Tekla handoffs, plus input checking, deterministic grading, bounded corrections, evidence auditing and report generation.

### Why eight agents?

They separate requirements, readiness, generation, frame analysis, connection analysis, evidence validation, CAD publication and reporting. Each has a clear state contract and failure boundary.

### Are these eight GPT models?

No. They are LangGraph node functions. GPT is an optional constrained selector used only in certain correction situations.

### Are you using the DeepAgents library?

No such import/dependency is present. Describe this as a LangGraph stateful agentic engineering workflow; 'deep' is an informal project description.

### What is the difference between checker and grader?

A checker asks whether evidence is complete, consistent and correctly applied. A grader asks whether valid evidence meets the configured acceptance limits.

### What actually performs the calculations?

STAAD's solver/design engine evaluates the frame. IDEA's connection solver evaluates declared connection families. Tekla constructs the detailed physical model; GPT does not replace these numerical engines.

### How does a loop differ from a retry?

A correction loop records a real permitted old-to-new profile/parameter change, reruns the tool and grades new evidence. Repeating the same unmodified request is not engineering correction.

### What does the LLM see?

Sanitized stage evidence and a supplied candidate list. Its output is a strict candidate ID and rationale. Backend credentials are used for transport and are not copied into workflow state.

### Can GPT change loads to make a design pass?

No. The advisor contract forbids altering loads, supports, materials or acceptance criteria. The code validates candidate membership and requires tool rerun.

### What happens if GPT fails?

The implementation records sanitized failure metadata and falls back to the deterministic candidate. Call limits and timeouts bound its use.

### Does every failed check get fixed automatically?

Only failures with supported correction mappings, exposed numeric parameters and remaining budget. Unsupported, unstable, incomplete or uncorrectable evidence stops with a reason.

### How do you stop endless loops?

Revision caps, permitted candidate ladders, candidate-history checks, no-op detection and unchanged-check signatures, plus the overall runtime limit.

### Why can you see three attempts?

An initial calculation plus two permitted corrections makes three calculations. Several families or load effects are separate work, not necessarily correction retries.

### Does a bigger section always improve the grade?

No universal monotonic improvement is guaranteed. The new candidate must be rerun and regraded; limits can still end in a reported failure.

### Why were support templates blocking before?

Automation initially required all families while the senior manual handoff calculated only selected inner families and used standard Tekla details elsewhere. The user confirmed that reference scope.

### Did you certify all thirteen families with IDEA?

No. The recorded run separately calculated two required families and declared eleven standard-detail families. The report explicitly preserves that distinction.

### How do you avoid stale results?

A4 clears dependent evidence; frame and connection signatures bind inputs/generation/template content; A6 reconstructs those signatures and independently regrades source results.

### Why separate the API and worker?

Desktop COM/.NET calls can block and require STA initialization. A disposable worker keeps API progress/stop requests responsive and gives the controller an owned process to terminate.

### Why does importing order matter?

STAAD expects a compatible STA COM apartment. Initializing pythonnet/CLR first can select a conflicting apartment; the worker imports the STAAD helper before Tekla.

### How do you know an API change was applied?

Read back section/material identity, brace angle, parameter expressions, and persisted Tekla attributes. Returned booleans or a completed request alone are insufficient.

### What was the STAAD command bug?

PerformAnalysis(6) silently omitted the input command on the installed build. Option 0 was verified to insert it; the saved STD order is checked before solving.

### What was the Tekla integer bug?

pythonnet could choose a ref-double read overload for integer fields. Explicit Int32 made persisted integer attribute checks work correctly.

### Can a browser refresh rerun the design?

The restoration path uses GET requests only and renders saved results or follows the owned active run. It does not POST another calculation.

### Can a failed design be signed off?

The acceptance endpoint rejects failed/cancelled or unvalidated states. It requires a validated package awaiting review with A7 passed.

### What is proven by the live run?

Recorded evidence shows frame analysis, both scoped IDEA grades, package validation and saved Tekla publication reaching A8, including a corrected publication retry. It is not universal verification of all inputs.

### Were correction loops exercised in that live run?

No engineering correction was needed in that run. Regression tests exercise frame and connection correction routing; broader live correction coverage remains work to demonstrate.

### Is SQLite a LangGraph checkpointer?

Not in this code. It is an application snapshot/event store. The graph is compiled without a database checkpointer; only the explicit A7 recovery path provides selective reuse.

### Do .env auth/PostgreSQL flags enable those features?

No. Those supplied names are not wired into the inspected application. The implementation uses SQLite and local API defaults; production controls need separate engineering/software work.

### Did Excel values replace the calculation basis?

No. Workbooks were reviewed statically for useful checklist/reference evidence. Conflicting limits, fixed counts, external links and macros were not blindly adopted.

### What is the most honest final claim?

We automated the declared senior workflow, verified its live tool handoffs and implemented bounded checked correction loops. The final engineer reviews the scope and evidence before acceptance; unsupported cases fail explicitly.

# Appendix A. Every production Python function

This catalogue covers every production Python def/async def declaration found by the source AST, including nested helpers and methods. Each entry has a curated purpose plus its exact signature and source line. Inline sorting/filtering/normalization lambdas are explained with their containing function. Identical short names are disambiguated by module/qualified name. Line numbers apply to this snapshot and will move after edits.

## src/agentic/advisor.py

### choose_correction  ·  line 20

choose_correction(state, stage: str, evidence: dict, options: list[dict], default_id: str) -> dict

Asks the optional GPT advisor to select only one supplied candidate ID, validates its structured response, and returns the corresponding existing candidate. It caps model calls, records an audit event, and falls back to the deterministic default on refusal, invalid output or service failure.

## src/agentic/agents/a1_requirements_agent.py

### run_a1_requirements  ·  line 53

run_a1_requirements(state: WorkflowState) -> WorkflowState

Validates the input grid, resolves declared load combinations, initializes active profiles and records the workflow/reference basis. Invalid input becomes an A1 failure before model modification.

## src/agentic/agents/a2_readiness_agent.py

### run_a2_readiness  ·  line 13

run_a2_readiness(state: WorkflowState) -> WorkflowState

Checks live enablement, a saved idle analytical STAAD model, IDEA service/SDK product-release compatibility, a saved Tekla model and template availability. It records actual readiness instead of assuming that an open window means an API is ready.

## src/agentic/agents/a3_frame_generator_agent.py

### _lookup_profile_param  ·  line 50

_lookup_profile_param(profile_name: str, getter) -> float

Safely probes a profile-parameter getter for geometry generation, returning zero when the lookup fails.

### _fallback_dimension  ·  line 58

_fallback_dimension(profile_name: str, dim_index: int) -> float

Provides legacy dimension lookup/parsing for generation helpers. The live A3 path separately validates installed catalogue dimensions before generation, so a fallback alone does not establish live readiness.

### _lookup_profile_height  ·  line 71

_lookup_profile_height(profile_name: str) -> float

Looks up Tekla height and retains the legacy generation-only fallback if lookup is unavailable. Live catalogue validation remains a separate prerequisite.

### _lookup_profile_width  ·  line 84

_lookup_profile_width(profile_name: str) -> float

Looks up Tekla width with the legacy generation helper fallback; it is not a substitute for live catalogue verification.

### generate_members  ·  line 97

generate_members(cfg: Dict[str, Any]) -> Dict[str, Any]

Generates physical CAD members, split analytical members and a summary from the same grid. It accounts for section dimensions, offsets and mirrored bracing while preserving cross-tool names.

### generate_members.point  ·  line 159

point(i: int, j: int, z: float) -> List[float]

Nested generator helper that produces a grid-space physical point from bay indices and elevation.

### generate_members.transform_to_staad_co_ordinates  ·  line 162

transform_to_staad_co_ordinates(p_in: List[float]) -> List[float]

Converts a generated physical point to the analytical STAAD coordinate convention, maintaining the explicit difference between CAD and analysis axes.

### check_geometry  ·  line 352

check_geometry(candidate: Dict[str, Any])

Rejects missing, invalid, nonfinite, zero-length, duplicate or disconnected analytical geometry before sending it to the desktop solver.

### run_a3_frame_generator  ·  line 404

run_a3_frame_generator(state: WorkflowState) -> WorkflowState

Applies active correction profiles to the grid, validates catalogues and geometry, generates the frame and declares its connection plan. It increments design generation and rejects missing templates required by that plan.

### validate_profile_catalogue  ·  line 480

validate_profile_catalogue(cfg: dict) -> None

Requires positive dimensions from the actual installed Tekla catalogue for all selected profiles before a live frame is generated.

## src/agentic/agents/a4_staad_analyzer_agent.py

### send_to_staad  ·  line 22  ·  async

send_to_staad(data: dict) -> dict

A4's thin adapter delegates to the live implementation; the radiator implementation resets/builds STAAD, assigns verified properties/loads, runs unattended analysis and extracts forces, design ratios and normalized displacements. Live errors are not replaced by simulation.

### grade_staad_results  ·  line 28

grade_staad_results(staad_res: Dict[str, Any], col_height_mm: float) -> GraderResult

First rejects invalid/incomplete live evidence, then checks all returned member-type ratios and serviceability drift. It returns a boolean grade plus an allowed correction target only for a supported engineering failure.

### run_a4_staad_analyzer  ·  line 137  ·  async

run_a4_staad_analyzer(state: WorkflowState) -> WorkflowState

Clears downstream evidence, analyzes the current frame and grades it. A permitted failed frame selects a larger catalogue candidate, records an actual profile change and routes through A3 again; unrecoverable or invalid evidence stops.

Key calls: frame_signature, grade_staad_results, send_to_staad

## src/agentic/agents/a5_ideastatica_analyzer_agent.py

### _to_jsonable  ·  line 85

_to_jsonable(obj: Any) -> Any

Converts SDK enums/models and nested objects to ordinary JSON-compatible data for exported evidence and API handling.

### _get_client  ·  line 108

_get_client(attacher_only: bool=True)

Attaches to the already-running IDEA Connection REST API service. It does not silently start a substitute offline solver.

### _resolve_connection  ·  line 132

_resolve_connection(api_client, project_id: str, selector: Dict[str, Any])

Selects the intended connection by ID, index or unique name. Missing and ambiguous matches are errors.

### _resolve_member  ·  line 166

_resolve_member(members: List[Any], member_name: str)

Resolves an exact or unique member-name match and rejects ambiguous/missing members instead of dropping their loads or properties.

### _section_update_member  ·  line 181

_section_update_member(members, name)

Handles the senior transverse-placeholder name explicitly when the template contains only the actual aligned transverse beam. Subsequent verification permits a no-op only when its section/material already match.

### _free_end_position  ·  line 191

_free_end_position(member: Any) -> Any

Infers a member's free end from connected_by when an effect does not specify a position. Missing connection-end information fails rather than guessing.

### _resolve_cross_section_id  ·  line 202

_resolve_cross_section_id(api_client, project_id: str, mprl_cross_section_name: str, mprl_material_name: str) -> int

Reuses or adds a section only when both section name and embedded material name match the requested catalogue identity. Added material/section evidence is checked before its ID is accepted.

### _resolve_cross_section_id.matching  ·  line 203

matching(section)

Nested section/material matcher that normalizes whitespace/case and examines the material embedded in each IDEA cross-section record.

### _list_operations  ·  line 243

_list_operations(api_client, project_id: str, connection_id: int)

Returns the selected connection's IDEA operations for diagnostics and supported parameter inspection.

### _list_operations_properties  ·  line 247

_list_operations_properties(api_client, project_id: str, connection_id: int)

Returns common operation properties from the SDK for connection inspection.

### _get_parameters  ·  line 251

_get_parameters(api_client, project_id: str, connection_id: int, include_hidden: bool=False)

Reads exposed IDEA parameters, optionally including hidden fields for diagnostics. Corrections may only update supported exposed keys.

### _find_parameters  ·  line 255

_find_parameters(api_client, project_id: str, connection_id: int, name_contains: str, include_hidden: bool=True)

Filters IDEA parameters by key/description text for diagnostic discovery.

### _quote_expression_if_needed  ·  line 263

_quote_expression_if_needed(expression: str) -> str

Leaves numeric or already quoted expressions intact and quotes textual expressions for IDEA's expression interface.

### _set_parameters  ·  line 276

_set_parameters(api_client, project_id: str, connection_id: int, updates: List[Dict[str, str]])

Sends typed SDK parameter-update objects and propagates failures, rather than recording a proposed correction as though it had been applied.

### to_json_convention_string  ·  line 290

to_json_convention_string(obj: Any) -> str

Formats SDK-derived data as JSON for inspection through the shared converter.

### _validate_template_limits  ·  line 296

_validate_template_limits(setup: dict) -> dict

Checks the template's enabled local-deformation check and physical plastic-strain/deformation limits before summary utilization is graded.

### design_joint  ·  line 310

design_joint(data: dict) -> dict

Opens the exact template; checks topology, sections/materials and brace angles; replaces template loads with actual effects; applies/reads back parameters; validates physical settings; calculates and exports a saved project and checks. Every integration failure remains visible.

Key calls: api_client.calculation.calculate, api_client.member.update_member, _section_update_member

### design_joint.progress  ·  line 324

progress(message)

Nested adapter callback that prints the current IDEA/Tekla phase and reports it to the worker when supplied. It reports activity, not engineering acceptance.

### grade_joint_check_summary  ·  line 521

grade_joint_check_summary(check_summary: Dict[str, Any]) -> GraderResult

Interprets IDEA check_value as utilization percent, checks finite values and SDK statuses, and identifies a permitted size category for a numeric exceedance. A false SDK status within the numeric limit is unresolved evidence, not permission for a blind retry.

### _connection_payload  ·  line 578

_connection_payload(state: WorkflowState, joint_group: str) -> dict

Builds one scoped IDEA request from all strength envelopes, explicit template aliases and the declared reference material mapping. It refuses global-force input, empty loads, inconsistent properties and missing templates.

### _connection_signature  ·  line 628

_connection_signature(payload: dict, generation: int) -> str

Hashes the complete calculational payload, design generation and template content so only unchanged passing connection evidence can be reused.

### _correct_connection_parameters  ·  line 637

_correct_connection_parameters(parameters: list, fix_type: str) -> tuple

Steps only exposed numeric parameters that match the failing size category and returns both proposed overrides and an explicit old/new change list.

### run_a5_ideastatica_analyzer  ·  line 660

run_a5_ideastatica_analyzer(state: WorkflowState) -> WorkflowState

Calculates required IDEA families, reuses only matching passing evidence, grades actual checks and runs bounded per-family detailing corrections. No-op changes, repeated unchanged checks, unsupported parameters and exhausted retries stop the run.

Key calls: _connection_signature, WORKFLOW_STORE.save_state, grade_joint_check_summary

### run_a5_ideastatica_analyzer.update_progress  ·  line 685

update_progress(message)

Nested A5/A7 callback that saves phase/status messages for UI polling and the watchdog. A5 declares a bounded multi-effect calculation budget when it starts FEA.

Key calls: WORKFLOW_STORE.save_state

## src/agentic/agents/a6_package_validator_agent.py

### run_a6_package_validator  ·  line 19

run_a6_package_validator(state: WorkflowState) -> WorkflowState

Independently regrades source evidence, checks frame/connection signatures and scope, then builds the evaluated Tekla handoff and package hash. Failed evidence triggers bounded recovery or terminal failure; cached PASS flags alone are insufficient.

Key calls: grade_staad_results, frame_signature, idea_agent._connection_signature, grade_joint_check_summary

## src/agentic/agents/a7_tekla_modeler_agent.py

### _index_to_letters  ·  line 82

_index_to_letters(n: int) -> str

Converts a positive grid index to spreadsheet-style labels, including rollover beyond Z.

### generate_y_axis_labels  ·  line 90

generate_y_axis_labels(n_trans_bays: int) -> list

Creates the transverse grid-label sequence for the configured number of bays.

### get_bolt_parameters  ·  line 94

get_bolt_parameters(bolt_dia: float) -> dict

Returns the existing bolt-hole tolerance, hole diameter and edge-distance rule for the selected diameter. It supports reference detailing geometry; it does not replace IDEA bolt checks.

### _catalog_dimension  ·  line 105

_catalog_dimension(profile_name: str, property_name: str) -> float

Requires a finite positive installed Tekla dimension for the modeler's connection-geometry calculations.

### get_profile_width  ·  line 115

get_profile_width(profile_name: str) -> float

Returns the profile width from the installed Tekla catalogue; A7 validates that it is finite and positive.

### get_profile_height  ·  line 119

get_profile_height(profile_name: str) -> float

Returns the profile height from the installed Tekla catalogue. A7's wrapper requires a finite positive result rather than guessing.

### get_profile_web_thickness  ·  line 123

get_profile_web_thickness(profile_name: str) -> float

Reads the profile web/wall thickness used by the reference connection-geometry calculations.

### clean_tekla  ·  line 127

clean_tekla(progress=None)

Enumerates the active model, preserves grids/grid surfaces and DefaultGroup, removes components before remaining parts, verifies cleanup and saves. Optional callbacks report long scans/deletions; failures propagate.

### modify_grid  ·  line 132

modify_grid(model, coord_x, coord_y, coord_z, label_x, label_y, label_z)

Updates existing grid coordinates/labels or delegates to the shared grid helper. A7 derives levels from the actual generated geometry instead of fixed sample heights.

### connection_attributes  ·  line 137

connection_attributes(joint_groups: dict, joint_group_ids, connection_number: int, bracing: Optional[str]=None) -> dict

Maps evaluated IDEA parameter keys to component attributes and converts metres to millimetres. It handles the double-brace transverse D2 namespace and rejects invalid numeric mapped values.

### joint_group_columns  ·  line 156

joint_group_columns(joint_groups: dict) -> dict

Builds the column-name set for each calculated family so connection parameters reach the relevant physical columns.

### joint_group_beam_pairs  ·  line 164

joint_group_beam_pairs(joint_groups: dict) -> dict

Builds longitudinal/transverse physical beam pairs at the family joints for component-30 parameter assignment.

### _component_integer  ·  line 179

_component_integer(value)

Creates an explicit .NET Int32 for integer component attributes. This avoids pythonnet choosing the ref-double overload during read-back.

### set_component_attribute  ·  line 189

set_component_attribute(component, param_name: str, param_value)

Selects integer, string or floating-point SetAttribute handling according to the component field. Explicit rejection propagates to the publication result.

Key calls: component.SetAttribute

### _verify_component  ·  line 200

_verify_component(model, component, attributes)

Selects the inserted component again and checks requested attributes using the proper typed GetAttribute overload and numeric/string comparisons.

Key calls: persisted.GetAttribute

### add_base_connection  ·  line 219

add_base_connection(model, column, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, attribute_file: str='baseplate_1004', attributes: Optional[dict]=None)

Loads the existing 1004 attribute preset, applies requested overrides, assigns the column/reference point and inserts the detail. It verifies the inserted component rather than assuming Insert or loading succeeded.

### add_column2beam_connection  ·  line 249

add_column2beam_connection(model, secondary, primary, connection_number: int, detail_name: str, design_code: str, grid_specs: dict, classCode: int, attribute_file: str='column_longitudinal_beam', upVector=None, attributes: Optional[dict]=None)

Loads the chosen standard component preset, assigns primary/secondary members, applies attributes and positioning, inserts it and verifies its persisted attributes.

### _stringify  ·  line 283

_stringify(value) -> object

Safely turns diagnostic property values into displayable strings.

### dump_attributes  ·  line 292

dump_attributes(obj) -> dict

Collects .NET object properties and user properties for diagnostic inspection, reporting read errors as diagnostics.

### print_attributes  ·  line 316

print_attributes(obj) -> None

Prints a structured object's diagnostic attributes; it is not an acceptance checker.

### pick_and_print_attributes  ·  line 323

pick_and_print_attributes() -> None

Interactive diagnostic utility for objects selected in Tekla. The automatic graph does not require this manual picker.

### send_to_tekla  ·  line 339

send_to_tekla(data: dict) -> dict

Resets the target model, supplies missing reference assets, sets the actual grid, inserts physical members and standard/calculated component attributes, verifies geometry/profile/materials and saves on success. It returns counts and concrete errors; it cannot return a simulated live pass.

### send_to_tekla.progress  ·  line 347

progress(message)

Nested adapter callback that prints the current IDEA/Tekla phase and reports it to the worker when supplied. It reports activity, not engineering acceptance.

### send_to_tekla.connect  ·  line 379

connect(factory, *args, **kwargs)

Nested modeler wrapper that records each successfully inserted/verified component ID and reports progress in batches.

### run_a7_tekla_modeler  ·  line 551

run_a7_tekla_modeler(state: WorkflowState) -> WorkflowState

Requires a passing A6 package, sends only its validated handoff to Tekla and checks member counts, verification counts, simulation flags and errors. Incomplete publication is terminal and described concisely while full diagnostics remain in state.

Key calls: send_to_tekla, WORKFLOW_STORE.save_state

### run_a7_tekla_modeler.update_progress  ·  line 573

update_progress(message)

Nested A5/A7 callback that saves phase/status messages for UI polling and the watchdog. A5 declares a bounded multi-effect calculation budget when it starts FEA.

Key calls: WORKFLOW_STORE.save_state

## src/agentic/agents/a8_reporting_acceptance_agent.py

### run_a8_reporting  ·  line 14

run_a8_reporting(state: WorkflowState) -> WorkflowState

Builds the calculation summary, scope, correction history, connection results and source-run lineage. It ends automation at PAUSED_AT_GATE3, awaiting the responsible engineer's acceptance.

### accept_gate3  ·  line 77

accept_gate3(state: WorkflowState, engineer_name: str='Lead Structural Engineer') -> WorkflowState

Requires a validated package already awaiting review with A7 PASSED, then records the named engineer and timestamp and marks COMPLETED. It cannot approve an arbitrary failed run.

## src/agentic/catalogue.py

### section_exists  ·  line 11

section_exists(profile: str, country: str='EUROPE', directory: Path | None=None) -> bool

Reads the installed European STAAD section databases in read-only mode and checks the translated section name. Unsupported countries or unavailable catalogue entries return False; a guessed section is not accepted.

### frame_options  ·  line 47

frame_options(current: str, target: str, country: str='EUROPE') -> list[dict]

Filters the larger entries in the configured column, beam or brace ladder against STAAD availability and positive Tekla dimensions. It returns named candidates for the correction selector; availability is not an independent engineering approval.

## src/agentic/checks.py

### preview_joint_groups  ·  line 6

preview_joint_groups(candidate: dict) -> dict

Builds a temporary node/member connectivity dictionary from generated analytical members, then uses the same grouping logic as STAAD. It plans connection families without calling model-changing OpenSTAAD methods.

### template_coverage  ·  line 25

template_coverage(candidate: dict) -> dict

Compares previewed joint-family names with available IDEA filenames and returns required, available and missing families plus the preview. Reference-scope planning subsequently distinguishes calculated families from standard details.

## src/agentic/config.py

### workflow_mode  ·  line 10

workflow_mode() -> str

Reads and validates the workflow mode. Only poc1_automation, poc2_automation and reviewed_engineering are accepted.

### load_environment  ·  line 17

load_environment(path: Path | None=None) -> None

Reads simple KEY=value settings from the repository .env without evaluating shell commands or interpolation. Existing process environment values win; credentials are not copied into workflow state.

### _integer  ·  line 35

_integer(name: str, default: int, minimum: int=1) -> int

Parses an integer setting and enforces a minimum. Invalid revision, timeout or call-limit values fail configuration rather than silently becoming usable defaults.

### settings  ·  line 57

settings() -> Settings

Returns an immutable Settings instance containing the configuration actually consumed by this application. A setting present in .env is not necessarily implemented unless code reads it.

## src/agentic/evidence.py

### frame_signature  ·  line 7

frame_signature(state) -> str

Serializes the current grid, loads, physical members and analytical members in a stable order and computes SHA-256. A6 compares this signature with the one attached to the STAAD result.

## src/agentic/execution.py

### RunController.__init__  ·  line 17

__init__(self)

Initializes the active run ID, owned worker handle and cancellation flag. One controller instance tracks one worker at a time.

### RunController._terminate  ·  line 22  ·  async

_terminate(self)

Stops only the owned worker process tree and awaits its exit. On Windows it invokes taskkill for that worker; an external IDEA REST service is not necessarily part of this tree.

### RunController.cancel  ·  line 41  ·  async

cancel(self, run_id: str)

Checks that the requested run belongs to this controller, marks cancellation and terminates its worker. Cancellation during process startup is also handled.

### RunController.execute  ·  line 47  ·  async

execute(self, payload: dict, run_id: str, resume_state: WorkflowState | None=None) -> WorkflowState

Persists initial input, starts the disposable STA worker, captures its log, polls durable progress and enforces time budgets. It records worker crashes, cancellations and timeouts as terminal outcomes, and supports a prepared publication-only retry.

Key calls: WORKFLOW_STORE.save_state

## src/agentic/ladders.py

### get_next_profile  ·  line 71

get_next_profile(current_profile: str, member_type: str='column') -> Optional[str]

Returns the next configured profile for a recognized member category. Unknown profiles and ladder ceilings do not justify inventing a new section.

### get_next_plate_thickness  ·  line 99

get_next_plate_thickness(current_t: float) -> float

Returns the next configured plate thickness in millimetres, or the ceiling. The caller must check that a real increase occurred.

### get_next_bolt_diameter  ·  line 107

get_next_bolt_diameter(current_d: float) -> float

Returns the next configured bolt diameter in millimetres. It changes only a proposed parameter; IDEA must still rerun and pass.

### get_next_weld_size  ·  line 115

get_next_weld_size(current_w: float) -> float

Returns the next configured weld size in millimetres. This is a bounded candidate generator, not a weld-capacity calculation.

### step_parameter_expression  ·  line 123

step_parameter_expression(key: str, expression: str, fix_type: str='auto') -> str

Steps a finite positive numeric expression through the appropriate sizing ladder while preserving its metre/mm convention. Formula strings, invalid values and ceiling values are not arbitrarily rewritten.

## src/agentic/load_combinations.py

### reference_combinations  ·  line 14

reference_combinations() -> list

Creates the senior procedure's exact eight combinations and three envelope IDs. The original opposing-direction factors are preserved only as the declared reference procedure, not relabeled as independently approved project criteria.

### validate_combinations  ·  line 33

validate_combinations(combinations: list, *, reference_mode: bool=False) -> list

Checks required keys, unique names, known load cases, finite nonzero factors and both serviceability/strength coverage. Custom combinations reject opposing wind and temperature cases; the exact declared reference set has an explicit compatibility exception.

### resolve_combinations  ·  line 59

resolve_combinations(load_config: dict) -> list

Resolves combinations from the run payload, then the configured JSON file, then automation-mode reference defaults. Reviewed mode requires explicit input; the resolved combinations are validated before model changes.

## src/agentic/orchestrator.py

### _sync_state  ·  line 29

_sync_state(state: WorkflowState)

Saves graph progress to the durable store and updates the server cache when available. Its wrapper catches synchronization exceptions; persistence is not a distributed transaction with the external engineering applications.

Key calls: WORKFLOW_STORE.save_state

### _start_agent  ·  line 42

_start_agent(state: WorkflowState, agent_id: str)

Sets the current agent and RUNNING message, then synchronizes progress before a node begins its work.

### node_a1  ·  line 48

node_a1(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A1: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a1_requirements

### node_a2  ·  line 55

node_a2(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A2: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a2_readiness

### node_a3  ·  line 62

node_a3(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A3: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a3_frame_generator

### node_a4  ·  line 69  ·  async

node_a4(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A4: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a4_staad_analyzer

### node_a5  ·  line 76

node_a5(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A5: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a5_ideastatica_analyzer

### node_a6  ·  line 83

node_a6(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A6: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a6_package_validator

### node_a7  ·  line 90

node_a7(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A7: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a7_tekla_modeler

### node_a8  ·  line 97

node_a8(state: WorkflowState) -> WorkflowState

LangGraph wrapper for A8: records RUNNING, calls the stage implementation and persists the returned state. It separates stage logic from graph orchestration.

Key calls: run_a8_reporting

### route_after_a4  ·  line 106

route_after_a4(state: WorkflowState) -> Literal['a3_frame_generator', 'a5_ideastatica_analyzer', '__end__']

Ends on failure, returns to A3 for a permitted frame revision, or advances a passing frame to A5. Routing uses explicit stage status rather than trusting a progress label.

### route_after_a5  ·  line 126

route_after_a5(state: WorkflowState) -> Literal['a5_ideastatica_analyzer', 'a6_package_validator', '__end__']

Ends on terminal connection failure, reruns A5 after an actual parameter revision, or advances a passing scoped connection package to A6.

### route_after_a6  ·  line 146

route_after_a6(state: WorkflowState) -> Literal['a3_frame_generator', 'a5_ideastatica_analyzer', 'a7_tekla_modeler', '__end__']

Routes a bounded validation recovery to A3 or A5, advances a valid package to A7, or ends a failed audit.

### build_workflow_graph  ·  line 160

build_workflow_graph() -> StateGraph

Registers the eight LangGraph nodes, sequential edges and conditional correction/recovery edges. Failed early stages terminate; the workflow does not continue merely to color every stage green.

### run_agentic_workflow  ·  line 229  ·  async

run_agentic_workflow(raw_input: Dict[str, Any], run_id: Optional[str]=None) -> WorkflowState

Creates typed initial state with configured limits, compiles the graph and invokes it with a bounded recursion setting. On graph exceptions it records failure against the latest saved stage.

## src/agentic/publication_recovery.py

### prepare_publication_retry  ·  line 7

prepare_publication_retry(source, run_id)

Requires a failed A7 run with a validated calculation package, deep-copies it into a new run, records lineage and clears old publication/acceptance data. It cannot be used to skip a failed A4 or A5.

### run_publication_retry  ·  line 26  ·  async

run_publication_retry(state)

Runs A6 again against retained source evidence, stops if evidence changed or failed, and only then reruns A7 and A8. It avoids repeating unchanged FEA solely to repair a Tekla handoff.

## src/agentic/reference_procedure.py

### build_connection_scope  ·  line 22

build_connection_scope(groups) -> dict

Constructs the explicit split between required IDEA families and standard-detail families. Automation follows the senior procedure; reviewed mode requires all families. It records component presets and the reference material-catalogue mapping.

### scope_for_state  ·  line 36

scope_for_state(state) -> dict

Audits exact, nonoverlapping family coverage and rejects stale plans, dropped required inner calculations or altered reference presets/material mappings. A legacy state with no declared plan retains full coverage.

### template_for_group  ·  line 61

template_for_group(group: str, reference: bool=False) -> Path

Resolves an exact filename or the explicit inner double-brace alias in reference mode. It does not choose an unrelated template by similarity.

## src/agentic/router.py

### start_agentic_run  ·  line 30  ·  async

start_agentic_run(payload: Dict[str, Any])

Acquires the API's run lock, assigns a new run ID and waits for the managed worker's final state. Concurrent runs through this server receive HTTP 409.

Key calls: WORKFLOW_STORE.save_state

### get_active_run  ·  line 53  ·  async

get_active_run()

Returns the durable snapshot of the controller's current run, or null when idle. Browser progress polling uses this read-only endpoint.

### retry_publication  ·  line 60  ·  async

retry_publication(source_run_id: str)

Looks up a failed source run, prepares an eligible publication retry and executes it under the same tool lock. It preserves the failed source and returns the new tracked run.

### cancel_run  ·  line 84  ·  async

cancel_run(run_id: str)

Delegates a stop request to the controller and rejects requests for runs it does not own. The eventual durable state records the cancellation outcome.

### list_recent_runs  ·  line 93  ·  async

list_recent_runs(limit: int=50)

Returns recent durable run metadata for history and page-refresh restoration.

### get_run_status  ·  line 101  ·  async

get_run_status(run_id: str)

Loads one saved run and serializes it for the browser. Missing run IDs return HTTP 404; integer result keys survive JSON conversion.

### accept_run  ·  line 113  ·  async

accept_run(run_id: str, payload: Dict[str, Any]=None)

Loads a package, invokes the final acceptance guard, persists its accepted state and returns it. It does not make failed or cancelled runs acceptable.

Key calls: WORKFLOW_STORE.save_state

## src/agentic/state.py

### fail_workflow  ·  line 98

fail_workflow(state: WorkflowState, agent_id: str, message: str) -> WorkflowState

Sets FAILED, the current agent and an explanatory error, updates the stage status and appends a timestamped log. It keeps ended failures from appearing to run forever.

## src/agentic/store.py

### monotonic_now_iso  ·  line 26

monotonic_now_iso() -> str

Produces strictly increasing UTC timestamps for saved run ordering, including rapid writes in the same process. This is a storage-order helper, not an engineering clock.

### SqliteWorkflowStore.__init__  ·  line 40

__init__(self, db_path: Optional[str]=None)

Creates the database directory, synchronization lock/cache and required SQLite tables/indexes.

### SqliteWorkflowStore._connection  ·  line 48

_connection(self)

Provides a context-managed SQLite connection with named rows and a lock-wait timeout, closing it afterward.

### SqliteWorkflowStore._init_db  ·  line 56

_init_db(self)

Creates workflow_runs, workflow_events and the status index if absent. This implementation uses local SQLite, not a PostgreSQL migration framework.

### SqliteWorkflowStore.save_state  ·  line 86

save_state(self, state: WorkflowState) -> WorkflowState

Upserts a complete serialized state snapshot and commits it atomically in SQLite. It preserves the original created_at on updates and advances updated_at.

### SqliteWorkflowStore.get_state  ·  line 114

get_state(self, run_id: str) -> Optional[WorkflowState]

Reads state from SQLite on every call so another process's changes are visible. It then refreshes the in-process cache and validates the stored model.

### SqliteWorkflowStore.list_runs  ·  line 130

list_runs(self, limit: int=50) -> List[Dict[str, Any]]

Returns run metadata ordered by most recent update, bounded by the requested limit.

### SqliteWorkflowStore.record_log  ·  line 142

record_log(self, run_id: str, entry: AgentLogEntry)

Inserts a structured event row and commits it. Snapshot logs and event rows should not be confused with a LangGraph checkpointer.

## src/agentic/worker.py

### main  ·  line 11

main()

Loads a saved run, initializes a Windows STA COM apartment before pythonnet imports, executes the full graph or publication-only recovery, and persists the terminal result.

Key calls: WORKFLOW_STORE.save_state, run_agentic_workflow

## src/common/build_idea_statica_input.py

### load_json  ·  line 36

load_json(path: str) -> Any

Reads a UTF-8 JSON file for the command-line handoff builder and returns its decoded data.

### load_operation_parameters  ·  line 41

load_operation_parameters(path: str) -> List[Dict[str, Any]]

Accepts a parameter list or an object containing operation_parameters and checks each entry has key and expression.

### build_design_input  ·  line 58

build_design_input(staad_output: Dict[str, Any], operation_parameters: List[Dict[str, Any]], envelope_id: str, joint_group_id: str) -> Dict[str, Any]

Builds the senior-style manual IDEA payload for one explicit envelope and family, using actual STAAD loads and properties. It rejects missing families and empty effects.

### main  ·  line 101

main() -> None

Parses CLI arguments, loads STAAD/parameter JSON, builds one manual IDEA input and writes it to disk.

## src/common/build_tekla_input.py

### load_json  ·  line 43

load_json(path: str) -> Any

Reads a UTF-8 JSON file for the command-line handoff builder and returns its decoded data.

### _warn  ·  line 48

_warn(message: str) -> None

Writes a command-line handoff-builder diagnostic to stderr. These legacy warnings are not the automated A6 acceptance policy.

### build_joint_groups  ·  line 52

build_joint_groups(staad_output: Dict[str, Any], idea_outputs: List[Dict[str, Any]], idea_output_paths: List[str]) -> Dict[str, Any]

Associates supplied IDEA output files with STAAD joint groups and Tekla member names. This manual CLI utility warns about failed outputs; automated A6 is the stricter publication gate.

### main  ·  line 110

main() -> None

Parses CLI arguments, loads generated geometry plus selected IDEA outputs and writes the manual Tekla handoff JSON.

## src/common/staad_common.py

### searchDictByKeyStart  ·  line 354

searchDictByKeyStart(dict: Dict[str, Any], key_start: str) -> Any

Returns the value of the first dictionary entry whose key starts with the requested prefix, or None if no entry matches. It does not return a filtered dictionary.

### get_ubc97_ce  ·  line 385

get_ubc97_ce(height_m: float, exposure: str) -> float

Interpolates the existing UBC97 exposure coefficient table by height, with explicit low/high-height handling and invalid-exposure rejection. Its table is a project/reference input, not a newly derived standard.

### calculate_wind_pressure_profile  ·  line 431

calculate_wind_pressure_profile(wind_speed_mph: float, wind_exposure_category: str, frame_pressure_coefficient_cq: float, iw: float=1.0) -> tuple[list[float], list[float]]

Calculates the reference pressure profile from exposure, speed, frame coefficient and importance factor, returning heights and pressures. The PDF explains the implementation rather than independently certifying the load model.

### tekla2staad  ·  line 459

tekla2staad(tekla_profile, tekla_material)

Normalizes profile/material strings and translates them to STAAD conventions. The assignment path separately handles European cold-formed tables and verifies actual section/material read-back.

### tekla2ideastatica_profile  ·  line 478

tekla2ideastatica_profile(tekla_profile: str) -> str

Converts CFRHS dimensions to IDEA SHS/RHS catalogue syntax, such as CFRHS120X120X5 to SHS120/120/5.0. Other cleaned profile names pass through unchanged.

### country2code  ·  line 487

country2code(country)

Maps a country label to the configured STAAD section-table code, returning an invalid sentinel for an unknown label.

### _SequentialIdAllocator.__init__  ·  line 516

__init__(self, start: int)

Initializes a deterministic noncolliding node/member ID sequence for newly generated STAAD entities.

### _SequentialIdAllocator.next  ·  line 519

next(self) -> int

Returns the current entity ID and advances the sequence.

### _get_openstaad  ·  line 537

_get_openstaad()

Connects to the running OpenSTAAD analytical session and enables silent mode. Connection problems propagate as an adapter error.

### _format_com_error  ·  line 562

_format_com_error(exc: Exception) -> str

Formats COM exceptions into an actionable diagnostic without pretending a failed call succeeded.

### _node_key  ·  line 578

_node_key(point: List[float]) -> tuple

Rounds coordinates into a consistent node-cache key so coincident beam endpoints can share one analytical node.

### _get_or_create_node  ·  line 584

_get_or_create_node(geometry: OSGeometry, node_cache: Dict[tuple, int], node_ids: '_SequentialIdAllocator', point: List[float]) -> int

Reuses a cached STAAD node or creates a new one with the allocator, converting generated millimetres to metres for the model API.

### _create_beam  ·  line 619

_create_beam(geometry: OSGeometry, node_cache: Dict[tuple, int], node_ids: '_SequentialIdAllocator', beam_ids: '_SequentialIdAllocator', member: dict) -> tuple[int, int, int]

Creates one member in the relevant live application. In STAAD it resolves shared nodes and creates the analytical beam; in Tekla it sets physical endpoints, profile, material, class and positioning before insertion.

### _resolve_profile_name  ·  line 654

_resolve_profile_name(profile: str) -> str

Looks up the configured STAAD profile-name translation instead of synthesizing an arbitrary replacement.

### _assign_property  ·  line 658

_assign_property(property: OSProperty, beam_no: int, member: dict, property_id_cache: Optional[Dict[str, int]]=None, country: str='EUROPE') -> None

Creates/reuses a STAAD section property in the proper table, assigns it and its material, and reads both identities back. An SDK boolean alone is not accepted as evidence of assignment.

### _create_combo_load_case  ·  line 703

_create_combo_load_case(load: OSLoad, currentLoadCaseId: int, comboCaseTitle: str, comboLoadCases: List[int], primary_case_ids: List[int], load_factors: List[float]) -> int

Creates one STAAD combination and adds each nonzero primary-load factor. It advances the combination ID used by the caller.

### _verify_open_model  ·  line 716

_verify_open_model(staad, path: Path) -> None

Waits until STAAD reports the requested path and zero geometry counts. It proves the cleanup staging/original model actually opened before proceeding.

### prepare_saved_analysis  ·  line 738

prepare_saved_analysis(staad) -> Path

Saves the generated STD, backs it up if correction is needed, inserts PerformAnalysis(0), saves again and checks serialized command order. It refuses to solve if the command is still absent.

Key calls: staad.Command.PerformAnalysis

### clean_staad  ·  line 763

clean_staad() -> None

Saves and backs up the current analytical STD, switches through an empty staging model, replaces/reopens the original path and verifies its saved tokens. It rejects physical models and active analysis; cleanup is not a rollback transaction.

## src/common/staad_input.py

### prepare_analysis_input  ·  line 6

prepare_analysis_input(source: str) -> str

Purely examines serialized command order and returns an idempotently prepared string with PERFORM ANALYSIS before DEFINE ENVELOPE. Ambiguous ordering or missing envelopes raise a validation error.

### analysis_errors  ·  line 24

analysis_errors(report: str) -> list[str]

Extracts solver ERROR lines and wrapped explanations from an ANL report so the user sees the root solver error instead of only missing downstream outputs.

## src/common/tekla_common.py

### _get_connected_model  ·  line 64

_get_connected_model() -> Model

Connects to Tekla's running model and verifies connectivity. The model must already be open and saved for the live workflow.

### _copy_missing_connection_attributes  ·  line 74

_copy_missing_connection_attributes(model: Model) -> int

Copies missing reference attribute assets into the active model's attributes folder. Existing destination files are left untouched; this is not a silent overwrite of model-specific settings.

### _copy_missing_tekla_package_dbs  ·  line 99

_copy_missing_tekla_package_dbs(model: Model) -> int

Copies missing packaged Tekla database assets needed by the standard model. Existing files are preserved.

### delete_all_grids  ·  line 125

delete_all_grids(model: Model)

Legacy manual utility that explicitly deletes model grids. The automated cleanup path does not call it because it preserves grids.

### create_grid  ·  line 146

create_grid(model: Model, X: str, Y: str, Z: str, labelX: str, labelY: str, labelZ: str, extensionLeftX: int, extensionLeftY: int, extensionLeftZ: int, extensionRightX: int, extensionRightY: int, extensionRightZ: int, originX: int, originY: int, originZ: int)

Creates a Tekla grid with coordinate, label, extension and origin settings. This is an explicit model-changing helper.

### modify_grid  ·  line 170

modify_grid(model: Model, X: str, Y: str, Z: str, labelX: str, labelY: str, labelZ: str)

Updates existing grid coordinates/labels or delegates to the shared grid helper. A7 derives levels from the actual generated geometry instead of fixed sample heights.

### to_tekla_profile  ·  line 197

to_tekla_profile(name: str) -> str

Normalizes profile strings to the installed Tekla naming convention before member creation and verification.

### _create_beam  ·  line 210

_create_beam(member: dict) -> Beam

Creates one member in the relevant live application. In STAAD it resolves shared nodes and creates the analytical beam; in Tekla it sets physical endpoints, profile, material, class and positioning before insertion.

### _get_profile_parameter  ·  line 261

_get_profile_parameter(profile_name: str, property_name: str) -> Optional[float]

Reads a named property from the Tekla profile catalogue for geometric calculations.

### get_profile_height  ·  line 272

get_profile_height(profile_name: str) -> Optional[float]

Returns the profile height from the installed Tekla catalogue. A7's wrapper requires a finite positive result rather than guessing.

### get_profile_width  ·  line 295

get_profile_width(profile_name: str) -> Optional[float]

Returns the profile width from the installed Tekla catalogue; A7 validates that it is finite and positive.

### get_profile_web_thickness  ·  line 318

get_profile_web_thickness(profile_name: str) -> Optional[float]

Reads the profile web/wall thickness used by the reference connection-geometry calculations.

### _index_to_letters  ·  line 335

_index_to_letters(n: int) -> str

Converts a positive grid index to spreadsheet-style labels, including rollover beyond Z.

### generate_y_axis_labels  ·  line 352

generate_y_axis_labels(n_trans_bays: int) -> list

Creates the transverse grid-label sequence for the configured number of bays.

### get_bolt_parameters  ·  line 362

get_bolt_parameters(bolt_dia: float) -> dict

Returns the existing bolt-hole tolerance, hole diameter and edge-distance rule for the selected diameter. It supports reference detailing geometry; it does not replace IDEA bolt checks.

### clean_tekla  ·  line 385

clean_tekla(progress=None) -> None

Enumerates the active model, preserves grids/grid surfaces and DefaultGroup, removes components before remaining parts, verifies cleanup and saves. Optional callbacks report long scans/deletions; failures propagate.

### clean_tekla.snapshot  ·  line 393

snapshot()

Nested Tekla cleanup helper that snapshots deletable objects and preserved grid/default-group IDs, sorting components ahead of their generated parts.

## src/radiator_support/staad_integration.py

### memberData2type  ·  line 125

memberData2type(member_data: dict) -> str

Classifies a member-data record for load/design/grouping logic.

### memberName2type  ·  line 138

memberName2type(name: str) -> str

Classifies generated member names, including split beam and brace names, into consistent member categories.

### _get_top_node_coordinates  ·  line 152

_get_top_node_coordinates(node_cache: Dict[tuple, int], top_nodes: List[int]) -> Dict[tuple, int]

Selects the coordinate records associated with the model's top-node IDs.

### _get_boundary_nodes  ·  line 166

_get_boundary_nodes(node_cache: Dict[tuple, int], top_nodes: List[int], boundary_count: int) -> List[int]

Classifies top nodes by boundary count for corner/edge/inner load distribution.

### _get_corner_nodes  ·  line 179

_get_corner_nodes(node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]

Returns top nodes at plan corners using the shared boundary classifier.

### _get_edge_nodes  ·  line 183

_get_edge_nodes(node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]

Returns noncorner top nodes on the plan perimeter for load distribution.

### _get_inner_nodes  ·  line 187

_get_inner_nodes(node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]

Returns top nodes inside the plan boundary for load distribution.

### _get_edge_members  ·  line 191

_get_edge_members(members: List[Dict[str, Any]], node_cache: Dict[tuple, int], top_nodes: List[int]) -> List[int]

Finds perimeter members from geometry/top-node information for the reference loading rules.

### _apply_distributed_nodal_load  ·  line 212

_apply_distributed_nodal_load(load: OSLoad, corner_nodes: List[int], edge_nodes: List[int], inner_nodes: List[int], mass_kg: float, is_seismic: bool=False) -> None

Converts input mass to force where required and distributes nodal load among corner, edge and inner nodes according to the existing reference method.

### _apply_distributed_seismic_joint_weight  ·  line 234

_apply_distributed_seismic_joint_weight(load: OSLoad, corner_nodes: List[int], edge_nodes: List[int], inner_nodes: List[int], mass_kg: float) -> None

Adds distributed seismic joint weight through the reference nodal-distribution method.

### _apply_radiator_installation_load  ·  line 243

_apply_radiator_installation_load(load: OSLoad, corner_nodes: List[int], edge_nodes: List[int], inner_nodes: List[int], radiator_mass_kg: float) -> None

Applies the radiator installation mass/load using the corresponding reference distribution, keeping it separate from unrelated load types.

### _apply_radiator_wind_nodal_loads  ·  line 253

_apply_radiator_wind_nodal_loads(load: OSLoad, node_cache: Dict[tuple, int], top_nodes: List[int], wind_case_title: str, load_configuration: Dict[str, Any], grid_spec: Dict[str, Any]) -> None

Computes equipment wind force and its nodal effects from geometry, exposure and direction. Bay/radiator/fan dimensions are converted to metres before areas and moment arms are formed.

### _create_directional_wind_cases  ·  line 322

_create_directional_wind_cases(load: OSLoad, members: List[Dict[str, Any]], grid_spec: Dict[str, Any], wind_definition_id: int, first_case_id: int, is_open_structure: bool) -> List[int]

Creates directional frame-wind cases from the model extents, wind definition and open/closed structure setting.

### create_design_parameters  ·  line 369

create_design_parameters(design: OSDesign, grid_spec: Dict[str, Any], members: List[Dict[str, Any]], longitudinal_beams: List[Any], transverse_beams: List[Any], columns: List[Any], longitudinal_braces: List[Any], transverse_braces: List[Any]) -> None

Creates code-design parameters and assigns effective/unrestrained lengths to the appropriate member categories from grid geometry.

### _apply_load_configuration  ·  line 415

_apply_load_configuration(load: OSLoad, design: OSDesign, node_cache: Dict[tuple, int], load_configuration: Dict[str, Any], members: List[Dict[str, Any]], support_nodes: Set[int], grid_spec: Dict[str, Any]) -> Dict[str, Any]

Builds the reference primary loads, combinations, envelopes, strength load list and design parameters. It returns application/error metadata; failed load application cannot be treated as verified results.

Key calls: staad_common.calculate_wind_pressure_profile

### is_node_on_beams  ·  line 685

is_node_on_beams(node: int, beam_ids: list[int], beam_node_map: Dict[int, Tuple[int, int]]) -> bool

Tests whether a node is an endpoint of any member in the supplied beam set.

### add_to_group  ·  line 691

add_to_group(groups: dict, group_name: str, node: int, joint_member_ends: Dict[int, List[Tuple[int, int]]], member_data_by_id: dict[int, dict]) -> None

Adds a node's connected member/end records to a named joint family, preserving Tekla identities and member roles.

### find_num_bracings  ·  line 711

find_num_bracings(node: int, joint_member_ends: Dict[int, List[Tuple[int, int]]], longitudinal_bracings: list[int], transverse_bracings: list[int]) -> Tuple[int, int]

Counts longitudinal and transverse braces meeting a node to distinguish joint topologies.

### group_name_suffix_from_bracings  ·  line 722

group_name_suffix_from_bracings(num_longitudinal_bracings: int, num_transverse_bracings: int) -> str

Builds the family-name suffix from longitudinal/transverse brace counts.

### _create_joint_groups  ·  line 742

_create_joint_groups(joint_member_ends: Dict[int, List[Tuple[int, int]]], member_data_by_id: dict[int, dict], node_cache: Dict[tuple, int], beam_node_map: Dict[int, Tuple[int, int]], support_nodes: Set[int]) -> dict

Classifies supports and top joints into corner, edge and inner families with their brace counts. A3 planning and A4 result extraction share this topology logic.

### summarize_forces_displacements  ·  line 801

summarize_forces_displacements(data: dict[int, list[list[float]]]) -> Dict[str, Any]

Summarizes component minima/maxima and the corresponding entity IDs from a result dictionary.

### find_connected_members_forces  ·  line 828

find_connected_members_forces(node_id: int, joint_member_ends: Dict[int, List[Tuple[int, int]]], forces: dict[int, list[list[float]]]) -> List[Dict[str, Any]]

Collects the force vector at each member end attached to one node, not at the remote end.

### find_joint_group_governing_forces  ·  line 853

find_joint_group_governing_forces(joint_group: Dict[str, Any], forces_by_load_case: Dict[Any, dict[int, list[list[float]]]], load_cases: List[Any], component_map: Dict[str, List[str]]=JOINT_DESIGN_FORCE_COMPONENTS) -> Dict[str, Dict[str, Dict[str, Any]]]

Finds component extremes across load cases and joints while retaining governing case, node, member and end. Current selection includes columns and all six force/moment components.

### joint_load_effect  ·  line 879

joint_load_effect(joint: Dict[str, Any], forces: dict[int, list[list[float]]], name: str) -> Dict[str, Any]

Builds one coherent IDEA effect from all members at one joint in one case. Through beams use opposite positions; distinct braces receive distinct names matching template slots.

### build_joint_group_load_effects  ·  line 903

build_joint_group_load_effects(joint_group: Dict[str, Any], forces_by_load_case: Dict[Any, dict[int, list[list[float]]]], load_cases: List[Any], load_case_titles: Dict[Any, str]) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], List[Dict[str, Any]]]

Deduplicates governing case/node pairs and creates their complete joint load effects. This is component-extreme screening, not a mathematical proof of every nonlinear multiaxial interaction.

### find_extreme_forces  ·  line 925

find_extreme_forces(forces: dict[int, list[list[float]]], forces_summary: list[Dict[str, Any]], summary_key: str, force_key: str, beam_node_map: Dict[int, Tuple[int, int]], lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any], ends: List[int]=[0, 1]) -> Dict[str, Any]

Updates an envelope's force extrema and attached governing case/node/member evidence.

### find_extreme_member_displacements  ·  line 947

find_extreme_member_displacements(member_displacements: dict[int, list[list[float]]], member_displacements_summary: list[Dict[str, Any]], summary_key: str, displacement_key: str, beam_node_map: Dict[int, Tuple[int, int]], lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any]) -> Dict[str, Any]

Updates extreme member displacement records and governing evidence.

### find_extreme_node_displacements  ·  line 969

find_extreme_node_displacements(displacements: dict[int, list[list[float]]], displacements_summary: list[Dict[str, Any]], summary_key: str, displacement_key: str, lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any]) -> Dict[str, Any]

Updates extreme nodal displacement records used by the serviceability summary.

### find_extreme_support_reactions  ·  line 988

find_extreme_support_reactions(support_reactions: dict[int, list[list[float]]], support_reactions_summary: list[Dict[str, Any]], summary_key: str, force_key: str, lc: str, lc_title: str, joint_member_ends: Dict[int, List[Tuple[int, int]]], summary_results: Dict[str, Any]) -> Dict[str, Any]

Updates extreme support-reaction records without substituting arbitrary sample reactions.

### initialize_summary_results  ·  line 1003

initialize_summary_results() -> Dict[str, Any]

Creates the nested summary structure for forces, reactions and displacements before scanning actual results.

### initialize_forces_summary_results  ·  line 1054

initialize_forces_summary_results(force_types: list[str]) -> Dict[str, Any]

Initializes per-component force-extreme tracking for the requested categories.

### compute_summary_results  ·  line 1066

compute_summary_results(forces, forces_summary, support_reactions, support_reactions_summary, beam_node_map, node_displacements, node_displacements_summary, member_displacements, member_displacements_summary, lc, lc_title, joint_member_ends, summary_results) -> None

Applies the extrema helpers to one load case and updates the combined summary dictionary.

### send_to_staad  ·  line 1088  ·  async

send_to_staad(data: dict) -> dict

A4's thin adapter delegates to the live implementation; the radiator implementation resets/builds STAAD, assigns verified properties/loads, runs unattended analysis and extracts forces, design ratios and normalized displacements. Live errors are not replaced by simulation.

Key calls: openstaad.AnalyzeEx

### main  ·  line 1583  ·  async

main() -> None

Standalone adapter diagnostic entry point. It is separate from the managed A1-A8 server route and should not be confused with the normal workflow launcher.

Key calls: send_to_staad

## src/server.py

### create_app  ·  line 211

create_app()

Constructs the FastAPI application, request schemas, static UI, manual integration endpoints and agentic router. It defines the HTTP boundary; it does not itself perform a structural calculation.

Key calls: send_to_tekla, send_to_staad

### create_app.reserve_desktop_tools  ·  line 385  ·  async

reserve_desktop_tools(request, call_next)

Rejects manual model-changing requests with HTTP 409 while this server's agentic controller owns the desktop tools. This protects against conflicting actions through this API, not against another unrelated application or server.

### create_app.radiator_support_ui  ·  line 399

radiator_support_ui()

Serves the radiator support HTML page. Rendering or refreshing this page is not an instruction to start a calculation.

### create_app.favicon  ·  line 403

favicon()

Returns the application's icon asset to the browser.

### create_app.generate_radiator_support  ·  line 407

generate_radiator_support(request: RadiatorSupportGenerateRequest)

Validates the manual generation request and returns generated geometry and its summary. This keeps the senior application's manual generation route available.

### create_app.to_tekla  ·  line 428

to_tekla(request: ToTeklaRequest)

Converts the manual Tekla request into the integration payload and calls the modeler. Actual adapter exceptions become HTTP errors rather than fabricated success.

Key calls: send_to_tekla

### create_app.to_staad  ·  line 444  ·  async

to_staad(request: ToStaadRequest)

Converts the manual STAAD request into the analytical payload and awaits the live STAAD integration. It returns the actual analysis/result dictionary.

Key calls: send_to_staad

### create_app.to_ideastatica  ·  line 463

to_ideastatica(request: ToIdeaStaticaRequest)

Passes a manual connection request to the IDEA adapter, excluding absent optional fields. It preserves the manual integration route alongside automation.

### start_api_server  ·  line 486

start_api_server(port)

Builds the API application and starts Uvicorn using configured host and port. Development defaults bind to loopback.

### main  ·  line 503

main()

Parses the server port, loads environment configuration and starts the local API. It is the application entry point used by start_server.bat.

## Compatibility modules with no local function definitions

src/common/idea_statica_integration.py re-exports the A5 implementation; src/radiator_support/tekla_integration.py re-exports A7; src/radiator_support/generate_grid.py re-exports A3 generation. These preserve import paths used by the senior/manual workflow. Empty __init__.py files mark packages. The default-parameter JavaScript is data, not an independent engineering solver.

# Appendix B. Frontend functions and event handlers

The following covers every named JavaScript function in the production UI and results renderer. Anonymous event callbacks are grouped afterward by user action, with source locations.

## src/static/radiator_support/index.html

addBraceRow(containerId, defaults)

Adds an editable bay/mirror row to a brace list; its remove handler deletes that UI row.

readBraceList(containerId)

Reads the configured brace rows into bay/mirror input records.

num(id)

Reads a form control as a floating-point number.

int(id)

Reads a form control as a base-10 integer.

str(id)

Reads a form control as text.

buildPayload()

Builds the grid and geometry-generation request from the form. It does not calculate structural acceptance in the browser.

resizeRenderer()

Resizes the Three.js renderer/camera for the current canvas area.

animate()

Schedules rendering and updates the 3D scene controls.

indexToLetters(n)

Builds spreadsheet-style grid labels, including indices beyond Z.

makeIndexLabel(text)

Creates a visual grid-index label for the 3D viewer.

toThree(p)

Transforms stored geometry coordinates into the viewer convention.

clearStructureView()

Removes scene geometry/labels and clears old STAAD selection state, disposing graphics resources.

staadClassColor(memberClass)

Chooses display colors for STAAD member classes; colors are not design grades.

renderStructure(members, staadResult = null)

Draws physical or returned analytical members, grid indices and support/joint data in the viewer.

renderStaadJoints(members, diagonal)

Creates joint markers from returned analytical connectivity.

renderStaadViewSummary(members)

Shows metadata for the analytical structure currently displayed.

staadHit(event, jointsOnly = false)

Raycasts pointer coordinates against displayed members/joints for selection.

highlightStaadJoints(nodeIds, memberIds)

Highlights exactly the requested joint/member IDs and restores others' normal colors.

selectStaadJoint(nodeId)

Selects a joint and shows its connected-member information.

selectStaadJointGroup(name)

Highlights all joints/members belonging to the chosen family.

renderSummary(summary)

Displays generated geometry counts and summary fields.

buildLoadConfiguration()

Reads the load form into the backend request, preserving the manual workflow's field names.

isBooleanExpression(expression)

Recognizes boolean-like parameter text so the UI can render an appropriate control.

renderIdeaOperationParameters(jointGroup)

Builds the manual parameter editor for the selected family using stored defaults.

buildOperationParameters()

Reads edited key/expression pairs for a manual IDEA request.

updateAgentSteps(agentStatuses, loopA, loopB)

Updates A1-A8 stage colors and revision badges from saved statuses and actual loop counts.

renderAgenticRunState(state)

Renders one saved/final state into geometry, software-result panels, statuses and Gate 3. It explicitly distinguishes IDEA-checked families from standard details.

restoreSavedAgenticRun()

Loads an explicit or latest saved run using GET only, follows a currently owned active run, and restores its final display after refresh. It never starts a replacement calculation; orphaned progress does not disable the button forever.

## src/static/radiator_support/results.js

resultElement(tag, text, className)

Creates a result-display element with text and optional CSS class.

resultLabel(key)

Converts a result key to a readable label.

resultValue(value)

Formats a result value for display.

resultTable(parent, records, columns, columnLabel = resultLabel)

Builds a table from result records and selected columns.

resultSection(parent, title, open = false)

Creates an expandable result section.

resultData(parent, data)

Recursively renders structured result data.

openResultPanel(kind)

Opens the requested integration-results drawer/panel.

renderIntegrationStatus(content, result, analysis = false)

Displays counts, analysis status and reported integration failures.

memberName2type(name)

Infers display categories from generated/split member names.

staadLoadCases(result)

Combines and deduplicates load-case IDs across envelopes while retaining their titles.

renderStaadLoadCases(result)

Populates the analytical load-case selector.

renderStaadJointGroups(result)

Populates family selection from the actual response and removes stale choices.

renderIdeaEnvelopes(result)

Populates the manual IDEA envelope selector from returned analysis data.

staadForceTable(parent, title, forces, units)

Displays member force components and units in a table.

renderStaadTooltip(parent, result, loadCaseId, member, nodeId)

Shows member metadata, both member ends, selected case forces and support reactions, or explicit missing data.

renderStaadResults(result)

Renders the actual STAAD result hierarchy and associated selectors.

renderTeklaResults(result)

Shows Tekla insertion/verification counts and errors.

renderIdeaStaticaResults(result)

Shows returned IDEA checks and operation parameters for a calculated connection.

initializePaneDividers()

Registers resizable-pane drag behavior for the viewer and results drawer.

| Event / callback group | Behavior |
| --- | --- |

| Brace add/remove controls | Edit form rows only; later request building reads bay and mirror values. |

| Window resize / animation | Updates the camera and renderer; no engineering acceptance change. |

| Pointer down/up/move/cancel/leave | Distinguishes click/drag, selects or highlights joints/members and shows/hides tooltips. |

| Joint-group / load-case change | Updates highlight/selection and prevents stale tooltip data. |

| Generate Structure button | POSTs the manual generation payload and renders the returned geometry. |

| Manual Tekla / STAAD / IDEA buttons | Build the existing manual payloads, call their compatibility routes and render the actual responses. |

| Run Agentic Design button | Starts one managed POST, polls /active, renders its final state and resets button/cancel controls. |

| Stop button | Calls the cancel endpoint for currentAgenticRunId and reports stop errors without inventing success. |

| Gate 3 acceptance button | Submits explicit final acceptance for the currently displayed eligible run. |

| Pane divider callbacks | Resize the results/viewer layout; they do not modify the engineering model. |

# Appendix C. Data models and test-case index

Classes below are defined in production source. Pydantic models describe accepted request/state shape; plain exception classes identify adapter failures; the settings dataclass stores immutable runtime configuration. A schema declaration is not itself a structural calculation.

## Proposal (src/agentic/advisor.py:14)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| option_id | str / None |

| rationale | str |

## GridInputSchema (src/agentic/agents/a1_requirements_agent.py:18)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| n_long_bays | int |

| n_trans_bays | int |

| bay_long_mm | float |

| bay_trans_mm | float |

| col_height_mm | float |

| total_structure_height | float |

| starting_point | list[float] |

| beam_material | str |

| column_material | str |

| longitudinal_brace_material | str |

| transverse_brace_material | str |

| beam_profile | str |

| column_profile | str |

| longitudinal_brace_profile | str |

| transverse_brace_profile | str |

| beamClass | str |

| columnClass | str |

| longitudinalClass | str |

| transverseClass | str |

| longitudinal_beam_extra_mm | float |

| vertical_base_eccentricity_mm | float |

| gusset_plate_thickness_mm | float |

| embedded_plate_thickness_mm | float |

| bracing_distance_from_column_mm | float |

| longitudinal_brace_bays | list[Dict[str, Any]] |

| transverse_brace_bays | list[Dict[str, Any]] |

## Settings (src/agentic/config.py:43)

Base types: plain Python class

| Field | Declared type |
| --- | --- |

| host | str |

| port | int |

| run_timeout | int |

| agent_timeout | int |

| frame_revisions | int |

| connection_revisions | int |

| model_call_limit | int |

| model | str |

| live_enabled | bool |

| llm_enabled | bool |

| templates | Path |

## GraderResult (src/agentic/state.py:12)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| passed | bool |

| score | float |

| summary | str |

| details | Dict[str, Any] |

| corrective_action_needed | bool |

| corrective_action | Optional[Dict[str, Any]] |

## AgentLogEntry (src/agentic/state.py:22)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| agent_id | str |

| agent_name | str |

| status | str |

| message | str |

| timestamp | str |

| loop_info | Optional[str] |

## WorkflowState (src/agentic/state.py:32)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| run_id | str |

| status | str |

| current_agent | str |

| logs | List[AgentLogEntry] |

| agent_statuses | Dict[str, Dict[str, Any]] |

| raw_input | Dict[str, Any] |

| grid_config | Dict[str, Any] |

| load_config | Dict[str, Any] |

| readiness | Dict[str, Any] |

| simulation_mode | bool |

| model_call_count | int |

| model_events | List[Dict[str, Any]] |

| frame_candidate | Dict[str, Any] |

| active_column_profile | str |

| active_beam_profile | str |

| active_long_brace_profile | str |

| active_trans_brace_profile | str |

| design_generation | int |

| candidate_history | List[str] |

| staad_results | Dict[str / int, Any] |

| frame_grader_result | Optional[GraderResult] |

| loop_a_count | int |

| max_loop_a | int |

| loop_a_history | List[Dict[str, Any]] |

| ideastatica_results | Dict[str, Any] |

| joint_grader_results | Dict[str, GraderResult] |

| active_operation_params | Dict[str, List[Dict[str, Any]]] |

| loop_b_count | Dict[str, int] |

| max_loop_b | int |

| loop_b_history | List[Dict[str, Any]] |

| validation_report | Dict[str, Any] |

| loop_c_count | int |

| max_loop_c | int |

| loop_c_history | List[Dict[str, Any]] |

| package_hash | str |

| tekla_results | Dict[str, Any] |

| final_report | Dict[str, Any] |

| gate3_approved | bool |

| accepted_by | Optional[str] |

| acceptance_timestamp | Optional[str] |

| error | Optional[str] |

## create_app.BraceBay (src/server.py:230)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| bay | int |

| mirror | bool |

## create_app.RadiatorSupportGridConfig (src/server.py:236)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| n_long_bays | int |

| n_trans_bays | int |

| bay_long_mm | float |

| bay_trans_mm | float |

| col_height_mm | float |

| starting_point | List[float] |

| longitudinal_brace_bays | List[BraceBay] |

| transverse_brace_bays | List[BraceBay] |

| vertical_base_eccentricity_mm | float |

| base_plate_offset_above_column_base_mm | float |

| gusset_plate_thickness_mm | float |

| embedded_plate_thickness_mm | float |

| bracing_distance_from_column_mm | float |

| total_structure_height | float |

| longitudinal_beam_extra_mm | float |

| beam_material | str |

| column_material | str |

| longitudinal_brace_material | str |

| transverse_brace_material | str |

| beam_profile | str |

| column_profile | str |

| longitudinal_brace_profile | str |

| transverse_brace_profile | str |

| beamClass | str |

| columnClass | str |

| longitudinalClass | str |

| transverseClass | str |

## create_app.RadiatorSupportGenerateRequest (src/server.py:273)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| grid | RadiatorSupportGridConfig |

## create_app.Member (src/server.py:279)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| p1 | List[float] |

| p2 | List[float] |

| profile | str |

| material | str |

| type | str |

| name | str |

| class_ | str |

## create_app.ToTeklaRequest (src/server.py:292)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| members | List[Member] |

| grid | Optional[Dict[str, Any]] |

| joint_groups | Dict[str, Any] |

## create_app.ToStaadRequest (src/server.py:305)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| grid | Optional[Dict[str, Any]] |

| members_split_beams | List[Member] |

| load_configuration | Optional[Dict[str, Any]] |

## create_app.ConnectionSelector (src/server.py:315)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| name | Optional[str] |

| id | Optional[int] |

| index | Optional[int] |

## create_app.PlateReference (src/server.py:323)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| plate_side | str |

| member | str |

| part_type | str |

## create_app.MemberUpdate (src/server.py:329)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| member_name | str |

| cross_section_mprl_name | str |

| material_mprl_name | str |

| gama_pitch | Optional[float] |

| aligned_plate | Optional[PlateReference] |

| related_plate | Optional[PlateReference] |

## create_app.MemberLoading (src/server.py:339)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| member_name | str |

| n | float |

| vy | float |

| vz | float |

| mx | float |

| my | float |

| mz | float |

| position | Optional[str] |

## create_app.LoadEffect (src/server.py:351)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| name | str |

| member_loadings | List[MemberLoading] |

| load_case | Optional[int] |

| node | Optional[int] |

| governs | List[str] |

## create_app.OperationParameter (src/server.py:360)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| key | str |

| expression | str |

## create_app.ToIdeaStaticaRequest (src/server.py:368)

Base types: BaseModel

| Field | Declared type |
| --- | --- |

| template_project_path | str |

| connection_selector | ConnectionSelector |

| member_updates | List[MemberUpdate] |

| load_effects | List[LoadEffect] |

| output_dir | str |

| save_project_path | Optional[str] |

| operation_parameters | List[OperationParameter] |

| extract_raw_results | bool |

| extract_pdf_report | bool |

## Python test cases by module

test_ladders_and_expression_stepping

test_domain_geometry_checks

test_frame_grader

test_joint_grader

test_loop_c_package_recovery

test_sqlite_persistence

test_orchestrator_stops_on_failed_analysis

test_staad_execution_errors_cannot_pass

test_missing_nonfinite_or_negative_ratios_cannot_pass

test_missing_displacement_checks_cannot_pass

test_displacement_flag_cannot_hide_exceeded_limit

test_live_exception_stops_a4_without_simulation

test_failed_analysis_stops_before_a5

test_dominant_bracing_failure_targets_brace

test_false_status_with_small_value_is_not_claimed_over_limit

test_local_deformation_is_not_graded_as_plate_strain

test_empty_or_incomplete_connection_results_cannot_pass

test_generic_plate_summary_is_utilization_not_raw_strain

test_template_cannot_relax_or_disable_physical_checks

test_weld_correction_changes_only_exposed_weld_parameter

test_correction_never_downsizes_or_rewrites_formula

test_failed_run_cannot_be_accepted

test_a5_retries_only_changed_group

test_rejected_update_stops_without_fake_correction

test_live_adapter_rejects_unknown_template_parameter_before_calculation

test_live_adapter_does_not_ignore_parameter_update_rejection

test_exhausted_retry_stops_before_other_groups

test_strength_envelopes_combined_without_sample_loads

test_staad_reanalysis_invalidates_connection_evidence

test_property_mapping_and_zero_id_rejection

test_tekla_partial_publication_never_reaches_reporting

test_component_readback_rejects_different_attribute

test_tekla_attribute_rejection_propagates

test_full_graph_corrects_frame_and_joint_then_reaches_review

test_wrong_grade_is_not_reused

test_requested_grade_is_created_if_missing

test_incorrect_added_material_is_rejected

test_components_first_and_grids_preserved_and_saved

test_cascaded_deletions_are_not_repeated

test_failed_delete_raises_without_saving

test_success_return_without_deletion_does_not_loop_forever

test_dependent_objects_are_retried

test_unexpected_grid_deletion_is_reported

test_commit_failure_is_reported

test_save_failure_is_reported

test_grid_only_model_is_saved

test_default_load_group_is_preserved

test_other_load_groups_are_deleted

test_reset_backup_and_reopen_same_path

test_repeated_cleanup_keeps_previous_backup

test_no_open_file_rejected

test_missing_file_rejected

test_physical_model_rejected

test_running_analysis_rejected

test_failed_switch_does_not_overwrite

test_failed_reopen_reports_backup

test_residual_geometry_rejected

test_saved_nonempty_input_rejected

test_missing_template_rejected_before_save

test_final_model_geometry_is_verified

test_asynchronous_open_waits_for_path_and_empty_geometry

test_save_exception_propagates_before_reset

test_staad_common_import_does_not_load_openstaadpy

test_retry_isolated_from_source_and_clears_publication_acceptance

test_analysis_or_unvalidated_failures_cannot_skip_to_publication

test_changed_evidence_stops_before_any_tekla_mutation

test_existing_form_passes_a1_without_an_extra_combination_file

test_default_cases_preserve_reference_coefficients_and_envelopes

test_explicit_project_combinations_are_not_replaced

test_reviewed_mode_retains_explicit_criteria_requirement

test_reference_plan_separates_calculation_and_standard_details

test_reviewed_mode_keeps_full_joint_requirement

test_missing_plan_does_not_silently_reduce_existing_scope

test_plan_cannot_drop_a_required_calculation

test_stale_scope_rejected_for_new_geometry_groups

test_reference_plan_cannot_substitute_a_weaker_material

test_standard_detail_groups_are_not_recorded_as_idea_passes

test_double_brace_transverse_parameters_reach_tekla_in_mm

test_integer_attributes_use_the_integer_readback_overload

test_environment_does_not_override_process_settings

test_opposing_wind_and_temperature_are_rejected

test_empty_unknown_or_nonfinite_combinations_rejected

test_gpt_can_select_only_a_supplied_candidate

test_unapproved_gpt_action_falls_back_without_applying_it

test_model_budget_prevents_network_call

test_new_store_instance_sees_another_workers_update

test_worker_crash_cannot_leave_run_running

test_cancel_stops_worker_and_persists_cancelled_state

test_stalled_worker_times_out

test_cancel_during_process_startup_still_terminates_child

test_saved_input_uses_supported_option_and_verifies_actual_file

test_silent_sdk_failure_never_starts_solver

test_missing_sdk_command_inserted_after_loads_before_envelopes

test_existing_analysis_retained

test_ambiguous_order_is_rejected

test_report_includes_wrapped_error

test_uses_the_connected_member_end_and_preserves_sign

test_separate_cases_do_not_share_results

test_missing_forces_raise_instead_of_creating_zero_loads

test_distinct_braces_receive_distinct_template_member_names

test_governing_forces_use_joint_end_only_and_dedupe_load_cases

test_load_effect_contains_all_joint_members_at_joint_end

test_column_only_support_receives_its_real_governing_loads

test_moment_governing_case_is_not_lost_to_force_only_selection

test_corners_edges_and_interior_preserve_total_weight

test_single_bay_has_only_corners

test_supplied_top_nodes_are_used_regardless_of_elevation

test_no_top_nodes_rejects_load

test_edge_members_require_endpoints_on_same_boundary

test_configuration_separates_installation_and_edge_dead_loads

test_radiator_wind_forces_use_two_rows_and_endpoint_multipliers

test_radiator_wind_force_scales_with_speed_squared

test_radiator_wind_requires_two_rows

test_directional_wind_cases_use_unique_rows_and_requested_ranges

test_native_load_case_ids_validate_and_serialize

test_a4_results_survive_next_graph_node_validation

test_results_reload_from_sqlite

## JavaScript test modules

tests/test_staad_results.cjs: viewer categories, envelopes, result labels, tooltips, highlighting, resource disposal and grid labeling. tests/test_agentic_restore.cjs: read-only final restoration, active-run following and orphaned-state button release.

# Appendix D. Evidence sources and reproducibility

The installed project uses the IDEA 26.0/API 4 SDK/service path checked in A2. Online documentation can describe newer API 5 endpoints. Use the installed matching SDK and local service contract rather than copying a different release's routes blindly.

[Primary reference: IDEA API version history and service architecture](https://developer.ideastatica.com/docs/api/connection-api/connection_api_overview.html)

WORKFLOW_VALIDATION.md

Latest live results and historical diagnoses; latest section supersedes earlier scope blockers.

SENIOR_WORKFLOW_REVIEW.md

Manual/automatic source comparison and the user's scope decision.

WORKBOOK_REVIEW.md

Read-only spreadsheet review and reasons conflicting inputs were not blindly adopted.

output_validation/reference-end-to-end-summary.json

Recorded passing package and explicit family scope.

output_validation/reference-publication-retry.json

Full saved final retry state, including lineage.

output_validation/reference-full-run.json

Original calculation run and the initial failed A7 diagnostic.

output_validation/senior-reference-hashes.json

Fingerprints of inspected senior source/input files.

data/worker_logs/run_45715b4c.log / run_80c61cca.log

Actual analysis and publication progress logs.

output_validation/cantilever_probe_report.json

Independent local force/moment/displacement calibration evidence.

This PDF was created by static source inspection plus recorded artifacts. The documentation build does not execute application functions, contact engineering software, invoke GPT, read credential values or execute workbook macros. It does not rerun the live engineering test.

Rebuild command: .venv\Scripts\python.exe docs\build_technical_guide.py. Documentation tooling requires reportlab; PDF QA uses the already available PyMuPDF. These are documentation tools, not new application runtime requirements.

Generated companion files: Agentic_Steel_Workflow_Technical_Guide.md (editable text), technical_guide_inventory.json (all documented symbols/models/tests), and technical_guide_manifest.json (source SHA-256 fingerprints). The complete source fingerprint list stays in the manifest to keep this PDF readable.

Review rule: implementation fact != live evidence != engineering approval.
Always state the declared scope and the limit of the evidence when presenting the project.
