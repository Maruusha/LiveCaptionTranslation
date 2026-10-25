from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from app.history.database import HistoryDatabase
from app.main import create_application
from app.ui.people_dialog import RememberedPeopleDialog


def remembered_database() -> tuple[HistoryDatabase, int]:
    database = HistoryDatabase(":memory:")
    session_id = database.create_session(
        title="People",
        audio_source="microphone",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
    )
    session_person_id = database.upsert_session_person(
        session_id=session_id,
        session_label="Person A",
        person_id=None,
        confidence=0.9,
    )
    database.add_session_voice_signature(
        session_person_id=session_person_id,
        embedding=b"\x00" * 8,
        model_name="test",
        model_version="1",
        dimensions=2,
        quality_score=0.9,
    )
    person_id = database.remember_session_person(session_person_id, "Alice")
    return database, person_id


def test_people_dialog_renames_toggles_and_forgets_profile() -> None:
    create_application(["live-caption-translation-test"])
    database, person_id = remembered_database()
    dialog = RememberedPeopleDialog(database)

    assert dialog.people_list.count() == 1
    assert "Alice" in dialog.people_list.item(0).text()
    assert "1 signature" in dialog.people_list.item(0).text()

    with patch(
        "app.ui.people_dialog.QInputDialog.getText",
        return_value=("Alicia", True),
    ):
        dialog.rename_button.click()
    assert database.list_people()[0].name == "Alicia"

    dialog.toggle_button.click()
    assert not database.list_people()[0].recognition_enabled
    dialog.toggle_button.click()
    assert database.list_people()[0].recognition_enabled

    with patch(
        "app.ui.people_dialog.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        dialog.forget_button.click()
    summary = database.list_people()[0]
    assert summary.id == person_id
    assert summary.signature_count == 0
    assert not summary.recognition_enabled
    dialog.close()
