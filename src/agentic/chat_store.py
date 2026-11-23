"""Separate SQLite conversation history; existing engineering tables are untouched."""
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self, path=None):
        self.path = str(path or os.getenv("PLANT_CHAT_DATABASE_PATH") or Path(__file__).resolve().parents[2] / "data" / "chat.db")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS chat_sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_turns (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_change_plans (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_request_limits (session_id TEXT NOT NULL, at REAL NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS chat_active_session ON chat_turns(session_id) WHERE status='RUNNING'")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS chat_open_request ON chat_turns(session_id) WHERE status IN ('RUNNING','AWAITING_APPROVAL')")
            # Interrupted requests are never replayed against desktop models after a restart.
            for ident, raw in db.execute("SELECT id,data FROM chat_turns WHERE status='RUNNING'").fetchall():
                turn = json.loads(raw)
                turn.update(status="ERROR", error="Server restarted during this turn. Inspect any engineering run before retrying.")
                db.execute("UPDATE chat_turns SET status='ERROR',data=? WHERE id=?", (json.dumps(turn), ident))
            for ident, raw in db.execute("SELECT id,data FROM chat_change_plans WHERE status='EXECUTING'").fetchall():
                plan = json.loads(raw)
                plan.update(status="INTERRUPTED", error="Server restarted during execution. Inspect the saved run/model before proposing another change; no automatic replay.")
                db.execute("UPDATE chat_change_plans SET status='INTERRUPTED',data=? WHERE id=?", (json.dumps(plan), ident))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def session(self, ident=None):
        with self.lock, self.connection() as db:
            if ident:
                row = db.execute("SELECT data FROM chat_sessions WHERE id=?", (ident,)).fetchone()
                if row is None:
                    raise KeyError("Chat session not found.")
                return json.loads(row[0])
            session = {"id": "chat_" + uuid.uuid4().hex, "created_at": now(), "title": "New chat", "project_input": {}, "run_id": None}
            db.execute("INSERT INTO chat_sessions VALUES (?,?)", (session["id"], json.dumps(session)))
            return session

    def save_session(self, session):
        with self.lock, self.connection() as db:
            existing = db.execute("SELECT data FROM chat_sessions WHERE id=?", (session["id"],)).fetchone()
            if existing:
                title = json.loads(existing[0]).get("title", "New chat")
                if title != "New chat":
                    session["title"] = title  # An in-flight tool must not overwrite a rename.
            persisted = {key: value for key, value in session.items() if not key.startswith("_")}
            db.execute("UPDATE chat_sessions SET data=? WHERE id=?", (json.dumps(persisted, allow_nan=False), session["id"]))

    def rename_session(self, ident, title):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT data FROM chat_sessions WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError("Chat session not found.")
            session = json.loads(row[0])
            session["title"] = title
            db.execute("UPDATE chat_sessions SET data=? WHERE id=?", (json.dumps(session), ident))
            return session

    def new_turn(self, session_id, message):
        turn = {"id": "turn_" + uuid.uuid4().hex, "session_id": session_id, "created_at": now(),
                "message": message, "assistant": "", "status": "RUNNING", "events": [], "error": None, "run_id": None}
        with self.lock, self.connection() as db:
            from agentic.chat_usage import limit
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT COUNT(*) FROM chat_turns WHERE status='RUNNING'").fetchone()[0]
            if active >= limit('PLANT_CHAT_CONCURRENT_REQUESTS',4):
                raise ValueError('Chat concurrent-request limit reached. Wait for an active request to finish.')
            db.execute("INSERT INTO chat_turns VALUES (?,?,?,?)", (turn["id"], session_id, "RUNNING", json.dumps(turn)))
        return turn

    def turn(self, ident):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT data FROM chat_turns WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError("Chat turn not found.")
            return json.loads(row[0])

    def save_turn(self, turn):
        with self.lock, self.connection() as db:
            db.execute("UPDATE chat_turns SET status=?,data=? WHERE id=?",
                       (turn["status"], json.dumps(turn, allow_nan=False), turn["id"]))

    def admit_request(self, session_id):
        """SQLite-backed budgets also apply across API processes and new sessions."""
        import time
        from agentic.chat_usage import limit
        stamp = time.time()
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM chat_request_limits WHERE at < ?", (stamp - 60,))
            total = db.execute("SELECT COUNT(*) FROM chat_request_limits").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM chat_request_limits WHERE session_id=?", (session_id,)).fetchone()[0]
            if total >= limit("PLANT_CHAT_GLOBAL_REQUESTS_PER_MINUTE", 120) or own >= limit("PLANT_CHAT_REQUESTS_PER_MINUTE", 30):
                raise ValueError("Chat request-rate limit reached. Wait before trying again.")
            db.execute("INSERT INTO chat_request_limits VALUES (?,?)", (session_id, stamp))

    def save_plan(self, plan):
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO chat_change_plans VALUES (?,?,?,?)",
                       (plan['id'], plan['session_id'], plan['status'], json.dumps(plan, allow_nan=False)))

    def plan(self, ident, session_id):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT data FROM chat_change_plans WHERE id=? AND session_id=?", (ident, session_id)).fetchone()
            if row is None:
                raise KeyError("Change proposal not found in this conversation.")
            return json.loads(row[0])

    def pending_plans(self, session_id):
        with self.lock, self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT data FROM chat_change_plans WHERE session_id=? AND status IN ('PENDING','EXECUTING') ORDER BY rowid DESC",
                (session_id,))]

    def transition_plan(self, ident, session_id, expected, status, **updates):
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM chat_change_plans WHERE id=? AND session_id=? AND status=?",
                             (ident, session_id, expected)).fetchone()
            if row is None:
                raise ValueError("Proposal has already been handled or is no longer available.")
            plan = json.loads(row[0])
            plan.update(status=status, **updates)
            db.execute("UPDATE chat_change_plans SET status=?,data=? WHERE id=? AND session_id=? AND status=?",
                       (status, json.dumps(plan, allow_nan=False), ident, session_id, expected))
            return plan

    def complete_approval(self, ident, run_id, accepted_by, accepted_at):
        """Record an observed workflow acceptance once; never approves a workflow."""
        with self.lock, self.connection() as db:
            row = db.execute('SELECT data FROM chat_turns WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError('Chat turn not found.')
            turn = json.loads(row[0])
            if turn['status'] != 'AWAITING_APPROVAL' or turn.get('run_id') != run_id:
                return turn
            message = f'Design accepted and signed off by {accepted_by} on {accepted_at}. Run {run_id} is completed. You can continue in this conversation.'
            turn.update(status='COMPLETED', approval={'run_id':run_id, 'accepted_by':accepted_by, 'accepted_at':accepted_at})
            turn['assistant'] = (turn.get('assistant', '') + '\n\n' + message).strip()
            turn['events'].append({'sequence':len(turn['events'])+1, 'at':now(), 'type':'approval_recorded',
                                   'run_id':run_id, 'message':message})
            db.execute("UPDATE chat_turns SET status='COMPLETED',data=? WHERE id=? AND status='AWAITING_APPROVAL'",
                       (json.dumps(turn, allow_nan=False), ident))
            # Another poll may already have completed this turn in a second process.
            return json.loads(db.execute('SELECT data FROM chat_turns WHERE id=?', (ident,)).fetchone()[0])

    def list_sessions(self):
        with self.lock, self.connection() as db:
            rows = db.execute("SELECT s.data, (SELECT COUNT(*) FROM chat_turns t WHERE t.session_id=s.id), "
                              "EXISTS(SELECT 1 FROM chat_turns t WHERE t.session_id=s.id AND t.status='RUNNING'), "
                              "EXISTS(SELECT 1 FROM chat_turns t WHERE t.session_id=s.id AND t.status='AWAITING_APPROVAL') "
                              "FROM chat_sessions s ORDER BY s.rowid DESC").fetchall()
            return [{"id": value["id"], "title": value.get("title", "New chat"),
                     "created_at": value["created_at"], "turn_count": count, "running": bool(running),
                     "awaiting_approval": bool(waiting)}
                    for raw, count, running, waiting in rows for value in [json.loads(raw)]]

    def history(self, session_id, limit=50):
        with self.lock, self.connection() as db:
            query = "SELECT data FROM chat_turns WHERE session_id=? ORDER BY rowid DESC"
            rows = db.execute(query + (" LIMIT ?" if limit is not None else ""),
                              (session_id, limit) if limit is not None else (session_id,)).fetchall()
            return [json.loads(row[0]) for row in reversed(rows)]
