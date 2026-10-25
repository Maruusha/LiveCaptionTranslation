import pytest
import sqlite3
from time import perf_counter

from app.history.database import HistoryDatabase


def create_session(
    database: HistoryDatabase,
    title: str = "Test conversation",
) -> int:
    return database.create_session(
        title=title,
        audio_source="computer_audio",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
    )


def test_session_and_messages_are_saved_and_listed() -> None:
    database = HistoryDatabase(":memory:")
    session_id = database.create_session(
        title="Test conversation",
        audio_source="computer_audio",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
    )
    database.add_message(
        session_id=session_id,
        speaker_label="Computer audio",
        start_offset_seconds=1.0,
        end_offset_seconds=2.5,
        source_language="fr",
        language_confidence=0.95,
        original_text="Bonjour",
        target_language="en",
        translated_text="Hello",
    )
    database.finish_session(session_id)

    sessions = database.list_sessions()
    messages = database.get_messages(session_id)

    assert len(sessions) == 1
    assert sessions[0].status == "completed"
    assert sessions[0].preview == "Bonjour"
    assert sessions[0].message_count == 1
    assert len(messages) == 1
    assert messages[0].original_text == "Bonjour"
    assert messages[0].translated_text == "Hello"
    assert messages[0].speaker_label == "Computer audio"


def test_interrupted_session_is_recovered_as_incomplete() -> None:
    database = HistoryDatabase(":memory:")
    session_id = database.create_session(
        title="Interrupted",
        audio_source="microphone",
        language_mode="en",
        preferred_source_language="en",
        target_language="fr",
    )

    assert database.recover_interrupted_sessions() == 1

    session = database.get_session(session_id)
    assert session is not None
    assert session.status == "incomplete"
    assert session.ended_at is not None


def test_pause_and_resume_keep_one_open_session_and_record_events() -> None:
    database = HistoryDatabase(":memory:")
    session_id = database.create_session(
        title="One session",
        audio_source="microphone",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
    )

    database.pause_session(session_id)
    paused = database.get_session(session_id)
    assert paused is not None
    assert paused.status == "paused"
    assert paused.ended_at is None

    database.resume_session(session_id)
    resumed = database.get_session(session_id)
    assert resumed is not None
    assert resumed.status == "live"
    assert resumed.ended_at is None

    events = database._connection.execute(
        "SELECT event_type FROM session_events WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    assert [str(event["event_type"]) for event in events] == [
        "start",
        "pause",
        "resume",
    ]


def test_pause_and_resume_reject_duplicate_state_changes() -> None:
    database = HistoryDatabase(":memory:")
    session_id = database.create_session(
        title="One session",
        audio_source="microphone",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
    )

    with pytest.raises(ValueError, match="not paused"):
        database.resume_session(session_id)

    database.pause_session(session_id)
    with pytest.raises(ValueError, match="not live"):
        database.pause_session(session_id)


def test_schema_contains_future_person_and_voice_tables() -> None:
    database = HistoryDatabase(":memory:")
    rows = database._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    table_names = {str(row["name"]) for row in rows}

    assert {
        "persons",
        "person_voice_signatures",
        "sessions",
        "session_events",
        "session_persons",
        "session_voice_signatures",
        "messages",
    } <= table_names


def test_search_finds_titles_original_text_and_translations() -> None:
    database = HistoryDatabase(":memory:")
    lesson_id = create_session(database, "Tuesday lesson")
    game_id = create_session(database, "Game dialogue")
    database.add_message(
        session_id=lesson_id,
        speaker_label="Speaker",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="fr",
        language_confidence=0.9,
        original_text="Bonjour professeur",
        target_language="en",
        translated_text="Hello teacher",
    )
    database.add_message(
        session_id=game_id,
        speaker_label="Computer audio",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="en",
        language_confidence=0.9,
        original_text="Open the ancient gate",
        target_language="fr",
        translated_text="Ouvrez la porte ancienne",
    )

    assert [item.id for item in database.list_sessions("Tuesday")] == [lesson_id]
    assert [item.id for item in database.list_sessions("prof")] == [lesson_id]
    assert [item.id for item in database.list_sessions("teacher")] == [lesson_id]
    assert [item.id for item in database.list_sessions("ancienne")] == [game_id]
    assert database.list_sessions("missing phrase") == []


