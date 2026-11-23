"""Keep chat run inputs separate from the read-only master configuration."""
from copy import deepcopy
import json

from agentic.config import ROOT


def master_inputs():
    from agentic.chat import project_input
    path = ROOT / 'src' / 'radiator_support' / 'frame_specs.json'
    value = project_input(json.loads(path.read_text(encoding='utf-8-sig')))
    value.pop('run_policy', None)
    return value


def reset_finished_inputs(session, store, workflows, state=None):
    run_id = session.get('temporary_input_run_id')
    if not run_id:
        return False
    state = state if state is not None and state.run_id == run_id else workflows.get_state(run_id)
    if state is None or state.status not in {'COMPLETED', 'FAILED', 'CANCELLED'}:
        return False
    try:
        baseline = master_inputs()
    except (OSError, ValueError):
        # Reset failure cannot discard a completed calculation or substitute
        # guessed defaults. Keep the marker so a later read can retry the reset.
        session['input_reset_error'] = 'Master inputs could not be loaded; correct frame_specs.json before the next run. '
        store.save_session(session)
        return False
    previous = deepcopy(state.raw_input)
    previous.pop('run_policy', None)
    session['project_input'] = baseline
    session['input_reset'] = {'after_run_id':run_id, 'previous_run_input':previous,
                              'master_input':deepcopy(baseline), 'source':'frame_specs.json'}
    session.pop('temporary_input_run_id', None)
    session.pop('input_reset_error', None)
    store.save_session(session)
    return True


def next_request_inputs(session, incoming, fresh=False):
    """Ignore an unchanged old-run form snapshot, while accepting deliberate edits."""
    reset = session.get('input_reset')
    if not reset:
        return incoming
    def without_policy(value):
        return {key: val for key, val in (value or {}).items() if key != 'run_policy'}
    stale = incoming is not None and without_policy(incoming) == reset['previous_run_input']
    same_master = incoming is None or without_policy(incoming) == reset['master_input']
    if fresh and (stale or same_master) and session['project_input'] == reset['master_input']:
        session['project_input'] = master_inputs()
        if incoming and 'run_policy' in incoming:
            return {**deepcopy(session['project_input']), 'run_policy':incoming['run_policy']}
        return deepcopy(session['project_input'])
    if stale:
        if 'run_policy' in incoming:
            return {**deepcopy(session['project_input']), 'run_policy':incoming['run_policy']}
        return None
    return incoming
