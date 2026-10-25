"""Tests for the compact always-on-top caption window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from app.main import create_application
from app.ui.caption_overlay import CaptionOverlay, OverlayAppearance
from app.ui.overlay_settings import (
    MemorySettings,
    OverlaySettingsDialog,
    load_overlay_appearance,
    save_overlay_appearance,
)


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


def test_overlay_applies_safe_appearance_and_click_through() -> None:
    create_application(["live-caption-translation-test"])
    overlay = CaptionOverlay()

    overlay.apply_appearance(
        OverlayAppearance(
            font_size=22,
            text_colour="#33cc99",
            background_colour="#102030",
            opacity_percent=50,
            click_through=True,
        )
    )

    assert overlay.caption_label.font().pointSize() == 22
    assert "rgba(16, 32, 48, 128)" in overlay.panel.styleSheet()
    assert "#33cc99" in overlay.caption_label.styleSheet()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.size_grip is not None
    overlay.close()


def test_overlay_settings_round_trip_and_repair_invalid_values() -> None:
    settings = MemorySettings()
    save_overlay_appearance(
        settings,
        OverlayAppearance(
            font_size=18,
            text_colour="#abcdef",
            background_colour="#123456",
            opacity_percent=72,
            click_through=True,
        ),
    )

    assert load_overlay_appearance(settings) == OverlayAppearance(
        font_size=18,
        text_colour="#abcdef",
        background_colour="#123456",
        opacity_percent=72,
        click_through=True,
    )

    settings.setValue("overlay/font_size", 200)
    settings.setValue("overlay/opacity_percent", -4)
    settings.setValue("overlay/text_colour", "not-a-colour")
    repaired = load_overlay_appearance(settings)
    assert repaired.font_size == 30
    assert repaired.opacity_percent == 30
    assert repaired.text_colour == "#ffffff"


def test_overlay_settings_dialog_can_reset_defaults() -> None:
    create_application(["live-caption-translation-test"])
    dialog = OverlaySettingsDialog(
        OverlayAppearance(font_size=21, opacity_percent=55, click_through=True)
    )

    dialog.reset_button.click()

    assert dialog.appearance == OverlayAppearance()
    dialog.close()
