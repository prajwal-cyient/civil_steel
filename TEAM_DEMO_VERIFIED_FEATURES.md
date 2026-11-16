# Engineering automation: verified features and evidence

Prepared from saved development-environment test artifacts on 15 November 2026.
This is an evidence review, not a new execution or an engineering sign-off.

## Statement for the team

> We have demonstrated the automated A1–A8 path for the tested default input, including native STAAD and IDEA calculations, engineering checks and grading, package validation, and verified Tekla publication. Separate native tests demonstrated a targeted STAAD correction loop and recovery of missing connection-check evidence. The baseline reached A8 awaiting engineer sign-off. General IDEA automatic redesign remains under validation; unsupported or unresolved cases stop with a failure.

## Features verified

| Feature | Actual observed result | Evidence |
| --- | --- | --- |
| Input validation and readiness | A1 and A2 passed for the tested 21×5-bay input; saved STAAD/Tekla models and IDEA service were available. | [Baseline run](output_validation/backend_loop_test/baseline_report.json), run `run_c9b23567` |
| Frame generation | A3 generated 194 physical members. | [Complete saved state](output_validation/backend_loop_test/baseline_256a16/full_state.json) |
| STAAD analyzer and frame grader | A4 passed: maximum ratio **0.3234004**, with serviceability drift within the configured H/200 limit. | Baseline `frame_grade` and complete state `staad_results` |
| IDEA analyzer and connection grader | A5 passed the **two required IDEA families**. Bolt utilizations were **21.2459% / 23.8319%**; weld utilizations were **56.4809% / 65.7757%**. Plate and local-deformation checks also passed. | Baseline `joint_grades` |
| Package checker | A6 validated the package, with hash **a17410b76665**. | Baseline `validation_report` |
| Tekla automation and read-back | A7 verified **194 members and 464 connections**, with **0 failures** and an empty error list. Read-back checks covered member geometry, profiles/materials and specified connection attributes. | Baseline `tekla_results` |
| Reporting | A8 generated the package and reached **PAUSED_AT_GATE3 / AWAITING_APPROVAL**. | Baseline `final_report` and `agent_statuses.A8` |

The two IDEA families were `inner_node_2_longitudinal_bracings` and
`inner_node_2_longitudinal_1_transverse_bracing`. Eleven other families used the
senior procedure's declared standard Tekla details. They are **not separate IDEA
calculation passes**. The 464 verified Tekla connections are not 464 independent
IDEA design verifications.

## Proof that feedback causes a real correction and rerun

The default baseline needed **zero corrections**. The following proof comes from
separate, explicitly isolated test cases.

**Native STAAD Loop A — demonstrated:**

1. A deliberately weak test frame returned maximum ratio **6.9240838** and failed drift.
2. Feedback identified exactly four failing columns: **900001–900004**.
3. The loop changed their profile from **CFRHS100X100X4 to CFRHS250X250X10**.
4. A fresh native solve returned maximum ratio **0.3442811** and passed drift.
5. Saved `.std` comparison showed only the four-column section-assignment line changed. Loads, combinations, geometry, beams and braces remained unchanged.

Evidence: [Loop report](output_validation/backend_loop_test/frame_probe_22daf7/live_frame_report.json),
[input before correction](output_validation/backend_loop_test/frame_probe_22daf7/frame_attempt_0.std),
[input after correction](output_validation/backend_loop_test/frame_probe_22daf7/frame_attempt_1.std).

**Loop C package recovery — demonstrated:**

- A weld-check category was deliberately removed from a copy of genuine connection evidence while retaining its cached PASS flag.
- A6 detected the missing check and routed recovery to A5.
- A5 recalculated the affected family in native IDEA and restored the weld check.
- The other passing family was reused unchanged, and A6 revalidated the package.

Evidence: [Native recovery report](output_validation/backend_loop_test/package_recovery_report.json).

## Negative tests and remaining boundary

- A real mixed-column failure stopped because a shared-profile change would also resize passing columns: [mixed-member test](output_validation/backend_loop_test/frame_probe_8e0dff/live_frame_report.json).
- A real exhausted-revision test stopped with the failure unresolved: [revision-limit test](output_validation/backend_loop_test/frame_probe_58a278/live_frame_report.json).
- A real overloaded IDEA case returned analysis **94.140625% with failed status**, bolts **89.4173%**, and welds **100.6885%**. It was rejected without an unsupported correction: [IDEA case](output_validation/backend_loop_test/connection_4_0_completed_failed_checks.json).
- Its previous 180-second timeout was replaced by bounded IDEA-specific runtime handling. The same input returned actual engineering failures: [retest comparison](output_validation/backend_loop_test/idea_runtime_retest_qa.json).
- **Native IDEA failure → automatic correction → passing rerun is still unverified.** Local correction bindings and their Tekla handoff require validation. A safe stop is not evidence of convergence.
- The installed legacy service lacks asynchronous job routes. Newer-service polling/cancellation paths have controlled tests; native cancellation on this installed service was not demonstrated.

## Regression evidence and presentation boundaries

**143 Python cases passed:** [per-case results](output_validation/backend_loop_test/regression-cases.json).
**12 Node logic tests passed:** [execution output](output_validation/backend_loop_test/javascript-regressions.txt).
These controlled/logic tests are separate from the native solver demonstrations.

Original STAAD input bytes and source Tekla databases remained unchanged during
the isolated full baseline test, and the original models were restored.

Do not claim all engineering inputs converge, every connection family has IDEA
verification, all automatic IDEA corrections work, or the package has engineer
approval. Software test results do not certify the underlying engineering assumptions.

Full details: [LOOP_ENGINEERING_TEST_REPORT.txt](LOOP_ENGINEERING_TEST_REPORT.txt).
Keep `output_validation/backend_loop_test/` with this handout when sharing its
evidence links; generated artifacts are locally ignored by Git.
