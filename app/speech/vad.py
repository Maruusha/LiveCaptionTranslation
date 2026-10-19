"""Streaming voice activity detection for 16 kHz mono audio."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Literal, Protocol

import numpy as np

from app.core.queues import BoundedAudioFrameQueue


VAD_SAMPLE_RATE = 16_000
VAD_WINDOW_SAMPLES = 512


class VoiceActivityError(RuntimeError):
    """Raised when the VAD worker cannot stop safely."""


@dataclass(frozen=True, slots=True)
class SpeechEvent:
    """A speech boundary located on the processed audio timeline."""

    kind: Literal["speech_start", "speech_end"]
    sample_index: int

    @property
    def audio_seconds(self) -> float:
        return self.sample_index / VAD_SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class VoiceActivityStats:
    """Small, safe-to-display measurements from the VAD worker."""

    model_load_seconds: float
    processed_windows: int
    processed_audio_seconds: float
    inference_seconds: float
    speech_starts: int
    speech_ends: int

    @property
    def average_inference_milliseconds(self) -> float:
        if self.processed_windows == 0:
            return 0.0
        return self.inference_seconds / self.processed_windows * 1_000.0

    @property
    def realtime_percent(self) -> float:
        if self.processed_audio_seconds == 0.0:
            return 0.0
        return self.inference_seconds / self.processed_audio_seconds * 100.0


class StreamingDetector(Protocol):
    def __call__(self, samples: np.ndarray) -> dict[str, int] | None: ...


def create_silero_detector() -> StreamingDetector:
    """Load the package-bundled Silero ONNX model without network access."""
    import torch
    from silero_vad import VADIterator, load_silero_vad

    torch.set_num_threads(1)
    model = load_silero_vad(onnx=True)
    iterator = VADIterator(
        model,
        threshold=0.5,
        sampling_rate=VAD_SAMPLE_RATE,
        min_silence_duration_ms=400,
        speech_pad_ms=100,
    )

    def detect(samples: np.ndarray) -> dict[str, int] | None:
        return iterator(torch.from_numpy(samples))

    return detect


class VoiceActivityWorker:
    """Consume converted audio and emit speech start/end events."""

    def __init__(
        self,
        source_queue: BoundedAudioFrameQueue,
        event_callback: Callable[[SpeechEvent], None],
        warning_callback: Callable[[str], None],
        detector_factory: Callable[[], StreamingDetector] = create_silero_detector,
    ) -> None:
        self.source_queue = source_queue
        self._event_callback = event_callback
        self._warning_callback = warning_callback
        self._detector_factory = detector_factory
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stats_lock = Lock()
        self._model_load_seconds = 0.0
        self._processed_windows = 0
        self._inference_seconds = 0.0
        self._speech_starts = 0
        self._speech_ends = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> VoiceActivityStats:
        with self._stats_lock:
            processed_audio_seconds = (
                self._processed_windows * VAD_WINDOW_SAMPLES / VAD_SAMPLE_RATE
            )
            return VoiceActivityStats(
                model_load_seconds=self._model_load_seconds,
                processed_windows=self._processed_windows,
                processed_audio_seconds=processed_audio_seconds,
                inference_seconds=self._inference_seconds,
                speech_starts=self._speech_starts,
                speech_ends=self._speech_ends,
            )

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        with self._stats_lock:
            self._model_load_seconds = 0.0
            self._processed_windows = 0
            self._inference_seconds = 0.0
            self._speech_starts = 0
            self._speech_ends = 0
        self._thread = Thread(
            target=self._run,
            name="voice-activity-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise VoiceActivityError("Voice activity detection did not stop.")
        self._thread = None

    def _run(self) -> None:
        try:
            model_load_started = perf_counter()
            detector = self._detector_factory()
            with self._stats_lock:
                self._model_load_seconds = perf_counter() - model_load_started
            pending = np.empty(0, dtype=np.float32)
            total_samples = 0
            speech_active = False

            while True:
                frame = self.source_queue.get_nowait()
                if frame is None:
                    if self._stop_event.is_set():
                        break
                    self._stop_event.wait(0.005)
                    continue

                if frame.sample_rate != VAD_SAMPLE_RATE:
                    self._warning_callback(
                        f"VAD skipped audio at {frame.sample_rate} Hz; expected "
                        f"{VAD_SAMPLE_RATE} Hz."
                    )
                    continue

                pending = np.concatenate((pending, frame.samples))
                while pending.size >= VAD_WINDOW_SAMPLES:
                    window = np.ascontiguousarray(
                        pending[:VAD_WINDOW_SAMPLES],
                        dtype=np.float32,
                    )
                    pending = pending[VAD_WINDOW_SAMPLES:]
                    inference_started = perf_counter()
                    result = detector(window)
                    inference_seconds = perf_counter() - inference_started
                    with self._stats_lock:
                        self._processed_windows += 1
                        self._inference_seconds += inference_seconds
                    total_samples += VAD_WINDOW_SAMPLES
                    if not result:
                        continue
                    if "start" in result:
                        speech_active = True
                        with self._stats_lock:
                            self._speech_starts += 1
                        self._event_callback(
                            SpeechEvent("speech_start", int(result["start"]))
                        )
                    elif "end" in result:
                        speech_active = False
                        with self._stats_lock:
                            self._speech_ends += 1
                        self._event_callback(
                            SpeechEvent("speech_end", int(result["end"]))
                        )

            if speech_active:
                with self._stats_lock:
                    self._speech_ends += 1
                self._event_callback(SpeechEvent("speech_end", total_samples))
        except Exception as error:
            self._warning_callback(f"Voice activity detection stopped: {error}")
