"""Compact always-on-top window for the newest live caption."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.ui.chat_view import format_caption_text


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
        self._drag_offset: QPoint | None = None

        panel = QFrame()
        panel.setObjectName("captionOverlayPanel")
        panel.setStyleSheet(
            "QFrame#captionOverlayPanel {"
            "background-color: rgba(20, 24, 28, 225);"
            "border: 1px solid rgba(255, 255, 255, 80);"
            "border-radius: 12px;"
            "}"
        )

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

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 12, 16, 14)
        panel_layout.setSpacing(7)
        panel_layout.addWidget(self.header_label)
        panel_layout.addWidget(self.caption_label, stretch=1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(panel)

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
        self.header_label.setText("Live captions · drag to move")
        self.caption_label.setText("Waiting for a live caption…")
        self.caption_label.setStyleSheet("color: #ffffff;")

    def _render_message(self) -> None:
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
        colour = "#ffd166" if bool(message["warning"]) else "#ffffff"
        self.caption_label.setStyleSheet(f"color: {colour};")

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
