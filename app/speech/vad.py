"""Streaming voice activity detection for 16 kHz mono audio."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Literal, Protocol

import numpy as np

from app.core.queues import BoundedAudioFrameQueue


VAD_SAMPLE_RATE = 16_000
VAD_WINDOW_SAMPLES = 512
DEFAULT_MAX_SEGMENT_SECONDS = 30.0
DEFAULT_MIN_SEGMENT_SECONDS = 0.2


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
class SpeechSegment:
    """One completed in-memory speech chunk ready for later recognition."""

    samples: np.ndarray
    start_sample: int
    end_sample: int
    forced_split: bool = False

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / VAD_SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class VoiceActivityStats:
    """Small, safe-to-display measurements from the VAD worker."""

    model_load_seconds: float
    processed_windows: int
    processed_audio_seconds: float
    inference_seconds: float
    speech_starts: int
    speech_ends: int
    completed_segments: int
    filtered_short_segments: int

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


def create_silero_detector(threshold: float = 0.5) -> StreamingDetector:
    """Load the package-bundled Silero ONNX model without network access."""
    import torch
    from silero_vad import VADIterator, load_silero_vad

    torch.set_num_threads(1)
    model = load_silero_vad(onnx=True)
    iterator = VADIterator(
        model,
        threshold=threshold,
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
        segment_callback: Callable[[SpeechSegment], None] | None = None,
        detector_factory: Callable[[], StreamingDetector] | None = None,
        max_segment_seconds: float = DEFAULT_MAX_SEGMENT_SECONDS,
        min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
        speech_threshold: float = 0.5,
    ) -> None:
        if max_segment_seconds <= 0.0:
            raise ValueError("max_segment_seconds must be greater than zero.")
        if min_segment_seconds < 0.0:
            raise ValueError("min_segment_seconds cannot be negative.")
        if not 0.0 < speech_threshold < 1.0:
            raise ValueError("speech_threshold must be between zero and one.")
        self.source_queue = source_queue
        self._event_callback = event_callback
        self._warning_callback = warning_callback
        self._segment_callback = segment_callback
        self.speech_threshold = float(speech_threshold)
        self._detector_factory = detector_factory or (
            lambda: create_silero_detector(self.speech_threshold)
        )
        self._max_segment_samples = max(
            VAD_WINDOW_SAMPLES,
            int(round(max_segment_seconds * VAD_SAMPLE_RATE)),
        )
        self._min_segment_samples = int(round(min_segment_seconds * VAD_SAMPLE_RATE))
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stats_lock = Lock()
        self._model_load_seconds = 0.0
        self._processed_windows = 0
        self._inference_seconds = 0.0
        self._speech_starts = 0
        self._speech_ends = 0
        self._completed_segments = 0
        self._filtered_short_segments = 0
        self._next_sample = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def max_segment_seconds(self) -> float:
        return self._max_segment_samples / VAD_SAMPLE_RATE

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
                completed_segments=self._completed_segments,
                filtered_short_segments=self._filtered_short_segments,
            )

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
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
            with self._stats_lock:
                run_start_sample = self._next_sample
            model_load_started = perf_counter()
            detector = self._detector_factory()
            with self._stats_lock:
                self._model_load_seconds += perf_counter() - model_load_started
            pending = np.empty(0, dtype=np.float32)
            total_samples = run_start_sample
            speech_active = False
            history: deque[tuple[int, np.ndarray]] = deque(maxlen=64)
            segment_parts: list[np.ndarray] = []
            segment_start = 0
            segment_sample_count = 0

            while True:
                frame = self.source_queue.get(timeout=0.05)
                if frame is None:
                    if self._stop_event.is_set():
                        break
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
                    window_start = total_samples
                    history.append((window_start, window))
                    if speech_active:
                        segment_parts.append(window)
                        segment_sample_count += window.size
                    inference_started = perf_counter()
                    result = detector(window)
                    inference_seconds = perf_counter() - inference_started
                    with self._stats_lock:
                        self._processed_windows += 1
                        self._inference_seconds += inference_seconds
                    total_samples += VAD_WINDOW_SAMPLES
                    with self._stats_lock:
                        self._next_sample = total_samples
                    if result and "start" in result:
                        speech_active = True
                        segment_start = run_start_sample + int(result["start"])
                        segment_parts = self._history_from(history, segment_start)
                        segment_sample_count = sum(
                            part.size for part in segment_parts
                        )
                        with self._stats_lock:
                            self._speech_starts += 1
                        self._event_callback(
                            SpeechEvent("speech_start", segment_start)
                        )
                    elif result and "end" in result:
                        speech_active = False
                        segment_end = run_start_sample + int(result["end"])
                        with self._stats_lock:
                            self._speech_ends += 1
                        self._event_callback(
                            SpeechEvent("speech_end", segment_end)
                        )
                        self._emit_segment(
                            segment_parts,
                            segment_start,
                            segment_end,
                            forced_split=False,
                        )
                        segment_parts = []
                        segment_sample_count = 0

                    while (
                        speech_active
                        and segment_sample_count >= self._max_segment_samples
                    ):
                        combined = np.concatenate(segment_parts)
                        split_end = segment_start + self._max_segment_samples
                        self._emit_segment(
                            [combined[: self._max_segment_samples]],
                            segment_start,
                            split_end,
                            forced_split=True,
                        )
                        remainder = combined[self._max_segment_samples :].copy()
                        segment_parts = [remainder] if remainder.size else []
                        segment_sample_count = remainder.size
                        segment_start = split_end

            if speech_active:
                if pending.size:
                    segment_parts.append(pending.copy())
                    total_samples += pending.size
                    with self._stats_lock:
                        self._next_sample = total_samples
                with self._stats_lock:
                    self._speech_ends += 1
                self._event_callback(SpeechEvent("speech_end", total_samples))
                self._emit_segment(
                    segment_parts,
                    segment_start,
                    total_samples,
                    forced_split=False,
                )
        except Exception as error:
            self._warning_callback(f"Voice activity detection stopped: {error}")

    @staticmethod
    def _history_from(
        history: deque[tuple[int, np.ndarray]],
        start_sample: int,
    ) -> list[np.ndarray]:
        parts: list[np.ndarray] = []
        for part_start, samples in history:
            part_end = part_start + samples.size
            if part_end <= start_sample:
                continue
            offset = max(0, start_sample - part_start)
            parts.append(samples[offset:])
        return parts

    def _emit_segment(
        self,
        parts: list[np.ndarray],
        start_sample: int,
        end_sample: int,
        forced_split: bool,
    ) -> None:
        if self._segment_callback is None or not parts or end_sample <= start_sample:
            return
        samples = np.concatenate(parts)
        wanted_samples = min(samples.size, end_sample - start_sample)
        if wanted_samples <= 0:
            return
        if not forced_split and wanted_samples < self._min_segment_samples:
            with self._stats_lock:
                self._filtered_short_segments += 1
            return
        owned_samples = np.ascontiguousarray(samples[:wanted_samples], dtype=np.float32)
        owned_samples.setflags(write=False)
        segment = SpeechSegment(
            samples=owned_samples,
            start_sample=start_sample,
            end_sample=start_sample + owned_samples.size,
            forced_split=forced_split,
        )
        with self._stats_lock:
            self._completed_segments += 1
        self._segment_callback(segment)
