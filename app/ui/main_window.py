"""Main desktop window for the application shell."""

from collections.abc import Callable
from datetime import datetime
import logging
from queue import Empty, SimpleQueue
import sqlite3
from threading import Thread

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QStandardItemModel
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
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
from app.audio.loopback import (
    LoopbackCaptureMonitor,
    OutputAudioDevice,
    list_output_devices,
    refresh_output_devices,
)
from app.core.performance import PerformanceReport, format_mebibytes
from app.core.state import RuntimeState, RuntimeStateMachine
from app.history.database import HistoryDatabase, local_timestamp
from app.speech.recognition import RecognitionStats
from app.speech.vad import (
    VAD_SAMPLE_RATE,
    SpeechEvent,
    SpeechSegment,
    VoiceActivityStats,
)
from app.translation.translator import TranslationResult, TranslationStats
from app.ui.chat_view import CaptionChatView
from app.ui.finishing_dialog import FinishingCaptionsDialog
from app.ui.history_sidebar import ConversationSidebar, LIVE_ITEM_KEY


MonitorFactory = Callable[
    [
        int | tuple[int, ...],
        Callable[[float], None],
        Callable[[str], None],
        Callable[[SpeechEvent], None],
        Callable[[SpeechSegment], None],
        Callable[[TranslationResult], None],
        str | None,
        float,
        float,
        int,
    ],
    MicrophoneLevelMonitor,
]
LoopbackMonitorFactory = Callable[
    [
        OutputAudioDevice,
        Callable[[float], None],
        Callable[[str], None],
        Callable[[SpeechEvent], None],
        Callable[[SpeechSegment], None],
        Callable[[TranslationResult], None],
        str | None,
        float,
        float,
        int,
    ],
    LoopbackCaptureMonitor,
]

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Application shell for local microphone captions and translation."""

    microphone_level_received = Signal(float)
    microphone_warning_received = Signal(str)
    speech_event_received = Signal(object)
    speech_segment_received = Signal(object)
    caption_received = Signal(object)
    queued_captions_ready = Signal()

    def __init__(
        self,
        device_provider: Callable[[], list[AudioDevice]] | None = None,
        monitor_factory: MonitorFactory | None = None,
        connected_name_provider: Callable[[], set[str]] | None = None,
        output_device_provider: Callable[[], list[OutputAudioDevice]] | None = None,
        loopback_monitor_factory: LoopbackMonitorFactory | None = None,
        history_database: HistoryDatabase | None = None,
    ) -> None:
        super().__init__()
        self._runtime = RuntimeStateMachine()
        self._history = history_database or HistoryDatabase()
        recovered_sessions = self._history.recover_interrupted_sessions()
        if recovered_sessions:
            logger.info(
                "Recovered %d interrupted caption session(s)",
                recovered_sessions,
            )
        self._active_history_session_id: int | None = None
        self._viewing_history_session_id: int | None = None
        self._showing_live_conversation = False
        self._device_provider = device_provider or list_input_devices
        self._refresh_device_provider = device_provider or refresh_input_devices
        self._monitor_factory = monitor_factory or MicrophoneLevelMonitor
        self._output_device_provider = output_device_provider or list_output_devices
        self._refresh_output_device_provider = (
            output_device_provider or refresh_output_devices
        )
        self._loopback_monitor_factory = (
            loopback_monitor_factory or LoopbackCaptureMonitor
        )
        self._connected_name_provider = (
            connected_name_provider or self._qt_connected_microphone_names
        )
        self._microphone_monitor: MicrophoneLevelMonitor | None = None
        self._active_source: str | None = None
        self._active_microphone_name: str | None = None
        self._active_output_id: str | None = None
        self._caption_source_label = "Speaker"
        self._device_refresh_pending = False
        self._outputs_loaded = False
        self._completed_speech_chunks = 0
        self._stopping_monitor: MicrophoneLevelMonitor | None = None
        self._stop_thread: Thread | None = None
        self._stop_error: MicrophoneCaptureError | None = None
        self._show_ready_after_stop = True
        self._close_after_stop = False
        self._allow_window_close = False
        self._after_stop: Callable[[], None] | None = None
        self._finishing_dialog: FinishingCaptionsDialog | None = None
        self._pending_caption_results: SimpleQueue[TranslationResult] = SimpleQueue()

        self.microphone_level_received.connect(self._set_microphone_level)
        self.microphone_warning_received.connect(self._show_microphone_warning)
        self.speech_event_received.connect(self._show_speech_event)
        self.speech_segment_received.connect(self._show_speech_segment)
        self.caption_received.connect(self._show_caption)
        self.queued_captions_ready.connect(self._drain_queued_captions)

        self._media_devices = QMediaDevices(self)
        self._media_devices.audioInputsChanged.connect(
            self._handle_connected_microphones_changed
        )
        self._media_devices.audioOutputsChanged.connect(
            self._handle_connected_outputs_changed
        )

        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 640)
        self.resize(1050, 760)

        title = QLabel(APP_NAME)
        title.setObjectName("applicationTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_font = QFont(title.font())
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)

        message = QLabel("Choose where the live speech will come from.")
        message.setObjectName("welcomeMessage")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.microphone_source_button = QRadioButton("Microphone")
        self.microphone_source_button.setObjectName("microphoneSourceButton")
        self.computer_audio_source_button = QRadioButton("Computer audio")
        self.computer_audio_source_button.setObjectName("computerAudioSourceButton")
        self.microphone_source_button.setChecked(True)
        self.source_button_group = QButtonGroup(self)
        self.source_button_group.addButton(self.microphone_source_button)
        self.source_button_group.addButton(self.computer_audio_source_button)
        self.source_button_group.buttonToggled.connect(self._handle_source_changed)

        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Audio source:"))
        source_layout.addWidget(self.microphone_source_button)
        source_layout.addWidget(self.computer_audio_source_button)
        source_layout.addStretch()

        self.microphone_selector = QComboBox()
        self.microphone_selector.setObjectName("microphoneSelector")
        self.microphone_selector.setMinimumWidth(360)

        self.output_selector = QComboBox()
        self.output_selector.setObjectName("computerAudioSelector")
        self.output_selector.setMinimumWidth(360)
        self.output_selector.addItem("Select Computer audio to load devices")
        self.output_selector.setEnabled(False)
        self.output_selector.setVisible(False)

        self.refresh_devices_button = QPushButton("Refresh")
        self.refresh_devices_button.setObjectName("refreshMicrophonesButton")
        self.refresh_devices_button.clicked.connect(self.refresh_selected_devices)

        selector_layout = QHBoxLayout()
        selector_layout.addWidget(self.microphone_selector, stretch=1)
        selector_layout.addWidget(self.output_selector, stretch=1)
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

        self.language_selector = QComboBox()
        self.language_selector.setObjectName("speechLanguageSelector")
        self.language_selector.addItem("Automatic", userData=None)
        self.language_selector.addItem("French", userData="fr")
        self.language_selector.addItem("English", userData="en")

        self.caption_view_selector = QComboBox()
        self.caption_view_selector.setObjectName("captionViewSelector")
        self.caption_view_selector.addItem("Original and translation", userData="both")
        self.caption_view_selector.addItem("Original only", userData="original")
        self.caption_view_selector.addItem("Translation only", userData="translation")

        language_layout = QHBoxLayout()
        language_layout.addWidget(QLabel("Spoken language:"))
        language_layout.addWidget(self.language_selector, stretch=1)

        caption_view_layout = QHBoxLayout()
        caption_view_layout.addWidget(QLabel("Caption view:"))
        caption_view_layout.addWidget(self.caption_view_selector, stretch=1)

        self.speech_sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.speech_sensitivity_slider.setObjectName("speechSensitivitySlider")
        self.speech_sensitivity_slider.setRange(1, 9)
        self.speech_sensitivity_slider.setValue(5)
        self.speech_sensitivity_slider.setTickInterval(1)
        self.speech_sensitivity_slider.setTickPosition(
            QSlider.TickPosition.TicksBelow
        )
        self.speech_sensitivity_slider.setToolTip(
            "Higher sensitivity accepts quieter speech but may accept more noise."
        )
        self.speech_sensitivity_label = QLabel()
        self.speech_sensitivity_label.setObjectName("speechSensitivityLabel")
        self.speech_sensitivity_slider.valueChanged.connect(
            self._update_speech_sensitivity_label
        )
        self._update_speech_sensitivity_label()

        sensitivity_layout = QHBoxLayout()
        sensitivity_layout.addWidget(self.speech_sensitivity_label)
        sensitivity_layout.addWidget(self.speech_sensitivity_slider, stretch=1)

        self.whisper_workers_slider = QSlider(Qt.Orientation.Horizontal)
        self.whisper_workers_slider.setObjectName("whisperWorkersSlider")
        self.whisper_workers_slider.setRange(1, 2)
        self.whisper_workers_slider.setValue(1)
        self.whisper_workers_slider.setTickInterval(1)
        self.whisper_workers_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.whisper_workers_slider.setToolTip(
            "Two workers can process two chunks together, but use more RAM and CPU."
        )
        self.whisper_workers_label = QLabel()
        self.whisper_workers_label.setObjectName("whisperWorkersLabel")
        self.whisper_workers_slider.valueChanged.connect(
            self._update_whisper_workers_label
        )
        self._update_whisper_workers_label()

        whisper_workers_layout = QHBoxLayout()
        whisper_workers_layout.addWidget(self.whisper_workers_label)
        whisper_workers_layout.addWidget(self.whisper_workers_slider, stretch=1)

        self.speech_chunk_duration_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.speech_chunk_duration_slider.setObjectName(
            "speechChunkDurationSlider"
        )
        self.speech_chunk_duration_slider.setRange(5, 60)
        self.speech_chunk_duration_slider.setSingleStep(5)
        self.speech_chunk_duration_slider.setPageStep(5)
        self.speech_chunk_duration_slider.setTickInterval(5)
        self.speech_chunk_duration_slider.setTickPosition(
            QSlider.TickPosition.TicksBelow
        )
        self.speech_chunk_duration_slider.setValue(30)
        self.speech_chunk_duration_slider.setToolTip(
            "Maximum length of uninterrupted speech. Silence can end a chunk earlier."
        )
        self.speech_chunk_duration_label = QLabel()
        self.speech_chunk_duration_label.setObjectName(
            "speechChunkDurationLabel"
        )
        self.speech_chunk_duration_slider.valueChanged.connect(
            self._update_speech_chunk_duration_label
        )
        self._update_speech_chunk_duration_label()

        chunk_duration_layout = QHBoxLayout()
        chunk_duration_layout.addWidget(self.speech_chunk_duration_label)
        chunk_duration_layout.addWidget(
            self.speech_chunk_duration_slider,
            stretch=1,
        )

        self.start_stop_button = QPushButton("Start microphone")
        self.start_stop_button.setObjectName("startStopMicrophoneButton")
        self.start_stop_button.setMinimumHeight(48)
        self.start_stop_button.clicked.connect(self.toggle_microphone)

        self.microphone_group = QGroupBox("Live audio")
        self.microphone_group.setObjectName("liveAudioGroup")
        microphone_layout = QVBoxLayout()
        microphone_layout.setContentsMargins(12, 18, 12, 16)
        microphone_layout.setSpacing(8)
        microphone_layout.addLayout(source_layout)
        microphone_layout.addLayout(language_layout)
        microphone_layout.addLayout(caption_view_layout)
        microphone_layout.addLayout(chunk_duration_layout)
        microphone_layout.addLayout(selector_layout)
        microphone_layout.addWidget(self.device_status)
        microphone_layout.addWidget(self.input_level)
        microphone_layout.addWidget(self.speech_status)
        microphone_layout.addWidget(self.speech_turn_status)
        microphone_layout.addWidget(self.start_stop_button)
        microphone_layout.addSpacing(4)
        self.microphone_group.setLayout(microphone_layout)
        self.microphone_group.setMinimumHeight(
            self.microphone_group.minimumSizeHint().height()
        )

        self.caption_chat = CaptionChatView()
        self.caption_view_selector.currentIndexChanged.connect(
            self._update_caption_display_mode
        )
        self.clear_captions_button = QPushButton("Clear screen")
        self.clear_captions_button.setObjectName("clearCaptionsButton")
        self.clear_captions_button.clicked.connect(self._clear_caption_screen)
        self.to_bottom_button = QPushButton("To bottom")
        self.to_bottom_button.setObjectName("toBottomButton")
        self.to_bottom_button.clicked.connect(self.caption_chat.scroll_to_bottom)

        chat_actions = QHBoxLayout()
        chat_actions.addStretch()
        chat_actions.addWidget(self.to_bottom_button)
        chat_actions.addWidget(self.clear_captions_button)

        self.chat_group = QGroupBox("Live conversation")
        chat_layout = QVBoxLayout(self.chat_group)
        chat_layout.addLayout(chat_actions)
        chat_layout.addWidget(self.caption_chat, stretch=1)

        self.conversation_sidebar = ConversationSidebar()
        self.conversation_sidebar.live_selected.connect(
            self._show_live_conversation
        )
        self.conversation_sidebar.session_selected.connect(
            self._show_saved_conversation
        )

        conversation_layout = QHBoxLayout()
        conversation_layout.setSpacing(12)
        conversation_layout.addWidget(self.conversation_sidebar)
        conversation_layout.addWidget(self.chat_group, stretch=1)

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

        performance_group = QGroupBox("Processing settings and performance")
        performance_layout = QVBoxLayout()
        performance_layout.addLayout(sensitivity_layout)
        performance_layout.addLayout(whisper_workers_layout)
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
            self.microphone_group,
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
        layout.addLayout(conversation_layout, stretch=1)

        content = QWidget()
        content.setLayout(layout)
        self.setCentralWidget(content)

        self.refresh_microphones()
        self._refresh_conversation_sidebar()

    @property
    def runtime_state(self) -> RuntimeState:
        """Return the current processing state for UI and diagnostics."""
        return self._runtime.state

    def _refresh_conversation_sidebar(
        self,
        selected_key: str | int | None = None,
    ) -> None:
        if selected_key is None:
            selected_key = self.conversation_sidebar.selected_key()
        self.conversation_sidebar.set_sessions(
            self._history.list_sessions(),
            show_live=self._active_history_session_id is not None,
            selected_key=selected_key,
        )
        if self._viewing_history_session_id is None:
            current_key = self.conversation_sidebar.selected_key()
            if current_key == LIVE_ITEM_KEY:
                self._show_live_conversation()
            elif isinstance(current_key, int):
                self._show_saved_conversation(current_key)

    def _show_live_conversation(self) -> None:
        session_id = self._active_history_session_id
        if session_id is None:
            return
        self._showing_live_conversation = True
        self._viewing_history_session_id = session_id
        self.chat_group.setTitle("Live conversation")
        self._load_history_messages(session_id)

    def _show_saved_conversation(self, session_id: int) -> None:
        session = self._history.get_session(session_id)
        if session is None:
            return
        self._showing_live_conversation = False
        self._viewing_history_session_id = session_id
        status = session.status.capitalize()
        self.chat_group.setTitle(f"{session.title} · {status}")
        self._load_history_messages(session_id)

    def _load_history_messages(self, session_id: int) -> None:
        self.caption_chat.clear_messages()
        for message in self._history.get_messages(session_id):
            self.caption_chat.add_message(
                original_text=message.original_text,
                source_language=message.source_language,
                translated_text=message.translated_text,
                target_language=message.target_language,
                speaker=message.speaker_label,
                timestamp=self._history_message_time(message.created_at),
                warning=message.warning,
            )
        QTimer.singleShot(0, self.caption_chat.scroll_to_bottom)

    @staticmethod
    def _history_message_time(timestamp: str) -> str:
        try:
            return datetime.fromisoformat(timestamp).astimezone().strftime("%H:%M")
        except ValueError:
            return timestamp

    def _create_history_session(self, active_source: str) -> int:
        source_label = (
            "Computer audio" if active_source == "computer_audio" else "Microphone"
        )
        preferred_language = self.language_selector.currentData()
        language_mode = preferred_language or "automatic"
        target_language = (
            "en" if preferred_language == "fr" else "fr"
            if preferred_language == "en"
            else None
        )
        title_time = datetime.now().astimezone().strftime("%d %b %Y %H:%M")
        return self._history.create_session(
            title=f"{source_label} · {title_time}",
            audio_source=active_source,
            language_mode=str(language_mode),
            preferred_source_language=preferred_language,
            target_language=target_language,
        )

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
        if self._microphone_monitor is not None or self._stop_thread is not None:
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

    def refresh_outputs(
        self,
        provider: Callable[[], list[OutputAudioDevice]] | None = None,
        refreshed: bool = False,
    ) -> None:
        """Reload connected Windows speaker and headphone endpoints."""
        selected = self.output_selector.currentData()
        selected_id = selected.id if isinstance(selected, OutputAudioDevice) else None
        self.output_selector.clear()
        try:
            devices = (provider or self._output_device_provider)()
        except MicrophoneCaptureError as error:
            logger.exception("Computer audio device discovery failed")
            self.output_selector.addItem("Computer audio check failed")
            self.output_selector.setEnabled(False)
            self.start_stop_button.setEnabled(False)
            self.device_status.setText(str(error))
            self.statusBar().showMessage("Computer audio discovery failed")
            self._outputs_loaded = True
            return

        self._outputs_loaded = True
        if not devices:
            self.output_selector.addItem("No output devices found")
            self.output_selector.setEnabled(False)
            self.start_stop_button.setEnabled(False)
            self.device_status.setText(
                "Connect speakers or headphones, then refresh."
            )
            self.statusBar().showMessage("No computer audio device found")
            return

        for device in devices:
            self.output_selector.addItem(device.display_name, userData=device)
        if selected_id is not None:
            for index, device in enumerate(devices):
                if device.id == selected_id:
                    self.output_selector.setCurrentIndex(index)
                    break

        self.output_selector.setEnabled(True)
        self.start_stop_button.setEnabled(True)
        noun = "device" if len(devices) == 1 else "devices"
        self.device_status.setText(
            f"Found {len(devices)} connected computer audio {noun}."
        )
        self.statusBar().showMessage("Ready")
        if refreshed:
            logger.info(
                "Computer audio list refreshed: %d connected device(s): %s",
                len(devices),
                ", ".join(device.name for device in devices),
            )

    def refresh_connected_outputs(self) -> None:
        if self._microphone_monitor is not None or self._stop_thread is not None:
            return
        self.refresh_devices_button.setEnabled(False)
        self.refresh_devices_button.setText("Refreshing…")
        self.statusBar().showMessage("Refreshing computer audio list…")
        try:
            self.refresh_outputs(
                provider=self._refresh_output_device_provider,
                refreshed=True,
            )
        finally:
            self.refresh_devices_button.setText("Refresh")
            self.refresh_devices_button.setEnabled(True)

    def refresh_selected_devices(self) -> None:
        if self.computer_audio_source_button.isChecked():
            self.refresh_connected_outputs()
        else:
            self.refresh_connected_microphones()

    def _handle_source_changed(self, _button, checked: bool) -> None:
        if not checked or self._microphone_monitor is not None:
            return
        computer_audio = self.computer_audio_source_button.isChecked()
        self.microphone_selector.setVisible(not computer_audio)
        self.output_selector.setVisible(computer_audio)
        if computer_audio and not self._outputs_loaded:
            self.refresh_outputs()
        elif computer_audio:
            self.start_stop_button.setEnabled(self.output_selector.isEnabled())
            self.device_status.setText(
                f"Selected computer audio: {self.output_selector.currentText()}."
            )
            self.statusBar().showMessage("Ready")
        else:
            self.start_stop_button.setEnabled(self.microphone_selector.isEnabled())
            self.device_status.setText(
                f"Found {self.microphone_selector.count()} connected "
                f"{'microphone' if self.microphone_selector.count() == 1 else 'microphones'}."
            )
            self.statusBar().showMessage("Ready")
        self.start_stop_button.setText(
            "Start computer audio" if computer_audio else "Start microphone"
        )

    def _update_speech_chunk_duration_label(self, _value: int | None = None) -> None:
        seconds = self.speech_chunk_duration_slider.value()
        self.speech_chunk_duration_label.setText(
            f"Maximum speech chunk: {seconds} seconds"
        )

    def _update_speech_sensitivity_label(self, _value: int | None = None) -> None:
        value = self.speech_sensitivity_slider.value()
        if value < 5:
            description = "Less sensitive"
        elif value > 5:
            description = "More sensitive"
        else:
            description = "Balanced"
        self.speech_sensitivity_label.setText(
            f"Speech sensitivity: {value}/9 ({description})"
        )

    def _update_whisper_workers_label(self, _value: int | None = None) -> None:
        count = self.whisper_workers_slider.value()
        self.whisper_workers_label.setText(f"Whisper workers: {count}")

    def _speech_threshold(self) -> float:
        # Keep Silero's exit threshold (speech threshold - 0.15) positive.
        return round(0.875 - self.speech_sensitivity_slider.value() * 0.075, 3)

    def toggle_microphone(self) -> None:
        """Start or stop the caption pipeline for the selected audio source."""
        if self._microphone_monitor is not None:
            self._stop_microphone()
            return

        if self._runtime.state is RuntimeState.ERROR:
            self._runtime.transition(RuntimeState.IDLE)

        computer_audio = self.computer_audio_source_button.isChecked()
        if computer_audio:
            selected_output = self.output_selector.currentData()
            if not isinstance(selected_output, OutputAudioDevice):
                self.statusBar().showMessage("Select a computer audio device first")
                return
            monitor = self._loopback_monitor_factory(
                selected_output,
                self.microphone_level_received.emit,
                self.microphone_warning_received.emit,
                self.speech_event_received.emit,
                self.speech_segment_received.emit,
                self._queue_caption_from_worker,
                self.language_selector.currentData(),
                float(self.speech_chunk_duration_slider.value()),
                self._speech_threshold(),
                self.whisper_workers_slider.value(),
            )
            selected_name = selected_output.name
            active_source = "computer_audio"
        else:
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
                self._queue_caption_from_worker,
                self.language_selector.currentData(),
                float(self.speech_chunk_duration_slider.value()),
                self._speech_threshold(),
                self.whisper_workers_slider.value(),
            )
            selected_name = selected_device.name
            active_source = "microphone"

        self._runtime.transition(RuntimeState.STARTING)
        try:
            history_session_id = self._create_history_session(active_source)
        except sqlite3.Error as error:
            self._runtime.transition(RuntimeState.ERROR)
            logger.exception("Caption history session could not be created")
            self.device_status.setText(
                "Local conversation history could not be started."
            )
            self.statusBar().showMessage("History database error")
            return

        self._active_history_session_id = history_session_id
        try:
            monitor.start()
        except MicrophoneCaptureError as error:
            try:
                self._history.finish_session(
                    history_session_id,
                    status="incomplete",
                )
            except sqlite3.Error:
                logger.exception("Failed to close unsuccessful history session")
            self._active_history_session_id = None
            self._refresh_conversation_sidebar(history_session_id)
            self._runtime.transition(RuntimeState.ERROR)
            self.input_level.setValue(0)
            self.device_status.setText(str(error))
            self.statusBar().showMessage("Audio capture could not start")
            return

        self._microphone_monitor = monitor
        self._active_source = active_source
        self._active_microphone_name = (
            selected_name if active_source == "microphone" else None
        )
        self._active_output_id = (
            selected_output.id if active_source == "computer_audio" else None
        )
        self._caption_source_label = (
            "Computer audio" if active_source == "computer_audio" else "Microphone"
        )
        self._runtime.transition(RuntimeState.LIVE)
        self._showing_live_conversation = True
        self._viewing_history_session_id = history_session_id
        self.chat_group.setTitle("Live conversation")
        self._refresh_conversation_sidebar(LIVE_ITEM_KEY)
        self.microphone_source_button.setEnabled(False)
        self.computer_audio_source_button.setEnabled(False)
        self.microphone_selector.setEnabled(False)
        self.output_selector.setEnabled(False)
        self.language_selector.setEnabled(False)
        self.speech_chunk_duration_slider.setEnabled(False)
        self.speech_sensitivity_slider.setEnabled(False)
        self.whisper_workers_slider.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.start_stop_button.setText(
            "Stop computer audio" if computer_audio else "Stop microphone"
        )
        self.device_status.setText(
            f"Listening to {selected_name}. Audio is not saved."
        )
        self.statusBar().showMessage(
            "Computer audio active" if computer_audio else "Microphone active"
        )
        self.speech_status.setText("Speech detection: listening…")
        self._completed_speech_chunks = 0
        self.speech_turn_status.setText("Completed speech chunks: 0")
        self.caption_chat.clear_messages()
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

        if self._runtime.state in {RuntimeState.LIVE, RuntimeState.ERROR}:
            self._runtime.transition(RuntimeState.STOPPING)

        self._microphone_monitor = None
        self._active_source = None
        self._active_microphone_name = None
        self._active_output_id = None
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
        recognition_stats = getattr(monitor, "recognition_stats", None)
        translation_stats = getattr(monitor, "translation_stats", None)
        recognition_remaining, recognition_estimate, _ = self._queue_progress(
            recognition_stats,
            queued_name="queued_segments",
            active_name="active_segments",
            elapsed_name="active_elapsed_seconds",
            completed_name="completed_segments",
            failed_name="failed_segments",
            default_seconds=10.0,
        )
        translation_remaining, translation_estimate, translation_average = (
            self._queue_progress(
                translation_stats,
                queued_name="queued_captions",
                active_name="active_captions",
                elapsed_name="active_elapsed_seconds",
                completed_name="completed_captions",
                failed_name="failed_captions",
                default_seconds=5.0,
            )
        )
        remaining = recognition_remaining + translation_remaining
        if remaining <= 0:
            dialog.update_progress(
                remaining_captions=0,
                estimated_seconds=None,
            )
            return

        estimated_seconds = (
            recognition_estimate
            + translation_estimate
            + recognition_remaining * translation_average
        )
        dialog.update_progress(
            remaining_captions=remaining,
            estimated_seconds=estimated_seconds,
        )

    @staticmethod
    def _queue_progress(
        stats: object | None,
        *,
        queued_name: str,
        active_name: str,
        elapsed_name: str,
        completed_name: str,
        failed_name: str,
        default_seconds: float,
    ) -> tuple[int, float, float]:
        if stats is None:
            return 0, 0.0, default_seconds
        queued = int(getattr(stats, queued_name, 0))
        active = int(getattr(stats, active_name, 0))
        remaining = queued + active
        completed = int(getattr(stats, completed_name, 0))
        failed = int(getattr(stats, failed_name, 0))
        processing_seconds = float(getattr(stats, "processing_seconds", 0.0))
        processed = completed + failed
        average_seconds = (
            processing_seconds / processed if processed else default_seconds
        )
        average_seconds = max(1.0, average_seconds)
        active_elapsed = float(getattr(stats, elapsed_name, 0.0))
        active_remaining = (
            max(float(active), active * average_seconds - active_elapsed)
            if active
            else 0.0
        )
        return remaining, queued * average_seconds + active_remaining, average_seconds

    def _skip_remaining_captions(self) -> None:
        monitor = self._stopping_monitor
        if monitor is None:
            return
        cancel = getattr(monitor, "cancel_pending_captions", None)
        if not callable(cancel):
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

        # The processing workers are now stopped, so every accepted result is in
        # this handoff queue. Drain it before closing the active history session.
        self._drain_queued_captions()

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
                monitor.recognition_stats,
                monitor.translation_stats,
            )

        history_session_id = self._active_history_session_id
        if history_session_id is not None:
            history_status = "incomplete" if stop_error is not None else "completed"
            try:
                self._history.finish_session(
                    history_session_id,
                    status=history_status,
                )
            except sqlite3.Error:
                logger.exception("Caption history session could not be finished")
                self.statusBar().showMessage("History database error")
            self._active_history_session_id = None
            if self._showing_live_conversation:
                self._showing_live_conversation = False
                self._viewing_history_session_id = history_session_id
                session = self._history.get_session(history_session_id)
                if session is not None:
                    self.chat_group.setTitle(
                        f"{session.title} · {session.status.capitalize()}"
                    )
            self._refresh_conversation_sidebar(
                self._viewing_history_session_id
            )

        self.speech_status.setText("Speech detection: stopped")
        computer_audio = self.computer_audio_source_button.isChecked()
        self.microphone_source_button.setEnabled(True)
        self.computer_audio_source_button.setEnabled(True)
        self.microphone_selector.setEnabled(
            not computer_audio
            and isinstance(self.microphone_selector.currentData(), AudioDevice)
        )
        self.output_selector.setEnabled(
            computer_audio
            and isinstance(self.output_selector.currentData(), OutputAudioDevice)
        )
        self.language_selector.setEnabled(True)
        self.speech_chunk_duration_slider.setEnabled(True)
        self.speech_sensitivity_slider.setEnabled(True)
        self.whisper_workers_slider.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        self.start_stop_button.setEnabled(
            self.output_selector.isEnabled()
            if computer_audio
            else self.microphone_selector.isEnabled()
        )
        self.start_stop_button.setText(
            "Start computer audio" if computer_audio else "Start microphone"
        )

        if stop_error is not None:
            self._runtime.transition(RuntimeState.ERROR)
            self.device_status.setText(str(stop_error))
            self.statusBar().showMessage("Microphone stop warning")
        elif show_ready:
            self._runtime.transition(RuntimeState.IDLE)
            self.device_status.setText("Audio capture stopped. Audio was not saved.")
            self.statusBar().showMessage("Ready")
        else:
            self._runtime.transition(RuntimeState.IDLE)

        if self._device_refresh_pending and show_ready:
            self._device_refresh_pending = False
            self.refresh_selected_devices()
        if after_stop is not None:
            after_stop()
        if close_after_stop:
            self._allow_window_close = True
            self.close()

    def _set_microphone_level(self, level: float) -> None:
        self.input_level.setValue(int(round(level)))

    def _show_microphone_warning(self, warning: str) -> None:
        source_name = (
            "Computer audio"
            if self._active_source == "computer_audio"
            else "Microphone"
        )
        logger.warning("%s pipeline warning: %s", source_name, warning)
        self.statusBar().showMessage(f"{source_name} warning: {warning}")

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
        split_note = (
            f" ({self.speech_chunk_duration_slider.value()}-second split)"
            if forced_split
            else ""
        )
        self.speech_turn_status.setText(
            f"Completed speech chunks: {self._completed_speech_chunks}  |  "
            f"Last turn: {duration_seconds:.1f}s{split_note}"
        )
        logger.info(
            "Temporary speech segment completed: duration=%.3fs, forced split=%s",
            duration_seconds,
            forced_split,
        )

    def _queue_caption_from_worker(self, result: TranslationResult) -> None:
        """Hand a worker result to the UI without losing it during shutdown."""
        self._pending_caption_results.put(result)
        self.queued_captions_ready.emit()

    def _drain_queued_captions(self) -> None:
        """Display all worker results currently waiting for the UI thread."""
        while True:
            try:
                result = self._pending_caption_results.get_nowait()
            except Empty:
                return
            self._show_caption(result)

    def _show_caption(self, result: TranslationResult) -> None:
        recognition = result.recognition
        created_at = local_timestamp()
        self._save_caption_to_history(result, created_at)
        if recognition.skipped or recognition.error:
            if self._showing_live_conversation:
                self.caption_chat.add_message(
                    original_text=recognition.text,
                    source_language=recognition.language,
                    speaker=self._caption_source_label,
                    timestamp=self._history_message_time(created_at),
                    warning=True,
                )
            self.statusBar().showMessage("Caption warning")
            return

        original_text = recognition.text or "[no speech recognized]"
        if self._showing_live_conversation:
            self.caption_chat.add_message(
                original_text=original_text,
                source_language=recognition.language,
                translated_text=result.translated_text,
                target_language=result.target_language,
                speaker=self._caption_source_label,
                timestamp=self._history_message_time(created_at),
                warning=bool(result.error),
            )

        if result.error:
            self.statusBar().showMessage("Translation warning")
        logger.info(
            "Caption completed: source=%s, target=%s, original characters=%d, "
            "translated characters=%d, recognition=%.3fs, translation=%.3fs",
            recognition.language or "unknown",
            result.target_language or "unavailable",
            len(recognition.text),
            len(result.translated_text or ""),
            recognition.processing_seconds,
            result.processing_seconds,
        )

    def _save_caption_to_history(
        self,
        result: TranslationResult,
        created_at: str,
    ) -> None:
        session_id = self._active_history_session_id
        if session_id is None:
            return
        recognition = result.recognition
        try:
            self._history.add_message(
                session_id=session_id,
                speaker_label=self._caption_source_label,
                start_offset_seconds=recognition.start_sample / VAD_SAMPLE_RATE,
                end_offset_seconds=recognition.end_sample / VAD_SAMPLE_RATE,
                source_language=recognition.language,
                language_confidence=recognition.language_probability,
                original_text=recognition.text or "[no speech recognized]",
                target_language=result.target_language,
                translated_text=result.translated_text,
                warning=bool(
                    recognition.skipped or recognition.error or result.error
                ),
                error_text=recognition.error or result.error,
                created_at=created_at,
            )
        except sqlite3.Error:
            logger.exception("Caption could not be saved to local history")
            self.statusBar().showMessage("Caption history save failed")
            return

        selected_key: str | int | None = (
            LIVE_ITEM_KEY
            if self._showing_live_conversation
            else self._viewing_history_session_id
        )
        self._refresh_conversation_sidebar(selected_key)

    def _update_caption_display_mode(self) -> None:
        self.caption_chat.set_display_mode(
            str(self.caption_view_selector.currentData())
        )

    def _clear_caption_screen(self) -> None:
        self.caption_chat.clear_messages()
        logger.info("Visible caption messages cleared")
        self.statusBar().showMessage("Caption screen cleared")

    def _update_performance_stats(self) -> None:
        monitor = self._microphone_monitor
        if monitor is None:
            return
        self._show_performance_report(
            monitor.performance_report,
            monitor.voice_activity_stats,
            monitor.recognition_stats,
            monitor.translation_stats,
        )

    def _show_performance_report(
        self,
        report: PerformanceReport | None,
        vad_stats: VoiceActivityStats | None = None,
        recognition_stats: RecognitionStats | None = None,
        translation_stats: TranslationStats | None = None,
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
        if recognition_stats is not None:
            summary += (
                f"\nRecognition: {recognition_stats.completed_segments} completed  |  "
                f"{recognition_stats.queued_segments} queued  |  "
                f"{recognition_stats.failed_segments} failed"
            )
        if translation_stats is not None:
            summary += (
                f"\nTranslation: {translation_stats.completed_captions} completed  |  "
                f"{translation_stats.queued_captions} queued  |  "
                f"{translation_stats.failed_captions} failed  |  "
                f"{translation_stats.processing_seconds:.2f}s total"
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
        if self._active_source == "computer_audio":
            return
        active_name = self._active_microphone_name
        if active_name is None:
            if self.microphone_source_button.isChecked():
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

    def _handle_connected_outputs_changed(self) -> None:
        """React to speaker and headphone connection changes reported by Qt."""
        if self._active_source == "microphone":
            return
        active_output_id = self._active_output_id
        if active_output_id is None:
            if self.computer_audio_source_button.isChecked() and self._outputs_loaded:
                self.refresh_connected_outputs()
            return

        try:
            connected_ids = {
                device.id for device in self._output_device_provider()
            }
        except MicrophoneCaptureError as error:
            logger.warning("Could not verify computer audio devices: %s", error)
            self.statusBar().showMessage("Computer audio device check failed")
            return
        if active_output_id not in connected_ids:
            logger.warning("Active computer audio device disconnected")
            self._device_refresh_pending = False

            def finish_disconnect() -> None:
                self.refresh_connected_outputs()
                self.device_status.setText(
                    "The selected computer audio device was disconnected. "
                    "Capture stopped."
                )
                self.statusBar().showMessage("Computer audio disconnected")

            self._stop_microphone(
                show_ready=False,
                after_stop=finish_disconnect,
            )
            return

        self._device_refresh_pending = True
        logger.info("Computer audio devices changed while capture is active")
        self.statusBar().showMessage(
            "Computer audio devices changed; the list will refresh after capture stops"
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


