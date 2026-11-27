# Civil Steel: A1-A7 technical guide

The application has one supported workflow layout: `layout_version=2`.

| Stage | Responsibility | Entry point |
|---|---|---|
| A1 | Validate engineering inputs and prepare load basis | `orchestration.workflow.node_a1` |
| A2 | Check saved models, native services and template readiness | `orchestration.workflow.node_a2` |
| A3 | Evaluate geometry plan and generate frame | `orchestration.workflow.node_a3` |
| A4 | Run STAAD, grade results and decide bounded frame corrections | `orchestration.workflow.node_a4` |
| A5 | Calculate and grade connections; audit package at A5c | `orchestration.workflow.node_a5_full` |
| A6 | Publish to Tekla, verify read-back and handle permitted retries | `orchestration.workflow.node_a6` |
| A7 | Build report and await human engineer acceptance | `orchestration.workflow.node_a7` |

The package-audit helper is `node_package_audit`, with stage ID `A5c`. It supports selected-stage chat actions and publication recovery; it is not an additional main stage.

See [the detailed editable workflow and source map](workflow_diagram.md) and [the offline visual diagram](workflow_diagram.html).

## Execution and recovery

- Loop A returns from A4 to A3 for permitted frame corrections.
- Loop B revises eligible failed connection groups in A5.
- Loop C performs permitted package-evidence recovery through A5.
- Tekla retries reuse the validated payload and require eligible recovery and remaining budget.
- Unrecoverable errors, invalid evidence, exhausted budgets, or a stop decision end the run.
- Reports wait at `PAUSED_AT_GATE3`; only explicit engineer acceptance completes the run.

`PLANT_DEEP_AGENTS_ENABLED` and `PLANT_LLM_ENABLED` default to `true`, enabling bounded AI wrappers around the same coded stages. Set the Deep Agents flag to `false` for direct stage execution; set the LLM flag to `false` as well to disable correction-advisor calls. Chat has its own switch. Grading and native calculation evidence remain authoritative.

## Persistence and historical data

New states default to layout 2; other layout values are rejected. The dashboard, chat plans, Deep Agent roles, archives and stage function names all use A1-A7 numbering. Historical records with other identifiers remain on disk but cannot be loaded, resumed, or listed as current runs. Their IDs are never silently reassigned to different engineering responsibilities.

The previous guide and its evidence reports are preserved under [historical documentation](historical/README.md). They are not current operating instructions or evidence that the revised implementation has passed live native verification.
