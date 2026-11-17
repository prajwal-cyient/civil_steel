# Workflow validation

## 17 November 2026: Deep Agents A1-A8 verified live

Deep Agents SDK 0.7.39 and langchain-openai 1.8.0 are integrated into every
A1-A8 stage while LangGraph retains sequencing, recovery loops and engineering
gates. The local `.env` enables this mode. A blank workflow-mode setting now uses
the documented `poc2_automation` default rather than failing at A1.

Live run **`run_feea9ea5`** used the existing 8x5 reference input, actual configured
`gpt-6-sol` requests, and native STAAD, IDEA and Tekla on isolated model copies.
No model or engineering adapters were mocked in this run.

| Check | Observed result |
| --- | --- |
| Deep Agent stage executions | A1-A8 all `VERIFIED`; exactly one stage tool call per stage |
| Actual GPT requests | 16, two per stage; token usage and reviews saved |
| A1-A7 engineering statuses | All `PASSED` |
| A8 | Report generated; `AWAITING_APPROVAL` at Gate 3 |
| Physical / analytical members | 90 / 168 |
| STAAD | Maximum ratio 0.1305644222222222; zero errors, one warning |
| IDEA combined family | Bolts 26.3463%, welds 68.7001%; native checks passed |
| IDEA longitudinal family | Bolts 34.8327%, welds 63.7858%; native checks passed |
| Tekla | 90 verified members, 204 connections with verified attributes, zero failures |
| Package hash | `020f07e5bcb3` |

The 13 remaining joint families retain the reference procedure's standard Tekla
details; they are not separate IDEA calculation passes. Gate 3 was not accepted.
The original open models were restored and their saved source STAAD file and
Tekla database hashes remained unchanged after the isolated run.

The first model smoke request exposed the deployment's rejection of GPT-6
reasoning/tool calls via Chat Completions. The integration now uses Responses
with storage disabled; the subsequent smoke and full live run passed.

All **169 Python regression tests passed**, including real Deep Agents SDK graphs
with a controlled model and mocked engineering adapters. Those tests exercise
Loops A/B/C, exact-once stage execution on the worker COM thread, unauthorized
tool rejection, call limits, model failure, and preservation of the engineer gate.
The successful live run needed zero correction iterations, so live correction
loops are not claimed to have been exercised by this run. `pip check` and Git
whitespace checks also passed. No commit or push was made.

Evidence: `output_exports/deep_agents_live_20261117/run_feea9ea5/` contains
input, complete output, per-agent JSON, final report, execution audit and native
software files. `live_validation.json` records source-model restoration checks.
`src/agentic/deep_runtime.py` implements bounded model/tool/review cycles; existing
deterministic engineering tools and graders remain authoritative.

## Historical validation: 15 November 2026

## Latest result: senior-scope workflow reached A8

The user confirmed reproduction of the senior project's calculation scope.
Source run `run_45715b4c` completed A1–A6 with live STAAD and IDEA results. Its
first A7 attempt exposed the integer-attribute read-back bug. After that fix,
publication retry `run_80c61cca` revalidated the unchanged source evidence at A6,
successfully published and saved Tekla, and reached A8 with no error.

Current status: **PAUSED_AT_GATE3 — final engineer acceptance pending**.

| Evidence | Actual result |
| --- | --- |
| STAAD generated analytical members | 402 |
| Maximum member ratio | 0.3234004 |
| Drift check | Passed H/200 |
| STAAD status | Zero errors, one warning |
| Required IDEA families | Both passed |
| Combined double-brace family | Bolts 21.25%, welds 56.48% |
| Longitudinal double-brace family | Bolts 23.83%, welds 65.78% |
| Tekla physical members | 194 inserted and verified |
| Tekla connections | 464 inserted with verified attributes |
| Tekla failures | Zero |
| Package hash | `a17410b76665` |

The two IDEA families cover the applicable inner double-brace connections. The
other 11 families use the senior workflow's standard Tekla details; they are
explicitly identified and are **not** reported as separate IDEA passes. Full
all-family engineering verification remains a different mode. See
`SENIOR_WORKFLOW_REVIEW.md` for the source comparison and scope decision.

No engineering section/detail correction was needed in this live run. Regression
tests exercise failed-frame and failed-connection correction loops, candidate
validation, changed-evidence rejection and terminal failure handling. Acceptance
limits and standard attribute-file contents were not relaxed to obtain a pass.

