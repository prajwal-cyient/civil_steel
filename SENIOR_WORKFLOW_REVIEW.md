# Senior workflow comparison

Reference inspected read-only:
`C:\Users\lb89380\projects\plant-engineering\civil-steel-standard-web`.
Reference source/input hashes are recorded locally in
`output_validation/senior-reference-hashes.json`.

| Stage | Senior application | Automated handoff |
| --- | --- | --- |
| Geometry and loads | Engineer submits grid and load form | A1 validates inputs; A3 generates the frame |
| STAAD | Engineer clicks Send to STAAD | A4 builds, analyzes and grades the actual saved model |
| Connection selection | Engineer selects a joint group and envelope | A3 records an explicit connection plan; A5 processes its required families and all strength envelopes |
| IDEA | UI sends actual STAAD member updates, loads and exposed parameters | A5 verifies member/material identity, updates exposed parameters, calculates and grades returned checks |
| Corrections | Engineer edits a section or detailing parameter and sends again | Bounded frame and connection loops make validated changes, rerun and record outcomes |
| Tekla | UI collects selected IDEA outputs and passes them with geometry | A6 audits source signatures; A7 publishes matching members and evaluated connection dimensions |
| Standard connections | Tekla inserts standard 1004/144/20/30 details | Same component numbers and attribute files; successful insertion and typed attribute read-back required |
| Acceptance | Engineer reviews software results | A8 reports the calculation scope and waits for final engineer acceptance |

## Scope confirmed with the user

The saved `analysis_output_tekla_input.json` contains only
`inner_node_1_longitudinal_bracing` and
`inner_node_1_longitudinal_1_transverse_bracing` in `joint_groups`, although the
STAAD result identifies additional families. The senior workflow uses standard
Tekla details for the remaining families. It does not contain a full automatic
IDEA design/grade loop for every support and edge group.

The user explicitly selected reproduction of this senior workflow. Automation
modes now calculate the applicable inner families and declare the rest as
standard-detail families. The two supplied double-longitudinal templates extend
this mapping to matching inner double-brace groups; unrelated support/edge
templates are not substituted. Full `reviewed_engineering` mode retains separate
IDEA requirements for every group. A missing scope on an older persisted state
retains its original full-coverage requirement.

The final report and Gate 3 UI distinguish standard details from IDEA checks.
A6 rejects an incomplete, overlapping or stale plan and will not allow required
inner calculations to be relabeled as standard details.

## Integration differences corrected

- STAAD 2025 silently omitted `PerformAnalysis(6)`. Option 0 works; serialized
  analysis/envelope order is verified before solving.
- STAAD unattended analysis uses `AnalyzeEx` to close its completion dialog.
- The senior transverse placeholder is absent from supplied templates. The real
  aligned beam is now verified without a redundant member update when its section
  and material already match. A differing section requires a compatible template.
- IDEA sections must match the requested catalogue material as well as profile.
  The explicit reference catalogue mapping uses the senior templates' `S 355`
  analysis label for requested `S355JR`; the Tekla material remains `S355JR`.
- The added transverse branch in the double-brace template uses `D2_CPL2_*`
  parameters. These evaluated values now reach Tekla component 20 in millimetres.
- Tekla integer attribute reads use explicit .NET `Int32`, preventing pythonnet
  from selecting the wrong `GetAttribute` overload. Publication saves the model.
- IDEA calculation timeouts account for multiple load effects, within the total
  run limit. Phase updates identify the calculation currently in progress.
- A failed A7 publication can be retried in a new tracked run after A6 revalidates
  unchanged source evidence. A failed analysis cannot skip ahead to publication.

No acceptance limits, section ladders, reference load factors or standard Tekla
attribute-file contents were relaxed for these changes. Existing fake-pass
fallbacks remain disabled. Live results and test totals are recorded in
`WORKFLOW_VALIDATION.md`; this comparison alone is not an end-to-end pass.
