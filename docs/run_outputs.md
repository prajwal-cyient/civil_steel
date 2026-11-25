# Local run archives

New runs create a directory under `store output run/` in the repository root:

```text
run_<id>_23_November_2026_17-20-44_IST/
  00_input/                 original_input.json, effective_input.json, run_policy.json
  01_agents/A1/attempt_001/ input.json, output.json, process.json
  01_agents/A2/ ... A8/     one folder per agent and execution attempt
  02_software/STAAD/        results, input and available native files per attempt
  02_software/IDEA_StatiCa/ exact group names, one folder per calculation or reused result
  02_software/Tekla/        publication JSON, verified components and model file copy
  03_final_output/          workflow state, report, validation, timing and approval JSON
  04_logs/                 recorded events and available worker log
  manifest.json            stage coverage, archive warnings and software manifest paths
```

- The original input is the JSON actually dispatched to the workflow. The
  effective input records normalized inputs and the rules used. The master
  `frame_specs.json` and native source files are never rewritten by the archive.
- Agent input/output files contain the shared state at that execution boundary.
  Process JSON contains recorded events, statuses, loop counts and timing. It
  does not contain private LLM reasoning. Failed operations retain partial
  evidence; agents that never execute are marked `NOT_RUN`.
- Retries use separate agent attempt folders. IDEA calculations, including
  individual correction attempts, keep separate copies before a later solve can
  overwrite the native service's shared output paths. Reused results are marked.
- Final JSON updates during execution and after manual engineer acceptance.
  An A8 report alone does not indicate acceptance; read `engineer_approval.json`.
- Each native copy manifest lists copied files with SHA-256 hashes, missing or
  locked files, and unavailable outputs. Raw IDEA results or PDF reports are
  archived only when the solver generated them; archiving does not enable new
  calculations or report generation.
- STAAD copies the saved `.std` and available same-model companion files after
  returned analysis evidence. Tekla copies the saved model folder after a
  successful A7, including attributes, excluding locks and temporary files.
  The captured files are checked for changes during copying. This is an
  **open-model file copy, not an independently reopen-verified Tekla backup**.
  Read its `snapshot_status`; incomplete copies are explicitly marked. The
  active model is not closed, switched or saved again by the archiver.
- Archiving adds disk I/O and uses additional storage. Failures are logged and
  recorded in `output_archive.warnings` where possible; they never turn failed
  engineering checks into passes or silently stop an otherwise valid workflow.
- The folder is ignored by Git. Existing historical runs are not replayed or
  reconstructed. New runs need the updated API/worker after restart. Test
  databases use archive folders next to their isolated database, not live data.

## Evaluation and correction audit

- `04_logs/events.json` retains the existing agent messages used by the UI.
  `04_logs/audit_events.json` adds operation timings, progress, evaluations,
  exception frame locations and corrections, correlated by run, agent and attempt.
- `03_final_output/evaluation_results.json` and `corrective_actions.json` provide
  filtered views of that same audit. Agent `process.json` also contains its audit.
- Corrections start as `AWAITING_RECHECK`. The next matching tool/group evaluation
  links back to them and records `VERIFIED` or `RECHECK_FAILED`. A terminal failure
  marks unresolved actions `BLOCKED`. Tekla retries identify the source run and
  require the existing package revalidation; logging does not enable new repairs.
- SQLite saves agent messages and audit records alongside the workflow snapshot.
  Repeated checkpoints do not duplicate the same recorded event. Historical runs
  are not replayed or assigned invented operation timestamps.
- Archive JSON uses short temporary names, Windows extended paths, bounded
  sharing-violation retries and a cross-process checkpoint lock. Checkpoints read
  the latest committed database state to avoid exporting an older caller snapshot.
  Permanent failures remain explicit archive warnings and do not change grades.
- When configured tracing is enabled, agent, tool, evaluation, correction and
  advisor-request spans include correlation metadata and bounded summaries.
  The worker supplies a parent workflow span; local operation records retain
  trace/span IDs when available for locating the corresponding cloud trace.
  Tracing is optional; delivery problems do not change engineering decisions.
  Worker shutdown flushes pending traces with a bounded wait. Dashboard receipt
  still depends on network access and valid LangSmith configuration.
- Diagnostic traces contain exception types and stack-frame locations, without
  arbitrary SDK response bodies or local variables. This does not collect native
  solver CPU/memory telemetry or invent progress unavailable from the native API.
