"""Main desktop window for the application shell."""

from collections.abc import Callable
import logging
from threading import Thread

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QStandardItemModel
from PySide6.QtMultimedia import QMediaDevices
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
from app.core.performance import PerformanceReport, format_mebibytes
from app.speech.recognition import RecognitionResult
from app.speech.vad import SpeechEvent, SpeechSegment, VoiceActivityStats
from app.ui.finishing_dialog import FinishingCaptionsDialog


MonitorFactory = Callable[
    [
        int | tuple[int, ...],
        Callable[[float], None],
        Callable[[str], None],
        Callable[[SpeechEvent], None],
        Callable[[SpeechSegment], None],
        Callable[[RecognitionResult], None],
    ],
    MicrophoneLevelMonitor,
]

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Application shell with local microphone discovery and level monitoring."""

    microphone_level_received = Signal(float)
    microphone_warning_received = Signal(str)
    speech_event_received = Signal(object)
    speech_segment_received = Signal(object)
    caption_received = Signal(object)

    def __init__(
        self,
        device_provider: Callable[[], list[AudioDevice]] | None = None,
        monitor_factory: MonitorFactory | None = None,
        connected_name_provider: Callable[[], set[str]] | None = None,
    ) -> None:
        super().__init__()
        self._device_provider = device_provider or list_input_devices
        self._refresh_device_provider = device_provider or refresh_input_devices
        self._monitor_factory = monitor_factory or MicrophoneLevelMonitor
        self._connected_name_provider = (
            connected_name_provider or self._qt_connected_microphone_names
        )
        self._microphone_monitor: MicrophoneLevelMonitor | None = None
        self._active_microphone_name: str | None = None
        self._device_refresh_pending = False
        self._completed_speech_chunks = 0
        self._stopping_monitor: MicrophoneLevelMonitor | None = None
        self._stop_thread: Thread | None = None
        self._stop_error: MicrophoneCaptureError | None = None
        self._show_ready_after_stop = True
        self._close_after_stop = False
        self._allow_window_close = False
        self._after_stop: Callable[[], None] | None = None
        self._finishing_dialog: FinishingCaptionsDialog | None = None

        self.microphone_level_received.connect(self._set_microphone_level)
        self.microphone_warning_received.connect(self._show_microphone_warning)
        self.speech_event_received.connect(self._show_speech_event)
        self.speech_segment_received.connect(self._show_speech_segment)
        self.caption_received.connect(self._show_caption)

        self._media_devices = QMediaDevices(self)
        self._media_devices.audioInputsChanged.connect(
            self._handle_connected_microphones_changed
        )

        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(820, 440)
        self.resize(940, 520)

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

        self.speech_status = QLabel("Speech detection: stopped")
        self.speech_status.setObjectName("speechDetectionStatus")

        self.speech_turn_status = QLabel("Completed speech chunks: 0")
        self.speech_turn_status.setObjectName("speechTurnStatus")

        self.latest_caption = QLabel("Latest caption: waiting for microphone")
        self.latest_caption.setObjectName("latestCaption")
        self.latest_caption.setWordWrap(True)
        self.latest_caption.setMinimumHeight(54)

        self.start_stop_button = QPushButton("Start microphone")
        self.start_stop_button.setObjectName("startStopMicrophoneButton")
        self.start_stop_button.setMinimumHeight(48)
        self.start_stop_button.clicked.connect(self.toggle_microphone)

        microphone_group = QGroupBox("Microphone")
        microphone_layout = QVBoxLayout()
        microphone_layout.addLayout(selector_layout)
        microphone_layout.addWidget(self.device_status)
        microphone_layout.addWidget(self.input_level)
        microphone_layout.addWidget(self.speech_status)
        microphone_layout.addWidget(self.speech_turn_status)
        microphone_layout.addWidget(self.latest_caption)
        microphone_layout.addWidget(self.start_stop_button)
        microphone_group.setLayout(microphone_layout)

        self.performance_view_selector = QComboBox()
        self.performance_view_selector.setObjectName("performanceViewSelector")
        self.performance_view_selector.addItem("Simple stats")
        self.performance_view_selector.addItem(
            "Graphs and detailed metrics (planned)"
        )
        performance_model = self.performance_view_selector.model()
        if isinstance(performance_model, QStandardItemModel):
            planned_item = performance_model.item(1)
            if planned_item is not None:
                planned_item.setEnabled(False)

        self.performance_summary = QLabel("No capture measured yet.")
        self.performance_summary.setObjectName("performanceSummary")
        self.performance_summary.setWordWrap(True)

        performance_selector_layout = QHBoxLayout()
        performance_selector_layout.addWidget(QLabel("View:"))
        performance_selector_layout.addWidget(
            self.performance_view_selector,
            stretch=1,
        )

        performance_group = QGroupBox("Performance")
        performance_layout = QVBoxLayout()
        performance_layout.addLayout(performance_selector_layout)
        performance_layout.addWidget(self.performance_summary)
        performance_layout.addStretch()
        performance_group.setLayout(performance_layout)

        self._performance_timer = QTimer(self)
        self._performance_timer.setInterval(1_000)
        self._performance_timer.timeout.connect(self._update_performance_stats)

        self._stop_poll_timer = QTimer(self)
        self._stop_poll_timer.setInterval(200)
        self._stop_poll_timer.timeout.connect(self._poll_microphone_stop)

        panels_layout = QHBoxLayout()
        panels_layout.setSpacing(16)
        panels_layout.addWidget(
            microphone_group,
            stretch=3,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        panels_layout.addWidget(
            performance_group,
            stretch=2,
            alignment=Qt.AlignmentFlag.AlignTop,
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addSpacing(4)
        layout.addLayout(panels_layout)
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
        selected_device = self.microphone_selector.currentData()
        selected_key = (
            self._device_selection_key(selected_device)
            if isinstance(selected_device, AudioDevice)
            else None
        )
        self.microphone_selector.clear()

        try:
            devices = (provider or self._device_provider)()
        except DeviceDiscoveryError as error:
            logger.exception("Microphone discovery failed")
            self.microphone_selector.addItem("Microphone check failed")
            self.microphone_selector.setEnabled(False)
            self.start_stop_button.setEnabled(False)
            self.device_status.setText(str(error))
            self.statusBar().showMessage("Microphone discovery failed")
            return

        if not devices:
            logger.info("Microphone list refreshed: no connected microphones")
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

        if selected_key is not None:
            for index, device in enumerate(devices):
                if self._device_selection_key(device) == selected_key:
                    self.microphone_selector.setCurrentIndex(index)
                    break

        self.microphone_selector.setEnabled(True)
        self.start_stop_button.setEnabled(True)
        noun = "microphone" if len(devices) == 1 else "microphones"
        self.device_status.setText(f"Found {len(devices)} connected {noun}.")
        self.statusBar().showMessage("Ready")
        if refreshed:
            logger.info(
                "Microphone list refreshed: %d connected device(s): %s",
                len(devices),
                ", ".join(device.name for device in devices),
            )

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
            self.speech_event_received.emit,
            self.speech_segment_received.emit,
            self.caption_received.emit,
        )

        try:
            monitor.start()
        except MicrophoneCaptureError as error:
            self.input_level.setValue(0)
            self.device_status.setText(str(error))
            self.statusBar().showMessage("Microphone could not start")
            return

        self._microphone_monitor = monitor
        self._active_microphone_name = selected_device.name
        self.microphone_selector.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.start_stop_button.setText("Stop microphone")
        self.device_status.setText(
            f"Listening to {self.microphone_selector.currentText()}. Audio is not saved."
        )
        self.statusBar().showMessage("Microphone active")
        self.speech_status.setText("Speech detection: listening…")
        self._completed_speech_chunks = 0
        self.speech_turn_status.setText("Completed speech chunks: 0")
        self.latest_caption.setText("Latest caption: listening…")
        self.performance_summary.setText("Measuring performance…")
        self._performance_timer.start()
        self._update_performance_stats()

    def _stop_microphone(
        self,
        show_ready: bool = True,
        *,
        close_after_stop: bool = False,
        after_stop: Callable[[], None] | None = None,
    ) -> None:
        """Stop capture in the background so the interface remains responsive."""
        if self._stop_thread is not None:
            self._show_ready_after_stop = self._show_ready_after_stop and show_ready
            self._close_after_stop = self._close_after_stop or close_after_stop
            self._after_stop = after_stop or self._after_stop
            if self._finishing_dialog is not None:
                self._finishing_dialog.set_closing_application(
                    self._close_after_stop
                )
            return

        monitor = self._microphone_monitor
        if monitor is None:
            if close_after_stop:
                self._allow_window_close = True
                self.close()
            return

        self._microphone_monitor = None
        self._active_microphone_name = None
        self._performance_timer.stop()
        self.input_level.setValue(0)
        self.speech_status.setText("Speech detection: finishing captions…")
        self.microphone_selector.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.start_stop_button.setEnabled(False)
        self.start_stop_button.setText("Stopping…")
        self.statusBar().showMessage("Finishing accepted captions…")

        self._stopping_monitor = monitor
        self._stop_error = None
        self._show_ready_after_stop = show_ready
        self._close_after_stop = close_after_stop
        self._after_stop = after_stop
        self._stop_thread = Thread(
            target=self._stop_monitor_in_background,
            args=(monitor,),
            name="microphone-stop-worker",
            daemon=True,
        )
        self._stop_thread.start()
        self._stop_poll_timer.start()
        QTimer.singleShot(200, self._show_finishing_dialog_if_needed)

    def _stop_monitor_in_background(self, monitor: MicrophoneLevelMonitor) -> None:
        try:
            monitor.stop()
        except MicrophoneCaptureError as error:
            self._stop_error = error
            logger.exception("Microphone did not stop cleanly")

    def _show_finishing_dialog_if_needed(self) -> None:
        thread = self._stop_thread
        if thread is None or not thread.is_alive() or self._finishing_dialog is not None:
            return
        dialog = FinishingCaptionsDialog(
            self,
            closing_application=self._close_after_stop,
        )
        dialog.finish_now_requested.connect(self._skip_remaining_captions)
        self._finishing_dialog = dialog
        self._update_finishing_dialog()
        dialog.open()

    def _poll_microphone_stop(self) -> None:
        thread = self._stop_thread
        if thread is None:
            self._stop_poll_timer.stop()
            return
        if thread.is_alive():
            self._update_finishing_dialog()
            return
        thread.join(timeout=0.0)
        self._finish_microphone_stop()

    def _update_finishing_dialog(self) -> None:
        dialog = self._finishing_dialog
        monitor = self._stopping_monitor
        if dialog is None or monitor is None:
            return
        stats = getattr(monitor, "recognition_stats", None)
        if stats is None:
            dialog.update_progress(
                remaining_captions=0,
                estimated_seconds=None,
            )
            return
        remaining = stats.queued_segments + stats.active_segments
        dialog.update_progress(
            remaining_captions=remaining,
            estimated_seconds=self._estimated_recognition_seconds(stats),
        )

    @staticmethod
    def _estimated_recognition_seconds(stats: object) -> float | None:
        queued = int(getattr(stats, "queued_segments", 0))
        active = int(getattr(stats, "active_segments", 0))
        remaining = queued + active
        if remaining <= 0:
            return None

        completed = int(getattr(stats, "completed_segments", 0))
        failed = int(getattr(stats, "failed_segments", 0))
        processing_seconds = float(getattr(stats, "processing_seconds", 0.0))
        processed = completed + failed
        average_seconds = processing_seconds / processed if processed else 10.0
        average_seconds = max(1.0, average_seconds)
        active_elapsed = float(getattr(stats, "active_elapsed_seconds", 0.0))
        active_remaining = (
            max(1.0, average_seconds - active_elapsed) if active else 0.0
        )
        return queued * average_seconds + active_remaining

    def _skip_remaining_captions(self) -> None:
        monitor = self._stopping_monitor
        if monitor is None:
            return
        cancel = getattr(monitor, "cancel_pending_recognition", None)
        cancelled = int(cancel()) if callable(cancel) else 0
        logger.info("User skipped %d queued caption(s) during shutdown", cancelled)
        if self._finishing_dialog is not None:
            self._finishing_dialog.show_finishing_current_caption()

    def _finish_microphone_stop(self) -> None:
        monitor = self._stopping_monitor
        dialog = self._finishing_dialog
        after_stop = self._after_stop
        close_after_stop = self._close_after_stop
        show_ready = self._show_ready_after_stop
        stop_error = self._stop_error

        self._stop_poll_timer.stop()
        self._stop_thread = None
        self._stopping_monitor = None
        self._stop_error = None
        self._after_stop = None
        self._finishing_dialog = None
        if dialog is not None:
            dialog.accept()
            dialog.deleteLater()

        if monitor is not None:
            self._show_performance_report(
                monitor.performance_report,
                monitor.voice_activity_stats,
            )

        self.speech_status.setText("Speech detection: stopped")
        self.microphone_selector.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        self.start_stop_button.setEnabled(True)
        self.start_stop_button.setText("Start microphone")

        if stop_error is not None:
            self.device_status.setText(str(stop_error))
            self.statusBar().showMessage("Microphone stop warning")
        elif show_ready:
            self.device_status.setText("Microphone stopped. Audio was not saved.")
            self.statusBar().showMessage("Ready")

        if self._device_refresh_pending and show_ready:
            self._device_refresh_pending = False
            self.refresh_connected_microphones()
        if after_stop is not None:
            after_stop()
        if close_after_stop:
            self._allow_window_close = True
            self.close()

    def _set_microphone_level(self, level: float) -> None:
        self.input_level.setValue(int(round(level)))

    def _show_microphone_warning(self, warning: str) -> None:
        self.statusBar().showMessage(f"Microphone warning: {warning}")

    def _show_speech_event(self, event: object) -> None:
        kind = getattr(event, "kind", None)
        audio_seconds = float(getattr(event, "audio_seconds", 0.0))
        if kind == "speech_start":
            self.speech_status.setText("Speech detection: speech")
            logger.info("Speech started at %.3fs", audio_seconds)
        elif kind == "speech_end":
            self.speech_status.setText("Speech detection: silence")
            logger.info("Speech ended at %.3fs", audio_seconds)

    def _show_speech_segment(self, segment: object) -> None:
        duration_seconds = float(getattr(segment, "duration_seconds", 0.0))
        forced_split = bool(getattr(segment, "forced_split", False))
        self._completed_speech_chunks += 1
        split_note = " (30-second split)" if forced_split else ""
        self.speech_turn_status.setText(
            f"Completed speech chunks: {self._completed_speech_chunks}  |  "
            f"Last turn: {duration_seconds:.1f}s{split_note}"
        )
        logger.info(
            "Temporary speech segment completed: duration=%.3fs, forced split=%s",
            duration_seconds,
            forced_split,
        )

    def _show_caption(self, result: RecognitionResult) -> None:
        if result.skipped or result.error:
            self.latest_caption.setText(result.text)
            self.statusBar().showMessage("Caption warning")
            return

        text = result.text or "[no speech recognized]"
        language = (result.language or "unknown").upper()
        self.latest_caption.setText(f"Latest caption [{language}]: {text}")
        logger.info(
            "Caption completed: language=%s, characters=%d, processing=%.3fs",
            result.language or "unknown",
            len(result.text),
            result.processing_seconds,
        )

    def _update_performance_stats(self) -> None:
        monitor = self._microphone_monitor
        if monitor is None:
            return
        self._show_performance_report(
            monitor.performance_report,
            monitor.voice_activity_stats,
        )

    def _show_performance_report(
        self,
        report: PerformanceReport | None,
        vad_stats: VoiceActivityStats | None = None,
    ) -> None:
        if report is None:
            return

        minutes, seconds = divmod(int(report.elapsed_seconds), 60)
        summary = (
            f"Duration: {minutes:02d}:{seconds:02d}  |  "
            f"Average CPU: {report.average_cpu_percent:.1f}%\n"
            f"Memory: {format_mebibytes(report.memory_end_bytes)}  |  "
            f"Change: {format_mebibytes(report.memory_change_bytes)}  |  "
            f"Process peak: {format_mebibytes(report.peak_memory_bytes)}"
        )
        if vad_stats is not None:
            summary += (
                f"\nVAD audio: {vad_stats.processed_audio_seconds:.1f}s  |  "
                f"Detection: {vad_stats.average_inference_milliseconds:.2f} ms/window  |  "
                f"Load: {vad_stats.model_load_seconds:.2f}s\n"
                f"Speech starts: {vad_stats.speech_starts}  |  "
                f"Completed turns: {vad_stats.speech_ends}  |  "
                f"Audio chunks: {vad_stats.completed_segments}  |  "
                f"Short events ignored: {vad_stats.filtered_short_segments}"
            )
        self.performance_summary.setText(summary)

    @staticmethod
    def _qt_connected_microphone_names() -> set[str]:
        return {device.description() for device in QMediaDevices.audioInputs()}

    @staticmethod
    def _normalized_device_name(name: str) -> str:
        return " ".join(name.casefold().split())

    @classmethod
    def _device_selection_key(cls, device: AudioDevice) -> tuple[str, str]:
        """Identify one logical device even if refresh changes backend indices."""
        return (
            cls._normalized_device_name(device.name),
            cls._normalized_device_name(device.host_api),
        )

    def _handle_connected_microphones_changed(self) -> None:
        """React to microphone connection changes reported by Windows and Qt."""
        active_name = self._active_microphone_name
        if active_name is None:
            self.refresh_connected_microphones()
            return

        connected_names = {
            self._normalized_device_name(name)
            for name in self._connected_name_provider()
        }
        if self._normalized_device_name(active_name) not in connected_names:
            disconnected_name = active_name
            logger.warning("Active microphone disconnected: %s", disconnected_name)
            self._device_refresh_pending = False

            def finish_disconnect() -> None:
                self.refresh_connected_microphones()
                self.device_status.setText(
                    f"{disconnected_name} was disconnected. "
                    "Microphone capture stopped."
                )
                self.statusBar().showMessage("Microphone disconnected")

            self._stop_microphone(
                show_ready=False,
                after_stop=finish_disconnect,
            )
            return

        self._device_refresh_pending = True
        logger.info("Audio devices changed while microphone capture is active")
        self.statusBar().showMessage(
            "Audio devices changed; the list will refresh after capture stops"
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Release audio asynchronously before allowing the window to close."""
        if self._allow_window_close:
            super().closeEvent(event)
            return
        if self._microphone_monitor is not None or self._stop_thread is not None:
            event.ignore()
            self._stop_microphone(
                show_ready=False,
                close_after_stop=True,
            )
            return
        super().closeEvent(event)


