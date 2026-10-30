"""Evaluate the real VAD against local speech and non-speech variants."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from app.core.queues import BoundedAudioFrameQueue
from app.speech.vad import (
    SpeechEvent,
    SpeechSegment,
    VAD_SAMPLE_RATE,
    VoiceActivityWorker,
    create_silero_detector,
)


@dataclass(frozen=True, slots=True)
class VadEvaluationResult:
    name: str
    audio_seconds: float
    speech_starts: int
    speech_ends: int
    segments: int
    filtered_short_segments: int
    detected_speech_seconds: float
    model_load_seconds: float
    average_inference_milliseconds: float
    realtime_percent: float
    wall_seconds: float
    warnings: tuple[str, ...]


def evaluate_audio(
    name: str,
    samples: np.ndarray,
    detector_factory=create_silero_detector,
) -> tuple[VadEvaluationResult, list[SpeechSegment]]:
    """Run one complete in-memory sample through the production VAD worker."""
    audio = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    duration_seconds = audio.size / VAD_SAMPLE_RATE
    source = BoundedAudioFrameQueue(
        max_seconds=max(5.0, duration_seconds + 1.0),
        max_frames=16,
    )
    source.put(audio, sample_rate=VAD_SAMPLE_RATE)
    events: list[SpeechEvent] = []
    segments: list[SpeechSegment] = []
    warnings: list[str] = []
    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=events.append,
        warning_callback=warnings.append,
        segment_callback=segments.append,
        detector_factory=detector_factory,
    )

    started = perf_counter()
    worker.start()
    worker.stop(timeout=30.0)
    wall_seconds = perf_counter() - started
    stats = worker.stats
    return (
        VadEvaluationResult(
            name=name,
            audio_seconds=duration_seconds,
            speech_starts=sum(event.kind == "speech_start" for event in events),
            speech_ends=sum(event.kind == "speech_end" for event in events),
            segments=len(segments),
            filtered_short_segments=stats.filtered_short_segments,
            detected_speech_seconds=sum(
                segment.duration_seconds for segment in segments
            ),
            model_load_seconds=stats.model_load_seconds,
            average_inference_milliseconds=stats.average_inference_milliseconds,
            realtime_percent=stats.realtime_percent,
            wall_seconds=wall_seconds,
            warnings=tuple(warnings),
        ),
        segments,
    )


def build_evaluation_cases(speech: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Create deterministic quiet, noisy, and non-speech cases in memory."""
    rng = np.random.default_rng(2_026_10_19)
    quiet = speech * 0.1
    noisy = np.clip(speech * 0.65 + rng.normal(0.0, 0.025, speech.size), -1.0, 1.0)

    non_speech_samples = 10 * VAD_SAMPLE_RATE
    timeline = np.arange(non_speech_samples, dtype=np.float32) / VAD_SAMPLE_RATE
    music_like = 0.08 * np.sin(2 * np.pi * 220 * timeline)
    music_like += 0.04 * np.sin(2 * np.pi * 440 * timeline)
    background_noise = rng.normal(0.0, 0.02, non_speech_samples).astype(np.float32)
    return [
        ("normal speech", speech),
        ("quiet speech (-20 dB)", quiet),
        ("speech with strong noise", noisy.astype(np.float32)),
        ("music-like tones", music_like.astype(np.float32)),
        ("background noise", background_noise),
    ]


def main() -> int:
    from faster_whisper.audio import decode_audio

    project_root = Path(__file__).resolve().parents[2]
    sample_path = project_root / ".cache" / "test-audio" / "silero-en.wav"
    if not sample_path.is_file():
        raise SystemExit(f"Missing local test sample: {sample_path}")

    speech = decode_audio(str(sample_path), sampling_rate=VAD_SAMPLE_RATE)
    results: list[VadEvaluationResult] = []
    first_segments: list[SpeechSegment] = []
    for name, samples in build_evaluation_cases(speech):
        result, segments = evaluate_audio(name, samples)
        results.append(result)
        if name == "normal speech":
            first_segments = segments

    if first_segments:
        short = first_segments[0].samples
        padding = np.zeros(VAD_SAMPLE_RATE // 2, dtype=np.float32)
        short_phrase = np.concatenate((padding, short, padding))
        result, _ = evaluate_audio("short phrase", short_phrase)
        results.append(result)

    print(json.dumps([asdict(result) for result in results], indent=2))
    return 0 if all(not result.warnings for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
