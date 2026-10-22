"""SQLite storage for caption sessions and future speaker profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import sqlite3
from threading import RLock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "history.sqlite3"
VALID_SESSION_STATUSES = {"live", "paused", "incomplete", "completed"}


def local_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class HistorySession:
    id: int
    title: str
    started_at: str
    ended_at: str | None
    status: str
    audio_source: str
    language_mode: str
    preferred_source_language: str | None
    target_language: str | None
    preview: str
    message_count: int


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    id: int
    session_id: int
    created_at: str
    speaker_label: str
    start_offset_seconds: float
    end_offset_seconds: float
    source_language: str | None
    language_confidence: float | None
    original_text: str
    target_language: str | None
    translated_text: str | None
    warning: bool
    error_text: str | None


class HistoryDatabase:
    """Small synchronous repository used from the Qt interface thread."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.environ.get(
            "LIVE_CAPTION_HISTORY_PATH",
            str(DEFAULT_DATABASE_PATH),
        )
        self.path = str(configured_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = RLock()
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                INSERT OR IGNORE INTO schema_meta(key, value)
                VALUES ('schema_version', '1');

                CREATE TABLE IF NOT EXISTS persons (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    preferred_colour TEXT,
                    recognition_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('live', 'paused', 'incomplete', 'completed')
                    ),
                    audio_source TEXT NOT NULL,
                    language_mode TEXT NOT NULL,
                    preferred_source_language TEXT,
                    target_language TEXT,
                    model_settings_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES sessions(id)
                        ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS session_persons (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES sessions(id)
                        ON DELETE CASCADE,
                    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
                    session_label TEXT NOT NULL,
                    session_display_name TEXT,
                    session_colour TEXT,
                    confidence REAL,
                    is_local_user INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS person_voice_signatures (
                    id INTEGER PRIMARY KEY,
                    person_id INTEGER NOT NULL REFERENCES persons(id)
                        ON DELETE CASCADE,
                    embedding BLOB NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    quality_score REAL,
                    created_at TEXT NOT NULL,
                    source_session_person_id INTEGER REFERENCES session_persons(id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS session_voice_signatures (
                    id INTEGER PRIMARY KEY,
                    session_person_id INTEGER NOT NULL REFERENCES session_persons(id)
                        ON DELETE CASCADE,
                    embedding BLOB NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    quality_score REAL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES sessions(id)
                        ON DELETE CASCADE,
                    session_person_id INTEGER REFERENCES session_persons(id)
                        ON DELETE SET NULL,
                    speaker_label TEXT NOT NULL,
                    start_offset_seconds REAL NOT NULL,
                    end_offset_seconds REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    source_language TEXT,
                    language_confidence REAL,
                    original_text TEXT NOT NULL,
                    target_language TEXT,
                    translated_text TEXT,
                    recognition_confidence REAL,
                    overlap_status TEXT NOT NULL DEFAULT 'none',
                    warning INTEGER NOT NULL DEFAULT 0,
                    error_text TEXT
                );

                CREATE INDEX IF NOT EXISTS sessions_started_at_index
                    ON sessions(started_at DESC);
                CREATE INDEX IF NOT EXISTS messages_session_index
                    ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS session_events_session_index
                    ON session_events(session_id, id);
                """
            )

    def recover_interrupted_sessions(self) -> int:
        """Mark sessions left live or paused by an earlier exit as incomplete."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE sessions
                SET status = 'incomplete', ended_at = COALESCE(ended_at, ?)
                WHERE status IN ('live', 'paused')
                """,
                (local_timestamp(),),
            )
            return cursor.rowcount

    def create_session(
        self,
        *,
        title: str,
        audio_source: str,
        language_mode: str,
        preferred_source_language: str | None,
        target_language: str | None,
    ) -> int:
        started_at = local_timestamp()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO sessions(
                    title, started_at, status, audio_source, language_mode,
                    preferred_source_language, target_language
                ) VALUES (?, ?, 'live', ?, ?, ?, ?)
                """,
                (
                    title,
                    started_at,
                    audio_source,
                    language_mode,
                    preferred_source_language,
                    target_language,
                ),
            )
            session_id = int(cursor.lastrowid)
            self._connection.execute(
                """
                INSERT INTO session_events(session_id, event_type, occurred_at)
                VALUES (?, 'start', ?)
                """,
                (session_id, started_at),
            )
            return session_id

    def finish_session(self, session_id: int, status: str = "completed") -> None:
        if status not in VALID_SESSION_STATUSES:
            raise ValueError(f"Unknown session status: {status}")
        ended_at = local_timestamp()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?",
                (status, ended_at, session_id),
            )
            self._connection.execute(
                """
                INSERT INTO session_events(session_id, event_type, occurred_at)
                VALUES (?, 'stop', ?)
                """,
                (session_id, ended_at),
            )

    def add_message(
        self,
        *,
        session_id: int,
        speaker_label: str,
        start_offset_seconds: float,
        end_offset_seconds: float,
        source_language: str | None,
        language_confidence: float | None,
        original_text: str,
        target_language: str | None,
        translated_text: str | None,
        warning: bool = False,
        error_text: str | None = None,
        created_at: str | None = None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO messages(
                    session_id, speaker_label, start_offset_seconds,
                    end_offset_seconds, created_at, source_language,
                    language_confidence, original_text, target_language,
                    translated_text, warning, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    speaker_label,
                    start_offset_seconds,
                    end_offset_seconds,
                    created_at or local_timestamp(),
                    source_language,
                    language_confidence,
                    original_text,
                    target_language,
                    translated_text,
                    int(warning),
                    error_text,
                ),
            )
            return int(cursor.lastrowid)

    def list_sessions(self) -> list[HistorySession]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                    sessions.*,
                    COALESCE(
                        (
                            SELECT original_text
                            FROM messages
                            WHERE messages.session_id = sessions.id
                            ORDER BY messages.id DESC
                            LIMIT 1
                        ),
                        'No captions yet'
                    ) AS preview,
                    (
                        SELECT COUNT(*)
                        FROM messages
                        WHERE messages.session_id = sessions.id
                    ) AS message_count
                FROM sessions
                ORDER BY started_at DESC, id DESC
                """
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: int) -> HistorySession | None:
        return next(
            (
                session
                for session in self.list_sessions()
                if session.id == session_id
            ),
            None,
        )

    def get_messages(self, session_id: int) -> list[HistoryMessage]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        return [
            HistoryMessage(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                created_at=str(row["created_at"]),
                speaker_label=str(row["speaker_label"]),
                start_offset_seconds=float(row["start_offset_seconds"]),
                end_offset_seconds=float(row["end_offset_seconds"]),
                source_language=row["source_language"],
                language_confidence=row["language_confidence"],
                original_text=str(row["original_text"]),
                target_language=row["target_language"],
                translated_text=row["translated_text"],
                warning=bool(row["warning"]),
                error_text=row["error_text"],
            )
            for row in rows
        ]

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> HistorySession:
        return HistorySession(
            id=int(row["id"]),
            title=str(row["title"]),
            started_at=str(row["started_at"]),
            ended_at=row["ended_at"],
            status=str(row["status"]),
            audio_source=str(row["audio_source"]),
            language_mode=str(row["language_mode"]),
            preferred_source_language=row["preferred_source_language"],
            target_language=row["target_language"],
            preview=str(row["preview"]),
            message_count=int(row["message_count"]),
        )

