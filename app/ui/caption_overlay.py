"""Compact always-on-top window for the newest live caption."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from app.ui.chat_view import format_caption_text


@dataclass(frozen=True, slots=True)
class OverlayAppearance:
    """User-controlled overlay presentation values."""

    font_size: int = 13
    text_colour: str = "#ffffff"
    background_colour: str = "#14181c"
    opacity_percent: int = 88
    click_through: bool = False

    def normalized(self) -> "OverlayAppearance":
        """Return safe values even if a saved settings file was edited."""

        text_colour = QColor(self.text_colour)
        background_colour = QColor(self.background_colour)
        return OverlayAppearance(
            font_size=max(10, min(30, int(self.font_size))),
            text_colour=(
                text_colour.name() if text_colour.isValid() else "#ffffff"
            ),
            background_colour=(
                background_colour.name()
                if background_colour.isValid()
                else "#14181c"
            ),
            opacity_percent=max(30, min(100, int(self.opacity_percent))),
            click_through=bool(self.click_through),
        )


class CaptionOverlay(QWidget):
    """Movable overlay showing the newest completed live caption."""

    hidden_by_user = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("captionOverlay")
        self.setWindowTitle("Live caption overlay")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(420, 110)
        self.resize(720, 150)

        self._display_mode = "both"
        self._message: dict[str, str | bool | None] | None = None
        self._paused = False
        self._drag_offset: QPoint | None = None
        self._appearance = OverlayAppearance()

        self.panel = QFrame()
        self.panel.setObjectName("captionOverlayPanel")

        self.header_label = QLabel("Live captions · drag to move")
        self.header_label.setObjectName("captionOverlayHeader")
        self.header_label.setStyleSheet("color: #aebac1;")

        self.caption_label = QLabel("Waiting for a live caption…")
        self.caption_label.setObjectName("captionOverlayText")
        self.caption_label.setWordWrap(True)
        self.caption_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        caption_font = self.caption_label.font()
        caption_font.setPointSize(13)
        caption_font.setBold(True)
        self.caption_label.setFont(caption_font)
        self.caption_label.setStyleSheet("color: #ffffff;")

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(16, 12, 16, 14)
        panel_layout.setSpacing(7)
        panel_layout.addWidget(self.header_label)
        panel_layout.addWidget(self.caption_label, stretch=1)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch()
        self.size_grip = QSizeGrip(self.panel)
        self.size_grip.setObjectName("captionOverlaySizeGrip")
        grip_row.addWidget(self.size_grip)
        panel_layout.addLayout(grip_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.panel)
        self.apply_appearance(self._appearance)

    @property
    def appearance(self) -> OverlayAppearance:
        return self._appearance

    def apply_appearance(self, appearance: OverlayAppearance) -> None:
        """Apply validated appearance values to the live overlay."""

        self._appearance = appearance.normalized()
        colour = QColor(self._appearance.background_colour)
        alpha = round(255 * self._appearance.opacity_percent / 100)
        self.panel.setStyleSheet(
            "QFrame#captionOverlayPanel {"
            f"background-color: rgba({colour.red()}, {colour.green()}, "
            f"{colour.blue()}, {alpha});"
            "border: 1px solid rgba(255, 255, 255, 80);"
            "border-radius: 12px;"
            "}"
        )
        caption_font = self.caption_label.font()
        caption_font.setPointSize(self._appearance.font_size)
        caption_font.setBold(True)
        self.caption_label.setFont(caption_font)
        click_through_changed = (
            self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            != self._appearance.click_through
        )
        was_visible = self.isVisible()
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            self._appearance.click_through,
        )
        if click_through_changed:
            self.setWindowFlag(
                Qt.WindowType.WindowTransparentForInput,
                self._appearance.click_through,
            )
            if was_visible:
                self.show()
        self._render_colours()

    def ensure_visible_on_screen(self) -> None:
        """Move a restored overlay back if its old monitor is unavailable."""

        frame = self.frameGeometry()
        screens = QGuiApplication.screens()
        if any(frame.intersects(screen.availableGeometry()) for screen in screens):
            return
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        available = primary.availableGeometry()
        self.move(
            available.center().x() - self.width() // 2,
            available.bottom() - self.height() - 40,
        )

    def _render_colours(self) -> None:
        warning = bool(self._message and self._message["warning"])
        colour = "#ffd166" if warning else self._appearance.text_colour
        self.caption_label.setStyleSheet(f"color: {colour};")
        header_colour = QColor(self._appearance.text_colour)
        header_colour.setAlpha(180)
        self.header_label.setStyleSheet(
            f"color: {header_colour.name(QColor.NameFormat.HexArgb)};"
        )

    def set_message(
        self,
        *,
        speaker: str,
        original_text: str,
        source_language: str | None,
        translated_text: str | None = None,
        target_language: str | None = None,
        timestamp: str = "",
        warning: bool = False,
    ) -> None:
        self._message = {
            "speaker": speaker,
            "original_text": original_text,
            "source_language": source_language,
            "translated_text": translated_text,
            "target_language": target_language,
            "timestamp": timestamp,
            "warning": warning,
        }
        self._render_message()

    def set_display_mode(self, mode: str) -> None:
        if mode not in {"both", "original", "translation"}:
            raise ValueError(f"Unknown caption display mode: {mode}")
        self._display_mode = mode
        self._render_message()

    def clear_message(self) -> None:
        self._message = None
        self._paused = False
        self.header_label.setText("Live captions · drag to move")
        self.caption_label.setText("Waiting for a live caption…")
        self._render_colours()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self.header_label.setText("Paused")
            self.caption_label.setText("Audio capture is paused.")
            self._render_colours()
            return
        if self._message is None:
            self.header_label.setText("Live captions · drag to move")
            self.caption_label.setText("Waiting for a live caption…")
            self._render_colours()
            return
        self._render_message()

    def _render_message(self) -> None:
        if self._paused:
            return
        message = self._message
        if message is None:
            return
        speaker = str(message["speaker"])
        timestamp = str(message["timestamp"])
        self.header_label.setText(
            f"{speaker} · {timestamp}" if timestamp else speaker
        )
        self.caption_label.setText(
            format_caption_text(
                original_text=str(message["original_text"]),
                source_language=message["source_language"],
                translated_text=message["translated_text"],
                target_language=message["target_language"],
                mode=self._display_mode,
            )
        )
        self._render_colours()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.hidden_by_user.emit()
            event.accept()
            return
        super().keyPressEvent(event)