Validation: **117 Python tests and 12 JavaScript tests passed**; inline JavaScript
syntax and Git whitespace checks passed. The UI restores saved results after a
refresh without starting another calculation. The server is idle and available
at `http://127.0.0.1:8080/radiator_support?run_id=run_80c61cca`.

Artifacts (local, Git-ignored):

- `output_validation/reference-end-to-end-summary.json`
- `output_validation/reference-publication-retry.json`
- `output_validation/reference-full-run.json` (original attempt, including its
  failed A7 diagnostic)
- `output_validation/senior-reference-hashes.json`
- `data/worker_logs/run_45715b4c.log` and `run_80c61cca.log`

The notes below record earlier findings and should be read as historical evidence.

## Earlier live rerun: STAAD input and unattended analysis fixed

Run `run_8afc6b20` passed A1–A4 and stopped at A5 because
`support_node_no_bracing` has no compatible registered template. Final IDEA,
Tekla publication and A8 acceptance have not been verified for this run.

The installed STAAD 2025 API silently omitted `Command.PerformAnalysis(6)`.
Using `PerformAnalysis(0)` inserts the command correctly. The workflow now saves
and inspects the actual `.std` input before analysis, verifying that
`PERFORM ANALYSIS` precedes `DEFINE ENVELOPE`. It backs up the generated input
before attempting this correction and fails if the saved command is still absent.
Loads, envelope definitions and grading thresholds are preserved.

Cleanup now enables silent mode before switching models. Analysis uses
`AnalyzeEx(1, 1, 0)` so STAAD does not wait for a manual Done click after solving.
Live analysis completed with zero errors and one warning. The warning concerns
additional UBC/IBC steps if tension/compression, repeat loads, reanalysis or
optimization are used; it is retained in the `.anl` report.

Failed analyses now report solver errors without appending secondary complaints
about outputs that cannot exist after a failed solve. Missing connection-template
errors list all unmapped groups. Local diagnostic inventory:
`output_validation/connection-template-gaps.json`.

Validation: 101 Python tests and 9 JavaScript tests passed. Compatible connection
templates and verified member/end mappings remain necessary to complete A5–A8.

## Subsequent connection handoff fixes

Column forces were omitted from the governing-case selector, leaving column-only
support groups with no IDEA load effects. The selector now includes all six force
and moment components for columns, beams and braces, preserving the actual case
and joint forces for each selected extreme. A moment-governing case is no longer
discarded simply because its force components are not extreme.

Recomputation using actual saved forces from `run_8afc6b20` now produces nonempty
load effects for all 13 groups in both strength envelopes. The column-only support
group has six selected effects per envelope. This is a diagnostic replay of saved
forces, not a new verified IDEA calculation or an assertion that checking component
extremes proves every possible nonlinear interaction. Artifact:
`output_validation/corrected-load-effect-coverage.json`.

Cross-section reuse now checks its embedded material name as well as its profile.
An S235 or S275 section cannot silently satisfy an S355JR request. Adding a section
also requires material identity read-back. No unreviewed grade substitutions were
introduced. Live library probing rejected `S355JR` as an available IDEA material;
the existing templates use the library label `S 355`, which is not silently treated
as an approved project material mapping.

The scratch `baseplate_master.ideaCon` contains two designs, not a unique project
specification: connection 01 has a 20 mm plate, 600 mm C25/30 concrete depth and
250 mm anchor embedment; connection 02 has a 16 mm stiffened plate, 400 mm concrete
depth and 300 mm embedment. Its column uses S275. Neither connection has brace
members for the braced support groups. These designs were inspected through the
IDEA API without altering source files and were not registered as verified support
templates. Project baseplate, anchor, concrete and steel material specifications
are required to choose or adapt them correctly.

Validation after these changes: all 105 Python tests passed. Full A5–A8 completion
remains unverified and blocked on compatible connection definitions.

## Earlier compatibility update

The default `poc2_automation` mode now follows the reference application's
existing eight combinations and three envelopes automatically. Missing explicit
criteria no longer block A1, and template coverage no longer blocks A3 in this
mode. Strict preflight requirements below apply to `reviewed_engineering`.
All analyzers, graders, correction loops and actual-result failure checks remain.
OpenSTAADPy 25.0.1.1, supplied with STAAD.Pro 2025, is installed in the local venv.
The older strict-preflight observations below are historical validation evidence,
not the current default startup behavior.


The automation has been hardened and tested. A verified final design for the
current project has **not** been produced. The application is idle and the
previous long-running design was cancelled.

## Implemented

