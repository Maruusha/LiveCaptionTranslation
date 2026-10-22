"""Chat-style presentation for completed live captions."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def format_caption_text(
    *,
    original_text: str,
    source_language: str | None,
    translated_text: str | None,
    target_language: str | None,
    mode: str,
) -> str:
    """Format one caption consistently for the chat and compact overlay."""
    if mode not in {"both", "original", "translation"}:
        raise ValueError(f"Unknown caption display mode: {mode}")
    source = (source_language or "unknown").upper()
    target = (target_language or "unknown").upper()
    original_line = f"{source}: {original_text}"
    translated_line = (
        f"{target}: {translated_text}"
        if translated_text
        else "Translation unavailable"
    )
    if mode == "original":
        return original_line
    if mode == "translation":
        return translated_line
    if translated_text:
        return f"{original_line}\n{translated_line}"
    return f"{original_line}\nTranslation unavailable"


class CaptionMessageBubble(QFrame):
    """One completed caption and its optional translation."""

    def __init__(
        self,
        *,
        speaker: str,
        original_text: str,
        source_language: str | None,
        translated_text: str | None,
        target_language: str | None,
        timestamp: str,
        warning: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.original_text = original_text
        self.source_language = source_language
        self.translated_text = translated_text
        self.target_language = target_language
        self.warning = warning

        self.setObjectName("captionMessageBubble")
        self.setMaximumWidth(720)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        background = "#fff3cd" if warning else "#ffffff"
        border = "#e0b84f" if warning else "#d8d8d8"
        self.setStyleSheet(
            "QFrame#captionMessageBubble {"
            f"background-color: {background};"
            f"border: 1px solid {border};"
            "border-radius: 10px;"
            "}"
        )

        self.speaker_label = QLabel(speaker)
        self.speaker_label.setObjectName("captionSpeaker")
        speaker_font = self.speaker_label.font()
        speaker_font.setBold(True)
        self.speaker_label.setFont(speaker_font)

        self.time_label = QLabel(timestamp)
        self.time_label.setObjectName("captionTime")
        self.time_label.setStyleSheet("color: #667781;")

        header = QHBoxLayout()
        header.addWidget(self.speaker_label)
        header.addStretch()
        header.addWidget(self.time_label)

        self.message_label = QLabel()
        self.message_label.setObjectName("captionMessageText")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 9)
        layout.setSpacing(5)
        layout.addLayout(header)
        layout.addWidget(self.message_label)
        self.set_display_mode("both")

    def set_display_mode(self, mode: str) -> None:
        self.message_label.setText(
            format_caption_text(
                original_text=self.original_text,
                source_language=self.source_language,
                translated_text=self.translated_text,
                target_language=self.target_language,
                mode=mode,
            )
        )


class CaptionChatView(QScrollArea):
    """Scrollable caption bubbles that remain still while reading older text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("captionChatView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(240)
        self.setStyleSheet(
            "QScrollArea#captionChatView {"
            "background-color: #efeae2;"
            "border: 1px solid #d6d1ca;"
            "border-radius: 8px;"
            "}"
        )

        self._display_mode = "both"
        self._bubbles: list[CaptionMessageBubble] = []
        self._rows: list[QWidget] = []
        self._content = QWidget()
        self._content.setObjectName("captionChatContent")
        self._content.setStyleSheet(
            "QWidget#captionChatContent { background-color: #efeae2; }"
        )
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(10)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._placeholder = QLabel("Completed captions will appear here.")
        self._placeholder.setObjectName("captionChatPlaceholder")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #667781; padding: 24px;")
        self._layout.addWidget(self._placeholder)
        self.setWidget(self._content)

    @property
    def messages(self) -> tuple[CaptionMessageBubble, ...]:
        return tuple(self._bubbles)

    def add_message(
        self,
        *,
        original_text: str,
        source_language: str | None,
        translated_text: str | None = None,
        target_language: str | None = None,
        speaker: str = "Speaker",
        timestamp: str | None = None,
        warning: bool = False,
    ) -> CaptionMessageBubble:
        scrollbar = self.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self._placeholder.hide()

        bubble = CaptionMessageBubble(
            speaker=speaker,
            original_text=original_text,
            source_language=source_language,
            translated_text=translated_text,
            target_language=target_language,
            timestamp=timestamp or datetime.now().strftime("%H:%M"),
            warning=warning,
        )
        bubble.set_display_mode(self._display_mode)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(bubble)
        row_layout.addStretch()
        self._layout.addWidget(row)
        self._rows.append(row)
        self._bubbles.append(bubble)

        if was_at_bottom:
            QTimer.singleShot(0, self.scroll_to_bottom)
        return bubble

    def set_display_mode(self, mode: str) -> None:
        if mode not in {"both", "original", "translation"}:
            raise ValueError(f"Unknown caption display mode: {mode}")
        self._display_mode = mode
        for bubble in self._bubbles:
            bubble.set_display_mode(mode)

    def clear_messages(self) -> None:
        for row in self._rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._bubbles.clear()
        self._placeholder.show()

    def scroll_to_bottom(self) -> None:
        """Move the conversation view to its newest message."""
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

