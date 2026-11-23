# civil-steel-standard-web
Web App to design Steel Standard structures in Tekla, Staad.Pro and Idea Statica

## Agentic workflow checks

Live runs stop when an application call fails, STAAD results are incomplete,
or a connection check cannot be corrected using exposed numeric template
parameters. Live failures are never replaced with simulated passing results.
Connection retries recalculate the changed group and reuse unchanged passing
groups. All strength envelopes must supply actual member properties and loads;
sample loads and unrelated connection templates are not substituted.

The UI displays saved agent progress while a run is executing. Only one agentic
run can use the desktop tools at a time. A failed run cannot be signed off.
The final review gate still requires an engineer; automated checks do not
establish engineering acceptance by themselves.

The server loads `.env` from the repository without overriding existing process
environment variables. `PLANT_HOST` defaults to loopback and `PLANT_PORT` defaults
to 8080. A separate STA-initialized worker owns each run; the UI's **Stop current
run** button terminates that worker. `PLANT_RUN_TIMEOUT_SECONDS` and
`PLANT_AGENT_TIMEOUT_SECONDS` bound the run and stalled calculation time.
IDEA's solve budget is the greater of the per-effect agent budget and
`PLANT_IDEA_CALCULATION_TIMEOUT_SECONDS` (default 600 seconds), capped by the
total run budget. Services with calculation-job endpoints are polled and receive
a cancellation request on timeout. Older services use a bounded synchronous
request; its timeout does not confirm that their native computation stopped.
On Windows, readiness and IDEA attachment also wait briefly for native solver
cleanup and reject a new operation while an IDEA-owned `k2fem64.exe` is active.
An IDEA request with several load effects receives the per-effect allowance
multiplied by the effect count, capped by the total run limit.

`PLANT_AGENT_MODEL`, `OPENAI_API_KEY`, and `OPENAI_BASE_URL` configure the optional
GPT correction advisor. It selects only prevalidated candidate IDs when a
failure is severe or repeated. Every proposal is rechecked and recalculated.
Network failures and invalid proposals fall back to the deterministic candidate;
`PLANT_MODEL_CALL_LIMIT` caps calls. Set `PLANT_LLM_ENABLED=false` to disable it.
Credentials are never included in workflow state or browser responses.

### Engineering chat

The radiator-support page includes an **Engineering chat** panel with saved
conversations and document attachments. Set `PLANT_CHAT_ENABLED=true` and
configure the backend model, endpoint and API key. Chat uses its own switch;
`PLANT_LLM_ENABLED=false` and `PLANT_DEEP_AGENTS_ENABLED=false` still select
the deterministic A1-A8 workflow without disabling conversational tool selection.

Chat calls the existing guarded stages and retains their graders, correction
loops and human acceptance gate. While a chat turn is calculating, conflicting
manual actions stay locked, including when another conversation is opened.
An active worker's intermediate geometry and results update the main viewer.
General requests such as "21x5 current input run" or "run agentic workflow"
use the complete A1-A8 path, including Tekla publication. Explicit stage-only
requests retain their narrower scope. At A8, chat waits for the dashboard's
manual **Accept & Sign Off** action; it records completion after approval and
then accepts further messages in the same conversation. Waiting survives refresh.
Unverified previews clear prior calculation/sign-off displays and cannot be
sent directly through the manual native-tool buttons, including after refresh.
See [CHAT_INTERFACE.md](CHAT_INTERFACE.md) for API and attachment details.

### LangSmith tracing

The existing LangChain chat/tools and LangGraph workflow use LangSmith's SDK
integration. Configure these backend `.env` values and restart the idle API:

```env
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=plant-engineering
LANGSMITH_API_KEY=<your-private-LangSmith-key>
```

The API worker inherits these settings. Traces can include model prompts,
responses, tool inputs/outputs and workflow stage timing. Tracing is independent
of the A1-A8 LLM/Deep Agents switches and does not change engineering criteria.
Keep the real key in `.env`; `.env.example` intentionally leaves it blank.
Some tracing keys permit ingestion but not project listing or trace read-back.

### Deep Agents execution for A1-A8

Install `requirements.txt` in the project virtual environment and set
`PLANT_DEEP_AGENTS_ENABLED=true` in the backend `.env`. Configure
`PLANT_AGENT_MODEL`, `OPENAI_API_KEY` and `OPENAI_BASE_URL` for the intended
OpenAI-compatible deployment. The key stays on the backend; do not paste it
into the form or commit it. GPT-6 tool calling uses the Responses API with
storage disabled. The older correction advisor retains its separate call budget.

When enabled, every A1-A8 stage runs a real `deepagents.create_deep_agent`
planning/tool/review cycle inside the existing LangGraph sequence. Each stage
can plan with `write_todos` and execute its own approved engineering tool once.
The worker runs these tools on its STA thread, including async STAAD calls.
Deep Agents cannot edit inputs or files, invoke another stage, repeat a native
tool, override graders, or sign off Gate 3. A model error, skipped tool or
duplicate execution request fails the run; there is no silent legacy fallback.
The existing frame/joint graders and Loops A/B/C remain authoritative.

