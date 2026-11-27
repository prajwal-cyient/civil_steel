# Civil Steel: implemented engineering workflow

Checked against the current A1–A7 (`layout_version=2`) workflow. This is a control-flow map, not a claim that live engineering calculations have been verified. Solid arrows show execution; dotted arrows show shared configuration or recording. Each stage can stop on an error. Retry arrows require eligible recovery, remaining budget, and a continue decision.

Open [the offline visual diagram](workflow_diagram.html) in a browser, or preview the Mermaid diagrams below in a compatible Markdown viewer.

## Complete workflow and chat entry

```mermaid
flowchart TD
    INPUT["Inputs: grid, sections, loads, run policy"] --> FORM[Dashboard / form]
    INPUT --> CHAT[Chat assistant]
    CHAT --> QUESTION[Read-only questions / status / evidence]
    CHAT --> PROPOSE[Input-change preview]
    PROPOSE --> APPROVE{User approves?}
    APPROVE -->|Reject| UNCHANGED[Proposal rejected; no edits applied]
    APPROVE -->|Approve and Apply| APPLY[Save approved inputs]
    APPLY --> ACTION{Requested action}
    ACTION -->|Configuration only| SAVED[Inputs saved; analysis still required]
    ACTION -->|Complete workflow| API
    ACTION -->|Preview or individual stage| PARTIAL[Separate guarded chat action path]
    CHAT --> SELECTED[Selected-connection changes: separate diagram below]
    FORM -->|Run| API[FastAPI dispatch / one desktop job at a time]
    API --> WORKER[Disposable Windows STA worker / desktop lock]

    subgraph FLOW["LangGraph workflow — Deep Agent wrappers enabled by default around A1–A7"]
        WORKER --> A1["A1 Requirements: validate inputs and load basis"]
        A1 -->|Valid| A2["A2 Readiness: saved STAAD / Tekla models, IDEA service, templates"]
        subgraph FRAME["A3 Frame"]
            A3a["A3a Evaluate geometry plan / profile catalogue / column height"] --> A3b["A3b Generate members / split beams / connection scope"]
        end
        A2 -->|Ready| A3a
        subgraph STAAD["A4 Structural analysis"]
            A4a[A4a Build STAAD request] --> A4b[A4b STAAD / OpenSTAAD]
            A4b --> A4c[A4c Frame grader]
            A4c --> A4d["A4d Decision / correction — optional LLM advice"]
        end
        A3b --> A4a
        A4d -->|Loop A: permitted frame correction| A3a
        A4d -->|Passed| A5a
        subgraph IDEA["A5 IDEA connection design + package audit"]
            A5a["A5a Prepare next connection group / reuse eligible evidence"] -->|Needs calculation| A5b[A5b IDEA REST calculation]
            A5b --> A5c[A5c Joint grader]
            A5c --> A5d["A5d Decision / correction — optional LLM advice"]
            A5d -->|Group passed: next group| A5a
            A5d -->|Loop B: permitted connection correction / re-enter A5| A5a
            A5a -->|All groups complete| AUDIT["A5c Package audit: evidence signatures, grades, material identity"]
            AUDIT --> PACKAGE["A5d Package recovery decision — optional LLM advice"]
            PACKAGE -->|Loop C: eligible IDEA recovery / re-enter A5| A5a
        end
        PACKAGE -->|Joints and package passed| A6a
        subgraph TEKLA["A6 Tekla publication"]
            A6a[A6a Build validated Tekla payload] --> A6b[A6b Tekla Open API publication]
            A6b --> A6c[A6c Member and connection read-back checker]
            A6c --> A6d["A6d Decision / retry — optional LLM advice"]
            A6d -->|Permitted retry: same validated payload| A6a
        end
        A6d -->|Publication verified| A7[A7 Assemble report package]
        A7 --> WAIT[PAUSED_AT_GATE3: awaiting engineer acceptance]
    end
    WAIT -->|Engineer accepts through API / UI| ACCEPT[Record acceptance identity and timestamp]
    ACCEPT --> DONE[COMPLETED / accepted report]
    FAIL["FAILED / stopped: inspect saved evidence; no automatic progression"]
    A1 -->|Invalid| FAIL
    A2 -->|Not ready| FAIL
    A3a -->|Invalid plan| FAIL
    A3b -->|Generation / validation error| FAIL
    A4d -->|Unrecoverable / exhausted / stop| FAIL
    A5d -->|Unrecoverable / exhausted / stop| FAIL
    PACKAGE -->|Invalid recovery / exhausted / stop| FAIL
    A6d -->|Unrecoverable / exhausted / stop| FAIL
    A7 -->|Report / gate validation error| FAIL
    RULES["Run policy + coded graders + native solver checks"] -.-> A4c
    RULES -.-> A5c
    RULES -.-> AUDIT
    RULES -.-> A6c
    FLOW -.-> STORE["Saved workflow state / audit / timings / per-run evidence archive"]
```

For an approved input change requesting a complete run, A1 executes before the graph enters A2. A1 is not skipped. The complete workflow normally starts the graph at A1. Chat's preview and individual-stage actions have their own prerequisites and may finish at `READY`; an unverified geometry preview is not engineering approval.

Hard tool errors can stop a subgraph before its decision node. The failure arrows summarize these exits; they do not imply every error reaches the LLM or is retried. In A5, a passing group advances to the next group; a failed group requiring correction exits and re-enters A5 through the outer routing. Eligible existing results can be skipped/reused. Package recovery is Loop C, distinct from Loop B.

