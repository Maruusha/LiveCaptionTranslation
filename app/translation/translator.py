"""Bounded local French and English translation using CTranslate2."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Protocol

from app.speech.recognition import RecognitionResult
from app.speakers.identification import SpeakerIdentification
from app.core.paths import models_directory


MODEL_ROOT = models_directory() / "translation"
SUPPORTED_LANGUAGES = {"en", "fr"}
logger = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    """Raised when the translation worker cannot stop safely."""


class TextTranslationModel(Protocol):
    def translate(self, text: str) -> str: ...


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """One final recognition result and its optional translation."""

    recognition: RecognitionResult
    translated_text: str | None
    target_language: str | None
    processing_seconds: float
    error: str | None = None
    skipped: bool = False
    speaker: SpeakerIdentification | None = None


@dataclass(frozen=True, slots=True)
class TranslationStats:
    queued_captions: int
    active_captions: int
    active_elapsed_seconds: float
    completed_captions: int
    unsupported_captions: int
    failed_captions: int
    processing_seconds: float


class CTranslate2TranslationModel:
    """Tokenize and translate with one local OPUS-MT model."""

    def __init__(self, model_directory: Path) -> None:
        import ctranslate2
        import sentencepiece

        self._source_tokenizer = sentencepiece.SentencePieceProcessor(
            model_file=str(model_directory / "source.spm")
        )
        self._target_tokenizer = sentencepiece.SentencePieceProcessor(
            model_file=str(model_directory / "target.spm")
        )
        self._translator = ctranslate2.Translator(
            str(model_directory),
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=4,
        )

    def translate(self, text: str) -> str:
        source_tokens = [
            *self._source_tokenizer.encode(text, out_type=str),
            "</s>",
        ]
        result = self._translator.translate_batch(
            [source_tokens],
            beam_size=1,
            max_decoding_length=256,
        )[0]
        target_tokens = [
            token
            for token in result.hypotheses[0]
            if token not in {"</s>", "<pad>"}
        ]
        return self._target_tokenizer.decode(target_tokens).strip()


def create_translation_model(
    source_language: str,
    target_language: str,
) -> TextTranslationModel:
    """Load one translation direction strictly from the project folder."""
    if {source_language, target_language} != SUPPORTED_LANGUAGES:
        raise TranslationError(
            f"Unsupported translation direction: {source_language} to "
            f"{target_language}."
        )

    model_directory = (
        MODEL_ROOT
        / f"opus-mt-{source_language}-{target_language}-ct2-int8"
    )
    required_files = ("config.json", "model.bin", "source.spm", "target.spm")
    missing_files = [
        filename
        for filename in required_files
        if not (model_directory / filename).is_file()
    ]
    if missing_files:
        raise TranslationError(
            f"The local {source_language}-to-{target_language} translation "
            f"model is incomplete in {model_directory}."
        )
    return CTranslate2TranslationModel(model_directory)


class TranslationWorker:
    """Translate final captions without blocking recognition or the interface."""

    def __init__(
        self,
        result_callback: Callable[[TranslationResult], None],
        warning_callback: Callable[[str], None],
        model_factory: Callable[
            [str, str], TextTranslationModel
        ] = create_translation_model,
        max_queued_captions: int = 8,
    ) -> None:
        if max_queued_captions <= 0:
            raise ValueError("max_queued_captions must be greater than zero.")
        self._result_callback = result_callback
        self._warning_callback = warning_callback
        self._model_factory = model_factory
        self._queue: Queue[
            tuple[int, RecognitionResult, SpeakerIdentification | None]
        ] = Queue(maxsize=max_queued_captions)
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stats_lock = Lock()
        self._result_lock = Lock()
        self._next_sequence = 0
        self._next_result_sequence = 0
        self._pending_results: dict[int, TranslationResult] = {}
        self._completed_captions = 0
        self._unsupported_captions = 0
        self._failed_captions = 0
        self._processing_seconds = 0.0
        self._active_started_at: float | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> TranslationStats:
        with self._stats_lock:
            active_started_at = self._active_started_at
            return TranslationStats(
                queued_captions=self._queue.qsize(),
                active_captions=int(active_started_at is not None),
                active_elapsed_seconds=(
                    perf_counter() - active_started_at
                    if active_started_at is not None
                    else 0.0
                ),
                completed_captions=self._completed_captions,
                unsupported_captions=self._unsupported_captions,
                failed_captions=self._failed_captions,
                processing_seconds=self._processing_seconds,
            )

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            name="caption-translation-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        recognition: RecognitionResult,
        speaker: SpeakerIdentification | None = None,
    ) -> bool:
        """Queue a final caption, preserving captions that cannot be translated."""
        with self._result_lock:
            sequence = self._next_sequence
            self._next_sequence += 1
        if recognition.skipped or recognition.error or not recognition.text.strip():
            self._complete_result(
                sequence,
                TranslationResult(recognition, None, None, 0.0, speaker=speaker)
            )
            return False

        source_language = self._normalize_language(recognition.language)
        if source_language not in SUPPORTED_LANGUAGES:
            with self._stats_lock:
                self._unsupported_captions += 1
            logger.info(
                "Translation skipped for unsupported detected language: %s",
                source_language or "unknown",
            )
            self._complete_result(
                sequence,
                TranslationResult(
                    recognition,
                    None,
                    None,
                    0.0,
                    skipped=True,
                    speaker=speaker,
                )
            )
            return False

        try:
            self._queue.put_nowait((sequence, recognition, speaker))
            return True
        except Full:
            message = "Translation is behind; one caption was not translated."
            self._warning_callback(message)
            with self._stats_lock:
                self._failed_captions += 1
            self._complete_result(
                sequence,
                TranslationResult(
                    recognition,
                    None,
                    None,
                    0.0,
                    error=message,
                    skipped=True,
                    speaker=speaker,
                )
            )
            return False

    def stop(self, timeout: float | None = 60.0) -> None:
        """Finish accepted captions before stopping."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise TranslationError("Translation did not stop in time.")
        self._thread = None

    def cancel_pending(self) -> int:
        """Discard translations which have not started while preserving captions."""
        cancelled = 0
        while True:
            try:
                sequence, recognition, speaker = self._queue.get_nowait()
            except Empty:
                break
            cancelled += 1
            self._complete_result(
                sequence,
                TranslationResult(
                    recognition,
                    None,
                    None,
                    0.0,
                    error="Queued translation was cancelled by the user.",
                    skipped=True,
                    speaker=speaker,
                )
            )
        if cancelled:
            with self._stats_lock:
                self._failed_captions += cancelled
        return cancelled

    def _run(self) -> None:
        models: dict[tuple[str, str], TextTranslationModel] = {}
        while True:
            try:
                sequence, recognition, speaker = self._queue.get(timeout=0.05)
            except Empty:
                if self._stop_event.is_set():
                    break
                continue

            source_language = self._normalize_language(recognition.language)
            target_language = "fr" if source_language == "en" else "en"
            started = perf_counter()
            with self._stats_lock:
                self._active_started_at = started
            try:
                direction = (source_language, target_language)
                model = models.get(direction)
                if model is None:
                    model = self._model_factory(*direction)
                    models[direction] = model
                translated_text = model.translate(recognition.text)
                if not translated_text:
                    raise TranslationError("The translation model returned empty text.")
                processing_seconds = perf_counter() - started
                result = TranslationResult(
                    recognition,
                    translated_text,
                    target_language,
                    processing_seconds,
                    speaker=speaker,
                )
                with self._stats_lock:
                    self._completed_captions += 1
                    self._processing_seconds += processing_seconds
            except Exception as error:
                processing_seconds = perf_counter() - started
                message = f"Translation failed: {error}"
                self._warning_callback(message)
                result = TranslationResult(
                    recognition,
                    None,
                    target_language,
                    processing_seconds,
                    error=str(error),
                    speaker=speaker,
                )
                with self._stats_lock:
                    self._failed_captions += 1
                    self._processing_seconds += processing_seconds
            finally:
                with self._stats_lock:
                    self._active_started_at = None
            self._complete_result(sequence, result)

    def _complete_result(self, sequence: int, result: TranslationResult) -> None:
        """Keep warnings and unsupported captions behind earlier translations."""
        with self._result_lock:
            self._pending_results[sequence] = result
            while self._next_result_sequence in self._pending_results:
                ready = self._pending_results.pop(self._next_result_sequence)
                self._next_result_sequence += 1
                self._result_callback(ready)

    @staticmethod
    def _normalize_language(language: str | None) -> str:
        return (language or "").casefold().split("-", 1)[0]