`PLANT_DEEP_AGENT_MODEL_CALL_LIMIT` defaults to 64 model requests per run;
each stage visit also has a four-request cap and bounded request timeouts.
`deep_agent_model_call_count` and `deep_agent_events` in saved workflow JSON
record actual requests, token usage, tool executions, stage outcomes and reviews.
Repeated stage visits produce separate audit records. A8 includes an execution
summary and still pauses for engineer acceptance. Set
`PLANT_DEEP_AGENTS_ENABLED=false` to explicitly select the deterministic
workflow. The SDK integration tests use the real Deep Agents graph with a
controlled chat model and mocked engineering tools; they are distinct from
live FEA and live model verification.

The default `poc2_automation` mode uses the existing reference application's
8 load combinations and 3 envelopes automatically when the form supplies no
custom combinations. A1 does not require a new rubric file. The A1?A8 analyzers,
graders, correction loops, failure handling and result checks remain active.
Connection scope follows the senior project's saved handoff: inner longitudinal
and combined longitudinal/transverse families receive IDEA calculations; other
families use the standard Tekla components (1004, 144, 20 and 30). Available
double-longitudinal templates map explicitly to their inner families. Required
IDEA templates are checked during generation. The final package distinguishes
IDEA-checked families from standard details; standard details are never recorded
as separate IDEA passes. `S355JR` uses the senior templates' `S 355` static-analysis
catalogue entry in this mode, retaining the requested Tekla material and recording
the catalogue mapping in the package.

Explicit `load_configuration.load_combinations` or a JSON file selected by
`PLANT_LOAD_COMBINATIONS_FILE` override the reference defaults. The file contains
a `combinations` array; entries have `name`, `envelope`, `factors`, and optionally
`envelope_id`. Custom combinations are validated. `reviewed_engineering` mode
retains mandatory explicit criteria and early template coverage checks. Reference
procedure defaults are recorded as `reference_procedure`; this does not label
them as an independently approved project rubric.

`POST /api/v2/agentic/retry-publication/{run_id}` retries a failed A7 handoff in
a new tracked run. A6 first rechecks the original STAAD/IDEA evidence and template
hashes; changed or failed evidence requires a complete new analysis. The failed
source run is preserved. A7 verifies typed attributes and saves the model before
the package reaches final engineer review.

`PLANT_TEMPLATE_DIRECTORY` selects the template folder (default:
`sample_projects`). The live IDEA adapter checks actual members, applied
parameters and physical check limits. Missing templates are not replaced with
unrelated connections or simulated passing results. IDEA summary values are
utilization percentages, not raw strain percentages; see the
[official result contract](https://developer.ideastatica.com/docs/api/connection-api/connection_api_concepts.html).

## Installation

Prerequisites (Windows):
- Python 3.10 or newer, on `PATH`. Get it from [python downloads](https://www.python.org/downloads/windows/). Preferred [python stable release 3.14](https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe). Install for your own user id if you do not have admin access.
- Tekla Structures, STAAD.Pro and IDEA StatiCa Connection installed and
  licensed. Each tool is only needed for its own "Send to ..." button; the
  server starts without them.

1. Create and activate a virtual environment in the project folder
   (Command Prompt):

   ```bat
   cd path\to\civil-steel-standard-web
   python -m venv .venv
   .venv\Scripts\activate.bat
   ```

   In PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. If
   script execution is blocked, run
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

2. Install the packages into the venv:

   ```bat
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

   `openstaadpy` is not on PyPI; it ships with STAAD.Pro. Install it into the
   venv from the STAAD.Pro installation (adjust the path and version to your
   install):

   ```bat
   pip install "C:\Program Files\Bentley\Engineering\STAAD.Pro 2025\STAAD\OpenSTAADPy\Setup\openstaadpy-25.0.1.1.tar.gz"
   ```

   `ideastatica-connection-api` must match the installed IDEA StatiCa product
   release (for example, 26.0.x for a 26.0 service); edit its pin in
   `requirements.txt` if yours differs. Tekla has no
   pip package: its Open API DLLs are loaded through `pythonnet` from
   `C:\TeklaStructures\2025.0\bin`, or from the `TEKLA_BIN_PATH` environment
   variable if set.

3. Start the web server:

   ```bat
   start_server.bat
   ```

   The script activates `.venv` itself if it exists, so it can also be
   started by double-clicking it.

   Then open http://localhost:8080/radiator_support in your browser.

Before using the integrations, open Tekla Structures with a model and
STAAD.Pro, and start IDEA StatiCa's `IdeaStatiCa.ConnectionRestApi.exe`
(default URL `http://localhost:5000`, override with `IDEA_STATICA_BASE_URL`).

## Cleaning open models

These utilities are **destructive** and save changes automatically. Back up
important models first. Importing either module has no application side effects;
calling its synchronous `main()` performs cleanup without prompting.

```python
from common.tekla_common import clean_tekla
from common.staad_common import clean_staad

clean_tekla()
clean_staad()
```

They can also be run separately with `python src/common/tekla_common.py --clean` or
`python src/common/staad_common.py --clean`.

- [tekla_common.py](./src/common/tekla_common.py) uses the existing Tekla connection setup
  (`pythonnet`, `TEKLA_BIN_PATH`). It deletes all enumerated model objects except
  grids (including radial grids), grid surfaces/planes and Tekla's `DefaultGroup`
  load group (reported explicitly, since Tekla does not allow deleting it).
  Components are
  connections/details that can generate parts, bolts and welds; these are removed
  along with the structural objects. It commits and saves the open model.
  It does not remove drawings, catalogs, attributes or project-directory files.
- [staad_common.py](./src/common/staad_common.py) uses `openstaadpy` and requires an open,
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
