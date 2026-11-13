"""Tests for Windows computer-output discovery and loopback capture."""

from time import monotonic, sleep
from types import SimpleNamespace

import numpy as np
import pytest

from app.audio import loopback
from app.audio.microphone import MicrophoneCaptureError


def test_output_devices_are_cleaned_deduplicated_and_default_first(
    monkeypatch,
) -> None:
    speakers = [
        SimpleNamespace(id="speaker-2", name="  Monitor\nAudio "),
        SimpleNamespace(id="speaker-1", name="Headphones"),
        SimpleNamespace(id="speaker-1", name="Headphones"),
    ]
    monkeypatch.setattr(loopback.sc, "all_speakers", lambda: speakers)
    monkeypatch.setattr(
        loopback.sc,
        "default_speaker",
        lambda: SimpleNamespace(id="speaker-1", name="Headphones"),
    )

    result = loopback.list_output_devices()

    assert [device.id for device in result] == ["speaker-1", "speaker-2"]
    assert result[0].display_name == "Headphones (Default)"
    assert result[1].name == "Monitor Audio"


def test_output_device_errors_are_explained(monkeypatch) -> None:
    monkeypatch.setattr(
        loopback.sc,
        "all_speakers",
        lambda: (_ for _ in ()).throw(RuntimeError("WASAPI unavailable")),
    )

    with pytest.raises(MicrophoneCaptureError, match="could not be read"):
        loopback.list_output_devices()


def test_loopback_capture_downmixes_stereo_into_shared_pipeline(monkeypatch) -> None:
    class FakeRecorder:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def record(self, numframes: int):
            sleep(0.005)
            left = np.full(numframes, 0.25, dtype=np.float32)
            right = np.full(numframes, 0.75, dtype=np.float32)
            return np.column_stack((left, right))

    class FakeMicrophone:
        def recorder(self, **options):
            assert options["channels"] == 2
            assert options["samplerate"] == 48_000
            return FakeRecorder()

    monkeypatch.setattr(
        loopback.sc,
        "get_microphone",
        lambda device_id, include_loopback: (
            FakeMicrophone()
            if device_id == "speaker-1" and include_loopback
            else None
        ),
    )
    levels: list[float] = []
    monitor = loopback.LoopbackCaptureMonitor(
        loopback.OutputAudioDevice("speaker-1", "Headphones", True),
        levels.append,
        lambda _warning: None,
    )

    monitor.start()
    deadline = monotonic() + 1.0
    while monotonic() < deadline and monitor.processing_worker.stats.input_frames == 0:
        sleep(0.01)
    monitor.stop()

    assert levels
    assert monitor.processing_worker.stats.input_frames > 0
    assert monitor.performance_report is not None

