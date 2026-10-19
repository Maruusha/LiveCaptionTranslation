"""Tests for local microphone discovery."""

import pytest

from app.audio import devices


def test_input_devices_are_cleaned_filtered_and_default_first(monkeypatch) -> None:
    raw_devices = [
        {
            "name": "Speakers",
            "hostapi": 0,
            "max_input_channels": 0,
            "default_samplerate": 48_000.0,
        },
        {
            "name": "Default Microphone legacy",
            "hostapi": 0,
            "max_input_channels": 1,
            "default_samplerate": 44_100.0,
        },
        {
            "name": "Disconnected Headset",
            "hostapi": 1,
            "max_input_channels": 1,
            "default_samplerate": 48_000.0,
        },
        {
            "name": "Default\n Microphone",
            "hostapi": 2,
            "max_input_channels": 2,
            "default_samplerate": 48_000.0,
        },
        {
            "name": "Default Microphone low-level",
            "hostapi": 1,
            "max_input_channels": 2,
            "default_samplerate": 48_000.0,
        },
    ]
    host_apis = [
        {"name": "MME"},
        {"name": "Windows WDM-KS"},
        {"name": "Windows WASAPI"},
    ]

    monkeypatch.setattr(devices.sd, "query_devices", lambda: raw_devices)
    monkeypatch.setattr(devices.sd, "query_hostapis", lambda: host_apis)
    monkeypatch.setattr(devices, "_default_input_device_index", lambda: 1)

    result = devices.list_input_devices()

    assert [device.index for device in result] == [3]
    assert result[0].name == "Default Microphone"
    assert result[0].display_name == "Default Microphone (Default)"
    assert result[0].candidate_indices == (3, 4, 1)


def test_audio_driver_errors_are_wrapped(monkeypatch) -> None:
    def fail_to_query_devices():
        raise RuntimeError("driver unavailable")

    monkeypatch.setattr(devices.sd, "query_devices", fail_to_query_devices)

    with pytest.raises(devices.DeviceDiscoveryError):
        devices.list_input_devices()


def test_refresh_restarts_portaudio_before_listing(monkeypatch) -> None:
    calls: list[str] = []
    expected_devices = [object()]

    monkeypatch.setattr(devices.sd, "_terminate", lambda: calls.append("terminate"))
    monkeypatch.setattr(devices.sd, "_initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(devices, "list_input_devices", lambda: expected_devices)

    result = devices.refresh_input_devices()

    assert calls == ["terminate", "initialize"]
    assert result == expected_devices

