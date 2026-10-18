"""Startup tests for the first PySide6 application shell."""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app import APP_NAME, APP_VERSION
from app.audio.devices import AudioDevice
from app.main import create_application
from app.ui.main_window import MainWindow


class FakeMicrophoneMonitor:
    def __init__(self, device_index, level_callback, warning_callback) -> None:
        self.device_index = device_index
        self.level_callback = level_callback
        self.warning_callback = warning_callback
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


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


def test_application_has_expected_metadata() -> None:
    application = create_application(["live-caption-translation-test"])

    assert isinstance(application, QApplication)
    assert application.applicationName() == APP_NAME
    assert application.applicationVersion() == APP_VERSION


def test_main_window_starts_ready() -> None:
    create_application(["live-caption-translation-test"])
    window = MainWindow(device_provider=microphone_fixture)

    assert window.windowTitle() == APP_NAME
    assert window.statusBar().currentMessage() == "Ready"
    assert window.centralWidget() is not None
    assert window.microphone_selector.count() == 1
    assert window.microphone_selector.currentData().index == 12
    assert "Test Microphone" in window.microphone_selector.currentText()

    window.close()


def test_microphone_controls_start_update_and_stop() -> None:
    create_application(["live-caption-translation-test"])
    monitors: list[FakeMicrophoneMonitor] = []

    def monitor_factory(device_indices, level_callback, warning_callback):
        monitor = FakeMicrophoneMonitor(
            device_indices,
            level_callback,
            warning_callback,
        )
        monitors.append(monitor)
        return monitor

    window = MainWindow(
        device_provider=microphone_fixture,
        monitor_factory=monitor_factory,
    )

    window.start_stop_button.click()
    assert monitors[0].device_index == (12,)
    assert monitors[0].started
    assert window.start_stop_button.text() == "Stop microphone"
    assert not window.microphone_selector.isEnabled()

    window.microphone_level_received.emit(42.0)
    assert window.input_level.value() == 42

    window.start_stop_button.click()
    assert monitors[0].stopped
    assert window.start_stop_button.text() == "Start microphone"
    assert window.microphone_selector.isEnabled()

    window.close()


def test_refresh_button_reloads_devices_and_confirms_refresh() -> None:
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

    window.refresh_devices_button.click()

    assert provider_calls == 2
    assert window.microphone_selector.count() == 2
    assert "Refreshed at" in window.device_status.text()
    assert window.statusBar().currentMessage() == "Microphone list refreshed"

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


