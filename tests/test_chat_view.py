import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.main import create_application
from app.ui.chat_view import CaptionChatView


def test_chat_view_adds_timestamped_bilingual_bubble() -> None:
    create_application(["live-caption-translation-test"])
    chat = CaptionChatView()

    bubble = chat.add_message(
        speaker="Computer audio",
        original_text="Bonjour le monde",
        source_language="fr",
        translated_text="Hello world",
        target_language="en",
        timestamp="14:32",
    )

    assert len(chat.messages) == 1
    assert bubble.speaker_label.text() == "Computer audio"
    assert bubble.time_label.text() == "14:32"
    assert bubble.message_label.text() == "FR: Bonjour le monde\nEN: Hello world"
    assert chat._placeholder.isHidden()


def test_chat_view_updates_existing_bubbles_for_display_mode() -> None:
    create_application(["live-caption-translation-test"])
    chat = CaptionChatView()
    bubble = chat.add_message(
        original_text="Hello",
        source_language="en",
        translated_text="Bonjour",
        target_language="fr",
        timestamp="10:00",
    )

    chat.set_display_mode("translation")
    assert bubble.message_label.text() == "FR: Bonjour"

    chat.set_display_mode("original")
    assert bubble.message_label.text() == "EN: Hello"


def test_translation_mode_keeps_unsupported_language_original_visible() -> None:
    create_application(["live-caption-translation-test"])
    chat = CaptionChatView()
    bubble = chat.add_message(
        original_text="안녕하세요",
        source_language="ko",
        translated_text=None,
        target_language=None,
        timestamp="10:00",
    )

    chat.set_display_mode("translation")

    assert bubble.message_label.text() == "KO: 안녕하세요"


def test_clear_chat_removes_bubbles_and_restores_placeholder() -> None:
    create_application(["live-caption-translation-test"])
    chat = CaptionChatView()
    chat.add_message(
        original_text="Hello",
        source_language="en",
        timestamp="10:00",
    )

    chat.clear_messages()

    assert chat.messages == ()
    assert not chat._placeholder.isHidden()


def test_chat_view_shows_saved_session_event_divider() -> None:
    create_application(["live-caption-translation-test"])
    chat = CaptionChatView()

    event = chat.add_event("Paused", "10:05")

    assert event.text() == "Paused  ·  10:05"
    assert event.objectName() == "conversationEvent"
    assert chat._placeholder.isHidden()


def test_scroll_to_bottom_moves_to_newest_message() -> None:
    application = create_application(["live-caption-translation-test"])
    chat = CaptionChatView()
    chat.resize(360, 180)
    chat.show()
    for index in range(20):
        chat.add_message(
            original_text=f"Message {index}",
            source_language="en",
            timestamp="10:00",
        )
    chat._layout.activate()
    chat.widget().adjustSize()
    application.processEvents()

    scrollbar = chat.verticalScrollBar()
    scrollbar.setValue(0)
    assert scrollbar.value() < scrollbar.maximum()

    chat.scroll_to_bottom()

    assert scrollbar.value() == scrollbar.maximum()
    chat.close()


def test_history_reload_does_not_apply_stale_scroll_after_switching() -> None:
    application = create_application(["live-caption-translation-test"])
    chat = CaptionChatView()
    chat.resize(360, 240)
    chat.show()
    chat.begin_reload()
    for index in range(30):
        chat.add_message(original_text=f"Long history {index}", source_language="en")
    chat.finish_reload(scroll_position=500)
    # A second conversation is selected before the old deferred layout runs.
    chat.begin_reload()
    chat.add_message(original_text="Other conversation", source_language="en")
    chat.finish_reload(scroll_position=0)
    application.processEvents()
    application.processEvents()
    assert len(chat.messages) == 1
    assert chat.verticalScrollBar().value() == 0
    chat.close()

