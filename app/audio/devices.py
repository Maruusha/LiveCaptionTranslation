"""Discover audio input devices without coupling PortAudio to the UI."""

from __future__ import annotations

from dataclasses import dataclass
import re

import sounddevice as sd


class DeviceDiscoveryError(RuntimeError):
    """Raised when the local audio system cannot be queried."""


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """A currently active Windows microphone with hidden backend aliases."""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_sample_rate: float
    is_default: bool = False
    candidate_indices: tuple[int, ...] = ()

    @property
    def display_name(self) -> str:
        default_suffix = " (Default)" if self.is_default else ""
        return f"{self.name}{default_suffix}"

    @property
    def input_indices(self) -> tuple[int, ...]:
        return self.candidate_indices or (self.index,)


def _clean_device_name(name: object) -> str:
    """Collapse driver-provided whitespace and line breaks for display."""
    return " ".join(str(name).split())


def _default_input_device_index() -> int | None:
    try:
        input_index = int(sd.default.device[0])
    except (IndexError, TypeError, ValueError):
        return None
    return input_index if input_index >= 0 else None


def _normalized_name(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", name.casefold()))


def _matches_active_endpoint(active_name: str, alias_name: str) -> bool:
    """Match a driver alias to one WASAPI endpoint without exposing aliases."""
    active_prefix = active_name.split("(", 1)[0].strip()
    normalized_prefix = _normalized_name(active_prefix)
    normalized_alias = _normalized_name(alias_name)
    if not normalized_prefix or normalized_prefix not in normalized_alias:
        return False

    if "(" not in active_name:
        return True

    adapter_text = active_name.split("(", 1)[1]
    ignored_words = {"audio", "device", "driver", "hd", "r"}
    adapter_words = {
        word
        for word in _normalized_name(adapter_text).split()
        if len(word) > 2 and word not in ignored_words
    }
    return not adapter_words or any(word in normalized_alias for word in adapter_words)


def _backend_priority(host_api: str) -> int:
    priorities = {
        "windows wasapi": 0,
        "windows wdm ks": 1,
        "windows directsound": 2,
        "mme": 3,
    }
    return priorities.get(_normalized_name(host_api), 4)


def list_input_devices() -> list[AudioDevice]:
    """Return only active Windows input endpoints, not remembered devices."""
    try:
        raw_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        default_input_index = _default_input_device_index()

        input_entries: list[dict[str, object]] = []
        for index, raw_device in enumerate(raw_devices):
            input_channels = int(raw_device["max_input_channels"])
            if input_channels <= 0:
                continue

            host_api_index = int(raw_device["hostapi"])
            host_api_name = _clean_device_name(host_apis[host_api_index]["name"])
            input_entries.append(
                {
                    "index": index,
                    "name": _clean_device_name(raw_device["name"]),
                    "host_api": host_api_name,
                    "max_input_channels": input_channels,
                    "default_sample_rate": float(raw_device["default_samplerate"]),
                }
            )

        active_entries = [
            entry
            for entry in input_entries
            if _normalized_name(str(entry["host_api"])) == "windows wasapi"
        ]

        devices: list[AudioDevice] = []
        for active_entry in active_entries:
            active_name = str(active_entry["name"])
            matching_entries = [
                entry
                for entry in input_entries
                if _matches_active_endpoint(active_name, str(entry["name"]))
            ]
            matching_entries.sort(
                key=lambda entry: (
                    _backend_priority(str(entry["host_api"])),
                    int(entry["index"]),
                )
            )
            candidate_indices = tuple(int(entry["index"]) for entry in matching_entries)

            devices.append(
                AudioDevice(
                    index=int(active_entry["index"]),
                    name=active_name,
                    host_api=str(active_entry["host_api"]),
                    max_input_channels=int(active_entry["max_input_channels"]),
                    default_sample_rate=float(active_entry["default_sample_rate"]),
                    is_default=default_input_index in candidate_indices,
                    candidate_indices=candidate_indices,
                )
            )
    except Exception as error:
        raise DeviceDiscoveryError("Windows audio devices could not be read.") from error

    return sorted(
        devices,
        key=lambda device: (
            not device.is_default,
            device.name.casefold(),
            device.index,
        ),
    )


def refresh_input_devices() -> list[AudioDevice]:
    """Restart PortAudio discovery so hot-plug changes become visible."""
    terminate = getattr(sd, "_terminate", None)
    initialize = getattr(sd, "_initialize", None)
    if not callable(terminate) or not callable(initialize):
        raise DeviceDiscoveryError("The local audio backend cannot be refreshed.")

    try:
        terminate()
        initialize()
    except Exception as error:
        raise DeviceDiscoveryError(
            "Windows audio devices could not be refreshed."
        ) from error

    return list_input_devices()

