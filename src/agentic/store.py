"""
store.py

Durable SQLite persistence layer for agentic workflow runs.
Preserves all run states, logs, software results, and loop histories across
server restarts and unexpected process crashes.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional
from agentic.state import WorkflowState, AgentLogEntry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB_PATH = os.path.join(REPO_ROOT, "data", "runs.db")

_ts_lock = threading.Lock()
_last_ts: float = 0.0


def monotonic_now_iso() -> str:
    """Returns a strictly monotonic UTC ISO timestamp even on sub-millisecond calls."""
    global _last_ts
    with _ts_lock:
        now_val = time.time()
        if now_val <= _last_ts:
            now_val = _last_ts + 1e-6
        _last_ts = now_val
    return datetime.fromtimestamp(now_val, timezone.utc).isoformat()


class SqliteWorkflowStore:
    """Thread-safe SQLite store for durable execution tracking."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("PLANT_RUNS_DATABASE_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._cache: Dict[str, WorkflowState] = {}
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._lock:
            with self._connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        run_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        current_agent TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        state_json TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_runs_status ON workflow_runs(status);
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workflow_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        agent_id TEXT,
                        status TEXT,
                        message TEXT,
                        timestamp TEXT NOT NULL,
                        loop_info TEXT,
                        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
                    )
                """)
                conn.execute('''CREATE TABLE IF NOT EXISTS workflow_audit_events (
                    run_id TEXT NOT NULL, event_id TEXT NOT NULL, event_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, event_id))''')
                conn.commit()

    def save_state(self, state: WorkflowState) -> WorkflowState:
        """Saves or updates a WorkflowState record atomically."""
        now_str = monotonic_now_iso()

        with self._lock:
            with self._connection() as conn:
                from agentic.run_archive import guarded, prepare_archive
                existing = conn.execute('SELECT created_at FROM workflow_runs WHERE run_id=?', (state.run_id,)).fetchone()
                guarded(state, prepare_archive, self.db_path, existing['created_at'] if existing else now_str)
                state_json = json.dumps(state.model_dump())
                self._cache[state.run_id] = state
                conn.execute("""
                    INSERT INTO workflow_runs (run_id, status, current_agent, created_at, updated_at, state_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        status = excluded.status,
                        current_agent = excluded.current_agent,
                        updated_at = excluded.updated_at,
                        state_json = excluded.state_json
                """, (
                    state.run_id,
                    state.status,
                    state.current_agent,
                    now_str,
                    now_str,
                    state_json
                ))
                for entry in state.logs:
                    self._insert_log(conn, state.run_id, entry)
                for event in state.audit_events:
                    conn.execute('''INSERT INTO workflow_audit_events VALUES (?, ?, ?)
                        ON CONFLICT(run_id,event_id) DO UPDATE SET event_json=excluded.event_json''',
                        (state.run_id, event['id'], json.dumps(event)))
                conn.commit()
        from agentic.run_archive import guarded, checkpoint
        warning_count = len(state.output_archive.get('warnings', []))
        guarded(state, checkpoint, self.db_path)
        if len(state.output_archive.get('warnings', [])) > warning_count:
            # Preserve export failures without overwriting a newer worker/API state.
            with self._lock, self._connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT state_json FROM workflow_runs WHERE run_id=?', (state.run_id,)).fetchone()
                latest = json.loads(row['state_json'])
                warnings = latest.setdefault('output_archive', {}).setdefault('warnings', [])
                for warning in state.output_archive['warnings']:
                    if warning not in warnings:
                        warnings.append(warning)
                conn.execute('UPDATE workflow_runs SET state_json=? WHERE run_id=?', (json.dumps(latest), state.run_id))
                conn.commit()
        return state

    @staticmethod
    def _insert_log(conn, run_id, entry):
        values = (run_id, entry.agent_id, entry.status, entry.message, entry.timestamp, entry.loop_info)
        conn.execute('''INSERT INTO workflow_events
            (run_id,agent_id,status,message,timestamp,loop_info)
            SELECT ?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM workflow_events WHERE
            run_id=? AND agent_id IS ? AND status IS ? AND message IS ? AND timestamp=? AND loop_info IS ?)''',
            values + values)

    def get_state(self, run_id: str) -> Optional[WorkflowState]:
        """Retrieves a WorkflowState by run_id (from cache or SQLite)."""
        with self._lock:
            # A separate worker updates the database; cached objects may be stale.
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT state_json FROM workflow_runs WHERE run_id = ?",
                    (run_id,)
                ).fetchone()
                if row:
                    data = json.loads(row["state_json"])
                    state = WorkflowState(**data)
                    self._cache[run_id] = state
                    return state
        return None

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns metadata for recent runs ordered by update time."""
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute("""
                    SELECT run_id, status, current_agent, created_at, updated_at
                    FROM workflow_runs
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(r) for r in rows]

    def record_log(self, run_id: str, entry: AgentLogEntry):
        """Appends an individual execution event to the events table."""
        with self._lock:
            with self._connection() as conn:
                self._insert_log(conn, run_id, entry)
                conn.commit()


# Global shared singleton instance
WORKFLOW_STORE = SqliteWorkflowStore()
