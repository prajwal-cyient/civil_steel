# Workbook review

Reviewed on 15 November 2026. Both workbooks are useful references, but neither
was adopted as the current project's approved engineering basis. Application
code, live models, acceptance limits, and load combinations were left unchanged.
Workbook text was treated as document content, not as instructions to execute.
The XLSM macros were extracted and read statically; they were not executed.

## 1. Steel Design / VSM / Detailed Steps workbook

Source: `4 Steel Design -VSM_DetailedSteps_Radiator Structure.xlsx`.

Useful material:

- `Detailed Steps`: analysis, section optimization, reanalysis, representative
  connection selection, IDEA checking/correction, transfer to Tekla, and review.
- `Staad Analysis & Design Checkli`: loading, geometry, releases, design
  parameters, serviceability, support reactions, and independent static checks.
- `IS_Connection Design Checklist`: bolt/weld grades, edge distances, minimum
  sizes, ductility, connection suitability, releases, load transfer, base plates,
  constructability, and erection feasibility.
- `Tekla Model Checklist`: useful additional model/publication checks.

These can inform checker requirements and a traceable review checklist. They
do not establish that every requirement is already implemented in the app.
Drawing checklists are relevant to a later drawing-delivery scope. VSM effort,
time-saving percentages, and administrative/account information are not inputs
to structural calculations; account-detail content was excluded from extraction.

Criteria needing reconciliation before adoption:

| Document evidence | Current implementation | Review decision |
|---|---|---|
| `Detailed Steps!D38`, `D41`: member ratios at most 0.9 | Frame grader uses 1.0 | Do not silently change the project limit; establish which rubric applies. |
| `Detailed Steps!D42`: typically H/200 or L/300 | Current frame grader evaluates lateral H/200 | Beam/serviceability requirements need their own span and deflection checks. "Typically" does not specify a complete project criterion. |
| `Detailed Steps!D36`: resolve analysis warnings before proceeding | Successful STAAD status with warnings can pass if evidence is otherwise valid | Warning classification/review needs an explicit project rule. |
| `Detailed Steps!D86–D87`: component utilization below 100%, avoid excessive overdesign | Current bolt target is 75%, weld target 100% | Keep utilization, physical strain limits, and optimization targets distinct. Do not replace a stricter target automatically. |
| STAAD checklist `A26`: include 1.1 selfweight allowance for connection weight | Current dead-load selfweight call uses -1.0; seismic weighting has separate factors | Check the complete connection-weight allowance and avoid omitting or double-counting it. |
| STAAD checklist `A69`: sum reactions versus independently computed loads for every primary case | Earlier cantilever benchmark is not a per-project equilibrium checker | Useful candidate for an additional production checker. |

## 2. Radiator Supporting Structure / VBA workbook

Source: `Radiator Supporting Structure-VBA.xlsm`.

Useful material:

- `Geometry` and `Property & Releases`: geometry conventions, member families,
  sections, and releases; the geometry uses metres and explicit intermediate grids.
- Four separate wind methods: UBC 97, ASCE 7-10 building, ASCE 7-16 open
  structure, and EN 1991-1-4. They are alternatives to select from using the
  design basis, not formulas to mix indiscriminately.
- `Load Calculations`: equipment/live/snow distribution, projected wind areas,
  and overturning push/pull calculations.
- `Static Check`: independent load-total checks suitable as reference examples.
- Extracted property macro: table 34 for European cold-formed sections and table
  7 for HE beams, consistent with the section mapping already verified in STAAD.

### Independently reproduced examples

Using the workbook's own dimensions, pressure, and intermediate two-decimal
`ROUNDUP` convention, independent decimal arithmetic reproduced:

| Workbook cell | Quantity | Reproduced result |
|---|---|---:|
| `Load Calculations!C50` | X-direction corner wind force | 1.67 kN |
| `Load Calculations!C57` | Z-direction corner wind force | 2.24 kN |
| `Load Calculations!C66` | X-direction corner push/pull | 0.74 kN |
| `Load Calculations!C75` | Z-direction corner push/pull | 0.78 kN |

This verifies selected arithmetic examples, not the choice of design pressure,
the entire workbook, its standards compliance, or the current project loading.
The application uses full precision rather than rounding up every intermediate
step; comparisons must account for this difference.

### Material not suitable for direct import

- `Load Calculations!D25` uses `D22/100 + D23/100`, an approximate mass-to-force
  conversion. The app uses 9.81/1000 for kg to kN. The liquid-volume term also
  depends on the assumed density. This is not an interchangeable unit conversion.
- `Static Check!C5` contains `21*3`; other static checks contain fixed platform,
  bracing, and ladder quantities. They need parameterization and scope checks.
- `Wind-UBC 97!O70`, `O71`, `D149`, and `D150` reference an external workbook.
  Cached values alone do not verify those dependencies.
- Macros rely on exact cell locations, specific geometry patterns, and assigned
  property numbers. Their instructions explicitly warn about spreadsheet layout
  changes, require manual seismic definitions/lumping, and restrict transverse
  bracing generation to a particular grid count.
- Cached geometry includes zero-valued endpoint entries (for example
  `Geometry!X18`). Regenerate and validate geometry rather than trusting cached
  output as a ready-to-use analytical model.
- Static inspection found primary-load creation, but no calls/definitions for
  `CreateNewLoadCombination`, `AddLoadAndFactorToCombination`,
  `CreateLoadEnvelop`, or `LOAD COMB` in the extracted VBA. This does not resolve
  the missing explicit, reviewed load-combination input.

## Decision

Retain selected checklist items and independently checked examples as reference
evidence. Do not copy VBA execution, cached geometry, thresholds, external-link
results, or load factors into production calculations without reconciliation.
These workbooks do not resolve the missing compatible IDEA template mappings.

Read-only extracts and the independently verified sample calculations are in
`output_document_review/` (ignored by Git). The original workbooks were not saved
or modified. Source SHA-256 fingerprints:

- VSM/workflow workbook: `448be93f8b5c343f442de2a6f15d7110e9a59fefd740f40fc2492cd58d98eb82`
- Calculation workbook: `9abebe3a904bbf4e2700d3eed7b7b659c39716c48a749a57dac73ef458e92436`
