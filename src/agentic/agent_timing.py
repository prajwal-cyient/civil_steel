"""Persist stage execution durations without counting time between stages."""
from datetime import datetime, timezone
import time


def start_timing(state, agent_id):
    previous = state.agent_timings.get(agent_id, {})
    state.agent_timings[agent_id] = {
        'elapsed_seconds': previous.get('elapsed_seconds', 0.0),
        'attempts': previous.get('attempts', 0) + 1,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'finished_at': None,
        'running': True,
    }
    return time.monotonic()


def finish_timing(state, agent_id, started):
    timing = state.agent_timings[agent_id]
    timing['elapsed_seconds'] = round(timing['elapsed_seconds'] + max(0.0, time.monotonic() - started), 3)
    timing.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())


def stop_running_timings(state):
    """Freeze persisted timers if a worker was killed before its finally block."""
    now = datetime.now(timezone.utc)
    for timing in state.agent_timings.values():
        if timing.get('running'):
            started = datetime.fromisoformat(timing['started_at'])
            timing['elapsed_seconds'] = round(timing.get('elapsed_seconds', 0.0) + max(0.0, (now - started).total_seconds()), 3)
            timing.update(running=False, finished_at=now.isoformat())