def test_rename_and_delete_rules_preserve_active_sessions() -> None:
    database = HistoryDatabase(":memory:")
    closed_id = create_session(database, "Old title")
    database.finish_session(closed_id)
    live_id = create_session(database, "Still live")

    database.rename_session(closed_id, "  French   class  ")
    assert database.get_session(closed_id).title == "French class"
    with pytest.raises(ValueError, match="cannot be empty"):
        database.rename_session(closed_id, "   ")
    with pytest.raises(ValueError, match="completed or incomplete"):
        database.delete_session(live_id)

    assert database.delete_all_closed_sessions() == 1
    assert database.get_session(closed_id) is None
    assert database.get_session(live_id) is not None


def test_delete_session_cascades_session_data_but_keeps_saved_person() -> None:
    database = HistoryDatabase(":memory:")
    session_id = create_session(database)
    now = "2026-10-25T08:00:00+00:00"
    with database._connection:
        person_id = database._connection.execute(
            """
            INSERT INTO persons(name, created_at, updated_at)
            VALUES ('Alice', ?, ?)
            """,
            (now, now),
        ).lastrowid
        session_person_id = database._connection.execute(
            """
            INSERT INTO session_persons(session_id, person_id, session_label)
            VALUES (?, ?, 'Person A')
            """,
            (session_id, person_id),
        ).lastrowid
        database._connection.execute(
            """
            INSERT INTO session_voice_signatures(
                session_person_id, embedding, model_name, model_version,
                dimensions, created_at
            ) VALUES (?, X'0102', 'test', '1', 2, ?)
            """,
            (session_person_id, now),
        )
        database._connection.execute(
            """
            INSERT INTO person_voice_signatures(
                person_id, embedding, model_name, model_version, dimensions,
                created_at, source_session_person_id
            ) VALUES (?, X'0304', 'test', '1', 2, ?, ?)
            """,
            (person_id, now, session_person_id),
        )
    database.add_message(
        session_id=session_id,
        speaker_label="Person A",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="en",
        language_confidence=0.9,
        original_text="Remember this",
        target_language="fr",
        translated_text="Souvenez-vous de ceci",
    )
    database.finish_session(session_id)

    database.delete_session(session_id)

    for table in (
        "sessions",
        "session_events",
        "messages",
        "session_persons",
        "session_voice_signatures",
    ):
        assert database._connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0] == 0
    signature = database._connection.execute(
        "SELECT source_session_person_id FROM person_voice_signatures"
    ).fetchone()
    assert signature is not None
    assert signature[0] is None
    assert database._connection.execute(
        "SELECT COUNT(*) FROM persons"
    ).fetchone()[0] == 1


def test_backup_can_be_opened_as_a_complete_recovery_copy(tmp_path) -> None:
    database_path = tmp_path / "history.sqlite3"
    backup_path = tmp_path / "history-backup.sqlite3"
    database = HistoryDatabase(database_path)
    session_id = create_session(database, "Backup conversation")
    database.add_message(
        session_id=session_id,
        speaker_label="Speaker",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="en",
        language_confidence=0.9,
        original_text="Stored safely",
        target_language="fr",
        translated_text="Stocké en sécurité",
    )
    database.finish_session(session_id)

    database.backup_to(backup_path)
    database.delete_session(session_id)
    database.close()

    recovered = HistoryDatabase(backup_path)
    assert recovered.startup_integrity == ("ok",)
    assert recovered.get_session(session_id).title == "Backup conversation"
    assert recovered.get_messages(session_id)[0].original_text == "Stored safely"
    recovered.close()


