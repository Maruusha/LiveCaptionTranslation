import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.history.database import HistorySession
from app.main import create_application
from app.ui.history_sidebar import ConversationSidebar, LIVE_ITEM_KEY


def saved_session(session_id: int = 7) -> HistorySession:
    return HistorySession(
        id=session_id,
        title="French lesson",
        started_at="2026-10-22T14:32:00+02:00",
        ended_at="2026-10-22T14:40:00+02:00",
        status="completed",
        audio_source="computer_audio",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
        preview="Bonjour, comment allez-vous ?",
        message_count=3,
    )


def test_sidebar_shows_live_and_saved_sessions() -> None:
    create_application(["live-caption-translation-test"])
    sidebar = ConversationSidebar()

    sidebar.set_sessions([saved_session()], show_live=True, selected_key=LIVE_ITEM_KEY)

    assert sidebar.list_widget.count() == 2
    assert sidebar.selected_key() == LIVE_ITEM_KEY
    assert "Live conversation" in sidebar.list_widget.item(0).text()
    assert "French lesson" in sidebar.list_widget.item(1).text()
    assert "Completed" in sidebar.list_widget.item(1).text()


def test_sidebar_emits_selected_session_id() -> None:
    create_application(["live-caption-translation-test"])
    sidebar = ConversationSidebar()
    selected: list[int] = []
    sidebar.session_selected.connect(selected.append)
    sidebar.set_sessions(
        [saved_session(), saved_session(session_id=8)],
        show_live=False,
    )

    sidebar.select_session(8)

    assert selected == [8]

