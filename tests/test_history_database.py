from app.history.database import HistoryDatabase


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

