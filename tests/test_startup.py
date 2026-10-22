"""Startup tests for the first PySide6 application shell."""

import os
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from app import APP_NAME, APP_VERSION
from app.audio.devices import AudioDevice
from app.audio.loopback import OutputAudioDevice
from app.audio.microphone import MicrophoneCaptureError
from app.core.performance import PerformanceReport
from app.core.state import RuntimeState
from app.history.database import HistoryDatabase
from app.main import create_application
from app.speech.recognition import RecognitionResult, RecognitionStats
from app.speech.vad import VoiceActivityStats
from app.translation.translator import TranslationResult, TranslationStats
from app.ui.history_sidebar import LIVE_ITEM_KEY
from app.ui.main_window import MainWindow
from app.ui.caption_overlay import OverlayAppearance
from app.ui.overlay_settings import MemorySettings


class FakeMicrophoneMonitor:
    def __init__(
        self,
        device_index,
        level_callback,
        warning_callback,
        speech_event_callback,
        speech_segment_callback,
        caption_callback,
        recognition_language,
        max_segment_seconds,
        speech_threshold,
        recognition_workers,
    ) -> None:
        self.device_index = device_index
        self.level_callback = level_callback
        self.warning_callback = warning_callback
        self.started = False
        self.stopped = False
        self.speech_event_callback = speech_event_callback
        self.speech_segment_callback = speech_segment_callback
        self.caption_callback = caption_callback
        self.recognition_language = recognition_language
        self.max_segment_seconds = max_segment_seconds
        self.speech_threshold = speech_threshold
        self.recognition_workers = recognition_workers
        self.performance_report = PerformanceReport(
            elapsed_seconds=65.0,
            average_cpu_percent=2.5,
            memory_start_bytes=100 * 1024 * 1024,
            memory_end_bytes=104 * 1024 * 1024,
            peak_memory_bytes=110 * 1024 * 1024,
        )
        self.voice_activity_stats = VoiceActivityStats(
            model_load_seconds=0.75,
            processed_windows=100,
            processed_audio_seconds=3.2,
            inference_seconds=0.02,
            speech_starts=2,
            speech_ends=2,
            completed_segments=2,
            filtered_short_segments=1,
        )
        self.recognition_stats = RecognitionStats(
            queued_segments=0,
            active_segments=0,
            active_elapsed_seconds=0.0,
            completed_segments=2,
            failed_segments=0,
            processing_seconds=1.2,
        )
        self.translation_stats = TranslationStats(
            queued_captions=0,
            active_captions=0,
            active_elapsed_seconds=0.0,
            completed_captions=2,
            failed_captions=0,
            processing_seconds=0.3,
        )

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    application = QApplication.instance()
    while monotonic() < deadline:
        if predicate():
            return
        if application is not None:
            application.processEvents()
        sleep(0.01)
    assert predicate()


def microphone_fixture() -> list[AudioDevice]:
    return [
        AudioDevice(
            index=12,
            name="Test Microphone",
            host_api="Windows WASAPI",
            max_input_channels=2,
            default_sample_rate=48_000.0,
            is_default=True,
        )
    ]


def output_fixture() -> list[OutputAudioDevice]:
    return [
        OutputAudioDevice(
            id="speaker-1",
            name="Test Headphones",
            is_default=True,
        )
    ]


def test_application_has_expected_metadata() -> None:
    application = create_application(["live-caption-translation-test"])

    assert isinstance(application, QApplication)
    assert application.applicationName() == APP_NAME
    assert application.applicationVersion() == APP_VERSION


