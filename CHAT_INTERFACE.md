# Structural engineering chat

The existing page now has an **Engineering chat** button at the bottom right:
<http://127.0.0.1:8060/radiator_support>.

The repository currently serves a static HTML/JavaScript frontend, not React.
Chat is an isolated enhancement to that frontend. Existing engineering endpoints,
the engineering implementations, calculations, graders and correction rules are retained.

## Start and stop

From PowerShell:

```powershell
Set-Location 'C:\Users\lb89380\projects\prajwal\agentic-flow'
.\.venv\Scripts\python.exe src\server.py --port 8060
```

Stop this foreground server with **Ctrl+C**. Run the same command to start it again.
Only one server should use port 8060. If the app is already running, use that instance.

Chat uses the existing backend `OPENAI_API_KEY`, `OPENAI_BASE_URL` and
`PLANT_AGENT_MODEL`. `PLANT_CHAT_ENABLED=false` disables chat. No credentials are sent
to the browser. Existing `PLANT_LLM_ENABLED` and `PLANT_DEEP_AGENTS_ENABLED` settings
are unchanged; the conversational tool selector uses its own chat switch.
Restart the server after changing `.env`. Never paste credentials into a message.

### Run on the existing RDP engineering machine

Copy the changed source files listed below into the matching repository/branch on
the RDP machine. Keep that machine's `.env`, templates, engineering model paths,
native software installations and existing Python environment. Do not copy the local
`.venv`, `data` databases or test outputs over the RDP environment. Install the updated
requirements in that machine's existing environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The added attachment readers are `pypdf`, `openpyxl` and `Pillow`; engineering service
versions and software paths are unchanged.

Start `src\server.py` from that machine's repository folder with its existing virtual
environment, then open its local `/radiator_support` page. Request the desired action
in chat. Existing A2 readiness checks still verify the configured STAAD, IDEA and Tekla
services before a live candidate is generated. No software location or service URL
was changed for chat. First try a preview, then a live validated structure, STAAD,
IDEA, package validation and Tekla publishing. General requests to run the inputs
or agentic workflow select the complete A1-A7 path, including publication;
explicit stage-only requests remain limited to those actions. Gate 3 acceptance
still uses the existing approval control.

## Use the interface

1. Open Engineering chat. Grid and load inputs from the existing form are attached
   automatically; there are no input/run/live-tool checkboxes.
2. Use **+** in the compact message bar to attach files. Each file appears in a card
   above the input with its name, type, size and reader status. Click **×** to remove
   an unsent file; removed files are not sent to the model. Re-selecting the same file
   works. Sent attachments stay in the message history. A valid grid JSON can populate
   the form; removing it before sending restores the prior form if you have not edited
   those imported values. Supported project JSON shape:

   ```json
   {
     "grid": {"n_long_bays": 8, "n_trans_bays": 5},
     "load_configuration": {}
   }
   ```

   Grid-only JSON is also accepted by the file picker. Claimed solver outputs or
   grades in an imported file never become trusted engineering evidence.
3. Ask, for example: “Preview the frame using my current inputs.” A preview uses
   existing geometry functions and is explicitly **unverified**.
4. On the configured engineering/RDP host, request a live action directly in chat.
   The UI enables native tool dispatch for that message; the existing readiness,
   evidence checks, revision limits and human approval gate remain authoritative.
5. Matching displayed results are selected automatically. If form inputs have changed,
   the previous solver evidence is not rebound. An explicit run ID can still be used
   in a read-only question to inspect a different saved result.
6. Expand tool events to see parameters, results and errors. Open output JSON from
   the returned link. Geometry/results use the existing viewer. The header has a back
   arrow, title, **...** menu (Rename, Share, Copy chat), history icon and New chat icon.
   Share opens the system share sheet when available or copies a conversation link;
   that link requires access to the same app server. Copy chat copies the full text
   and tool results. It does not publish a public copy.
7. The New chat icon saves the current conversation and opens an empty one, including
   while a request is running. The old request continues in its own saved conversation
   and cannot write messages into the new window. Use the history icon to reopen it.
   All stored turns are returned when reopening/copying, not just the latest 50.
