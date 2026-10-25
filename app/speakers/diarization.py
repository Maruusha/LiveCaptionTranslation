"""CPU speaker diarization with project-local sherpa-onnx models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEAKER_MODEL_ROOT = PROJECT_ROOT / "models" / "speakers"
DEFAULT_SEGMENTATION_MODEL = (
    SPEAKER_MODEL_ROOT
    / "sherpa-onnx-pyannote-segmentation-3-0"
    / "model.int8.onnx"
)
DEFAULT_EMBEDDING_MODEL = SPEAKER_MODEL_ROOT / "nemo_en_titanet_small.onnx"
SPEAKER_SAMPLE_RATE = 16_000


class SpeakerDiarizationError(RuntimeError):
    """Raised when local speaker analysis cannot run safely."""


class OfflineDiarizationModel(Protocol):
    sample_rate: int

    def process(self, samples: np.ndarray) -> object: ...


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    """One time range assigned to a session-local speaker index."""

    start_seconds: float
    end_seconds: float
    speaker_index: int

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def label(self) -> str:
        return person_label(self.speaker_index)


@dataclass(frozen=True, slots=True)
class CaptionSpeakerAssignment:
    """Best timestamp match between one caption and a speaker timeline."""

    label: str
    speaker_index: int | None
    confidence: float
    overlap_detected: bool


@dataclass(frozen=True, slots=True)
class SpeakerDiarizationResult:
    """Speaker turns plus simple performance measurements."""

    turns: tuple[SpeakerTurn, ...]
    speaker_count: int
    audio_seconds: float
    model_load_seconds: float
    processing_seconds: float

    @property
    def realtime_percent(self) -> float:
        if self.audio_seconds <= 0.0:
            return 0.0
        return self.processing_seconds / self.audio_seconds * 100.0


def person_label(speaker_index: int) -> str:
    """Convert a zero-based index to Person A ... Z, AA, AB, and onward."""

    if speaker_index < 0:
        raise ValueError("speaker_index cannot be negative.")
    value = speaker_index + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"Person {letters}"


def assign_caption_speaker(
    turns: tuple[SpeakerTurn, ...] | list[SpeakerTurn],
    start_seconds: float,
    end_seconds: float,
    *,
    minimum_coverage: float = 0.35,
    minimum_margin: float = 0.15,
) -> CaptionSpeakerAssignment:
    """Choose a speaker by overlap duration without claiming uncertain identity."""

    if end_seconds <= start_seconds:
        raise ValueError("Caption end time must be after its start time.")
    if not 0.0 <= minimum_coverage <= 1.0:
        raise ValueError("minimum_coverage must be between zero and one.")
    if not 0.0 <= minimum_margin <= 1.0:
        raise ValueError("minimum_margin must be between zero and one.")

    caption_seconds = end_seconds - start_seconds
    overlap_by_speaker: dict[int, float] = {}
    for turn in turns:
        overlap = max(
            0.0,
            min(end_seconds, turn.end_seconds)
            - max(start_seconds, turn.start_seconds),
        )
        if overlap:
            overlap_by_speaker[turn.speaker_index] = (
                overlap_by_speaker.get(turn.speaker_index, 0.0) + overlap
            )

    if not overlap_by_speaker:
        return CaptionSpeakerAssignment("Unknown", None, 0.0, False)

    ranked = sorted(
        overlap_by_speaker.items(),
        key=lambda item: (-item[1], item[0]),
    )
    best_speaker, best_seconds = ranked[0]
    second_seconds = ranked[1][1] if len(ranked) > 1 else 0.0
    coverage = min(1.0, best_seconds / caption_seconds)
    margin = max(0.0, (best_seconds - second_seconds) / caption_seconds)
    overlap_detected = len(ranked) > 1 and second_seconds > 0.0
    if coverage < minimum_coverage or (
        overlap_detected and margin < minimum_margin
    ):
        return CaptionSpeakerAssignment(
            "Unknown",
            None,
            coverage,
            overlap_detected,
        )
    return CaptionSpeakerAssignment(
        person_label(best_speaker),
        best_speaker,
        coverage,
        overlap_detected,
    )


class SpeakerDiarizer:
    """Thin, testable wrapper around sherpa-onnx offline diarization."""

    def __init__(
        self,
        *,
        segmentation_model: Path = DEFAULT_SEGMENTATION_MODEL,
        embedding_model: Path = DEFAULT_EMBEDDING_MODEL,
        num_threads: int = 2,
        num_speakers: int = -1,
        cluster_threshold: float = 0.5,
        model: OfflineDiarizationModel | None = None,
    ) -> None:
        if num_threads <= 0:
            raise ValueError("num_threads must be greater than zero.")
        if num_speakers == 0 or num_speakers < -1:
            raise ValueError("num_speakers must be -1 or greater than zero.")
        if not 0.0 < cluster_threshold < 1.0:
            raise ValueError("cluster_threshold must be between zero and one.")

        started = perf_counter()
        self._model = model or self._create_model(
            segmentation_model=segmentation_model,
            embedding_model=embedding_model,
            num_threads=num_threads,
            num_speakers=num_speakers,
            cluster_threshold=cluster_threshold,
        )
        self.model_load_seconds = perf_counter() - started
        if int(self._model.sample_rate) != SPEAKER_SAMPLE_RATE:
            raise SpeakerDiarizationError(
                f"Speaker model expects {self._model.sample_rate} Hz; "
                f"the application uses {SPEAKER_SAMPLE_RATE} Hz."
            )

    @staticmethod
    def _create_model(
        *,
        segmentation_model: Path,
        embedding_model: Path,
        num_threads: int,
        num_speakers: int,
        cluster_threshold: float,
    ) -> OfflineDiarizationModel:
        missing = [
            str(path)
            for path in (segmentation_model, embedding_model)
            if not path.is_file()
        ]
        if missing:
            raise SpeakerDiarizationError(
                "Local speaker model file is missing: " + ", ".join(missing)
            )
        try:
            import sherpa_onnx

            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=(
                        sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                            model=str(segmentation_model)
                        )
                    ),
                    num_threads=num_threads,
                    provider="cpu",
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(embedding_model),
                    num_threads=num_threads,
                    provider="cpu",
                ),
                clustering=sherpa_onnx.FastClusteringConfig(
                    num_clusters=num_speakers,
                    threshold=cluster_threshold,
                ),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            if not config.validate():
                raise SpeakerDiarizationError(
                    "The local speaker-model configuration is invalid."
                )
            return sherpa_onnx.OfflineSpeakerDiarization(config)
        except SpeakerDiarizationError:
            raise
        except Exception as error:
            raise SpeakerDiarizationError(
                f"Speaker models could not load: {error}"
            ) from error

    def process(self, samples: np.ndarray) -> SpeakerDiarizationResult:
        """Analyze one 16 kHz mono float32 array without writing audio to disk."""

        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim != 1:
            raise ValueError("Speaker audio must be a one-dimensional mono array.")
        if not np.all(np.isfinite(audio)):
            raise ValueError("Speaker audio contains a non-finite sample.")
        audio = np.ascontiguousarray(np.clip(audio, -1.0, 1.0))
        started = perf_counter()
        try:
            raw_result = self._model.process(audio)
            raw_turns = raw_result.sort_by_start_time()
        except Exception as error:
            raise SpeakerDiarizationError(
                f"Speaker analysis failed: {error}"
            ) from error
        processing_seconds = perf_counter() - started
        turns = tuple(
            SpeakerTurn(
                start_seconds=float(turn.start),
                end_seconds=float(turn.end),
                speaker_index=int(turn.speaker),
            )
            for turn in raw_turns
            if float(turn.end) > float(turn.start)
        )
        speaker_count = len({turn.speaker_index for turn in turns})
        return SpeakerDiarizationResult(
            turns=turns,
            speaker_count=speaker_count,
            audio_seconds=audio.size / SPEAKER_SAMPLE_RATE,
            model_load_seconds=self.model_load_seconds,
            processing_seconds=processing_seconds,
        )
