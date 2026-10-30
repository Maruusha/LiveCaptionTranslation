"""Background conversion from native microphone audio to 16 kHz mono."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread

import numpy as np
import soxr

from app.core.queues import AudioFrame, BoundedAudioFrameQueue


class AudioProcessingError(RuntimeError):
    """Raised when the background audio worker cannot stop safely."""


@dataclass(frozen=True, slots=True)
class AudioProcessingStats:
    input_frames: int
    input_seconds: float
    output_frames: int
    output_seconds: float


class AudioProcessingWorker:
    """Drain native audio and produce temporary 16 kHz mono audio."""

    def __init__(
        self,
        source_queue: BoundedAudioFrameQueue,
        output_queue: BoundedAudioFrameQueue,
        warning_callback: Callable[[str], None],
        output_sample_rate: int = 16_000,
    ) -> None:
        if output_sample_rate <= 0:
            raise ValueError("output_sample_rate must be greater than zero.")

        self.source_queue = source_queue
        self.output_queue = output_queue
        self.output_sample_rate = output_sample_rate
        self._warning_callback = warning_callback
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stats_lock = Lock()
        self._input_frames = 0
        self._input_seconds = 0.0
        self._output_frames = 0
        self._output_seconds = 0.0
        self._error: Exception | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return

        self._stop_event.clear()
        self._error = None
        self._thread = Thread(
            target=self._run,
            name="audio-processing-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = 3.0) -> None:
        """Drain already accepted input, flush resampling, and stop."""
        thread = self._thread
        if thread is None:
            return

        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise AudioProcessingError("The audio processing worker did not stop.")
        self._thread = None
        if self._error is not None:
            raise AudioProcessingError("Audio processing failed.") from self._error

    @property
    def stats(self) -> AudioProcessingStats:
        with self._stats_lock:
            return AudioProcessingStats(
                input_frames=self._input_frames,
                input_seconds=self._input_seconds,
                output_frames=self._output_frames,
                output_seconds=self._output_seconds,
            )

    def _run(self) -> None:
        resampler: soxr.ResampleStream | None = None
        input_sample_rate: int | None = None
        last_capture_time: float | None = None

        try:
            while True:
                frame = self.source_queue.get(timeout=0.05)
                if frame is None:
                    if self._stop_event.is_set():
                        break
                    continue

                if frame.sample_rate != input_sample_rate:
                    if resampler is not None and last_capture_time is not None:
                        self._emit_output(
                            resampler.resample_chunk(
                                np.empty(0, dtype=np.float32),
                                last=True,
                            ),
                            last_capture_time,
                        )
                    input_sample_rate = frame.sample_rate
                    resampler = self._create_resampler(input_sample_rate)

                self._record_input(frame)
                last_capture_time = frame.captured_at
                if resampler is None:
                    self._emit_output(frame.samples, frame.captured_at)
                else:
                    self._emit_output(
                        resampler.resample_chunk(frame.samples),
                        frame.captured_at,
                    )

            if resampler is not None and last_capture_time is not None:
                self._emit_output(
                    resampler.resample_chunk(
                        np.empty(0, dtype=np.float32),
                        last=True,
                    ),
                    last_capture_time,
                )
        except Exception as error:
            self._error = error
            self._warning_callback(f"Audio processing stopped: {error}")

    def _create_resampler(
        self,
        input_sample_rate: int,
    ) -> soxr.ResampleStream | None:
        if input_sample_rate == self.output_sample_rate:
            return None
        return soxr.ResampleStream(
            input_sample_rate,
            self.output_sample_rate,
            num_channels=1,
            dtype="float32",
            quality="HQ",
        )

    def _record_input(self, frame: AudioFrame) -> None:
        with self._stats_lock:
            self._input_frames += 1
            self._input_seconds += frame.duration_seconds

    def _emit_output(self, samples: np.ndarray, captured_at: float) -> None:
        if samples.size == 0:
            return

        output = np.asarray(samples, dtype=np.float32).reshape(-1)
        self.output_queue.put(
            output,
            sample_rate=self.output_sample_rate,
            captured_at=captured_at,
        )
        with self._stats_lock:
            self._output_frames += 1
            self._output_seconds += output.size / self.output_sample_rate

