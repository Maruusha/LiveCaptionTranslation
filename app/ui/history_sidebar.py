"""Left-side list of live and saved caption conversations."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.history.database import HistorySession


LIVE_ITEM_KEY = "live"


class ConversationSidebar(QWidget):
    """Compact conversation list similar to a normal chat application."""

    live_selected = Signal()
    session_selected = Signal(int)
    search_changed = Signal(str)
    rename_requested = Signal(int)
    delete_requested = Signal(int)
    delete_all_requested = Signal()
    maintenance_requested = Signal()

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

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("conversationSearch")
        self.search_edit.setPlaceholderText("Search conversations")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.search_changed.emit)

        self.database_warning = QLabel()
        self.database_warning.setObjectName("historyDatabaseWarning")
        self.database_warning.setWordWrap(True)
        self.database_warning.setStyleSheet(
            "color: #8a3b12; background: #fff3cd; border: 1px solid #f0c36d; "
            "border-radius: 6px; padding: 6px;"
        )
        self.database_warning.hide()

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
        self.list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.list_widget.customContextMenuRequested.connect(
            self._open_context_menu
        )

        self.rename_button = QPushButton("Rename")
        self.rename_button.setObjectName("renameConversationButton")
        self.rename_button.clicked.connect(self._request_rename)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("deleteConversationButton")
        self.delete_button.clicked.connect(self._request_delete)
        selected_actions = QHBoxLayout()
        selected_actions.setSpacing(6)
        selected_actions.addWidget(self.rename_button)
        selected_actions.addWidget(self.delete_button)

        self.delete_all_button = QPushButton("Delete all")
        self.delete_all_button.setObjectName("deleteAllHistoryButton")
        self.delete_all_button.clicked.connect(self.delete_all_requested.emit)
        self.maintenance_button = QPushButton("Maintain")
        self.maintenance_button.setObjectName("maintainHistoryButton")
        self.maintenance_button.setToolTip(
            "Check the history database and reclaim unused space."
        )
        self.maintenance_button.clicked.connect(self.maintenance_requested.emit)
        database_actions = QHBoxLayout()
        database_actions.setSpacing(6)
        database_actions.addWidget(self.delete_all_button)
        database_actions.addWidget(self.maintenance_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.database_warning)
        layout.addWidget(self.list_widget, stretch=1)
        layout.addLayout(selected_actions)
        layout.addLayout(database_actions)
        self._update_action_buttons()

    def set_database_warning(self, message: str | None) -> None:
        self.database_warning.setText(message or "")
        self.database_warning.setVisible(bool(message))

    def set_sessions(
        self,
        sessions: list[HistorySession],
        *,
        show_live: bool,
        live_status: str = "Listening now",
        selected_key: str | int | None = None,
        has_saved_sessions: bool | None = None,
    ) -> None:
        self._loading = True
        self.list_widget.clear()
        if show_live:
            live_item = QListWidgetItem(f"Live conversation\n{live_status}")
            live_item.setData(Qt.ItemDataRole.UserRole, LIVE_ITEM_KEY)
            self.list_widget.addItem(live_item)

        previous_group = ""
        for session in sessions:
            group = self._date_group(session.started_at)
            if group != previous_group:
                group_item = QListWidgetItem(group)
                group_item.setFlags(Qt.ItemFlag.NoItemFlags)
                group_font = group_item.font()
                group_font.setBold(True)
                group_item.setFont(group_font)
                self.list_widget.addItem(group_item)
                previous_group = group
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
        self.delete_all_button.setEnabled(
            bool(sessions)
            if has_saved_sessions is None
            else has_saved_sessions
        )
        self._update_action_buttons()

    def selected_key(self) -> str | int | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select_live(self) -> None:
        self._select_key(LIVE_ITEM_KEY)

    def select_session(self, session_id: int) -> None:
        self._select_key(session_id)

    def _select_key(self, key: str | int | None) -> None:
        if key is not None:
            for row in range(self.list_widget.count()):
                item = self.list_widget.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == key:
                    self.list_widget.setCurrentItem(item)
                    return
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) is not None:
                self.list_widget.setCurrentItem(item)
                return

    def _selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if self._loading or current is None:
            return
        self._update_action_buttons()
        key = current.data(Qt.ItemDataRole.UserRole)
        if key == LIVE_ITEM_KEY:
            self.live_selected.emit()
        elif isinstance(key, int):
            self.session_selected.emit(key)

    def _request_rename(self) -> None:
        key = self.selected_key()
        if isinstance(key, int):
            self.rename_requested.emit(key)

    def _request_delete(self) -> None:
        key = self.selected_key()
        if isinstance(key, int):
            self.delete_requested.emit(key)

    def _update_action_buttons(self) -> None:
        saved_session_selected = isinstance(self.selected_key(), int)
        self.rename_button.setEnabled(saved_session_selected)
        self.delete_button.setEnabled(saved_session_selected)

    def _open_context_menu(self, position: QPoint) -> None:
        item = self.list_widget.itemAt(position)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(key, int):
            return
        self.list_widget.setCurrentItem(item)
        menu = QMenu(self)
        rename_action = menu.addAction("Rename conversation")
        delete_action = menu.addAction("Delete conversation")
        chosen = menu.exec(self.list_widget.mapToGlobal(position))
        if chosen is rename_action:
            self.rename_requested.emit(key)
        elif chosen is delete_action:
            self.delete_requested.emit(key)

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

    @staticmethod
    def _date_group(timestamp: str) -> str:
        try:
            value = datetime.fromisoformat(timestamp).astimezone().date()
        except ValueError:
            return "Older"
        today = datetime.now().astimezone().date()
        days_ago = (today - value).days
        if days_ago <= 0:
            return "Today"
        if days_ago == 1:
            return "Yesterday"
        return "Older"

