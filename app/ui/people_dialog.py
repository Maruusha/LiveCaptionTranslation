"""Local management controls for user-approved remembered people."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.history.database import HistoryDatabase, RememberedPersonSummary


class RememberedPeopleDialog(QDialog):
    """Rename or remove local voice profiles without touching conversations."""

    def __init__(
        self,
        database: HistoryDatabase,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._database = database
        self._people: dict[int, RememberedPersonSummary] = {}
        self.setWindowTitle("Remembered people")
        self.setMinimumSize(560, 360)

        explanation = QLabel(
            "Only people you explicitly remembered appear here. Voice data "
            "stays on this computer."
        )
        explanation.setWordWrap(True)

        self.people_list = QListWidget()
        self.people_list.setObjectName("rememberedPeopleList")
        self.people_list.currentItemChanged.connect(self._update_buttons)

        self.rename_button = QPushButton("Rename")
        self.rename_button.clicked.connect(self._rename_selected)
        self.toggle_button = QPushButton("Disable recognition")
        self.toggle_button.clicked.connect(self._toggle_selected)
        self.merge_button = QPushButton("Merge into…")
        self.merge_button.clicked.connect(self._merge_selected)
        self.forget_button = QPushButton("Forget voice")
        self.forget_button.clicked.connect(self._forget_selected)
        self.delete_button = QPushButton("Delete profile")
        self.delete_button.clicked.connect(self._delete_selected)

        action_row = QHBoxLayout()
        for button in (
            self.rename_button,
            self.toggle_button,
            self.merge_button,
            self.forget_button,
            self.delete_button,
        ):
            action_row.addWidget(button)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(self.people_list, stretch=1)
        layout.addLayout(action_row)
        layout.addWidget(close_buttons)
        self.refresh()

    def refresh(self, selected_id: int | None = None) -> None:
        people = self._database.list_people()
        self._people = {person.id: person for person in people}
        self.people_list.clear()
        for person in people:
            status = "enabled" if person.recognition_enabled else "disabled"
            signature_noun = (
                "signature" if person.signature_count == 1 else "signatures"
            )
            session_noun = "session" if person.session_count == 1 else "sessions"
            item = QListWidgetItem(
                f"{person.name}\n"
                f"Recognition {status} · {person.signature_count} "
                f"{signature_noun} · {person.session_count} {session_noun}"
            )
            item.setData(Qt.ItemDataRole.UserRole, person.id)
            self.people_list.addItem(item)
            if person.id == selected_id:
                self.people_list.setCurrentItem(item)
        if self.people_list.currentItem() is None and self.people_list.count():
            self.people_list.setCurrentRow(0)
        self._update_buttons()

    def _selected_person(self) -> RememberedPersonSummary | None:
        item = self.people_list.currentItem()
        if item is None:
            return None
        return self._people.get(item.data(Qt.ItemDataRole.UserRole))

    def _update_buttons(self, *_args) -> None:
        person = self._selected_person()
        available = person is not None
        self.rename_button.setEnabled(available)
        self.toggle_button.setEnabled(available and person.signature_count > 0)
        self.merge_button.setEnabled(available and len(self._people) > 1)
        self.forget_button.setEnabled(available and person.signature_count > 0)
        self.delete_button.setEnabled(available)
        if person is not None:
            self.toggle_button.setText(
                "Disable recognition"
                if person.recognition_enabled
                else "Enable recognition"
            )

    def _rename_selected(self) -> None:
        person = self._selected_person()
        if person is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Rename remembered person",
            "Name:",
            text=person.name,
        )
        if accepted and self._run_change(
            lambda: self._database.rename_person(person.id, name)
        ):
            self.refresh(person.id)

    def _toggle_selected(self) -> None:
        person = self._selected_person()
        if person is None:
            return
        if self._run_change(
            lambda: self._database.set_person_recognition_enabled(
                person.id,
                not person.recognition_enabled,
            )
        ):
            self.refresh(person.id)

    def _merge_selected(self) -> None:
        source = self._selected_person()
        if source is None:
            return
        choices = {
            f"{person.name} (profile {person.id})": person.id
            for person in self._people.values()
            if person.id != source.id
        }
        if not choices:
            return
        selected, accepted = QInputDialog.getItem(
            self,
            "Merge remembered people",
            f"Merge {source.name} into:",
            list(choices),
            editable=False,
        )
        if not accepted:
            return
        target_id = choices[selected]
        target_name = self._people[target_id].name
        choice = QMessageBox.question(
            self,
            "Confirm merge",
            f'Merge "{source.name}" into "{target_name}"?\n\n'
            "The duplicate profile will be removed. Conversations will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes and self._run_change(
            lambda: self._database.merge_people(source.id, target_id)
        ):
            self.refresh(target_id)

    def _forget_selected(self) -> None:
        person = self._selected_person()
        if person is None:
            return
        choice = QMessageBox.question(
            self,
            "Forget saved voice",
            f'Forget the saved voice for "{person.name}"?\n\n'
            "Conversation text remains, but future voices will no longer match "
            "this person.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes and self._run_change(
            lambda: self._database.forget_person_voice(person.id)
        ):
            self.refresh(person.id)

    def _delete_selected(self) -> None:
        person = self._selected_person()
        if person is None:
            return
        choice = QMessageBox.question(
            self,
            "Delete remembered person",
            f'Delete the profile "{person.name}"?\n\n'
            "Saved conversations and their original Person labels remain.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes and self._run_change(
            lambda: self._database.delete_person(person.id)
        ):
            self.refresh()

    def _run_change(self, operation: Callable[[], None]) -> bool:
        try:
            operation()
            return True
        except (sqlite3.Error, ValueError) as error:
            QMessageBox.warning(self, "Remembered people", str(error))
            return False
