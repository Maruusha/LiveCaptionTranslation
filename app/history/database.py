"""SQLite storage for caption sessions and future speaker profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "history.sqlite3"
VALID_SESSION_STATUSES = {"live", "paused", "incomplete", "completed"}
SCHEMA_VERSION = 2


def local_timestamp() -> str:
    """Return a portable UTC timestamp; the UI converts it to local time."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


@dataclass(frozen=True, slots=True)
class HistoryEvent:
    id: int
    session_id: int
    event_type: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class DatabaseMaintenanceResult:
    integrity_ok: bool
    integrity_details: tuple[str, ...]
    size_before_bytes: int
    size_after_bytes: int

    @property
    def reclaimed_bytes(self) -> int:
        return max(0, self.size_before_bytes - self.size_after_bytes)


class HistoryDatabase:
    """Small synchronous repository used from the Qt interface thread."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.environ.get(
            "LIVE_CAPTION_HISTORY_PATH",
            str(DEFAULT_DATABASE_PATH),
        )
        self.path = str(configured_path)
        database_file = None if self.path == ":memory:" else Path(self.path)
        self._database_preexisted = bool(
            database_file is not None
            and database_file.is_file()
            and database_file.stat().st_size > 0
        )
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
        self._lock = RLock()
        self._create_schema()
        self._apply_migrations()
        self.startup_integrity = self.integrity_check()

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

    def _apply_migrations(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current_version = int(row["value"]) if row is not None else 1
        if current_version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"History database version {current_version} is newer than "
                f"supported version {SCHEMA_VERSION}."
            )

        migrations = {2: self._migrate_to_version_2}
        while current_version < SCHEMA_VERSION:
            next_version = current_version + 1
            if self._database_preexisted:
                self._create_pre_migration_backup(next_version)
            migrations[next_version]()
            with self._connection:
                self._connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(next_version),),
                )
            current_version = next_version

    def _migrate_to_version_2(self) -> None:
        """Add indexed text search and relationship indexes."""

        with self._connection:
            self._connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS messages_created_at_index
                    ON messages(created_at);
                CREATE INDEX IF NOT EXISTS session_persons_session_index
                    ON session_persons(session_id, id);
                CREATE INDEX IF NOT EXISTS session_persons_person_index
                    ON session_persons(person_id);
                CREATE INDEX IF NOT EXISTS person_signatures_person_index
                    ON person_voice_signatures(person_id, id);

                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    original_text,
                    translated_text,
                    content='messages',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );

                CREATE TRIGGER IF NOT EXISTS messages_fts_after_insert
                AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(
                        rowid, original_text, translated_text
                    ) VALUES (
                        new.id,
                        new.original_text,
                        COALESCE(new.translated_text, '')
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS messages_fts_after_delete
                AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(
                        messages_fts, rowid, original_text, translated_text
                    ) VALUES (
                        'delete',
                        old.id,
                        old.original_text,
                        COALESCE(old.translated_text, '')
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS messages_fts_after_update
                AFTER UPDATE OF original_text, translated_text ON messages BEGIN
                    INSERT INTO messages_fts(
                        messages_fts, rowid, original_text, translated_text
                    ) VALUES (
                        'delete',
                        old.id,
                        old.original_text,
                        COALESCE(old.translated_text, '')
                    );
                    INSERT INTO messages_fts(
                        rowid, original_text, translated_text
                    ) VALUES (
                        new.id,
                        new.original_text,
                        COALESCE(new.translated_text, '')
                    );
                END;
                """
            )
            self._connection.execute(
                "INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')"
            )

    def _create_pre_migration_backup(self, target_version: int) -> Path:
        source = Path(self.path)
        base = source.with_name(f"{source.name}.pre-v{target_version}.backup")
        destination = base
        suffix = 1
        while destination.exists():
            destination = base.with_name(f"{base.name}.{suffix}")
            suffix += 1
        self.backup_to(destination)
        return destination

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

    def pause_session(self, session_id: int) -> None:
        """Mark one open session paused and record its pause boundary."""

        self._set_open_session_status(
            session_id,
            expected_status="live",
            new_status="paused",
            event_type="pause",
        )

    def resume_session(self, session_id: int) -> None:
        """Mark one paused session live and record its resume boundary."""

        self._set_open_session_status(
            session_id,
            expected_status="paused",
            new_status="live",
            event_type="resume",
        )

    def _set_open_session_status(
        self,
        session_id: int,
        expected_status: str,
        new_status: str,
        event_type: str,
    ) -> None:
        occurred_at = local_timestamp()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE sessions SET status = ?, ended_at = NULL
                WHERE id = ? AND status = ?
                """,
                (new_status, session_id, expected_status),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"Session {session_id} is not {expected_status}."
                )
            self._connection.execute(
                """
                INSERT INTO session_events(session_id, event_type, occurred_at)
                VALUES (?, ?, ?)
                """,
                (session_id, event_type, occurred_at),
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

    def list_sessions(self, search_text: str = "") -> list[HistorySession]:
        search_text = search_text.strip()
        where_clause = ""
        parameters: tuple[str, ...] = ()
        if search_text:
            escaped_like = (
                search_text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            fts_query = self._fts_prefix_query(search_text)
            if fts_query:
                where_clause = """
                    WHERE LOWER(sessions.title) LIKE LOWER(?) ESCAPE '\\'
                       OR EXISTS (
                           SELECT 1
                           FROM messages
                           WHERE messages.session_id = sessions.id
                             AND messages.id IN (
                                 SELECT rowid
                                 FROM messages_fts
                                 WHERE messages_fts MATCH ?
                             )
                       )
                """
                parameters = (f"%{escaped_like}%", fts_query)
            else:
                where_clause = """
                    WHERE LOWER(sessions.title) LIKE LOWER(?) ESCAPE '\\'
                       OR EXISTS (
                           SELECT 1
                           FROM messages
                           WHERE messages.session_id = sessions.id
                             AND (
                                 LOWER(messages.original_text)
                                     LIKE LOWER(?) ESCAPE '\\'
                                 OR LOWER(COALESCE(messages.translated_text, ''))
                                     LIKE LOWER(?) ESCAPE '\\'
                             )
                       )
                """
                like_value = f"%{escaped_like}%"
                parameters = (like_value, like_value, like_value)
        with self._lock:
            rows = self._connection.execute(
                f"""
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
                {where_clause}
                ORDER BY started_at DESC, id DESC
                """,
                parameters,
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: int) -> HistorySession | None:
        with self._lock:
            row = self._connection.execute(
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
                WHERE sessions.id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._session_from_row(row) if row is not None else None

    def rename_session(self, session_id: int, title: str) -> None:
        normalized_title = " ".join(title.split())
        if not normalized_title:
            raise ValueError("Conversation title cannot be empty.")
        if len(normalized_title) > 120:
            raise ValueError("Conversation title cannot exceed 120 characters.")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (normalized_title, session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Conversation {session_id} does not exist.")

    def delete_session(self, session_id: int) -> None:
        """Delete one closed conversation and its session-scoped children."""

        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM sessions
                WHERE id = ? AND status IN ('completed', 'incomplete')
                """,
                (session_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "Only a completed or incomplete conversation can be deleted."
                )

    def delete_all_closed_sessions(self) -> int:
        """Delete saved history without touching a live or paused session."""

        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM sessions
                WHERE status IN ('completed', 'incomplete')
                """
            )
            return cursor.rowcount

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

    def get_events(self, session_id: int) -> list[HistoryEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, session_id, event_type, occurred_at
                FROM session_events
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        return [
            HistoryEvent(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                event_type=str(row["event_type"]),
                occurred_at=str(row["occurred_at"]),
            )
            for row in rows
        ]

    def integrity_check(self) -> tuple[str, ...]:
        """Run SQLite's lightweight consistency check without changing data."""

        with self._lock:
            rows = self._connection.execute("PRAGMA quick_check").fetchall()
        return tuple(str(row[0]) for row in rows)

    def database_size_bytes(self) -> int:
        if self.path == ":memory:":
            return 0
        database_path = Path(self.path)
        related_paths = (
            database_path,
            Path(f"{database_path}-wal"),
            Path(f"{database_path}-shm"),
        )
        return sum(path.stat().st_size for path in related_paths if path.exists())

    def maintain(self) -> DatabaseMaintenanceResult:
        """Check integrity and reclaim unused SQLite pages when safe."""

        details = self.integrity_check()
        size_before = self.database_size_bytes()
        if details != ("ok",):
            return DatabaseMaintenanceResult(
                integrity_ok=False,
                integrity_details=details,
                size_before_bytes=size_before,
                size_after_bytes=size_before,
            )
        with self._lock:
            self._connection.execute("PRAGMA optimize")
            self._connection.commit()
            self._connection.execute("VACUUM")
        return DatabaseMaintenanceResult(
            integrity_ok=True,
            integrity_details=details,
            size_before_bytes=size_before,
            size_after_bytes=self.database_size_bytes(),
        )

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup without replacing an existing file."""

        if self.path == ":memory:" and str(destination) == ":memory:":
            raise ValueError("Backup destination must be a file.")
        destination_path = Path(destination)
        if destination_path.exists():
            raise FileExistsError(f"Backup already exists: {destination_path}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        backup_connection = sqlite3.connect(destination_path)
        try:
            with self._lock:
                self._connection.backup(backup_connection)
        finally:
            backup_connection.close()
        return destination_path

    @staticmethod
    def _fts_prefix_query(search_text: str) -> str:
        tokens = re.findall(r"\w+", search_text.casefold(), flags=re.UNICODE)
        return " AND ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens
        )

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

