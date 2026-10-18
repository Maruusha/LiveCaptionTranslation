"""Main desktop window for the application shell."""

from collections.abc import Callable

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.audio.devices import (
    AudioDevice,
    DeviceDiscoveryError,
    list_input_devices,
    refresh_input_devices,
)
from app.audio.microphone import MicrophoneCaptureError, MicrophoneLevelMonitor


MonitorFactory = Callable[
    [int | tuple[int, ...], Callable[[float], None], Callable[[str], None]],
    MicrophoneLevelMonitor,
]


class MainWindow(QMainWindow):
    """Application shell with local microphone discovery and level monitoring."""

    microphone_level_received = Signal(float)
    microphone_warning_received = Signal(str)

    def __init__(
        self,
        device_provider: Callable[[], list[AudioDevice]] | None = None,
        monitor_factory: MonitorFactory | None = None,
    ) -> None:
        super().__init__()
        self._device_provider = device_provider or list_input_devices
        self._refresh_device_provider = device_provider or refresh_input_devices
        self._monitor_factory = monitor_factory or MicrophoneLevelMonitor
        self._microphone_monitor: MicrophoneLevelMonitor | None = None

        self.microphone_level_received.connect(self._set_microphone_level)
        self.microphone_warning_received.connect(self._show_microphone_warning)

        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(640, 400)

        title = QLabel(APP_NAME)
        title.setObjectName("applicationTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_font = QFont(title.font())
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)

        message = QLabel("Choose the microphone that will provide live speech.")
        message.setObjectName("welcomeMessage")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.microphone_selector = QComboBox()
        self.microphone_selector.setObjectName("microphoneSelector")
        self.microphone_selector.setMinimumWidth(360)

        self.refresh_devices_button = QPushButton("Refresh")
        self.refresh_devices_button.setObjectName("refreshMicrophonesButton")
        self.refresh_devices_button.clicked.connect(self.refresh_connected_microphones)

        selector_layout = QHBoxLayout()
        selector_layout.addWidget(self.microphone_selector, stretch=1)
        selector_layout.addWidget(self.refresh_devices_button)

        self.device_status = QLabel()
        self.device_status.setObjectName("microphoneStatus")
        self.device_status.setWordWrap(True)

        self.input_level = QProgressBar()
        self.input_level.setObjectName("microphoneInputLevel")
        self.input_level.setRange(0, 100)
        self.input_level.setValue(0)
        self.input_level.setFormat("Input level: %p%")

        self.start_stop_button = QPushButton("Start microphone")
        self.start_stop_button.setObjectName("startStopMicrophoneButton")
        self.start_stop_button.clicked.connect(self.toggle_microphone)

        microphone_group = QGroupBox("Microphone")
        microphone_layout = QVBoxLayout()
        microphone_layout.addLayout(selector_layout)
        microphone_layout.addWidget(self.device_status)
        microphone_layout.addWidget(self.input_level)
        microphone_layout.addWidget(self.start_stop_button)
        microphone_group.setLayout(microphone_layout)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addSpacing(16)
        layout.addWidget(microphone_group)
        layout.addStretch()

        content = QWidget()
        content.setLayout(layout)
        self.setCentralWidget(content)

        self.refresh_microphones()

    def refresh_microphones(
        self,
        provider: Callable[[], list[AudioDevice]] | None = None,
        refreshed: bool = False,
    ) -> None:
        """Reload microphones and keep audio-driver errors inside the UI."""
        self.microphone_selector.clear()

        try:
            devices = (provider or self._device_provider)()
        except DeviceDiscoveryError as error:
            self.microphone_selector.addItem("Microphone check failed")
            self.microphone_selector.setEnabled(False)
            self.start_stop_button.setEnabled(False)
            self.device_status.setText(str(error))
            self.statusBar().showMessage("Microphone discovery failed")
            return

        if not devices:
            self.microphone_selector.addItem("No microphones found")
            self.microphone_selector.setEnabled(False)
            self.start_stop_button.setEnabled(False)
            self.device_status.setText(
                "Connect a microphone, check Windows privacy settings, then refresh."
            )
            self.statusBar().showMessage("No microphone found")
            return

        for device in devices:
            self.microphone_selector.addItem(device.display_name, userData=device)

        self.microphone_selector.setEnabled(True)
        self.start_stop_button.setEnabled(True)
        noun = "microphone" if len(devices) == 1 else "microphones"
        if refreshed:
            refresh_time = QTime.currentTime().toString("HH:mm:ss")
            self.device_status.setText(
                f"Refreshed at {refresh_time}. Found {len(devices)} connected {noun}."
            )
            self.statusBar().showMessage("Microphone list refreshed")
        else:
            self.device_status.setText(f"Found {len(devices)} connected {noun}.")
            self.statusBar().showMessage("Ready")

    def refresh_connected_microphones(self) -> None:
        """Restart device discovery and rebuild the visible microphone list."""
        if self._microphone_monitor is not None:
            return

        self.refresh_devices_button.setEnabled(False)
        self.refresh_devices_button.setText("Refreshing…")
        self.statusBar().showMessage("Refreshing microphone list…")
        try:
            self.refresh_microphones(
                provider=self._refresh_device_provider,
                refreshed=True,
            )
        finally:
            self.refresh_devices_button.setText("Refresh")
            self.refresh_devices_button.setEnabled(True)

    def toggle_microphone(self) -> None:
        """Start or stop level-only capture for the selected microphone."""
        if self._microphone_monitor is not None:
            self._stop_microphone()
            return

        selected_device = self.microphone_selector.currentData()
        if not isinstance(selected_device, AudioDevice):
            self.statusBar().showMessage("Select a microphone first")
            return

        monitor = self._monitor_factory(
            selected_device.input_indices,
            self.microphone_level_received.emit,
            self.microphone_warning_received.emit,
        )

        try:
            monitor.start()
        except MicrophoneCaptureError as error:
            self.input_level.setValue(0)
            self.device_status.setText(str(error))
            self.statusBar().showMessage("Microphone could not start")
            return

        self._microphone_monitor = monitor
        self.microphone_selector.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.start_stop_button.setText("Stop microphone")
        self.device_status.setText(
            f"Listening to {self.microphone_selector.currentText()}. Audio is not saved."
        )
        self.statusBar().showMessage("Microphone active")

    def _stop_microphone(self, show_ready: bool = True) -> None:
        monitor = self._microphone_monitor
        self._microphone_monitor = None

        if monitor is not None:
            try:
                monitor.stop()
            except MicrophoneCaptureError as error:
                self.device_status.setText(str(error))
                self.statusBar().showMessage("Microphone stop warning")
                show_ready = False

        self.input_level.setValue(0)
        self.microphone_selector.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        self.start_stop_button.setText("Start microphone")

        if show_ready:
            self.device_status.setText("Microphone stopped. Audio was not saved.")
            self.statusBar().showMessage("Ready")

    def _set_microphone_level(self, level: float) -> None:
        self.input_level.setValue(int(round(level)))

    def _show_microphone_warning(self, warning: str) -> None:
        self.statusBar().showMessage(f"Microphone warning: {warning}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Always release the audio device before the window closes."""
        self._stop_microphone(show_ready=False)
        super().closeEvent(event)


