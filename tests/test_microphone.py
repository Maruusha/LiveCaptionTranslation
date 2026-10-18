"""Tests for level-only microphone capture."""

import numpy as np

from app.audio import microphone


def test_level_calculation_covers_silence_and_full_scale() -> None:
    assert microphone.calculate_input_level(np.zeros((32, 1), dtype=np.float32)) == 0
    assert microphone.calculate_input_level(np.ones((32, 1), dtype=np.float32)) == 100


def test_monitor_starts_reports_level_and_stops(monkeypatch) -> None:
    levels: list[float] = []
    warnings: list[str] = []
    created_streams = []

    class FakeInputStream:
        def __init__(self, **settings) -> None:
            self.settings = settings
            self.started = False
            self.stopped = False
            self.closed = False
            created_streams.append(self)

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(microphone.sd, "InputStream", FakeInputStream)

    monitor = microphone.MicrophoneLevelMonitor(12, levels.append, warnings.append)
    monitor.start()

    stream = created_streams[0]
    assert stream.settings["device"] == 12
    assert stream.started
    assert monitor.is_running

    stream.settings["callback"](
        np.ones((32, 1), dtype=np.float32),
        32,
        None,
        "input overflow",
    )
    assert levels == [100.0]
    assert warnings == ["input overflow"]

    monitor.stop()
    assert stream.stopped
    assert stream.closed
    assert not monitor.is_running


def test_monitor_falls_back_to_another_backend(monkeypatch) -> None:
    attempted_indices: list[int] = []

    class FakeInputStream:
        def __init__(self, **settings) -> None:
            self.settings = settings

        def start(self) -> None:
            device_index = self.settings["device"]
            attempted_indices.append(device_index)
            if device_index == 12:
                raise RuntimeError("WASAPI alias unavailable")

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(microphone.sd, "InputStream", FakeInputStream)

    monitor = microphone.MicrophoneLevelMonitor((12, 17), lambda _level: None, lambda _warning: None)
    monitor.start()

    assert attempted_indices == [12, 17]
    assert monitor.active_device_index == 17

    monitor.stop()

