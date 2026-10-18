"""Tests for background audio-rate conversion."""

import numpy as np
import pytest

from app.audio.processing import AudioProcessingWorker
from app.core.queues import BoundedAudioFrameQueue


def drain_samples(queue: BoundedAudioFrameQueue) -> np.ndarray:
    chunks: list[np.ndarray] = []
    while (frame := queue.get_nowait()) is not None:
        assert frame.sample_rate == 16_000
        chunks.append(frame.samples)
    return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)


def test_worker_resamples_48khz_audio_to_16khz() -> None:
    source = BoundedAudioFrameQueue(max_seconds=2.0)
    output = BoundedAudioFrameQueue(max_seconds=2.0)
    warnings: list[str] = []
    input_audio = np.sin(
        2 * np.pi * 440 * np.arange(48_000, dtype=np.float32) / 48_000
    )

    for chunk in np.array_split(input_audio, 100):
        source.put(chunk, sample_rate=48_000)

    worker = AudioProcessingWorker(source, output, warnings.append)
    worker.start()
    worker.stop()

    converted = drain_samples(output)
    assert converted.size == pytest.approx(16_000, abs=2)
    assert worker.stats.input_seconds == pytest.approx(1.0)
    assert worker.stats.output_seconds == pytest.approx(1.0, abs=0.001)
    assert warnings == []


def test_worker_passes_through_existing_16khz_audio() -> None:
    source = BoundedAudioFrameQueue()
    output = BoundedAudioFrameQueue()
    samples = np.linspace(-1.0, 1.0, 320, dtype=np.float32)
    source.put(samples, sample_rate=16_000, captured_at=12.5)

    worker = AudioProcessingWorker(source, output, lambda _warning: None)
    worker.start()
    worker.stop()

    frame = output.get_nowait()
    assert frame is not None
    assert frame.captured_at == 12.5
    assert np.array_equal(frame.samples, samples)

