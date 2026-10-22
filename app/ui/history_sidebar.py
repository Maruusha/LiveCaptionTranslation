"""Left-side list of live and saved caption conversations."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from app.history.database import HistorySession


LIVE_ITEM_KEY = "live"


class ConversationSidebar(QWidget):
    """Compact conversation list similar to a normal chat application."""

    live_selected = Signal()
    session_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("conversationSidebar")
        self.setMinimumWidth(230)
        self.setMaximumWidth(320)
        self._loading = False

        heading = QLabel("Conversations")
        heading.setObjectName("conversationSidebarHeading")
        heading_font = heading.font()
        heading_font.setPointSize(14)
        heading_font.setBold(True)
        heading.setFont(heading_font)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("conversationList")
        self.list_widget.setWordWrap(True)
        self.list_widget.setSpacing(3)
        self.list_widget.setStyleSheet(
            "QListWidget#conversationList {"
            "background: #ffffff; border: 1px solid #d6d1ca; border-radius: 8px;"
            "}"
            "QListWidget#conversationList::item {"
            "padding: 10px; border-bottom: 1px solid #eeeeee;"
            "}"
            "QListWidget#conversationList::item:selected {"
            "background: #d9fdd3; color: #111111;"
            "}"
        )
        self.list_widget.currentItemChanged.connect(self._selection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(self.list_widget, stretch=1)

    def set_sessions(
        self,
        sessions: list[HistorySession],
        *,
        show_live: bool,
        selected_key: str | int | None = None,
    ) -> None:
        self._loading = True
        self.list_widget.clear()
        if show_live:
            live_item = QListWidgetItem("Live conversation\nListening now")
            live_item.setData(Qt.ItemDataRole.UserRole, LIVE_ITEM_KEY)
            self.list_widget.addItem(live_item)

        for session in sessions:
            status = session.status.capitalize()
            preview = " ".join(session.preview.split())
            if len(preview) > 46:
                preview = f"{preview[:43]}…"
            text = (
                f"{session.title}    {self._display_time(session.started_at)}\n"
                f"{preview}  ·  {status}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, session.id)
            item.setToolTip(session.title)
            self.list_widget.addItem(item)

        self._select_key(selected_key)
        self._loading = False

    def selected_key(self) -> str | int | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select_live(self) -> None:
        self._select_key(LIVE_ITEM_KEY)

    def select_session(self, session_id: int) -> None:
        self._select_key(session_id)

    def _select_key(self, key: str | int | None) -> None:
        if key is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
            return
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.list_widget.setCurrentItem(item)
                return

    def _selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if self._loading or current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if key == LIVE_ITEM_KEY:
            self.live_selected.emit()
        elif isinstance(key, int):
            self.session_selected.emit(key)

    @staticmethod
    def _display_time(timestamp: str) -> str:
        try:
            value = datetime.fromisoformat(timestamp)
        except ValueError:
            return timestamp
        now = datetime.now().astimezone()
        if value.astimezone().date() == now.date():
            return value.astimezone().strftime("%H:%M")
        return value.astimezone().strftime("%d %b")

