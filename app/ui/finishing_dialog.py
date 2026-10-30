"""Responsive progress dialog used while accepted captions finish."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FinishingCaptionsDialog(QDialog):
    """Explain shutdown progress without blocking the Qt event loop."""

    finish_now_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        closing_application: bool = False,
        pausing_session: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("finishingCaptionsDialog")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self.message = QLabel()
        self.message.setObjectName("finishingCaptionsMessage")
        self.message.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setObjectName("finishingCaptionsProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)

        self.detail = QLabel()
        self.detail.setObjectName("finishingCaptionsDetail")
        self.detail.setWordWrap(True)

        buttons = QDialogButtonBox()
        self.hide_progress_button = QPushButton("Hide progress")
        self.hide_progress_button.setObjectName("hideFinishingProgressButton")
        self.hide_progress_button.setDefault(True)
        self.finish_now_button = QPushButton()
        self.finish_now_button.setObjectName("finishNowButton")
        buttons.addButton(
            self.hide_progress_button,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.addButton(
            self.finish_now_button,
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )

        self.hide_progress_button.clicked.connect(self._hide_progress)
        self.finish_now_button.clicked.connect(self.finish_now_requested.emit)

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.message)
        layout.addWidget(self.progress)
        layout.addWidget(self.detail)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.set_operation(
            closing_application=closing_application,
            pausing_session=pausing_session,
        )
        self.update_progress(remaining_captions=0, estimated_seconds=None)

    def set_closing_application(self, closing_application: bool) -> None:
        self.set_operation(
            closing_application=closing_application,
            pausing_session=False,
        )

    def set_operation(
        self,
        *,
        closing_application: bool,
        pausing_session: bool,
    ) -> None:
        if closing_application:
            self.setWindowTitle("Finishing captions before closing")
            self.message.setText(
                "Audio capture has stopped. Accepted captions are still being "
                "processed before the application closes."
            )
            self.finish_now_button.setText("Skip queued captions")
            self.finish_now_button.show()
        elif pausing_session:
            self.setWindowTitle("Pausing captions")
            self.message.setText(
                "Audio capture has paused. Captions accepted before the pause "
                "are still being processed."
            )
            self.finish_now_button.hide()
        else:
            self.setWindowTitle("Finishing captions")
            self.message.setText(
                "Audio capture has stopped. Accepted captions are still being "
                "processed."
            )
            self.finish_now_button.setText("Skip remaining")
            self.finish_now_button.show()

    def update_progress(
        self,
        *,
        remaining_captions: int,
        estimated_seconds: float | None,
    ) -> None:
        if remaining_captions <= 0:
            self.detail.setText("Releasing the audio device and finishing cleanup…")
            return

        noun = "caption" if remaining_captions == 1 else "captions"
        if estimated_seconds is None:
            estimate = "Estimating the remaining time…"
        else:
            rounded_seconds = max(1, math.ceil(estimated_seconds))
            if rounded_seconds < 60:
                estimate = f"Estimated time: about {rounded_seconds} seconds."
            else:
                rounded_minutes = max(1, math.ceil(rounded_seconds / 60))
                minute_noun = "minute" if rounded_minutes == 1 else "minutes"
                estimate = f"Estimated time: about {rounded_minutes} {minute_noun}."
        self.detail.setText(
            f"{remaining_captions} {noun} remaining. {estimate}"
        )

    def show_finishing_current_caption(self) -> None:
        self.detail.setText(
            "Queued captions were skipped. Finishing the caption already in "
            "progress…"
        )
        self.finish_now_button.setEnabled(False)

    def _hide_progress(self) -> None:
        """Hide this modal view while the owning window keeps processing."""
        self.hide()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing the progress view never cancels queued captions."""
        self._hide_progress()
        event.ignore()

