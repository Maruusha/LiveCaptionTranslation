"""Tests for the compact always-on-top caption window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from app.main import create_application
from app.ui.caption_overlay import CaptionOverlay


def test_overlay_formats_latest_caption_and_display_modes() -> None:
    create_application(["live-caption-translation-test"])
    overlay = CaptionOverlay()

    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert overlay.caption_label.text() == "Waiting for a live caption…"

    overlay.set_message(
        speaker="Computer audio",
        original_text="Bonjour le monde",
        source_language="fr",
        translated_text="Hello world",
        target_language="en",
        timestamp="14:32",
    )

    assert overlay.header_label.text() == "Computer audio · 14:32"
    assert overlay.caption_label.text() == (
        "FR: Bonjour le monde\nEN: Hello world"
    )

    overlay.set_display_mode("translation")
    assert overlay.caption_label.text() == "EN: Hello world"

    overlay.clear_message()
    assert overlay.caption_label.text() == "Waiting for a live caption…"
    overlay.close()