def test_main_window_starts_ready() -> None:
    create_application(["live-caption-translation-test"])
    window = MainWindow(device_provider=microphone_fixture)

    assert window.windowTitle() == APP_NAME
    assert window.minimumWidth() == 900
    assert window.minimumHeight() == 640
    assert window.width() >= 1050
    assert window.height() >= 760
    assert window.statusBar().currentMessage() == "Ready"
    assert window.centralWidget() is not None
    assert window.microphone_selector.count() == 1
    assert window.microphone_selector.currentData().index == 12
    assert "Test Microphone" in window.microphone_selector.currentText()
    assert window.performance_view_selector.currentText() == "Simple stats"
    assert not window.performance_view_selector.model().item(1).isEnabled()
    assert window.performance_summary.text() == "No capture measured yet."
    assert window.language_selector.currentText() == "Automatic"
    assert window.caption_view_selector.currentText() == "Original and translation"
    assert window.speech_chunk_duration_slider.minimum() == 5
    assert window.speech_chunk_duration_slider.maximum() == 60
    assert window.speech_chunk_duration_slider.value() == 30
    assert window.speech_chunk_duration_label.text() == (
        "Maximum speech chunk: 30 seconds"
    )
    assert window.speech_sensitivity_slider.value() == 5
    assert window.speech_sensitivity_label.text() == (
        "Speech sensitivity: 5/9 (Balanced)"
    )
    assert window.whisper_workers_slider.value() == 1
    assert window.whisper_workers_label.text() == "Whisper workers: 1"
    assert window.start_stop_button.minimumHeight() == 48
    assert window.to_bottom_button.text() == "To bottom"
    assert window.overlay_toggle_button.text() == "Show overlay"
    assert window.overlay_settings_button.text() == "Overlay settings"
    assert not window.caption_overlay.isVisible()
    assert window.runtime_state is RuntimeState.IDLE

    window.show()
    QApplication.processEvents()
    button_bottom = window.start_stop_button.mapTo(
        window.microphone_group,
        QPoint(0, window.start_stop_button.height()),
    ).y()
    assert button_bottom <= window.microphone_group.contentsRect().bottom() - 12

    window.overlay_toggle_button.click()
    QApplication.processEvents()
    assert window.caption_overlay.isVisible()
    assert window.overlay_toggle_button.text() == "Hide overlay"
    window.overlay_toggle_button.click()
    assert not window.caption_overlay.isVisible()

    window.close()


def test_overlay_appearance_and_geometry_persist_between_windows() -> None:
    create_application(["live-caption-translation-test"])
    settings = MemorySettings()
    window = MainWindow(
        device_provider=microphone_fixture,
        interface_settings=settings,
    )
    expected = OverlayAppearance(
        font_size=19,
        text_colour="#aabbcc",
        background_colour="#112233",
        opacity_percent=70,
        click_through=False,
    )
    window.caption_overlay.apply_appearance(expected)
    window.caption_overlay.resize(640, 190)
    window._save_overlay_settings()
    window.close()

    restored = MainWindow(
        device_provider=microphone_fixture,
        interface_settings=settings,
    )

    assert restored.caption_overlay.appearance == expected
    assert restored.caption_overlay.size().width() == 640
    assert restored.caption_overlay.size().height() == 190
    restored.close()


def test_microphone_controls_start_update_and_stop() -> None:
    create_application(["live-caption-translation-test"])
    monitors: list[FakeMicrophoneMonitor] = []

    def monitor_factory(
        device_indices,
        level_callback,
        warning_callback,
        speech_event_callback,
        speech_segment_callback,
        caption_callback,
        recognition_language,
        max_segment_seconds,
        speech_threshold,
        recognition_workers,
    ):
        monitor = FakeMicrophoneMonitor(
            device_indices,
            level_callback,
            warning_callback,
            speech_event_callback,
            speech_segment_callback,
            caption_callback,
            recognition_language,
            max_segment_seconds,
            speech_threshold,
            recognition_workers,
        )
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=monitor_factory,
    )
    window.speech_chunk_duration_slider.setValue(45)
    window.speech_sensitivity_slider.setValue(7)
    window.whisper_workers_slider.setValue(2)
    assert window.speech_chunk_duration_label.text() == (
        "Maximum speech chunk: 45 seconds"
    )

    window.start_stop_button.click()
    assert monitors[0].device_index == (12,)
    assert monitors[0].recognition_language is None
    assert monitors[0].max_segment_seconds == 45.0
    assert monitors[0].speech_threshold == 0.35
    assert monitors[0].recognition_workers == 2
    assert monitors[0].started
    assert window.runtime_state is RuntimeState.LIVE
    assert window.start_stop_button.text() == "Stop microphone"
    assert not window.microphone_selector.isEnabled()
    assert not window.speech_chunk_duration_slider.isEnabled()
    assert not window.speech_sensitivity_slider.isEnabled()
    assert not window.whisper_workers_slider.isEnabled()
    assert window._active_history_session_id is not None
    assert window.conversation_sidebar.selected_key() == LIVE_ITEM_KEY
    assert window._history.list_sessions()[0].status == "live"

    window.microphone_level_received.emit(42.0)
    assert window.input_level.value() == 42

    window.speech_segment_received.emit(
        SimpleNamespace(duration_seconds=1.25, forced_split=False)
    )
    assert "Completed speech chunks: 1" in window.speech_turn_status.text()
    assert "Last turn: 1.2s" in window.speech_turn_status.text()

    window.caption_received.emit(
        TranslationResult(
            recognition=RecognitionResult(
                start_sample=0,
                end_sample=16_000,
                text="Bonjour le monde",
                language="fr",
                language_probability=0.99,
                processing_seconds=0.4,
            ),
            translated_text="Hello world",
            target_language="en",
            processing_seconds=0.1,
        )
    )
    assert len(window.caption_chat.messages) == 1
    assert window.caption_chat.messages[0].message_label.text() == (
        "FR: Bonjour le monde\nEN: Hello world"
    )
    assert window.caption_overlay.caption_label.text() == (
        "FR: Bonjour le monde\nEN: Hello world"
    )
    saved_messages = window._history.get_messages(
        window._active_history_session_id
    )
    assert len(saved_messages) == 1
    assert saved_messages[0].original_text == "Bonjour le monde"
    assert saved_messages[0].translated_text == "Hello world"

    window.start_stop_button.click()
    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    assert window.runtime_state is RuntimeState.IDLE
    assert window.start_stop_button.text() == "Start microphone"
    assert window.microphone_selector.isEnabled()
    assert window.speech_chunk_duration_slider.isEnabled()
    assert window.speech_sensitivity_slider.isEnabled()
    assert window.whisper_workers_slider.isEnabled()
    assert window._active_history_session_id is None
    assert window._history.list_sessions()[0].status == "completed"
    assert "Duration: 01:05" in window.performance_summary.text()
    assert "Average CPU: 2.5%" in window.performance_summary.text()
    assert "Memory: 104.0 MiB" in window.performance_summary.text()
    assert "VAD audio: 3.2s" in window.performance_summary.text()
    assert "Speech starts: 2" in window.performance_summary.text()
    assert "Completed turns: 2" in window.performance_summary.text()
    assert "Audio chunks: 2" in window.performance_summary.text()
    assert "Recognition: 2 completed" in window.performance_summary.text()
    assert "Translation: 2 completed" in window.performance_summary.text()

    window.close()


