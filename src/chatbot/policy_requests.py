"""Bind explicit numeric chat thresholds to the policy used by a new run."""
import math
import re

from config.run_policy import RunPolicy


def requested_policy_values(message):
    """Read supported explicit numbers from this message, never past history.

    The model still selects tools. This independent check prevents an input
    preview or run from silently dropping a threshold in the current message.
    """
    labels = {
        'max_member_ratio': r'(?:max_member_ratio|(?:maximum\s+|max\s+)?member\s+ratio)',
        'drift_denominator': r'(?:drift_denominator|drift(?:\s+limit)?)',
        'max_bolt_utilization_percent': r'(?:max_bolt_utilization_percent|(?:maximum\s+|max\s+)?bolt\s+(?:utili[sz]ation|limit))',
        'max_weld_utilization_percent': r'(?:max_weld_utilization_percent|(?:maximum\s+|max\s+)?weld\s+(?:utili[sz]ation|limit))',
        'max_plate_utilization_percent': r'(?:max_plate_utilization_percent|(?:maximum\s+|max\s+)?plate\s+(?:utili[sz]ation|limit))',
        'max_deformation_utilization_percent': r'(?:max_deformation_utilization_percent|(?:maximum\s+|max\s+)?(?:local\s+)?deformation\s+(?:utili[sz]ation|limit))',
    }
    values = {}
    number = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
    for field, label in labels.items():
        matches = re.findall(r'\b' + label + r'\s*(?:(?:to|of|at|is|must be|should be)\s*)?[=:<>≤]*\s*'
                             + (r'(?:H\s*/\s*)?' if field == 'drift_denominator' else '')
                             + '(' + number + r')(?![\w.])', str(message), flags=re.I)
        distinct = {float(value) for value in matches}
        if len(distinct) > 1:
            raise ValueError(f'Multiple values were supplied for {field}. Specify one limit for this run.')
        if distinct:
            values[field] = distinct.pop()
    if values:
        RunPolicy.model_validate(values)
    return values


def require_requested_policy(session, policy=None):
    required = session.get('_requested_run_policy', {})
    if not required:
        return
    if policy is None:
        policy = session.get('next_run_policy')
        if policy is None:
            policy = session['project_input'].get('run_policy', {})
    effective = RunPolicy.model_validate(policy).model_dump()
    mismatches = {key: value for key, value in required.items()
                  if not math.isclose(effective[key], value, rel_tol=0, abs_tol=1e-9)}
    if mismatches:
        raise ValueError('The requested run rules have not been applied. Call set_next_run_policy with '
                         f'{mismatches} before preparing the input preview or starting the run. '
                         'Do not substitute default limits.')
