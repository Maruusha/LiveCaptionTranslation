"""Run the local speaker models against a repeatable public sample."""

from __future__ import annotations

import argparse
from pathlib import Path
import wave

import numpy as np
import soxr

from app.core.performance import (
    build_performance_report,
    format_mebibytes,
    take_process_snapshot,
)
from app.speakers.diarization import SPEAKER_SAMPLE_RATE, SpeakerDiarizer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLE = (
    PROJECT_ROOT
    / "models"
    / "speakers"
    / "evaluation"
    / "1-two-speakers-en.wav"
)


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if sample_width != 2:
        raise ValueError("The evaluation WAV must contain 16-bit PCM audio.")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32_768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if sample_rate != SPEAKER_SAMPLE_RATE:
        samples = soxr.resample(samples, sample_rate, SPEAKER_SAMPLE_RATE)
        sample_rate = SPEAKER_SAMPLE_RATE
    return np.ascontiguousarray(samples, dtype=np.float32), sample_rate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="?", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--num-speakers", type=int, default=-1)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--threads", type=int, default=2)
    arguments = parser.parse_args()

    samples, sample_rate = read_wave(arguments.audio)
    if sample_rate != SPEAKER_SAMPLE_RATE:
        raise RuntimeError(f"Expected {SPEAKER_SAMPLE_RATE} Hz audio.")
    start = take_process_snapshot()
    diarizer = SpeakerDiarizer(
        num_threads=arguments.threads,
        num_speakers=arguments.num_speakers,
        cluster_threshold=arguments.threshold,
    )
    result = diarizer.process(samples)
    performance = build_performance_report(start, take_process_snapshot())

    print(f"Audio: {result.audio_seconds:.2f}s")
    print(f"Speakers: {result.speaker_count}")
    print(f"Turns: {len(result.turns)}")
    print(f"Model load: {result.model_load_seconds:.3f}s")
    print(f"Analysis: {result.processing_seconds:.3f}s")
    print(f"Real-time use: {result.realtime_percent:.1f}%")
    print(f"Average CPU: {performance.average_cpu_percent:.1f}%")
    print(f"Memory: {format_mebibytes(performance.memory_end_bytes)}")
    print(f"Peak memory: {format_mebibytes(performance.peak_memory_bytes)}")
    for turn in result.turns:
        print(
            f"{turn.start_seconds:7.3f} - {turn.end_seconds:7.3f}  "
            f"{turn.label}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