def test_caption_completed_during_stop_is_shown_and_saved() -> None:
    create_application(["live-caption-translation-test"])
    caption_emitted = Event()
    monitors: list[FakeMicrophoneMonitor] = []

    class CaptionOnStopMonitor(FakeMicrophoneMonitor):
        def stop(self) -> None:
            self.caption_callback(
                TranslationResult(
                    recognition=RecognitionResult(
                        start_sample=0,
                        end_sample=16_000,
                        text="Queued caption",
                        language="en",
                        language_probability=0.98,
                        processing_seconds=0.4,
                    ),
                    translated_text="Sous-titre en attente",
                    target_language="fr",
                    processing_seconds=0.1,
                )
            )
            self.stopped = True
            caption_emitted.set()

    def monitor_factory(*arguments):
        monitor = CaptionOnStopMonitor(*arguments)
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=monitor_factory,
    )
    window.start_stop_button.click()
    session_id = window._active_history_session_id
    assert session_id is not None

    window.start_stop_button.click()
    assert caption_emitted.wait(timeout=1.0)
    stop_thread = window._stop_thread
    assert isinstance(stop_thread, Thread)
    stop_thread.join(timeout=1.0)

    # Finish immediately, before Qt has to deliver the worker's wake-up signal.
    window._poll_microphone_stop()

    assert window._stop_thread is None
    assert len(window.caption_chat.messages) == 1
    assert "Queued caption" in window.caption_chat.messages[0].message_label.text()
    saved_messages = window._history.get_messages(session_id)
    assert len(saved_messages) == 1
    assert saved_messages[0].original_text == "Queued caption"
    assert window._history.get_session(session_id).status == "completed"
    window.close()


def test_capture_start_failure_enters_error_state() -> None:
    create_application(["live-caption-translation-test"])

    class FailingMonitor(FakeMicrophoneMonitor):
        def start(self) -> None:
            raise MicrophoneCaptureError("Test capture could not start.")

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=lambda *arguments: FailingMonitor(*arguments),
    )

    window.start_stop_button.click()

    assert window.runtime_state is RuntimeState.ERROR
    assert window._microphone_monitor is None
    assert window.statusBar().currentMessage() == "Audio capture could not start"
    assert window.device_status.text() == "Test capture could not start."
    window.close()


