"""SQLite storage with schema versioning and retention enforcement.

Raw audio is never stored. Transcript lines and Q&A are only stored when the user's
retention setting allows it; session statistics (durations, token counts) are always kept
because they contain no conversation content.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import Retention

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- live | practice
    platform TEXT,
    model TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    questions INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transcript_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    question TEXT NOT NULL,
    category TEXT,
    answer TEXT,
    feedback TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcript_ts ON transcript_lines(ts);
CREATE INDEX IF NOT EXISTS idx_qa_ts ON qa_items(ts);
"""


@dataclass(slots=True)
class SessionStats:
    id: int
    kind: str
    platform: str | None
    model: str | None
    started_at: float
    ended_at: float | None
    questions: int
    input_tokens: int
    output_tokens: int


class Database:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
                )

    # --- profiles ------------------------------------------------------------------
    def save_profile(self, name: str, data: dict, profile_id: int | None = None) -> int:
        payload = json.dumps(data, ensure_ascii=False)
        with self._lock, self._conn:
            if profile_id is None:
                cur = self._conn.execute(
                    "INSERT INTO profiles(name, data, is_active, updated_at) VALUES(?, ?, 0, ?)",
                    (name, payload, time.time()),
                )
                profile_id = int(cur.lastrowid)
                if self._conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1:
                    self._conn.execute("UPDATE profiles SET is_active=1 WHERE id=?", (profile_id,))
            else:
                self._conn.execute(
                    "UPDATE profiles SET name=?, data=?, updated_at=? WHERE id=?",
                    (name, payload, time.time(), profile_id),
                )
        return profile_id

    def list_profiles(self) -> list[tuple[int, str, bool]]:
        with self._lock:
            rows = self._conn.execute("SELECT id, name, is_active FROM profiles ORDER BY name").fetchall()
        return [(r["id"], r["name"], bool(r["is_active"])) for r in rows]

    def load_profile(self, profile_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM profiles WHERE id=?", (profile_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def active_profile_id(self) -> int | None:
        with self._lock:
            row = self._conn.execute("SELECT id FROM profiles WHERE is_active=1").fetchone()
        return int(row["id"]) if row else None

    def set_active_profile(self, profile_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE profiles SET is_active = (id = ?)", (profile_id,))

    def delete_profile(self, profile_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
            if self._conn.execute("SELECT COUNT(*) FROM profiles WHERE is_active=1").fetchone()[0] == 0:
                self._conn.execute(
                    "UPDATE profiles SET is_active=1 WHERE id=(SELECT MIN(id) FROM profiles)"
                )

    # --- sessions ------------------------------------------------------------------
    def start_session(self, kind: str, platform: str | None, model: str | None) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions(kind, platform, model, started_at) VALUES(?, ?, ?, ?)",
                (kind, platform, model, time.time()),
            )
        return int(cur.lastrowid)

    def end_session(self, session_id: int, questions: int, input_tokens: int, output_tokens: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at=?, questions=?, input_tokens=?, output_tokens=? WHERE id=?",
                (time.time(), questions, input_tokens, output_tokens, session_id),
            )

    def recent_sessions(self, limit: int = 20) -> list[SessionStats]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [SessionStats(**dict(r)) for r in rows]

    # --- conversation content (retention-gated by the caller) --------------------------
    def add_transcript_line(self, session_id: int, speaker: str, text: str, ts: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO transcript_lines(session_id, ts, speaker, text) VALUES(?, ?, ?, ?)",
                (session_id, ts or time.time(), speaker, text),
            )

    def add_qa(
        self,
        session_id: int,
        question: str,
        category: str | None,
        answer: str | None,
        feedback: str | None = None,
        ts: float | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO qa_items(session_id, ts, question, category, answer, feedback) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (session_id, ts or time.time(), question, category, answer, feedback),
            )

    def count_content(self) -> tuple[int, int]:
        with self._lock:
            lines = self._conn.execute("SELECT COUNT(*) FROM transcript_lines").fetchone()[0]
            qa = self._conn.execute("SELECT COUNT(*) FROM qa_items").fetchone()[0]
        return int(lines), int(qa)

    def purge_expired(self, retention: Retention, now: float | None = None) -> int:
        """Delete conversation content older than the retention window. Returns rows deleted."""
        days = retention.days
        if days is None:
            return 0
        cutoff = (now or time.time()) - days * 86400
        with self._lock, self._conn:
            a = self._conn.execute("DELETE FROM transcript_lines WHERE ts < ?", (cutoff,)).rowcount
            b = self._conn.execute("DELETE FROM qa_items WHERE ts < ?", (cutoff,)).rowcount
        return a + b

    def delete_all_content(self) -> None:
        """Delete every transcript line and Q&A item (session statistics remain)."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM transcript_lines")
            self._conn.execute("DELETE FROM qa_items")
        with self._lock:
            self._conn.execute("VACUUM")

    def delete_everything(self) -> None:
        with self._lock, self._conn:
            for table in ("transcript_lines", "qa_items", "sessions", "profiles"):
                self._conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        with self._lock:
            self._conn.execute("VACUUM")