8. The stop icon cancels the chat task and its active worker. Inspect the saved run
   before retrying. A server restart marks interrupted turns as errors without replay.

Example conversations:

A full run request stays `AWAITING_APPROVAL` after A7 prepares the report.
Click **Accept & Sign Off** on the dashboard. Actual persisted workflow acceptance
then marks that chat request `COMPLETED`, adds one approval confirmation and
enables another message in the same conversation. Chat cannot approve itself.
The waiting request survives refresh/restart and is not replayed against native tools.

| Request | Available action and prerequisite |
| --- | --- |
| “Change only the longitudinal bay count to 10.” | Update validated inputs; previous engineering evidence is not reused. |
| “Show an unverified preview.” | A1 validation and existing A3 geometry; no native solver required. |
| “Generate a live validated structure.” | A1, A2 readiness, A3; native tools/catalogues/templates required. |
| “Run STAAD analysis only.” | Selected live candidate; A4 and bounded existing Loop A. |
| “Design the connections in IDEA.” | Current passing STAAD evidence; A5 and existing Loop B. |
| “Check the complete engineering package.” | A5c audit and bounded connection-evidence recovery. |
| “Publish the verified package to Tekla.” | A5c evidence gate, A6 publishing, A7 reporting; final human Gate 3 retained. |
| “Run the full engineering workflow including Tekla publishing.” | A1–A7 LangGraph, with its correction loops and safety stops. |
| “Explain the selected run’s failures.” | Read saved grader results; no engineering execution. |

The model selects tools from descriptions. There is no keyword-to-tool routing table.
Missing prerequisites return errors; the model cannot supply its own passing grades.

### Attachment formats and limits

| Format | What chat can read |
| --- | --- |
| JSON, TXT, CSV, TSV, Markdown, YAML, XML, logs and other UTF-8 text | Text; valid grid JSON may populate project inputs. |
| PDF | Selectable text with page references; no OCR or interpretation of embedded page graphics. |
| DOCX, PPTX | Document/slide text and table cell text; embedded objects are not interpreted. |
| XLSX, XLSM | Cell references, formulas and previously saved cached values; no recalculation, VBA or macros execute. Preview limits are disclosed. |
| PNG, JPG, WEBP, GIF, BMP, TIFF | Image resized for the configured model; requires vision support. First frame/page only. |
| SVG, draw.io | Diagram XML/text, including standard compressed draw.io content; scripts never render or execute. |
| ZIP | Entry names and bounded text/document previews; no disk extraction, nested archive parsing or macro execution. |
| Any other extension | File card accepted; plain text is read when possible. Unsupported binary formats are clearly labeled. Legacy DOC/XLS and proprietary CAD formats require a supported export. |

There is no universal reader for every proprietary format. Cards say `unsupported`,
`limited` or `unreadable` when content cannot be interpreted. Limits: 20 MB per file,
eight attachments and 40 MB combined per message. Readers cap text, sheet/page/ZIP
previews and disclose truncation. Archives with unsafe paths, encryption or excessive
expansion are not processed. Uploading only parses locally; submitting a chat message
allows its attachment text/image to reach the configured model.

Normalized text/image previews are stored under `data/chat_attachments`, associated
with the conversation. Original executable files are not retained. The read-only
`read_attachment` tool retrieves text in chunks; files never become trusted solver
evidence. **×** deletes the local normalized draft; previously sent messages remain
part of the conversation audit.

## Architecture

```text
Existing form / JSON + chat message
  -> POST /api/v2/chat/messages -> persisted turn, immediate 202
  -> existing configured ChatOpenAI model + LangChain structured tools
  -> model selects tool -> logged execution -> result returned to model
  -> further selected tools or natural-language response
  -> polling displays events, progress, final response and existing run viewer

Engineering tools
  -> existing shared run lock / RunController / isolated STA worker
  -> A1–A7 stage functions, graders and routing decisions
  -> existing workflow SQLite evidence and human Gate 3
```