def test_saved_conversation_can_stay_open_while_live_caption_is_saved() -> None:
    create_application(["live-caption-translation-test"])
    history = HistoryDatabase(":memory:")
    old_session_id = history.create_session(
        title="Earlier conversation",
        audio_source="microphone",
        language_mode="automatic",
        preferred_source_language=None,
        target_language=None,
    )
    history.add_message(
        session_id=old_session_id,
        speaker_label="Microphone",
        start_offset_seconds=0.0,
        end_offset_seconds=1.0,
        source_language="en",
        language_confidence=0.9,
        original_text="Earlier message",
        target_language="fr",
        translated_text="Message précédent",
    )
    history.finish_session(old_session_id)
    monitors: list[FakeMicrophoneMonitor] = []

    def monitor_factory(*arguments):
        monitor = FakeMicrophoneMonitor(*arguments)
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=monitor_factory,
        history_database=history,
    )
    assert "Earlier message" in window.caption_chat.messages[0].message_label.text()

    window.start_stop_button.click()
    live_session_id = window._active_history_session_id
    assert live_session_id is not None
    window.conversation_sidebar.select_session(old_session_id)
    assert window._viewing_history_session_id == old_session_id

    window.caption_received.emit(
        TranslationResult(
            recognition=RecognitionResult(
                start_sample=0,
                end_sample=16_000,
                text="Live message",
                language="en",
                language_probability=0.99,
                processing_seconds=0.4,
            ),
            translated_text="Message en direct",
            target_language="fr",
            processing_seconds=0.1,
        )
    )

    assert len(history.get_messages(live_session_id)) == 1
    assert history.get_messages(live_session_id)[0].original_text == "Live message"
    assert len(window.caption_chat.messages) == 1
    assert "Earlier message" in window.caption_chat.messages[0].message_label.text()

    window.start_stop_button.click()
    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    window.close()


def test_computer_audio_source_uses_the_loopback_pipeline() -> None:
    create_application(["live-caption-translation-test"])
    monitors: list[FakeMicrophoneMonitor] = []

    def loopback_monitor_factory(
        output_device,
        level_callback,
        warning_callback,
        speech_event_callback,
        speech_segment_callback,
        caption_callback,
        recognition_language,
        max_segment_seconds,
        speech_threshold,
        recognition_workers,
    ):
        monitor = FakeMicrophoneMonitor(
            output_device,
            level_callback,
            warning_callback,
            speech_event_callback,
            speech_segment_callback,
            caption_callback,
            recognition_language,
            max_segment_seconds,
            speech_threshold,
            recognition_workers,
        )
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        output_device_provider=output_fixture,
        loopback_monitor_factory=loopback_monitor_factory,
    )
    window.computer_audio_source_button.click()

    assert window.microphone_selector.isHidden()
    assert not window.output_selector.isHidden()
    assert window.output_selector.count() == 1
    assert window.output_selector.currentData().id == "speaker-1"
    assert "Test Headphones" in window.output_selector.currentText()
    assert window.start_stop_button.text() == "Start computer audio"

    window.start_stop_button.click()
    assert monitors[0].device_index.id == "speaker-1"
    assert monitors[0].started
    assert window.start_stop_button.text() == "Stop computer audio"
    assert not window.computer_audio_source_button.isEnabled()

    window.start_stop_button.click()
    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    assert window.start_stop_button.text() == "Start computer audio"
    assert window.computer_audio_source_button.isEnabled()
    window.close()


def test_computer_audio_pipeline_warning_is_visible_and_logged(caplog) -> None:
    create_application(["live-caption-translation-test"])
    monitors: list[FakeMicrophoneMonitor] = []

    def loopback_monitor_factory(*arguments):
        monitor = FakeMicrophoneMonitor(*arguments)
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        output_device_provider=output_fixture,
        loopback_monitor_factory=loopback_monitor_factory,
    )
    window.computer_audio_source_button.click()
    window.start_stop_button.click()

    with caplog.at_level("WARNING"):
        monitors[0].warning_callback("recognition queue is full")

    assert window.statusBar().currentMessage() == (
        "Computer audio warning: recognition queue is full"
    )
    assert (
        "Computer audio pipeline warning: recognition queue is full"
        in caplog.text
    )

    window.start_stop_button.click()
    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    window.close()


