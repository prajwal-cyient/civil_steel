"""Separate SQLite conversation history; existing engineering tables are untouched."""
import json
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
        self.path = str(path or Path(__file__).resolve().parents[2] / "data" / "chat.db")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS chat_sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_turns (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS chat_active_session ON chat_turns(session_id) WHERE status='RUNNING'")
            # Interrupted requests are never replayed against desktop models after a restart.
            for ident, raw in db.execute("SELECT id,data FROM chat_turns WHERE status='RUNNING'").fetchall():
                turn = json.loads(raw)
                turn.update(status="ERROR", error="Server restarted during this turn. Inspect any engineering run before retrying.")
                db.execute("UPDATE chat_turns SET status='ERROR',data=? WHERE id=?", (json.dumps(turn), ident))

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
            db.execute("UPDATE chat_sessions SET data=? WHERE id=?", (json.dumps(session, allow_nan=False), session["id"]))

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

    def list_sessions(self):
        with self.lock, self.connection() as db:
            rows = db.execute("SELECT s.data, (SELECT COUNT(*) FROM chat_turns t WHERE t.session_id=s.id), "
                              "EXISTS(SELECT 1 FROM chat_turns t WHERE t.session_id=s.id AND t.status='RUNNING') "
                              "FROM chat_sessions s ORDER BY s.rowid DESC").fetchall()
            return [{"id": value["id"], "title": value.get("title", "New chat"),
                     "created_at": value["created_at"], "turn_count": count, "running": bool(running)}
                    for raw, count, running in rows for value in [json.loads(raw)]]

    def history(self, session_id, limit=50):
        with self.lock, self.connection() as db:
            query = "SELECT data FROM chat_turns WHERE session_id=? ORDER BY rowid DESC"
            rows = db.execute(query + (" LIMIT ?" if limit is not None else ""),
                              (session_id, limit) if limit is not None else (session_id,)).fetchall()
            return [json.loads(row[0]) for row in reversed(rows)]
