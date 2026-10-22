"""Bounded local speech recognition using faster-whisper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Protocol

from app.speech.vad import SpeechSegment


MODEL_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "models"
    / "speech"
    / "faster-whisper-small"
)


class RecognitionError(RuntimeError):
    """Raised when the recognition worker cannot start or stop safely."""


class WhisperLikeModel(Protocol):
    def transcribe(self, audio, **options): ...


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """Final text or a visible failure for one completed speech chunk."""

    start_sample: int
    end_sample: int
    text: str
    language: str | None
    language_probability: float | None
    processing_seconds: float
    error: str | None = None
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class RecognitionStats:
    queued_segments: int
    active_segments: int
    active_elapsed_seconds: float
    completed_segments: int
    failed_segments: int
    processing_seconds: float


def create_faster_whisper_model(cpu_threads: int = 4) -> WhisperLikeModel:
    """Load the multilingual small model strictly from the project folder."""
    if not (MODEL_DIRECTORY / "model.bin").is_file():
        raise RecognitionError(
            "The local speech-recognition model is missing from "
            f"{MODEL_DIRECTORY}."
        )

    from faster_whisper import WhisperModel

    return WhisperModel(
        str(MODEL_DIRECTORY),
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        local_files_only=True,
    )


class RecognitionWorker:
    """Transcribe final VAD chunks without blocking capture or the interface."""

    def __init__(
        self,
        result_callback: Callable[[RecognitionResult], None],
        warning_callback: Callable[[str], None],
        model_factory: Callable[[], WhisperLikeModel] | None = None,
        max_queued_segments: int = 8,
        language: str | None = None,
        worker_count: int = 1,
    ) -> None:
        if max_queued_segments <= 0:
            raise ValueError("max_queued_segments must be greater than zero.")
        if language not in {None, "en", "fr"}:
            raise ValueError("language must be None, 'en', or 'fr'.")
        if worker_count not in {1, 2}:
            raise ValueError("worker_count must be one or two.")
        self._result_callback = result_callback
        self._warning_callback = warning_callback
        self.worker_count = worker_count
        self.cpu_threads_per_worker = max(1, 4 // worker_count)
        self._model_factory = model_factory or (
            lambda: create_faster_whisper_model(self.cpu_threads_per_worker)
        )
        self._language = language
        self._queue: Queue[tuple[int, SpeechSegment]] = Queue(
            maxsize=max_queued_segments
        )
        self._stop_event = Event()
        self._threads: list[Thread] = []
        self._stats_lock = Lock()
        self._result_lock = Lock()
        self._next_sequence = 0
        self._next_result_sequence = 0
        self._pending_results: dict[int, RecognitionResult] = {}
        self._completed_segments = 0
        self._failed_segments = 0
        self._processing_seconds = 0.0
        self._active_started_at: dict[int, float] = {}

    @property
    def is_running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    @property
    def stats(self) -> RecognitionStats:
        with self._stats_lock:
            now = perf_counter()
            return RecognitionStats(
                queued_segments=self._queue.qsize(),
                active_segments=len(self._active_started_at),
                active_elapsed_seconds=sum(
                    now - started_at
                    for started_at in self._active_started_at.values()
                ),
                completed_segments=self._completed_segments,
                failed_segments=self._failed_segments,
                processing_seconds=self._processing_seconds,
            )

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._threads = [
            Thread(
                target=self._run,
                args=(worker_index,),
                name=f"speech-recognition-worker-{worker_index + 1}",
                daemon=True,
            )
            for worker_index in range(self.worker_count)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, segment: SpeechSegment) -> bool:
        """Queue one final chunk, or report a visible overload result."""
        with self._result_lock:
            sequence = self._next_sequence
            self._next_sequence += 1
        try:
            self._queue.put_nowait((sequence, segment))
            return True
        except Full:
            self._warning_callback(
                "Speech recognition is behind; one completed audio chunk was skipped."
            )
            self._complete_result(
                sequence,
                RecognitionResult(
                    start_sample=segment.start_sample,
                    end_sample=segment.end_sample,
                    text="[caption skipped: processing too slow]",
                    language=self._language,
                    language_probability=None,
                    processing_seconds=0.0,
                    skipped=True,
                ),
            )
            with self._stats_lock:
                self._failed_segments += 1
            return False

    def stop(self, timeout: float = 60.0) -> None:
        """Finish accepted chunks before stopping."""
        if not self._threads:
            return
        self._stop_event.set()
        deadline = perf_counter() + timeout
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - perf_counter()))
        if any(thread.is_alive() for thread in self._threads):
            raise RecognitionError("Speech recognition did not stop in time.")
        self._threads = []

    def cancel_pending(self) -> int:
        """Discard queued chunks while leaving an active transcription untouched."""
        cancelled = 0
        while True:
            try:
                sequence, segment = self._queue.get_nowait()
            except Empty:
                break
            cancelled += 1
            self._complete_result(
                sequence,
                RecognitionResult(
                    start_sample=segment.start_sample,
                    end_sample=segment.end_sample,
                    text="[caption skipped: application closed before processing]",
                    language=None,
                    language_probability=None,
                    processing_seconds=0.0,
                    error="The caption was cancelled while the application was closing.",
                    skipped=True,
                ),
            )
        if cancelled:
            with self._stats_lock:
                self._failed_segments += cancelled
        return cancelled

    def _run(self, worker_index: int) -> None:
        try:
            model = self._model_factory()
        except Exception as error:
            self._warning_callback(f"Speech-recognition model could not load: {error}")
            self._fail_waiting_segments("The local speech-recognition model could not load.")
            return

        while True:
            try:
                sequence, segment = self._queue.get(timeout=0.05)
            except Empty:
                if self._stop_event.is_set():
                    break
                continue

            started = perf_counter()
            with self._stats_lock:
                self._active_started_at[worker_index] = started
            try:
                parts, info = model.transcribe(
                    segment.samples,
                    task="transcribe",
                    language=self._language,
                    beam_size=1,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    word_timestamps=False,
                )
                text = " ".join(
                    part.text.strip() for part in parts if part.text.strip()
                ).strip()
                processing_seconds = perf_counter() - started
                result = RecognitionResult(
                    start_sample=segment.start_sample,
                    end_sample=segment.end_sample,
                    text=text,
                    language=self._language or getattr(info, "language", None),
                    language_probability=getattr(info, "language_probability", None),
                    processing_seconds=processing_seconds,
                )
                with self._stats_lock:
                    self._completed_segments += 1
                    self._processing_seconds += processing_seconds
            except Exception as error:
                processing_seconds = perf_counter() - started
                self._warning_callback(f"Speech recognition failed: {error}")
                result = RecognitionResult(
                    start_sample=segment.start_sample,
                    end_sample=segment.end_sample,
                    text="[caption unavailable]",
                    language=self._language,
                    language_probability=None,
                    processing_seconds=processing_seconds,
                    error=str(error),
                )
                with self._stats_lock:
                    self._failed_segments += 1
                    self._processing_seconds += processing_seconds
            finally:
                with self._stats_lock:
                    self._active_started_at.pop(worker_index, None)
            self._complete_result(sequence, result)

    def _fail_waiting_segments(self, message: str) -> None:
        while True:
            try:
                sequence, segment = self._queue.get_nowait()
            except Empty:
                return
            with self._stats_lock:
                self._failed_segments += 1
            self._complete_result(
                sequence,
                RecognitionResult(
                    start_sample=segment.start_sample,
                    end_sample=segment.end_sample,
                    text="[caption unavailable]",
                    language=self._language,
                    language_probability=None,
                    processing_seconds=0.0,
                    error=message,
                ),
            )

    def _complete_result(self, sequence: int, result: RecognitionResult) -> None:
        """Emit parallel recognition results in their original audio order."""
        with self._result_lock:
            self._pending_results[sequence] = result
            while self._next_result_sequence in self._pending_results:
                ready = self._pending_results.pop(self._next_result_sequence)
                self._next_result_sequence += 1
                self._result_callback(ready)