def test_refresh_keeps_selected_computer_output_when_connected() -> None:
    create_application(["live-caption-translation-test"])
    headphones = output_fixture()[0]
    speakers = OutputAudioDevice(
        id="speaker-2",
        name="Test Speakers",
        is_default=False,
    )
    provider_calls = 0

    def changing_output_provider() -> list[OutputAudioDevice]:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return [headphones, speakers]
        return [speakers, headphones]

    window = MainWindow(
        device_provider=microphone_fixture,
        output_device_provider=changing_output_provider,
    )
    window.computer_audio_source_button.click()
    window.output_selector.setCurrentIndex(1)
    assert window.output_selector.currentData().id == "speaker-2"

    window.refresh_devices_button.click()

    assert window.output_selector.currentData().id == "speaker-2"
    assert window.output_selector.currentText() == "Test Speakers"
    window.close()


def test_active_computer_output_disconnect_stops_capture_and_refreshes() -> None:
    create_application(["live-caption-translation-test"])
    outputs = output_fixture()
    monitors: list[FakeMicrophoneMonitor] = []

    def connected_output_provider() -> list[OutputAudioDevice]:
        return list(outputs)

    def loopback_monitor_factory(*arguments):
        monitor = FakeMicrophoneMonitor(*arguments)
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        output_device_provider=connected_output_provider,
        loopback_monitor_factory=loopback_monitor_factory,
    )
    window.computer_audio_source_button.click()
    window.start_stop_button.click()
    assert monitors[0].started

    outputs.clear()
    window._handle_connected_outputs_changed()

    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    assert window.start_stop_button.text() == "Start computer audio"
    assert not window.start_stop_button.isEnabled()
    assert window.statusBar().currentMessage() == "Computer audio disconnected"
    assert "was disconnected" in window.device_status.text()
    window.close()


def test_refresh_button_reloads_devices_and_writes_log(caplog) -> None:
    create_application(["live-caption-translation-test"])
    first_device = microphone_fixture()[0]
    second_device = AudioDevice(
        index=20,
        name="USB Microphone",
        host_api="Windows WASAPI",
        max_input_channels=1,
        default_sample_rate=48_000.0,
    )
    provider_calls = 0

    def changing_device_provider() -> list[AudioDevice]:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return [first_device]
        return [first_device, second_device]

    window = MainWindow(device_provider=changing_device_provider)
    assert window.microphone_selector.count() == 1

    with caplog.at_level("INFO"):
        window.refresh_devices_button.click()

    assert provider_calls == 2
    assert window.microphone_selector.count() == 2
    assert window.device_status.text() == "Found 2 connected microphones."
    assert window.statusBar().currentMessage() == "Ready"
    assert "Microphone list refreshed: 2 connected device(s)" in caplog.text

    window.close()


def test_refresh_keeps_the_selected_microphone_when_it_is_still_connected() -> None:
    create_application(["live-caption-translation-test"])
    first_device = microphone_fixture()[0]
    usb_microphone = AudioDevice(
        index=20,
        name="USB Microphone",
        host_api="Windows WASAPI",
        max_input_channels=1,
        default_sample_rate=48_000.0,
    )
    provider_calls = 0

    def changing_device_provider() -> list[AudioDevice]:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return [first_device, usb_microphone]
        return [
            AudioDevice(
                index=37,
                name="USB Microphone",
                host_api="Windows WASAPI",
                max_input_channels=1,
                default_sample_rate=48_000.0,
            ),
            first_device,
        ]

    window = MainWindow(device_provider=changing_device_provider)
    window.microphone_selector.setCurrentIndex(1)
    assert window.microphone_selector.currentData().name == "USB Microphone"

    window.refresh_devices_button.click()

    selected_device = window.microphone_selector.currentData()
    assert selected_device.name == "USB Microphone"
    assert selected_device.index == 37

    window.close()


def test_active_microphone_disconnect_stops_capture_and_refreshes() -> None:
    create_application(["live-caption-translation-test"])
    connected_names = {"Test Microphone"}
    monitors: list[FakeMicrophoneMonitor] = []

    def connected_device_provider() -> list[AudioDevice]:
        return microphone_fixture() if connected_names else []

    def monitor_factory(
        device_indices,
        level_callback,
        warning_callback,
        speech_event_callback,
        speech_segment_callback,
        caption_callback,
        recognition_language,
        max_segment_seconds,
        speech_threshold,
        recognition_workers,
    ):
        monitor = FakeMicrophoneMonitor(
            device_indices,
            level_callback,
            warning_callback,
            speech_event_callback,
            speech_segment_callback,
            caption_callback,
            recognition_language,
            max_segment_seconds,
            speech_threshold,
            recognition_workers,
        )
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=connected_device_provider,
        monitor_factory=monitor_factory,
        connected_name_provider=lambda: connected_names,
    )
    window.start_stop_button.click()
    assert monitors[0].started

    connected_names.clear()
    window._handle_connected_microphones_changed()

    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    assert window.start_stop_button.text() == "Start microphone"
    assert not window.start_stop_button.isEnabled()
    assert window.statusBar().currentMessage() == "Microphone disconnected"
    assert "was disconnected" in window.device_status.text()

    window.close()


