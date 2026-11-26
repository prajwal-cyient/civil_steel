"""Local operation/evaluation audit and optional, non-blocking LangSmith spans.

Only bounded summaries are traced. Native payloads, credentials and exception
response bodies are never sent by this module. Engineering decisions stay with
the existing graders.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import math
import os
import re
from time import perf_counter
import traceback
import uuid

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'api[_-]?key|(?:access|refresh)[_-]?token|password|authorization|secret', str(k), re.I)
                         else clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if len(secret) >= 8 and re.search(r'API_KEY|TOKEN|PASSWORD|SECRET', key, re.I):
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?:sk-|lsv2_|ghp_)[\w-]{12,}', '[REDACTED]', value)
        value = re.sub(r'(?i)(bearer\s+)\S+', r'\1[REDACTED]', value)
        return value[:4000]
    return value


def event(state, kind, tool=None, **details):
    record = {'id': uuid.uuid4().hex, 'run_id': state.run_id,
              'agent': state.current_agent, 'tool': tool, 'kind': kind,
              'attempt': state.agent_timings.get(state.current_agent, {}).get('attempts', 0),
              'timestamp': now(), **clean(details)}
    state.audit_events.append(record)
    return record


def exception_record(state, operation, exc):
    # Frame locations are useful diagnostics; arbitrary SDK response bodies and
    # source lines may contain secrets, so retain neither in the traceback.
    frames = [{'file': f.filename, 'line': f.lineno, 'function': f.name}
              for f in traceback.extract_tb(exc.__traceback__)]
    record = event(state, 'exception', operation=operation, status='FAILED',
                   error_type=type(exc).__name__, traceback=frames)
    logger.error('Run %s %s: %s; frames=%s', state.run_id, operation,
                 type(exc).__name__, frames)
    return record


def tracing_enabled():
    flag = os.getenv('LANGSMITH_TRACING_V2', os.getenv('LANGSMITH_TRACING', 'false'))
    return flag.lower() in {'true', '1', 'yes'} and bool(os.getenv('LANGSMITH_API_KEY'))


@contextmanager
def observe(state, name, tool=None, kind='operation', **metadata):
    record = event(state, kind, tool, operation=name, status='RUNNING', **metadata)
    started = perf_counter()
    context = span = None
    if tracing_enabled():
        try:
            from langsmith import trace
            context = trace(name, run_type='tool' if tool else 'chain', inputs={},
                            metadata=clean({'run_id': state.run_id, 'agent': state.current_agent,
                                            'tool': tool, 'attempt': record['attempt'], **metadata}))
            span = context.__enter__()
            record.update(trace_id=str(span.trace_id), span_id=str(span.id))
        except Exception as exc:
            context = None
            event(state, 'tracing_warning', status='FAILED', error_type=type(exc).__name__)
    try:
        yield record
        if record['status'] == 'RUNNING':
            record['status'] = 'COMPLETED'
    except BaseException as exc:
        record.update(status='FAILED', error_type=type(exc).__name__)
        exception_record(state, name, exc)
        raise
    finally:
        record.update(finished_at=now(), elapsed_seconds=round(perf_counter() - started, 3))
        if context is not None:
            try:
                remote = clean(record)
                if record['status'] == 'FAILED' and kind in {'agent', 'workflow'}:
                    remote['reason'] = 'Engineering stage failed; see local diagnostics and evaluation spans.'
                span.end(outputs=remote, error=record.get('error_type') or
                         ('Engineering checks failed' if record['status'] == 'FAILED' else None))
            except Exception as exc:
                event(state, 'tracing_warning', status='FAILED', error_type=type(exc).__name__)
            finally:
                try:
                    # Always restore the parent context, even if span.end fails.
                    # Never forward an arbitrary native exception response body.
                    context.__exit__(None, None, None)
                except Exception as exc:
                    event(state, 'tracing_warning', status='FAILED', error_type=type(exc).__name__)


def evaluation(state, tool, result, **metadata):
    data = result.model_dump() if hasattr(result, 'model_dump') else result
    with observe(state, f'{tool} evaluation', tool, kind='evaluation', **metadata) as record:
        record.update(status='PASSED' if data.get('passed') else 'FAILED', result=clean(data))
        pending = [e for e in state.audit_events if e['kind'] == 'correction' and e.get('tool') == tool
                   and e.get('status') == 'AWAITING_RECHECK'
                   and (not metadata.get('joint_group') or e.get('action', {}).get('joint_group') == metadata['joint_group'])]
        record['correction_ids'] = [e['id'] for e in pending]
        for correction in pending:
            correction.update(status='VERIFIED' if data.get('passed') else 'RECHECK_FAILED',
                              evaluation_id=record['id'])


def stage_outcome(state, history_sizes):
    """Uniform corrections audit, retaining the original detailed loop records."""
    from agentic.state import workflow_stage
    if state.current_agent == workflow_stage(state, 'package') and state.validation_report:
        evaluation(state, 'Package', state.validation_report)
    for key, tool in [('loop_a_history', 'STAAD'), ('loop_b_history', 'IDEA_StatiCa'),
                      ('loop_c_history', 'Package')]:
        for correction in getattr(state, key)[history_sizes[key]:]:
            with observe(state, f'{tool} correction', tool, kind='correction') as record:
                record.update(status='AWAITING_RECHECK', action=clean(correction),
                              notice='Action recorded; subsequent evaluation establishes its outcome.')
    if state.status == 'FAILED':
        for correction in state.audit_events:
            if correction['kind'] == 'correction' and correction.get('status') == 'AWAITING_RECHECK':
                correction.update(status='BLOCKED', reason=clean(state.error))
        event(state, 'failure', status='FAILED', reason=state.error,
              corrective_action='No further automatic action taken; inspect failure and prior correction records.')


def flush_traces(state):
    if tracing_enabled():
        try:
            from langsmith.run_trees import get_cached_client
            get_cached_client().flush(timeout=2.0)
        except Exception as exc:
            event(state, 'tracing_warning', status='FAILED', error_type=type(exc).__name__)
