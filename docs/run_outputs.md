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