Selected-stage continuations fork the saved run before changing it, retaining the
source run for review. Changing inputs clears the selected run. The simplified UI
allows requested native actions; direct API clients still default to native tools
disabled unless they supply `allow_native_tools=true`. This is checked outside the model. Chat exposes no shell, arbitrary
file access or engineer acceptance tool. Requests are sequential and bounded to ten
model rounds and twelve tool calls; the existing engineering revision budgets remain.

Project inputs and selected result summaries are included in requests to the configured
model service when chat is used. Keep this server local or behind your existing access
controls; this change does not introduce a new authentication/deployment system.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /api/v2/chat/config` | Model name and configuration-presence status; not an authentication test. |
| `POST /api/v2/chat/sessions` | Create a saved empty conversation. |
| `GET /api/v2/chat/sessions` | List saved conversations and running status. |
| `PATCH /api/v2/chat/sessions/{session_id}` | Rename with `{"title":"New title"}`. |
| `POST /api/v2/chat/sessions/{session_id}/attachments?filename=...` | Raw file bytes; returns normalized reader metadata and an attachment ID. |
| `DELETE /api/v2/chat/sessions/{session_id}/attachments/{attachment_id}` | Remove an unsent draft; refuses deletion from a sent message. |
| `POST /api/v2/chat/messages` | Start a turn; returns `session_id`, `turn_id`, `RUNNING` with HTTP 202. |
| `GET /api/v2/chat/turns/{turn_id}` | Events, assistant/error, updated inputs and engineering progress. |
| `GET /api/v2/chat/sessions/{session_id}` | Saved context and complete conversation. |
| `POST /api/v2/chat/turns/{turn_id}/cancel` | Cancel the active turn. |

Message request:

```json
{
  "message": "Preview this frame without running a solver",
  "project_input": {"grid": {"n_long_bays": 8, "n_trans_bays": 5}},
  "allow_native_tools": false
}
```

For later turns supply `session_id`. `run_id` optionally selects an existing run.
To submit attachments include `attachment_ids: ["file_..."]` in the message. Only
files belonging to that conversation are accepted. Conversation responses include
sent attachment metadata and `draft_attachments` for unsent files.
Validation errors return 422, unknown sessions 404, conflicting active turns/input
bindings 409 and missing/disabled model configuration 503. A model rejection after
submission is recorded as an ERROR turn, with no fabricated assistant response.

## Files

| Files | Change |
| --- | --- |
| `src/agentic/chat.py` | Tool descriptions, model loop, input validation, events and protected dispatch. |
| `src/agentic/chat_actions.py` | Selected actions reuse existing stage functions and correction routes. |
| `src/agentic/chat_router.py` | Chat HTTP endpoints and task cancellation. |
| `src/agentic/chat_store.py` | Separate conversation SQLite store, `data/chat.db`. |
| `src/agentic/chat_attachments.py`, `tests/test_chat_attachments.py` | Local normalized attachment storage, bounded format readers and tests. |
| `src/static/radiator_support/chat.js`, `chat.css` | Chat panel, history, JSON import/export, polling and status. |
| `src/static/radiator_support/index.html` | Small bridge to existing inputs and rendering; chat assets included. |
| `src/server.py` | Registers chat routes. |
| `src/agentic/deep_runtime.py` | Allows chat to reuse model setup with an independent enable switch. |
| `src/agentic/execution.py`, `worker.py` | Optional selected action in existing worker; default full-run behavior retained. |
| `.env.example` | Documents the chat switch. `.env` was not modified. |
| `tests/test_chat.py` | Controlled model/tool/API, evidence, loop and gate regressions. |
| `scripts/validate_chat_routing.py` | Opt-in real-model routing checks with mocked engineering. |

## Tests and evidence

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_chat.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_chat*.py'
.\.venv\Scripts\python.exe scripts\run_backend_regressions.py
# Requires valid model credentials; makes actual model requests, never native FEA:
.\.venv\Scripts\python.exe scripts\validate_chat_routing.py --live-model
# If node is on PATH:
node --test tests/test_agentic_restore.cjs tests/test_staad_results.cjs
```

