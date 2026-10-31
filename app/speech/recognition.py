"""Bounded local speech recognition using faster-whisper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Protocol

from app.speech.vad import SpeechSegment
from app.core.paths import models_directory


MODEL_DIRECTORY = models_directory() / "speech" / "faster-whisper-small"


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
    ) -> None:
        if max_queued_segments <= 0:
            raise ValueError("max_queued_segments must be greater than zero.")
        if language not in {None, "en", "fr"}:
            raise ValueError("language must be None, 'en', or 'fr'.")
        self._result_callback = result_callback
        self._warning_callback = warning_callback
        self._model_factory = model_factory or create_faster_whisper_model
        self._language = language
        self._queue: Queue[tuple[int, SpeechSegment]] = Queue(
            maxsize=max_queued_segments
        )
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stats_lock = Lock()
        self._result_lock = Lock()
        self._next_sequence = 0
        self._next_result_sequence = 0
        self._pending_results: dict[int, RecognitionResult] = {}
        self._completed_segments = 0
        self._failed_segments = 0
        self._processing_seconds = 0.0
        self._active_started_at: float | None = None
        self._model_error: str | None = None
        self._cancel_requested = False

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def stats(self) -> RecognitionStats:
        with self._stats_lock:
            active_started_at = self._active_started_at
            return RecognitionStats(
                queued_segments=self._queue.qsize(),
                active_segments=int(active_started_at is not None),
                active_elapsed_seconds=(
                    perf_counter() - active_started_at
                    if active_started_at is not None
                    else 0.0
                ),
                completed_segments=self._completed_segments,
                failed_segments=self._failed_segments,
                processing_seconds=self._processing_seconds,
            )

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        with self._stats_lock:
            self._model_error = None
            self._cancel_requested = False
        self._thread = Thread(
            target=self._run,
            name="speech-recognition-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, segment: SpeechSegment) -> bool:
        """Queue one final chunk, or report a visible overload result."""
        with self._result_lock:
            sequence = self._next_sequence
            self._next_sequence += 1
        with self._stats_lock:
            model_error = self._model_error
            cancelled = self._cancel_requested
            try:
                if model_error is None and not cancelled:
                    self._queue.put_nowait((sequence, segment))
                    return True
            except Full:
                pass
            self._failed_segments += 1
        if cancelled:
            self._complete_result(sequence, self._cancelled_result(segment))
        elif model_error is not None:
            self._complete_result(
                sequence,
                RecognitionResult(
                    segment.start_sample, segment.end_sample,
                    "[caption unavailable]", self._language, None, 0.0,
                    error=model_error,
                ),
            )
        else:
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
        return False

    def stop(self, timeout: float | None = 60.0) -> None:
        """Finish accepted chunks before stopping."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise RecognitionError("Speech recognition did not stop in time.")
        self._thread = None

    def cancel_pending(self) -> int:
        """Discard queued chunks while leaving an active transcription untouched."""
        with self._stats_lock:
            self._cancel_requested = True
        cancelled = 0
        while True:
            try:
                sequence, segment = self._queue.get_nowait()
            except Empty:
                break
            cancelled += 1
            self._complete_result(
                sequence,
                self._cancelled_result(segment),
            )
        if cancelled:
            with self._stats_lock:
                self._failed_segments += cancelled
        return cancelled

    @staticmethod
    def _cancelled_result(segment: SpeechSegment) -> RecognitionResult:
        return RecognitionResult(
            segment.start_sample, segment.end_sample,
            "[caption skipped: queued processing cancelled]", None, None, 0.0,
            error="Queued caption processing was cancelled by the user.",
            skipped=True,
        )

    def _run(self) -> None:
        try:
            model = self._model_factory()
        except Exception as error:
            with self._stats_lock:
                self._model_error = "The local speech-recognition model could not load."
            self._warning_callback(f"Speech-recognition model could not load: {error}")
            self._fail_waiting_segments(self._model_error)
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
                self._active_started_at = started
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
                    self._active_started_at = None
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
        """Emit results in audio order, including immediate queue skips."""
        with self._result_lock:
            self._pending_results[sequence] = result
            while self._next_result_sequence in self._pending_results:
                ready = self._pending_results.pop(self._next_result_sequence)
                self._next_result_sequence += 1
                self._result_callback(ready)