def test_slow_stop_shows_caption_progress_without_blocking_the_window() -> None:
    create_application(["live-caption-translation-test"])
    release_stop = Event()
    monitors = []

    class SlowMonitor(FakeMicrophoneMonitor):
        cancelled = False

        def __init__(self, *arguments) -> None:
            super().__init__(*arguments)
            self.recognition_stats = SimpleNamespace(
                queued_segments=2,
                active_segments=1,
                active_elapsed_seconds=0.5,
                completed_segments=2,
                failed_segments=0,
                processing_seconds=4.0,
            )

        def stop(self) -> None:
            release_stop.wait(timeout=2.0)
            self.stopped = True

        def cancel_pending_recognition(self) -> int:
            self.cancelled = True
            return 2

    def monitor_factory(*arguments):
        monitor = SlowMonitor(*arguments)
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=monitor_factory,
    )
    window.start_stop_button.click()
    window.start_stop_button.click()

    assert window.start_stop_button.text() == "Stopping…"
    assert not window.start_stop_button.isEnabled()
    wait_until(lambda: window._finishing_dialog is not None)
    dialog = window._finishing_dialog
    assert dialog is not None
    assert "3 captions remaining" in dialog.detail.text()
    assert "about 9 seconds" in dialog.detail.text()

    dialog.finish_now_button.click()
    assert monitors[0].cancelled
    assert "already in progress" in dialog.detail.text()

    release_stop.set()
    wait_until(lambda: monitors[0].stopped and window._stop_thread is None)
    assert window.start_stop_button.isEnabled()
    window.close()


def test_window_close_waits_in_background_and_closes_automatically() -> None:
    create_application(["live-caption-translation-test"])
    release_stop = Event()

    class SlowCloseMonitor(FakeMicrophoneMonitor):
        def __init__(self, *arguments) -> None:
            super().__init__(*arguments)
            self.recognition_stats = SimpleNamespace(
                queued_segments=0,
                active_segments=1,
                active_elapsed_seconds=1.0,
                completed_segments=1,
                failed_segments=0,
                processing_seconds=3.0,
            )

        def stop(self) -> None:
            release_stop.wait(timeout=2.0)
            self.stopped = True

    def monitor_factory(*arguments):
        return SlowCloseMonitor(*arguments)

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=monitor_factory,
    )
    window.show()
    window.start_stop_button.click()

    started = monotonic()
    window.close()
    assert monotonic() - started < 0.5
    assert window._stop_thread is not None
    wait_until(lambda: window._finishing_dialog is not None)
    dialog = window._finishing_dialog
    assert dialog is not None
    assert dialog.windowTitle() == "Finishing captions before closing"
    assert dialog.finish_now_button.text() == "Skip queued captions"

    release_stop.set()
    wait_until(lambda: window._stop_thread is None)
    assert not window.isVisible()


def test_idle_device_change_refreshes_automatically() -> None:
    create_application(["live-caption-translation-test"])
    devices = microphone_fixture()
    provider_calls = 0

    def changing_device_provider() -> list[AudioDevice]:
        nonlocal provider_calls
        provider_calls += 1
        return devices[:provider_calls]

    devices.append(
        AudioDevice(
            index=20,
            name="USB Microphone",
            host_api="Windows WASAPI",
            max_input_channels=1,
            default_sample_rate=48_000.0,
        )
    )
    window = MainWindow(
        device_provider=changing_device_provider,
        connected_name_provider=lambda: {device.name for device in devices},
    )
    assert window.microphone_selector.count() == 1

    window._handle_connected_microphones_changed()

    assert window.microphone_selector.count() == 2
    assert window.device_status.text() == "Found 2 connected microphones."

    window.close()


def test_main_file_can_be_loaded_directly() -> None:
    project_root = Path(__file__).resolve().parent.parent
    command = (
        "import runpy; "
        "runpy.run_path('app/main.py', run_name='direct_startup_import_test')"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", command],
        cwd=project_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


