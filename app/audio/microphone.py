"""Live microphone monitoring without recording or retaining audio."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import sounddevice as sd


class MicrophoneCaptureError(RuntimeError):
    """Raised when a microphone stream cannot start or stop safely."""


def calculate_input_level(samples: np.ndarray) -> int:
    """Convert float audio samples to a 0-100 meter value using dBFS."""
    if samples.size == 0:
        return 0

    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    decibels = 20.0 * np.log10(max(rms, 1e-6))
    return int(round(np.clip((decibels + 60.0) / 60.0 * 100.0, 0.0, 100.0)))


class MicrophoneLevelMonitor:
    """Read a microphone only long enough to calculate its current level."""

    def __init__(
        self,
        device_indices: int | tuple[int, ...],
        level_callback: Callable[[float], None],
        warning_callback: Callable[[str], None],
    ) -> None:
        self.device_indices = (
            (device_indices,) if isinstance(device_indices, int) else device_indices
        )
        if not self.device_indices:
            raise ValueError("At least one microphone backend index is required.")

        self.device_index = self.device_indices[0]
        self.active_device_index: int | None = None
        self._level_callback = level_callback
        self._warning_callback = warning_callback
        self._stream: sd.InputStream | None = None

    @property
    def is_running(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """Open the selected microphone and begin level callbacks."""
        if self.is_running:
            return

        last_error: Exception | None = None
        for device_index in self.device_indices:
            stream: sd.InputStream | None = None
            try:
                stream = sd.InputStream(
                    device=device_index,
                    channels=1,
                    dtype="float32",
                    latency="low",
                    callback=self._handle_audio,
                )
                stream.start()
            except Exception as error:
                last_error = error
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue

            self._stream = stream
            self.active_device_index = device_index
            return

        raise MicrophoneCaptureError(
            "The selected microphone could not be started through any available "
            "Windows audio backend. Check its connection, Windows microphone "
            "privacy settings, and whether another application is using it."
        ) from last_error

    def stop(self) -> None:
        """Stop and release the microphone stream."""
        stream = self._stream
        self._stream = None
        self.active_device_index = None
        if stream is None:
            return

        try:
            stream.stop()
        except Exception as error:
            raise MicrophoneCaptureError(
                "The microphone did not stop cleanly."
            ) from error
        finally:
            stream.close()

    def _handle_audio(self, input_data, _frames, _time_info, status) -> None:
        if status:
            self._warning_callback(str(status))

        level = calculate_input_level(input_data)
        self._level_callback(float(level))