Verified during implementation:

- Attachment update: **23 controlled chat/attachment tests pass**, including PDF text,
  DOCX and ZIP content, Excel formulas, image delivery to a controlled model, ownership,
  deletion and attachment text returned through the model tool loop. Real local
  browser/API upload and X-removal checks also pass without calling an external model.
  Receipt: `output_chat_validation/attachment-real-api-smoke.json`; screenshots:
  `attachment-live-api-desktop.png`, `attachment-live-api-mobile.png` in that folder.
  The panel now uses the existing engineering UI navy/cyan palette and grid background.

- 14 controlled chat tests pass, including sequential tool results returned to the
  model, input changes, native permission, invalid evidence, Loops A/B/C and Gate 3.
  Session tests also cover complete history beyond 50 turns, renaming during an
  active request and starting a fresh chat without cancelling the previous one.
- Redesign browser regression: `python scripts/smoke_chat_ui.py` (requires an optional
  Playwright/Chromium test environment and the local app running). It uses mocked
  chat responses and never calls an LLM or engineering solver. Checks include the
  compact input, automatic context, New chat during a run, history, rename, share,
  copy and attachments. Receipt: `output_chat_validation/chat-redesign-smoke.json`.
- 12 existing frontend tests pass.
- Real isolated worker preview: `run_03ffbfe6`, 8×5 grid, 69 physical members,
  `READY`, `preview_only=true`; no structural pass is claimed.
- Real browser smoke: form bridge, uploaded JSON, message submission, tool events,
  response display and mobile layout pass using a controlled chat response.
  Receipt/screenshots: `output_chat_validation/browser-smoke.json`,
  `chat-desktop.png`, `chat-mobile.png`. Current form input validates locally.
- Untouched HEAD baseline: 169 backend tests, one failure and three subtest errors.
  Updated suite: 182 backend tests, the same one failure and three subtest errors.
  The same failures occur with chat changes: missing native Tekla assembly/catalogue
  prevents the existing reference tests from reaching their expected assertions.
  See `output_validation/backend_loop_test/regression-console.txt`.
- Real-model routing attempt: the configured endpoint returned
  `OpenAIAuthenticationError`; no automatic live routing success is claimed.
  Receipt: `output_chat_validation/model-routing.json`. Correct credentials and rerun
  the opt-in script before claiming verified natural-language selection.
- A further test sending the actual current form to the external model was blocked
  by automatic approval review because project data would leave the machine; it did
  not execute. Local form validation continued without external submission.
- Live STAAD, IDEA and Tekla solves/publication require the configured RDP/native
  installations and were not tested on this computer.

Runtime server logs use the `uvicorn.error.structural_chat` logger. Each event records
session/turn IDs and one of `message_received`, `tools_selected`, `tool_started`,
`tool_completed`, `tool_error`, `assistant_response` or `turn_error`. Parameters/results
are redacted before storage and logging. Worker logs remain in
`data/worker_logs/<run_id>.log`. Controlled tests demonstrate the event sequence;
they are not evidence of a live model or native solver calculation.

## Project guardrails and approved changes

Chat now checks project relevance before granting tools or applying incoming form
inputs. Unrelated requests receive a scope message; mixed or unclear requests need
clarification. Questions have read-only capabilities. Attachments cannot grant
permission, expose server secrets or override engineering checks. This is a local
operator workflow, not a new authentication or multi-tenant authorization system.

Registered operations reuse the existing Python/LangGraph worker. Exact existing
joint-group names and exposed IDEA parameter keys are retained. Numeric aliases
such as “group 1” and “parameter 1” are not inferred from dropdown positions.

Example (use a group/key actually present in your selected run):

> In `inner_node_2_longitudinal_bracings`, change `GRD_d` from 16 mm to 20 mm.
> Recalculate the connection checks. Do not change any other group or publish Tekla.