## AI execution layer (enabled by default)

```mermaid
flowchart LR
    STAGE[Stage requested by LangGraph] --> ENABLED{Deep Agents enabled?}
    ENABLED -->|No| DIRECT[Run coded engineering stage directly]
    ENABLED -->|Yes| AGENT[Deep Agent: bounded planning and assigned tool]
    AGENT --> TOOL[execute_stage exactly once per wrapper invocation]
    TOOL --> OPERATION[Run the same coded engineering stage / subgraph]
    OPERATION --> REVIEW[Agent explains recorded outcome]
    DIRECT --> RESULT[Validated stage result / graph routing]
    REVIEW --> RESULT
    AGENT -->|Model / budget / tool-contract failure| STOP[Fail workflow; no automatic direct-execution fallback]
```

`PLANT_DEEP_AGENTS_ENABLED` and `PLANT_LLM_ENABLED` default to `true` in the runtime and `.env.example`. The wrappers require a configured model and credentials. Set the Deep Agents flag to `false` for direct stage execution, and set the LLM flag to `false` as well to disable correction advice. Each wrapper is around an outer stage, not a separate Deep Agent at every a/b/c/d substep. It cannot bypass graders, call another stage, edit engineering criteria, or approve Gate 3; general-purpose subagent delegation is disabled. The correction advisor selects only prevalidated choices and falls back to the deterministic choice if unavailable or unsuccessful. Its fallback behavior differs from a Deep Agent wrapper failure. Chat has its own enable switch.

## Selected-connection changes

```mermaid
flowchart TD
    REQUEST[Chat: read existing joint parameters / propose exact selected-group changes] --> PREVIEW[Stored change preview / group scope / template fingerprints]
    PREVIEW --> APPROVAL{User approves?}
    APPROVAL -->|Reject| REJECTED[No edits applied]
    APPROVAL -->|Approve and Apply| CHECK[Validate proposal / current passing STAAD evidence / unchanged templates]
    CHECK --> LOCK[Same desktop lock and STA worker]
    LOCK --> REUSE{Eligible approved calculations reusable?}
    REUSE -->|No| IDEA[Calculate / grade selected groups in A5]
    REUSE -->|Yes| VERIFY[Verify selected values and package]
    IDEA --> VERIFY
    VERIFY --> PUBLISH{Tekla publication explicitly requested?}
    PUBLISH -->|No| READY[READY: selected calculations and package validated]
    PUBLISH -->|Yes| PATCH[Scoped Tekla connection updates / read-back]
    PATCH --> REPORT[A7 report / PAUSED_AT_GATE3]
    REPORT -->|Engineer accepts| COMPLETE[COMPLETED]
    CHECK -->|Stale / invalid evidence| FAILED[FAILED: stop and inspect]
    IDEA -->|Failed / broader recovery needed| FAILED
    VERIFY -->|Failed / scope mismatch| FAILED
    PATCH -->|Failed| FAILED
```

Selected updates preserve the approved group scope. A full-model Tekla rebuild is not substituted. Existing calculations are reused only where the scoped proposal permits it. This diagram covers selected connection edits; chat also exposes guarded preview, generate, frame analysis, connection design, package validation and publication actions.

## Source map

| Behavior | Implementation |
|---|---|
| Main graph, Deep Agent switch, A1 before A2 on changed-input reruns | `src/orchestration/workflow.py` |
| Frame / connection / package routing | `src/orchestration/routing.py` |
| Stage substeps | `src/agents/A3_generate_structure/graph.py`, `src/agents/A4_staad/graph.py`, `src/agents/A5_idea_statica/graph.py`, `src/agents/A6_tekla/graph.py` |
| Deep Agent tool limits and fail-closed execution | `src/services/deep_agents_runtime.py` |
| Optional correction choices and fallback | `src/services/correction_advisor.py` |
| Chat previews, approval execution, configuration-only outcome | `src/chatbot/chatbot.py` |
| Selected and individual-stage execution | `src/chatbot/chat_actions.py`, `src/agents/A6_tekla/selected_updates.py` |
| Run thresholds and graders | `src/config/run_policy.py`, `src/agents/A4_staad/A4C_evaluate_results.py`, `src/agents/A5_idea_statica/A5C_evaluate_and_audit.py`, `src/agents/A5_idea_statica/package_validation.py`, `src/agents/A6_tekla/A6C_check_publication.py` |
| Tekla retry qualification | `src/agents/A6_tekla/publication_corrections.py` |
| Report and acceptance | `src/agents/A7_reporting/build_report.py`, `src/agents/A7_reporting/human_gate.py`, `src/orchestration/dashboard_router.py` |
| Process ownership / mutual exclusion | `src/orchestration/execution.py`, `src/orchestration/worker.py`, `src/orchestration/desktop_lock.py` |
| State / audit / archives | `src/services/workflow_store.py`, `src/utils/audit.py`, `src/utils/run_archive.py` |

Only `layout_version=2` is supported. `node_package_audit` runs A5c, `node_a6` runs Tekla, and `node_a7` runs reporting. This map covers orchestration and its important branches, not every endpoint or exception handler. Historical records from other layouts are preserved on disk but excluded from the current application's run listing and loading; start a new run to use the current workflow.
