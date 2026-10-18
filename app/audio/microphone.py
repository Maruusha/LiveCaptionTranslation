"""Live microphone monitoring without recording or retaining audio."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import sounddevice as sd

from app.core.queues import BoundedAudioFrameQueue
from app.audio.processing import AudioProcessingError, AudioProcessingWorker


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
        audio_buffer: BoundedAudioFrameQueue | None = None,
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
        self.audio_buffer = audio_buffer or BoundedAudioFrameQueue(
            max_seconds=5.0,
            max_frames=2_048,
        )
        self.processed_audio_buffer = BoundedAudioFrameQueue(
            max_seconds=5.0,
            max_frames=2_048,
        )
        self.processing_worker = AudioProcessingWorker(
            source_queue=self.audio_buffer,
            output_queue=self.processed_audio_buffer,
            warning_callback=self._warning_callback,
            output_sample_rate=16_000,
        )
        self._stream: sd.InputStream | None = None
        self._active_sample_rate: int | None = None

    @property
    def is_running(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """Open the selected microphone and begin level callbacks."""
        if self.is_running:
            return

        self.processing_worker.start()
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
                self._active_sample_rate = int(round(float(stream.samplerate)))
                stream.start()
            except Exception as error:
                last_error = error
                self._active_sample_rate = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue

            self._stream = stream
            self.active_device_index = device_index
            return

        self.processing_worker.stop()
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
        self._active_sample_rate = None
        if stream is None:
            return

        stop_error: Exception | None = None
        try:
            stream.stop()
        except Exception as error:
            stop_error = error
        finally:
            stream.close()

        try:
            self.processing_worker.stop()
        except AudioProcessingError as error:
            stop_error = stop_error or error

        if stop_error is not None:
            raise MicrophoneCaptureError(
                "The microphone did not stop cleanly."
            ) from stop_error

    def _handle_audio(self, input_data, _frames, _time_info, status) -> None:
        if status:
            self._warning_callback(str(status))

        level = calculate_input_level(input_data)
        self._level_callback(float(level))

        sample_rate = self._active_sample_rate
        if sample_rate is None:
            return

        samples = np.asarray(input_data, dtype=np.float32)
        mono_samples = samples[:, 0] if samples.ndim == 2 else samples
        self.audio_buffer.put(mono_samples, sample_rate=sample_rate)