- Repository `.env` loading; credentials stay on the backend.
- A separate STA-initialized worker for each run, live progress, cancellation,
  per-calculation and overall timeouts, and exclusive access to the desktop tools.
- Actual readiness checks for saved STAAD/Tekla models and the IDEA API.
- Rejection of failed analyses, missing/nonfinite results, incomplete member
  coverage, inconsistent displacement flags, and missing serviceability evidence.
- Explicit project load combinations, with opposing wind/temperature combinations
  rejected before model modification. Corrected equipment-wind mm-to-m conversion.
- Catalogue-constrained frame changes; optional GPT selection among validated
  candidates, a call budget, audited proposals, and deterministic fallback.
- Correct IDEA utilization-percentage interpretation and verification of template
  physical strain/deformation limits. Failed check status cannot be overridden.
- Exact/unique member resolution, no fabricated loads or unrelated template
  fallback, parameter exposure checks, applied-value read-back, changed-group
  retries, and termination when a confirmed change produces unchanged checks.
- Calculation signatures bind results to the frame, loads, parameters, and
  template contents. Package validation independently rechecks the evidence.
- Tekla attribute-load/insert failures propagate, and member geometry, profile,
  material and configured connection attributes are read back. Grid labels and
  levels follow the actual model rather than hardcoded elevations.
- Failed/cancelled runs cannot be signed off or reach final reporting as successes.

## Verified

- 91 Python tests and 9 JavaScript results-viewer tests pass, including a
  controlled full graph with frame and connection correction loops.
- The configured GPT deployment passed one bounded structured-output smoke test.
  No engineering design was changed by that test.
- Real API worker cancellation returned CANCELLED and left the API responsive.
- Non-destructive API preflights stop at A1 for missing combinations and at A3
  for missing connection templates. All three desktop readiness checks passed.
- Real temporary STAAD section probes read back SHS120x120x6, HE140A, and STEEL.
  The SDK 26 / STAAD 2025 assignment boolean mismatch is handled by independent
  section/material read-back.
- A real one-member 5 m cantilever with a 10 kN tip load completed without
  analysis errors or warnings. Results after conversion:

| Quantity | STAAD result | Hand calculation |
|---|---:|---:|
| Support force | 10.000001 kN | 10 kN |
| Support moment | 50.000004 kNm | 50 kNm |
| Tip translation | 15.262557 mm | 15.243902 mm (bending only) |

The deflection check used a 0.1 mm tolerance; shear deformation contributes to
STAAD's beam result. This benchmark validates the current installed base-unit
conversion, not the complete radiator design or all supported design codes.
Original STAAD inputs were backed up and restored with unchanged SHA-256 hashes.
Detailed probe artifacts are in `output_validation/` (locally ignored by Git).

## Project inputs still needed

1. Engineer-reviewed load combinations/rubric. The neighboring project has a
   draft policy marked unapproved and an empty template registry; neither was
   imported as verified engineering data. Supply combinations in the run payload
   or a JSON file selected by `PLANT_LOAD_COMBINATIONS_FILE`.
2. Compatible connection templates and verified member/end/axis/parameter mappings
   for the current frame. The current preflight requires these missing groups:

- `corner_node`
- `inner_node_1_transverse_bracing`
- `inner_node_2_longitudinal_1_transverse_bracing`
- `inner_node_2_longitudinal_bracings`
- `inner_node_no_bracing`
- `longitudinal_edge_node_2_longitudinal_bracings`
- `longitudinal_edge_node_no_bracing`
- `support_node_1_longitudinal_1_transverse_bracing`
- `support_node_1_longitudinal_bracing`
- `support_node_1_transverse_bracing`
- `support_node_no_bracing`
- `transverse_edge_node_1_transverse_bracing`
- `transverse_edge_node_no_bracing`

The inspected reference folder does not supply matching files for these groups.
Additional experimental scratch templates exist, but inspection showed different
connection/member names. They were not silently substituted. Some may be reusable
once their topology, axes, boundary conditions and parameter mappings are verified.
Renaming a file does not establish compatibility.

Full live STAAD ? IDEA ? Tekla acceptance remains unverified until those project
inputs are supplied and the complete run passes. The application now reports that
boundary explicitly instead of producing a false passing result.

## API contracts checked

- [IDEA result summaries and utilization units](https://developer.ideastatica.com/docs/api/connection-api/connection_api_concepts.html)
- [IDEA SDK/service product-release compatibility](https://developer.ideastatica.com/docs/api/connection-api/connection_api_getting_started.html)
- [OpenAI structured correction output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
