"""SQLite storage for caption sessions and approved speaker profiles."""

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
SCHEMA_VERSION = 3


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
    session_person_id: int | None
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
class PersonVoiceSignature:
    embedding: bytes
    model_name: str
    model_version: str
    dimensions: int
    quality_score: float | None


@dataclass(frozen=True, slots=True)
class PersonProfile:
    id: int
    name: str
    signatures: tuple[PersonVoiceSignature, ...]


@dataclass(frozen=True, slots=True)
class RememberedPersonSummary:
    id: int
    name: str
    recognition_enabled: bool
    signature_count: int
    session_count: int


@dataclass(frozen=True, slots=True)
class SessionPerson:
    id: int
    session_id: int
    person_id: int | None
    session_label: str
    display_name: str | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class SessionSpeakerProfile:
    session_label: str
    person_id: int | None
    display_name: str | None
    signatures: tuple[PersonVoiceSignature, ...]


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
                CREATE INDEX IF NOT EXISTS session_signatures_person_index
                    ON session_voice_signatures(session_person_id, id);
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

        migrations = {
            2: self._migrate_to_version_2,
            3: self._migrate_to_version_3,
        }
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

    def _migrate_to_version_3(self) -> None:
        """Guarantee one row for each temporary label in a session."""

        with self._connection:
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS session_persons_label_index
                ON session_persons(session_id, session_label)
                """
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
        if status not in {"completed", "incomplete"}:
            raise ValueError(f"A finished session cannot have status: {status}")
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
        session_person_id: int | None = None,
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
                    session_id, session_person_id, speaker_label, start_offset_seconds,
                    end_offset_seconds, created_at, source_language,
                    language_confidence, original_text, target_language,
                    translated_text, warning, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    session_person_id,
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
                session_person_id=row["session_person_id"],
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

    def upsert_session_person(
        self,
        *,
        session_id: int,
        session_label: str,
        person_id: int | None,
        confidence: float | None,
    ) -> int:
        """Create or update one session-local speaker without duplicating it."""

        with self._lock, self._connection:
            display_name = None
            if person_id is not None:
                row = self._connection.execute(
                    "SELECT name FROM persons WHERE id = ?",
                    (person_id,),
                ).fetchone()
                display_name = row["name"] if row is not None else None
            self._connection.execute(
                """
                INSERT INTO session_persons(
                    session_id, person_id, session_label,
                    session_display_name, confidence
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, session_label) DO UPDATE SET
                    person_id = COALESCE(
                        excluded.person_id,
                        session_persons.person_id
                    ),
                    session_display_name = COALESCE(
                        excluded.session_display_name,
                        session_persons.session_display_name
                    ),
                    confidence = excluded.confidence
                """,
                (
                    session_id,
                    person_id,
                    session_label,
                    display_name,
                    confidence,
                ),
            )
            row = self._connection.execute(
                """
                SELECT id FROM session_persons
                WHERE session_id = ? AND session_label = ?
                """,
                (session_id, session_label),
            ).fetchone()
            return int(row["id"])

    def add_session_voice_signature(
        self,
        *,
        session_person_id: int,
        embedding: bytes,
        model_name: str,
        model_version: str,
        dimensions: int,
        quality_score: float | None,
        max_signatures: int = 5,
    ) -> int:
        if max_signatures <= 0:
            raise ValueError("max_signatures must be greater than zero.")
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT id FROM session_voice_signatures
                WHERE session_person_id = ? ORDER BY id
                """,
                (session_person_id,),
            ).fetchall()
            excess = len(rows) - max_signatures + 1
            if excess > 0:
                self._connection.executemany(
                    "DELETE FROM session_voice_signatures WHERE id = ?",
                    ((int(row["id"]),) for row in rows[:excess]),
                )
            cursor = self._connection.execute(
                """
                INSERT INTO session_voice_signatures(
                    session_person_id, embedding, model_name, model_version,
                    dimensions, quality_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_person_id,
                    embedding,
                    model_name,
                    model_version,
                    dimensions,
                    quality_score,
                    local_timestamp(),
                ),
            )
            signature_id = int(cursor.lastrowid)
            person_row = self._connection.execute(
                "SELECT person_id FROM session_persons WHERE id = ?",
                (session_person_id,),
            ).fetchone()
            person_id = person_row["person_id"] if person_row is not None else None
            if person_id is not None:
                saved_rows = self._connection.execute(
                    """
                    SELECT id FROM person_voice_signatures
                    WHERE person_id = ? ORDER BY id
                    """,
                    (person_id,),
                ).fetchall()
                duplicate = self._connection.execute(
                    """
                    SELECT 1 FROM person_voice_signatures
                    WHERE person_id = ? AND embedding = ?
                      AND model_name = ? AND model_version = ?
                    """,
                    (person_id, embedding, model_name, model_version),
                ).fetchone()
                if duplicate is None:
                    excess = len(saved_rows) - max_signatures + 1
                    if excess > 0:
                        self._connection.executemany(
                            "DELETE FROM person_voice_signatures WHERE id = ?",
                            ((int(row["id"]),) for row in saved_rows[:excess]),
                        )
                    self._connection.execute(
                        """
                        INSERT INTO person_voice_signatures(
                            person_id, embedding, model_name, model_version,
                            dimensions, quality_score, created_at,
                            source_session_person_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            person_id,
                            embedding,
                            model_name,
                            model_version,
                            dimensions,
                            quality_score,
                            local_timestamp(),
                            session_person_id,
                        ),
                    )
            return signature_id

    def get_session_person(self, session_person_id: int) -> SessionPerson | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, session_id, person_id, session_label,
                       session_display_name, confidence
                FROM session_persons WHERE id = ?
                """,
                (session_person_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionPerson(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            person_id=row["person_id"],
            session_label=str(row["session_label"]),
            display_name=row["session_display_name"],
            confidence=row["confidence"],
        )

    def remember_session_person(self, session_person_id: int, name: str) -> int:
        """Create or update one user-approved person and copy voice signatures."""

        normalized_name = self._validated_person_name(name)
        now = local_timestamp()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT person_id FROM session_persons WHERE id = ?",
                (session_person_id,),
            ).fetchone()
            if row is None:
                raise ValueError("The selected session speaker does not exist.")
            person_id = row["person_id"]
            if person_id is None:
                cursor = self._connection.execute(
                    """
                    INSERT INTO persons(name, created_at, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_name, now, now),
                )
                person_id = int(cursor.lastrowid)
            else:
                self._connection.execute(
                    "UPDATE persons SET name = ?, updated_at = ? WHERE id = ?",
                    (normalized_name, now, person_id),
                )
            self._connection.execute(
                """
                UPDATE session_persons
                SET person_id = ?, session_display_name = ?
                WHERE id = ?
                """,
                (person_id, normalized_name, session_person_id),
            )
            self._connection.execute(
                """
                UPDATE session_persons SET session_display_name = ?
                WHERE person_id = ?
                """,
                (normalized_name, person_id),
            )
            self._connection.execute(
                """
                INSERT INTO person_voice_signatures(
                    person_id, embedding, model_name, model_version,
                    dimensions, quality_score, created_at,
                    source_session_person_id
                )
                SELECT ?, source.embedding, source.model_name,
                       source.model_version, source.dimensions,
                       source.quality_score, ?, source.session_person_id
                FROM session_voice_signatures AS source
                WHERE source.session_person_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM person_voice_signatures AS saved
                      WHERE saved.person_id = ?
                        AND saved.embedding = source.embedding
                        AND saved.model_name = source.model_name
                        AND saved.model_version = source.model_version
                  )
                """,
                (person_id, now, session_person_id, person_id),
            )
            self._connection.execute(
                """
                UPDATE messages SET speaker_label = ?
                WHERE session_person_id IN (
                    SELECT id FROM session_persons WHERE person_id = ?
                )
                """,
                (normalized_name, person_id),
            )
            self._connection.execute(
                """
                DELETE FROM person_voice_signatures
                WHERE person_id = ? AND id NOT IN (
                    SELECT id FROM person_voice_signatures WHERE person_id = ?
                    ORDER BY quality_score DESC, id DESC LIMIT 5
                )
                """,
                (person_id, person_id),
            )
            return int(person_id)

    def list_person_profiles(self) -> list[PersonProfile]:
        """Return only enabled, named profiles with approved signatures."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT persons.id, persons.name, signatures.embedding,
                       signatures.model_name, signatures.model_version,
                       signatures.dimensions, signatures.quality_score
                FROM persons
                JOIN person_voice_signatures AS signatures
                  ON signatures.person_id = persons.id
                WHERE persons.recognition_enabled = 1
                  AND persons.name IS NOT NULL
                  AND TRIM(persons.name) != ''
                ORDER BY persons.id, signatures.id
                """
            ).fetchall()
        grouped: dict[int, tuple[str, list[PersonVoiceSignature]]] = {}
        for row in rows:
            person_id = int(row["id"])
            name, signatures = grouped.setdefault(
                person_id,
                (str(row["name"]), []),
            )
            signatures.append(
                PersonVoiceSignature(
                    embedding=bytes(row["embedding"]),
                    model_name=str(row["model_name"]),
                    model_version=str(row["model_version"]),
                    dimensions=int(row["dimensions"]),
                    quality_score=row["quality_score"],
                )
            )
        return [
            PersonProfile(person_id, name, tuple(signatures))
            for person_id, (name, signatures) in grouped.items()
        ]

    def list_people(self) -> list[RememberedPersonSummary]:
        """List remembered people, including disabled or signature-free rows."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT persons.id, persons.name, persons.recognition_enabled,
                       COUNT(DISTINCT signatures.id) AS signature_count,
                       COUNT(DISTINCT session_people.session_id) AS session_count
                FROM persons
                LEFT JOIN person_voice_signatures AS signatures
                  ON signatures.person_id = persons.id
                LEFT JOIN session_persons AS session_people
                  ON session_people.person_id = persons.id
                GROUP BY persons.id
                ORDER BY LOWER(COALESCE(persons.name, '')), persons.id
                """
            ).fetchall()
        return [
            RememberedPersonSummary(
                id=int(row["id"]),
                name=str(row["name"] or "Unnamed person"),
                recognition_enabled=bool(row["recognition_enabled"]),
                signature_count=int(row["signature_count"]),
                session_count=int(row["session_count"]),
            )
            for row in rows
        ]

    def rename_person(self, person_id: int, name: str) -> None:
        normalized_name = self._validated_person_name(name)
        now = local_timestamp()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE persons SET name = ?, updated_at = ? WHERE id = ?",
                (normalized_name, now, person_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("The selected remembered person does not exist.")
            self._connection.execute(
                """
                UPDATE session_persons SET session_display_name = ?
                WHERE person_id = ?
                """,
                (normalized_name, person_id),
            )
            self._connection.execute(
                """
                UPDATE messages SET speaker_label = ?
                WHERE session_person_id IN (
                    SELECT id FROM session_persons WHERE person_id = ?
                )
                """,
                (normalized_name, person_id),
            )

    def set_person_recognition_enabled(
        self,
        person_id: int,
        enabled: bool,
    ) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE persons
                SET recognition_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(enabled), local_timestamp(), person_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("The selected remembered person does not exist.")

    def forget_person_voice(self, person_id: int) -> None:
        """Remove reusable identity data while preserving conversation text."""

        with self._lock, self._connection:
            self._require_person(person_id)
            self._restore_original_session_labels(person_id)
            self._connection.execute(
                "DELETE FROM person_voice_signatures WHERE person_id = ?",
                (person_id,),
            )
            self._connection.execute(
                """
                UPDATE persons
                SET recognition_enabled = 0, updated_at = ? WHERE id = ?
                """,
                (local_timestamp(), person_id),
            )

    def delete_person(self, person_id: int) -> None:
        """Delete one profile while retaining its conversations and labels."""

        with self._lock, self._connection:
            self._require_person(person_id)
            self._restore_original_session_labels(person_id)
            self._connection.execute("DELETE FROM persons WHERE id = ?", (person_id,))

    def merge_people(
        self,
        source_person_id: int,
        target_person_id: int,
        *,
        max_signatures: int = 5,
    ) -> None:
        """Merge one mistaken duplicate into the selected remembered person."""

        if source_person_id == target_person_id:
            raise ValueError("Choose two different remembered people to merge.")
        if max_signatures <= 0:
            raise ValueError("max_signatures must be greater than zero.")
        with self._lock, self._connection:
            self._require_person(source_person_id)
            target_name = self._require_person(target_person_id)
            source_signatures = self._connection.execute(
                """
                SELECT embedding, model_name, model_version, dimensions,
                       quality_score, created_at, source_session_person_id
                FROM person_voice_signatures WHERE person_id = ?
                ORDER BY quality_score DESC, id DESC
                """,
                (source_person_id,),
            ).fetchall()
            for signature in source_signatures:
                duplicate = self._connection.execute(
                    """
                    SELECT 1 FROM person_voice_signatures
                    WHERE person_id = ? AND embedding = ?
                      AND model_name = ? AND model_version = ?
                    """,
                    (
                        target_person_id,
                        signature["embedding"],
                        signature["model_name"],
                        signature["model_version"],
                    ),
                ).fetchone()
                if duplicate is None:
                    self._connection.execute(
                        """
                        INSERT INTO person_voice_signatures(
                            person_id, embedding, model_name, model_version,
                            dimensions, quality_score, created_at,
                            source_session_person_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target_person_id,
                            signature["embedding"],
                            signature["model_name"],
                            signature["model_version"],
                            signature["dimensions"],
                            signature["quality_score"],
                            signature["created_at"],
                            signature["source_session_person_id"],
                        ),
                    )
            self._connection.execute(
                """
                UPDATE session_persons
                SET person_id = ?, session_display_name = ?
                WHERE person_id = ?
                """,
                (target_person_id, target_name, source_person_id),
            )
            self._connection.execute(
                """
                UPDATE messages SET speaker_label = ?
                WHERE session_person_id IN (
                    SELECT id FROM session_persons WHERE person_id = ?
                )
                """,
                (target_name, target_person_id),
            )
            self._connection.execute(
                "DELETE FROM persons WHERE id = ?",
                (source_person_id,),
            )
            kept = self._connection.execute(
                """
                SELECT id FROM person_voice_signatures
                WHERE person_id = ?
                ORDER BY quality_score DESC, id DESC
                LIMIT -1 OFFSET ?
                """,
                (target_person_id, max_signatures),
            ).fetchall()
            self._connection.executemany(
                "DELETE FROM person_voice_signatures WHERE id = ?",
                ((int(row["id"]),) for row in kept),
            )

    @staticmethod
    def _validated_person_name(name: str) -> str:
        normalized_name = " ".join(name.split())
        if not normalized_name:
            raise ValueError("Person name cannot be empty.")
        if len(normalized_name) > 120:
            raise ValueError("Person name cannot exceed 120 characters.")
        return normalized_name

    def _require_person(self, person_id: int) -> str:
        row = self._connection.execute(
            "SELECT name FROM persons WHERE id = ?",
            (person_id,),
        ).fetchone()
        if row is None:
            raise ValueError("The selected remembered person does not exist.")
        return str(row["name"] or "Unnamed person")

    def _restore_original_session_labels(self, person_id: int) -> None:
        self._connection.execute(
            """
            UPDATE messages
            SET speaker_label = (
                SELECT session_label FROM session_persons
                WHERE session_persons.id = messages.session_person_id
            )
            WHERE session_person_id IN (
                SELECT id FROM session_persons WHERE person_id = ?
            )
            """,
            (person_id,),
        )
        self._connection.execute(
            """
            UPDATE session_persons
            SET person_id = NULL, session_display_name = NULL
            WHERE person_id = ?
            """,
            (person_id,),
        )

    def list_session_speaker_profiles(
        self,
        session_id: int,
    ) -> list[SessionSpeakerProfile]:
        """Load temporary signatures needed to resume stable session labels."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT session_persons.id, session_persons.person_id,
                       session_persons.session_label,
                       session_persons.session_display_name,
                       signatures.embedding, signatures.model_name,
                       signatures.model_version, signatures.dimensions,
                       signatures.quality_score
                FROM session_persons
                JOIN session_voice_signatures AS signatures
                  ON signatures.session_person_id = session_persons.id
                WHERE session_persons.session_id = ?
                ORDER BY session_persons.id, signatures.id
                """,
                (session_id,),
            ).fetchall()
        grouped: dict[
            int,
            tuple[str, int | None, str | None, list[PersonVoiceSignature]],
        ] = {}
        for row in rows:
            session_person_id = int(row["id"])
            label, person_id, display_name, signatures = grouped.setdefault(
                session_person_id,
                (
                    str(row["session_label"]),
                    row["person_id"],
                    row["session_display_name"],
                    [],
                ),
            )
            signatures.append(
                PersonVoiceSignature(
                    embedding=bytes(row["embedding"]),
                    model_name=str(row["model_name"]),
                    model_version=str(row["model_version"]),
                    dimensions=int(row["dimensions"]),
                    quality_score=row["quality_score"],
                )
            )
        return [
            SessionSpeakerProfile(label, person_id, display_name, tuple(signatures))
            for label, person_id, display_name, signatures in grouped.values()
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
        return " AND ".join(f'"{token}"*' for token in tokens)

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