def test_version_one_database_is_backed_up_and_migrated(tmp_path) -> None:
    database_path = tmp_path / "history.sqlite3"
    database = HistoryDatabase(database_path)
    session_id = create_session(database, "Needs migration")
    database.add_message(
        session_id=session_id,
        speaker_label="Speaker",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="en",
        language_confidence=0.9,
        original_text="Migration searchable",
        target_language="fr",
        translated_text="Migration consultable",
    )
    database.close()

    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        UPDATE schema_meta SET value = '1' WHERE key = 'schema_version';
        DROP TRIGGER messages_fts_after_insert;
        DROP TRIGGER messages_fts_after_delete;
        DROP TRIGGER messages_fts_after_update;
        DROP TABLE messages_fts;
        """
    )
    connection.close()

    migrated = HistoryDatabase(database_path)
    version = migrated._connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    assert version == "3"
    assert migrated.list_sessions("searchable")[0].id == session_id
    assert list(tmp_path.glob("history.sqlite3.pre-v2.backup*"))
    migrated.close()


def test_large_history_search_and_maintenance_remain_practical(tmp_path) -> None:
    database = HistoryDatabase(tmp_path / "large.sqlite3")
    session_id = create_session(database, "Large conversation")
    now = "2026-10-25T08:00:00+00:00"
    rows = [
        (
            session_id,
            "Speaker",
            float(index),
            float(index + 1),
            now,
            "en",
            0.9,
            f"Ordinary message {index}",
            "fr",
            "Aiguille spéciale" if index == 1_999 else f"Message {index}",
        )
        for index in range(2_000)
    ]
    with database._connection:
        database._connection.executemany(
            """
            INSERT INTO messages(
                session_id, speaker_label, start_offset_seconds,
                end_offset_seconds, created_at, source_language,
                language_confidence, original_text, target_language,
                translated_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    started = perf_counter()
    results = database.list_sessions("aiguille")
    elapsed = perf_counter() - started

    assert results[0].id == session_id
    assert results[0].message_count == 2_000
    assert elapsed < 2.0
    report = database.maintain()
    assert report.integrity_ok
    assert report.integrity_details == ("ok",)
    database.close()


def test_damaged_database_is_not_silently_replaced(tmp_path) -> None:
    database_path = tmp_path / "damaged.sqlite3"
    original_contents = b"This is not a SQLite database."
    database_path.write_bytes(original_contents)

    with pytest.raises(sqlite3.DatabaseError):
        HistoryDatabase(database_path)

    assert database_path.read_bytes() == original_contents


def test_remembered_person_copies_approved_signature_and_renames_messages() -> None:
    database = HistoryDatabase(":memory:")
    session_id = create_session(database, "Speaker memory")
    session_person_id = database.upsert_session_person(
        session_id=session_id,
        session_label="Person A",
        person_id=None,
        confidence=0.91,
    )
    embedding = b"\x00\x00\x80?\x00\x00\x00\x00"
    database.add_session_voice_signature(
        session_person_id=session_person_id,
        embedding=embedding,
        model_name="test-model",
        model_version="1",
        dimensions=2,
        quality_score=0.91,
    )
    database.add_message(
        session_id=session_id,
        session_person_id=session_person_id,
        speaker_label="Person A",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="en",
        language_confidence=0.9,
        original_text="Remember me",
        target_language="fr",
        translated_text="Souviens-toi de moi",
    )

    person_id = database.remember_session_person(session_person_id, "  Alice  ")
    profiles = database.list_person_profiles()

    assert profiles[0].id == person_id
    assert profiles[0].name == "Alice"
    assert profiles[0].signatures[0].embedding == embedding
    assert database.get_messages(session_id)[0].speaker_label == "Alice"
    assert database.get_session_person(session_person_id).person_id == person_id
    session_profiles = database.list_session_speaker_profiles(session_id)
    assert session_profiles[0].session_label == "Person A"
    assert session_profiles[0].display_name == "Alice"
    assert session_profiles[0].signatures[0].embedding == embedding

    # Repeating the action renames the profile without copying duplicates.
    assert database.remember_session_person(session_person_id, "Alicia") == person_id
    assert database.list_person_profiles()[0].name == "Alicia"
    count = database._connection.execute(
        "SELECT COUNT(*) FROM person_voice_signatures WHERE person_id = ?",
        (person_id,),
    ).fetchone()[0]
    assert count == 1

    second_embedding = b"\x00\x00\x00\x00\x00\x00\x80?"
    database.add_session_voice_signature(
        session_person_id=session_person_id,
        embedding=second_embedding,
        model_name="test-model",
        model_version="1",
        dimensions=2,
        quality_score=0.95,
    )
    refreshed = database.list_person_profiles()[0]
    assert {item.embedding for item in refreshed.signatures} == {
        embedding,
        second_embedding,
    }

