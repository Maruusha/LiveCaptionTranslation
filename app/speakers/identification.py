"""Session speaker labels and opt-in cross-session voice matching."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Protocol

import numpy as np

from app.speakers.diarization import (
    DEFAULT_EMBEDDING_MODEL,
    SPEAKER_SAMPLE_RATE,
    person_label,
)
from app.speech.vad import SpeechSegment


SPEAKER_MODEL_NAME = "nemo_en_titanet_small"
SPEAKER_MODEL_VERSION = "sherpa-onnx-1.12.33"


class SpeakerIdentificationError(RuntimeError):
    """Raised when the speaker worker cannot start or stop safely."""


class SpeakerEmbeddingModel(Protocol):
    dimension: int

    def compute(self, samples: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class RememberedSpeakerProfile:
    person_id: int
    name: str
    embeddings: tuple[np.ndarray, ...]


@dataclass(frozen=True, slots=True)
class SessionSpeakerProfile:
    speaker_index: int
    remembered_person_id: int | None
    display_name: str | None
    embeddings: tuple[np.ndarray, ...]


@dataclass(frozen=True, slots=True)
class SpeakerIdentification:
    start_sample: int
    end_sample: int
    label: str
    session_speaker_index: int | None
    remembered_person_id: int | None
    confidence: float
    embedding: np.ndarray | None
    error: str | None = None


class SherpaSpeakerEmbeddingModel:
    """Small wrapper around the project-local sherpa-onnx extractor."""

    def __init__(self, num_threads: int = 1) -> None:
        if not DEFAULT_EMBEDDING_MODEL.is_file():
            raise SpeakerIdentificationError(
                f"Local speaker model is missing: {DEFAULT_EMBEDDING_MODEL}"
            )
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(DEFAULT_EMBEDDING_MODEL),
            num_threads=num_threads,
            provider="cpu",
        )
        if not config.validate():
            raise SpeakerIdentificationError(
                "The local speaker-embedding configuration is invalid."
            )
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.dimension = int(self._extractor.dim)

    def compute(self, samples: np.ndarray) -> np.ndarray:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SPEAKER_SAMPLE_RATE, samples)
        stream.input_finished()
        if not self._extractor.is_ready(stream):
            raise SpeakerIdentificationError(
                "The speech chunk is too short for a voice signature."
            )
        return _normalized_embedding(self._extractor.compute(stream))


def _normalized_embedding(values) -> np.ndarray:
    embedding = np.asarray(values, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(embedding))
    if embedding.size == 0 or not np.isfinite(norm) or norm <= 0.0:
        raise SpeakerIdentificationError("The voice signature is invalid.")
    embedding = np.ascontiguousarray(embedding / norm, dtype=np.float32)
    embedding.setflags(write=False)
    return embedding


class SpeakerIdentificationWorker:
    """Assign session labels and approved identities without blocking audio."""

    def __init__(
        self,
        result_callback: Callable[[SpeakerIdentification], None],
        warning_callback: Callable[[str], None],
        remembered_profiles: tuple[RememberedSpeakerProfile, ...] = (),
        session_profiles: tuple[SessionSpeakerProfile, ...] = (),
        model_factory: Callable[[], SpeakerEmbeddingModel] = (
            SherpaSpeakerEmbeddingModel
        ),
        max_queued_segments: int = 8,
        match_threshold: float = 0.65,
        minimum_match_margin: float = 0.05,
        minimum_speech_seconds: float = 0.8,
        max_signatures_per_speaker: int = 5,
    ) -> None:
        if max_queued_segments <= 0:
            raise ValueError("max_queued_segments must be greater than zero.")
        if not 0.0 < match_threshold < 1.0:
            raise ValueError("match_threshold must be between zero and one.")
        if not 0.0 <= minimum_match_margin <= 1.0:
            raise ValueError("minimum_match_margin must be between zero and one.")
        if minimum_speech_seconds < 0.0:
            raise ValueError("minimum_speech_seconds cannot be negative.")
        if max_signatures_per_speaker <= 0:
            raise ValueError("max_signatures_per_speaker must be greater than zero.")
        self._result_callback = result_callback
        self._warning_callback = warning_callback
        self._profiles = remembered_profiles
        self._initial_session_profiles = session_profiles
        self._model_factory = model_factory
        self._queue: Queue[SpeechSegment] = Queue(maxsize=max_queued_segments)
        self._match_threshold = match_threshold
        self._minimum_match_margin = minimum_match_margin
        self._minimum_samples = int(minimum_speech_seconds * SPEAKER_SAMPLE_RATE)
        self._max_signatures = max_signatures_per_speaker
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state_lock = Lock()
        self._model_error: str | None = None
        self._session_signatures: dict[int, list[np.ndarray]] = {}
        self._session_person_ids: dict[int, int | None] = {}
        self._session_names: dict[int, str | None] = {}

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        with self._state_lock:
            self._model_error = None
            self._session_signatures.clear()
            self._session_person_ids.clear()
            self._session_names.clear()
        self._thread = Thread(
            target=self._run,
            name="speaker-identification-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, segment: SpeechSegment) -> bool:
        with self._state_lock:
            model_error = self._model_error
        if model_error is not None:
            self._emit_unknown(segment, model_error)
            return False
        try:
            self._queue.put_nowait(segment)
            return True
        except Full:
            message = "Speaker detection is behind; one speaker label was skipped."
            self._warning_callback(message)
            self._emit_unknown(segment, message)
            return False

    def stop(self, timeout: float = 30.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise SpeakerIdentificationError(
                "Speaker identification did not stop in time."
            )
        self._thread = None

    def cancel_pending(self) -> int:
        cancelled = 0
        while True:
            try:
                segment = self._queue.get_nowait()
            except Empty:
                return cancelled
            cancelled += 1
            self._emit_unknown(
                segment,
                "Speaker detection was cancelled while the application was closing.",
            )

    def _run(self) -> None:
        try:
            model = self._model_factory()
            profiles = self._prepare_profiles(model.dimension)
            self._prepare_session_profiles(model.dimension)
        except Exception as error:
            message = f"Speaker-identification model could not load: {error}"
            with self._state_lock:
                self._model_error = message
            self._warning_callback(message)
            self.cancel_pending()
            return

        while True:
            try:
                segment = self._queue.get(timeout=0.05)
            except Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                if segment.samples.size < self._minimum_samples:
                    self._emit_unknown(segment)
                    continue
                embedding = _normalized_embedding(model.compute(segment.samples))
                self._result_callback(
                    self._classify(segment, embedding, profiles)
                )
            except Exception as error:
                message = f"Speaker identification failed: {error}"
                self._warning_callback(message)
                self._emit_unknown(segment, str(error))

    def _prepare_profiles(
        self,
        dimension: int,
    ) -> tuple[RememberedSpeakerProfile, ...]:
        prepared = []
        for profile in self._profiles:
            embeddings = tuple(
                _normalized_embedding(embedding)
                for embedding in profile.embeddings
                if np.asarray(embedding).size == dimension
            )
            if embeddings:
                prepared.append(
                    RememberedSpeakerProfile(
                        profile.person_id,
                        profile.name,
                        embeddings,
                    )
                )
        return tuple(prepared)

    def _prepare_session_profiles(self, dimension: int) -> None:
        for profile in self._initial_session_profiles:
            embeddings = [
                _normalized_embedding(embedding)
                for embedding in profile.embeddings
                if np.asarray(embedding).size == dimension
            ]
            if not embeddings:
                continue
            self._session_signatures[profile.speaker_index] = embeddings[
                -self._max_signatures :
            ]
            self._session_person_ids[profile.speaker_index] = (
                profile.remembered_person_id
            )
            self._session_names[profile.speaker_index] = profile.display_name

    def _classify(
        self,
        segment: SpeechSegment,
        embedding: np.ndarray,
        profiles: tuple[RememberedSpeakerProfile, ...],
    ) -> SpeakerIdentification:
        remembered_profile, remembered_score = self._best_remembered_match(
            embedding,
            profiles,
        )
        session_index, session_score = self._best_session_match(embedding)

        if remembered_profile is not None and remembered_score >= session_score:
            existing_index = self._session_index_for_person(
                remembered_profile.person_id
            )
            if existing_index is None:
                existing_index = self._new_session_speaker(
                    embedding,
                    remembered_profile.person_id,
                    remembered_profile.name,
                )
            else:
                self._keep_signature(existing_index, embedding)
                self._session_names[existing_index] = remembered_profile.name
            return SpeakerIdentification(
                segment.start_sample,
                segment.end_sample,
                remembered_profile.name,
                existing_index,
                remembered_profile.person_id,
                remembered_score,
                embedding,
            )

        if session_index is not None and session_score >= self._match_threshold:
            self._keep_signature(session_index, embedding)
            person_id = self._session_person_ids.get(session_index)
            remembered_name = self._session_names.get(session_index)
            return SpeakerIdentification(
                segment.start_sample,
                segment.end_sample,
                remembered_name or person_label(session_index),
                session_index,
                person_id,
                session_score,
                embedding,
            )

        new_index = self._new_session_speaker(embedding, None, None)
        return SpeakerIdentification(
            segment.start_sample,
            segment.end_sample,
            person_label(new_index),
            new_index,
            None,
            1.0,
            embedding,
        )

    def _best_remembered_match(
        self,
        embedding: np.ndarray,
        profiles: tuple[RememberedSpeakerProfile, ...],
    ) -> tuple[RememberedSpeakerProfile | None, float]:
        ranked = sorted(
            (
                (
                    max(float(np.dot(embedding, saved)) for saved in profile.embeddings),
                    profile,
                )
                for profile in profiles
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < self._match_threshold:
            return None, 0.0
        second_score = ranked[1][0] if len(ranked) > 1 else -1.0
        if ranked[0][0] - second_score < self._minimum_match_margin:
            return None, ranked[0][0]
        return ranked[0][1], ranked[0][0]

    def _best_session_match(
        self,
        embedding: np.ndarray,
    ) -> tuple[int | None, float]:
        if not self._session_signatures:
            return None, 0.0
        scores = {
            speaker_index: max(
                float(np.dot(embedding, saved)) for saved in signatures
            )
            for speaker_index, signatures in self._session_signatures.items()
        }
        best_index = max(scores, key=scores.get)
        return best_index, scores[best_index]

    def _session_index_for_person(self, person_id: int) -> int | None:
        return next(
            (
                index
                for index, saved_person_id in self._session_person_ids.items()
                if saved_person_id == person_id
            ),
            None,
        )

    def _new_session_speaker(
        self,
        embedding: np.ndarray,
        person_id: int | None,
        display_name: str | None,
    ) -> int:
        speaker_index = max(self._session_signatures, default=-1) + 1
        self._session_signatures[speaker_index] = [embedding]
        self._session_person_ids[speaker_index] = person_id
        self._session_names[speaker_index] = display_name
        return speaker_index

    def _keep_signature(self, speaker_index: int, embedding: np.ndarray) -> None:
        signatures = self._session_signatures[speaker_index]
        if len(signatures) < self._max_signatures:
            signatures.append(embedding)
        else:
            signatures.pop(0)
            signatures.append(embedding)

    def _emit_unknown(
        self,
        segment: SpeechSegment,
        error: str | None = None,
    ) -> None:
        self._result_callback(
            SpeakerIdentification(
                segment.start_sample,
                segment.end_sample,
                "Unknown",
                None,
                None,
                0.0,
                None,
                error,
            )
        )
