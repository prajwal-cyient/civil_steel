# Rules for one run

Chat can validate and queue acceptance thresholds for the next **new** run.
The existing LangGraph/Deep Agents stages, native calculations, correction
localization, retry limits, and manual engineer approval remain in use.

Example chat request:

> Run the current 8x5 inputs. For this run only, require member ratio <= 0.80,
> drift <= H/300, and bolt utilization <= 90%.

The model calls `set_next_run_policy`, then the requested engineering tool.
When geometry and thresholds are requested together, the policy must be set
before the input-change preview. Explicit supported numeric limits in the current
message are independently checked against the effective policy. A missing or
mismatched value blocks the preview/run instead of substituting a default.
The preview saves the requested limits with its signed policy for approval.
For an explicitly supplied rules file, it reads the attachment first.
Ambiguous rules need clarification; arbitrary formulas are unsupported.

| JSON field | Default | Accepted override |
|---|---:|---|
| `max_member_ratio` | 1.0 | Numeric, greater than 0, at most 1.0 |
| `drift_denominator` | 200 | Finite numeric, at least 200; H/300 means 300 |
| `max_bolt_utilization_percent` | 75 | Numeric, greater than 0, at most 100; 90 is supported |
| `max_weld_utilization_percent` | 100 | Numeric, greater than 0, at most 100 |
| `max_plate_utilization_percent` | 100 | Numeric, greater than 0, at most 100 |
| `max_deformation_utilization_percent` | 100 | Numeric, greater than 0, at most 100 |

The bolt rubric can be tightened or relaxed within the supported range for one
run. A 90% limit means a native-passing bolt check at 85% satisfies that run's
bolt rubric; a value above 90% fails it. A native-failed check still fails even
below the requested limit. Other thresholds can tighten their existing defaults.
Overrides cannot remove native checks, execute new formulas, or approve a design.
Unknown fields, numeric strings,
booleans, nonfinite values and out-of-range values are rejected. After a rejected
policy, execution is blocked until a valid policy is supplied. Ask chat to use
default rules to clear it (`set_next_run_policy` with `{}`).

## Lifetime and saved evidence

- The queued policy is consumed when a new live generation or full run is
  dispatched, including a run that subsequently fails. Busy/disabled tools do
  not consume it. A geometry-only preview also leaves the policy queued.
- A later **new run** uses defaults unless another policy is explicitly supplied.
- Selected-stage continuations retain their source run's policy. Changing rules
  requires a new run; existing solver evidence is not silently reassigned.
- Other conversations and global configuration are unchanged.
- Geometry, loads and bracing belong to that run. After completion, failure or
  cancellation, chat reloads `src/radiator_support/frame_specs.json` as the inputs
  for the next run. The master file is read only and is never rewritten by chat.
  A8 waiting for manual sign-off and intermediate stage continuations retain
  their custom inputs. Saved run JSON and exported inputs keep the actual custom
  values even after chat resets. An unchanged old-run form snapshot is ignored
  after reset; explicitly edited new inputs can still be supplied.
- `run_policy` is saved in workflow state, the validated package and A8's report.
  New IDEA summary JSON also records it. The package hash includes the policy.
- A4 evaluates drift from actual millimetre displacements using the selected
  denominator; it still verifies the native H/200 evidence for consistency.
- A5 feedback, cached-result rechecks and correction decisions use the same policy.
  A6 independently rechecks frame and connection evidence against it.

## JSON/API input

Use the existing input structure with an optional top-level object:

```json
{
  "grid": {"n_long_bays": 8, "n_trans_bays": 5},
  "run_policy": {
    "max_member_ratio": 0.8,
    "drift_denominator": 300,
    "max_bolt_utilization_percent": 90,
    "max_weld_utilization_percent": 90
  }
}
```

Omitted policy fields use defaults. A direct API request explicitly supplying
`run_policy` applies it to that request; resubmitting that JSON explicitly requests
the policy again. Chat consumes its queued policy once.

Restart the API **after any active run finishes**, then refresh the browser to
load the updated chat tool. No environment variables or dependencies are added.
Existing saved runs without a policy retain the original default checks.
