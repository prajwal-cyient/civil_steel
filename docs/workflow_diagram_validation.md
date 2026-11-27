# Workflow diagram verification

The diagram documents the single supported A1-A7 implementation (`layout_version=2`). Runtime role names, chat plans, archives, and the dashboard have been consolidated to this layout. `.env` settings were not changed.

## Automated application checks

After the single-layout refactor, all 362 backend tests and 35 frontend tests passed. The earlier diagram-only review ran 75 targeted backend tests, including these suites:

- `test_agentic_workflow`
- `test_deep_agents`
- `test_chat_approval`
- `test_chat_input_lifetime`
- `test_loop_localization`
- `test_publication_recovery`
- `test_run_policy`

The original targeted tests used isolated workflow/chat databases under `output_exports/workflow_diagram_checks`; the full refactor suite used `output_exports/single_layout_checks`. Both runs disabled inherited model/tracing switches and used the suites' controlled models and mocked engineering adapters. The final test process exited successfully. The initial sandbox run passed its assertions but reported Python/.NET initialization and shutdown errors; rerunning outside the sandbox resolved those errors.

The complete single-layout backend regression log is `output_exports/single_layout_checks/backend_tests.log`. The additional `test_single_layout` suite checks rejection of unsupported layout values, fixed role meanings and plans, and preservation/exclusion of historical stored records. Frontend suites cover saved runs, timers, result rendering and human acceptance using the seven-stage UI.

These checks cover the main routing and recovery logic, Deep Agent tool restrictions and failures, approval handling, input lifecycle, localized corrections, publication recovery, and evaluation policy. They do not verify live model responses, licenses, native engineering calculations or a real model publication.

## Diagram checks

- Built both SVG diagrams and the standalone HTML using `docs/build_workflow_diagram.py`.
- Opened the HTML in headless Microsoft Edge: two SVG diagrams, no page JavaScript errors, no document horizontal overflow at desktop or mobile widths.
- Verified the full-size toggle, and visually inspected the rendered page.
- The HTML and SVG diagrams work offline; the Markdown contains editable Mermaid and implementation references.

The diagram covers the current A1–A7 full workflow plus selected-connection changes and the important partial-action distinctions. It is a behavioral overview, not a literal map of every function, endpoint or exception. A numerical “100% match” is not a measured verification result.