1. Chat reads saved group/parameter evidence and validates explicit units.
2. It displays current/requested values and the required execution steps.
3. **Approve & Apply** authorizes that exact persisted proposal; **Reject** leaves
   inputs and models unchanged. The LLM cannot approve its own proposal.
4. Changed source data invalidates approval. Repeated approval cannot replay it.
5. Connection-only changes fork a candidate, reuse eligible unchanged evidence,
   solve selected families and run the existing A5c package checks.
6. Failed checks do not trigger unapproved parameter changes or a broader redesign.
7. Selected results are saved under `output_exports/dynamic_changes/<run_id>/`.

`update_project_inputs` now prepares a preview rather than immediately editing the
chat project. Approval can save inputs only or run the explicitly requested stage
with its prerequisites. A normal request to run unchanged inputs still uses the
A1–A7 path and pauses for manual final sign-off.

The selected-edit adapter supports exposed numeric length parameters with an
existing IDEA-to-Tekla map. Its `mm`/`m` conversion follows the existing factor of
1000; it does not infer units from ambiguous template labels. Unknown parameters,
arbitrary formulas and individual-joint edits without a validated mapping are not
supported. Frame spacing remains a frame-level input.

### Selected Tekla publication

The full A6 publisher retains its existing behavior and additionally records a
versioned component manifest. Selected publication requires that verified manifest
and matching model path, component IDs, names, numbers and source attributes.
Ownership is bound to members at the same physical joint, not simply a shared
column. Physical member geometry, sections and materials are read back before
and after a selected update.
It modifies existing supported attributes, reads them back and checks recorded
unselected attributes. It never calls full-model cleanup.

After validating selected edits, a later explicit publication request creates a
new approval preview. It reuses the saved IDEA calculations, repeats the A5c package
validation, updates supported selected Tekla attributes and prepares A7 reporting.
Final engineer sign-off remains manual. After a chat run completes, fails or is
cancelled, the next-run inputs reload the read-only
`src/radiator_support/frame_specs.json` master. Pending sign-off and stage
continuations retain the current run's inputs. Saved and exported runs keep their
actual custom inputs. Selected edits remain attached to their saved candidate
rather than silently changing global defaults.

Legacy publications without a manifest, shared components touching unselected
groups, and parameters requiring unimplemented derived-layout recalculation are
blocked. In particular, component 144 diameter/stiffener-layout changes cannot be
patched by silently retaining old calculated layout dimensions. A failed write
attempts rollback and reports whether recorded attributes were restored; native
commits are not claimed to be database transactions. A native model save failure
requires inspection before retrying. The native adapter needs live verification
before claiming a particular real model was updated successfully.

### Limits and metrics

See `.env.example` for request-rate, model-call, tool-call, token and context limits.
Chat usage records reported tokens, calls, model/tool durations and total latency.
Cost is unset unless input/output prices per million tokens are configured.
Deep Agents/advisor calls remain separate engineering metrics in run progress.
Default engineering limits remain unchanged. Run-specific overrides support
member ratio, drift denominator, bolt, weld, plate and deformation thresholds;
see `docs/run_policy.md` for the exact accepted ranges. For example, request
"maximum bolt utilization 90% for this run only" to use 90 instead of the default
75 for the next new run. Native failed checks and manual sign-off still apply.
No new dependencies are introduced.

Approved operations also save `project_input.json`, `change_summary.json` and,
when a candidate exists, `workflow_output.json` alongside their isolated outputs.
They do not rewrite `.env` or `frame_specs.json`.

Pending previews survive refresh. Executing proposals interrupted by a server
restart are marked interrupted and are not automatically replayed. Use the normal
server startup command after any active run finishes, then refresh the browser.

Controlled checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_dynamic_chat.py'
.\.venv\Scripts\python.exe scripts\smoke_chat_changes.py
```

The browser script requires an installed Edge browser and Playwright. Its HTTP and
native APIs are fixtures; it does not contact the production server or modify a
model. Use `PLANT_CHAT_DATABASE_PATH` and `PLANT_RUNS_DATABASE_PATH` pointing to
disposable stores when running a whole test suite, to keep startup recovery in
test imports separate from live conversations.
