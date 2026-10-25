"""Controls and persistence helpers for the caption overlay."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.caption_overlay import OverlayAppearance


class SettingsBackend(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...
    def setValue(self, key: str, value: Any) -> None: ...
    def sync(self) -> None: ...


class MemorySettings:
    """Small QSettings-compatible store used by automated tests."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def value(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def setValue(self, key: str, value: Any) -> None:
        self.values[key] = value

    def sync(self) -> None:
        pass


def create_interface_settings() -> SettingsBackend:
    """Create local interface settings, with an in-memory test mode."""

    configured_path = os.environ.get("LIVE_CAPTION_SETTINGS_PATH")
    if configured_path == ":memory:":
        return MemorySettings()
    if configured_path:
        path = Path(configured_path)
    else:
        config_dir = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppConfigLocation
            )
        )
        path = config_dir / "settings.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(path), QSettings.Format.IniFormat)


def _stored_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_overlay_appearance(settings: SettingsBackend) -> OverlayAppearance:
    """Read overlay values and repair invalid or old saved values."""

    try:
        font_size = int(settings.value("overlay/font_size", 13))
        opacity = int(settings.value("overlay/opacity_percent", 88))
    except (TypeError, ValueError):
        font_size, opacity = 13, 88
    return OverlayAppearance(
        font_size=font_size,
        text_colour=str(settings.value("overlay/text_colour", "#ffffff")),
        background_colour=str(
            settings.value("overlay/background_colour", "#14181c")
        ),
        opacity_percent=opacity,
        click_through=_stored_bool(
            settings.value("overlay/click_through", False)
        ),
    ).normalized()


def save_overlay_appearance(
    settings: SettingsBackend,
    appearance: OverlayAppearance,
) -> None:
    appearance = appearance.normalized()
    settings.setValue("overlay/font_size", appearance.font_size)
    settings.setValue("overlay/text_colour", appearance.text_colour)
    settings.setValue("overlay/background_colour", appearance.background_colour)
    settings.setValue("overlay/opacity_percent", appearance.opacity_percent)
    settings.setValue("overlay/click_through", appearance.click_through)


def load_overlay_geometry(settings: SettingsBackend) -> QByteArray | None:
    value = settings.value("overlay/geometry")
    if isinstance(value, QByteArray):
        return value
    if isinstance(value, bytes):
        return QByteArray(value)
    return None


def save_overlay_geometry(
    settings: SettingsBackend,
    geometry: QByteArray,
) -> None:
    settings.setValue("overlay/geometry", geometry)
    settings.sync()


class ColourButton(QPushButton):
    """Button that previews and selects one colour."""

    def __init__(self, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor(colour)
        self.clicked.connect(self._choose_colour)
        self._update_preview()

    @property
    def colour(self) -> str:
        return self._colour.name()

    def set_colour(self, colour: str) -> None:
        candidate = QColor(colour)
        if candidate.isValid():
            self._colour = candidate
            self._update_preview()

    def _choose_colour(self) -> None:
        selected = QColorDialog.getColor(self._colour, self, "Choose colour")
        if selected.isValid():
            self._colour = selected
            self._update_preview()

    def _update_preview(self) -> None:
        foreground = "#000000" if self._colour.lightness() > 145 else "#ffffff"
        self.setText(self._colour.name())
        self.setStyleSheet(
            f"background: {self._colour.name()}; color: {foreground};"
        )


class OverlaySettingsDialog(QDialog):
    """Simple controls for the settings useful during live captioning."""

    def __init__(
        self,
        appearance: OverlayAppearance,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Overlay settings")
        self.setModal(True)
        self.setMinimumWidth(390)

        self.font_size = QSpinBox()
        self.font_size.setObjectName("overlayFontSize")
        self.font_size.setRange(10, 30)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setObjectName("overlayOpacity")
        self.opacity.setRange(30, 100)
        self.opacity.setTickInterval(10)
        self.opacity.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.opacity_value = QLabel()
        self.opacity.valueChanged.connect(
            lambda value: self.opacity_value.setText(f"{value}%")
        )
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity, stretch=1)
        opacity_row.addWidget(self.opacity_value)

        self.text_colour = ColourButton(appearance.text_colour)
        self.text_colour.setObjectName("overlayTextColour")
        self.background_colour = ColourButton(appearance.background_colour)
        self.background_colour.setObjectName("overlayBackgroundColour")
        self.click_through = QCheckBox(
            "Let mouse clicks pass through the overlay"
        )
        self.click_through.setObjectName("overlayClickThrough")
        self.click_through.setToolTip(
            "Use the main window to hide or change the overlay while enabled."
        )

        form = QFormLayout()
        form.addRow("Caption font size:", self.font_size)
        form.addRow("Background opacity:", opacity_row)
        form.addRow("Text colour:", self.text_colour)
        form.addRow("Background colour:", self.background_colour)
        form.addRow("", self.click_through)

        self.reset_button = QPushButton("Reset defaults")
        self.reset_button.setObjectName("resetOverlaySettings")
        self.reset_button.clicked.connect(
            lambda: self.set_appearance(OverlayAppearance())
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self.reset_button)
        button_row.addStretch()
        button_row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(button_row)
        self.set_appearance(appearance)

    @property
    def appearance(self) -> OverlayAppearance:
        return OverlayAppearance(
            font_size=self.font_size.value(),
            text_colour=self.text_colour.colour,
            background_colour=self.background_colour.colour,
            opacity_percent=self.opacity.value(),
            click_through=self.click_through.isChecked(),
        )

    def set_appearance(self, appearance: OverlayAppearance) -> None:
        appearance = appearance.normalized()
        self.font_size.setValue(appearance.font_size)
        self.opacity.setValue(appearance.opacity_percent)
        self.text_colour.set_colour(appearance.text_colour)
        self.background_colour.set_colour(appearance.background_colour)
        self.click_through.setChecked(appearance.click_through)
