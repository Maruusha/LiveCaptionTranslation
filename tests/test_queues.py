"""Tests for bounded real-time processing queues."""

import numpy as np
import pytest
from threading import Thread

from app.core.queues import BoundedAudioFrameQueue


def test_audio_queue_drops_oldest_frames_when_duration_is_full() -> None:
    queue = BoundedAudioFrameQueue(max_seconds=0.5, max_frames=10)

    queue.put(np.array([1.0, 1.0], dtype=np.float32), sample_rate=10, captured_at=1.0)
    queue.put(np.array([2.0, 2.0], dtype=np.float32), sample_rate=10, captured_at=2.0)
    dropped = queue.put(
        np.array([3.0, 3.0], dtype=np.float32),
        sample_rate=10,
        captured_at=3.0,
    )

    assert dropped == 1
    assert queue.stats.queued_frames == 2
    assert queue.stats.queued_seconds == pytest.approx(0.4)
    assert queue.stats.dropped_frames == 1
    assert queue.stats.dropped_seconds == pytest.approx(0.2)

    first_remaining = queue.get_nowait()
    assert first_remaining is not None
    assert first_remaining.captured_at == 2.0
    assert first_remaining.samples.tolist() == [2.0, 2.0]


def test_audio_queue_owns_read_only_copy_and_respects_frame_limit() -> None:
    queue = BoundedAudioFrameQueue(max_seconds=10.0, max_frames=1)
    source = np.array([0.25, 0.5], dtype=np.float32)

    queue.put(source, sample_rate=16_000)
    source[:] = 0.0
    queue.put(np.array([0.75], dtype=np.float32), sample_rate=16_000)

    frame = queue.get_nowait()
    assert frame is not None
    assert frame.samples.tolist() == [0.75]
    assert not frame.samples.flags.writeable
    assert queue.stats.dropped_frames == 1


def test_audio_queue_clear_removes_all_queued_audio() -> None:
    queue = BoundedAudioFrameQueue()
    queue.put(np.ones(160, dtype=np.float32), sample_rate=16_000)

    queue.clear()

    assert queue.get_nowait() is None
    assert queue.stats.queued_frames == 0
    assert queue.stats.queued_seconds == 0.0


def test_audio_queue_wakes_a_waiting_consumer() -> None:
    queue = BoundedAudioFrameQueue()
    received = []
    consumer = Thread(target=lambda: received.append(queue.get(timeout=1.0)))

    consumer.start()
    queue.put(np.ones(160, dtype=np.float32), sample_rate=16_000)
    consumer.join(timeout=1.0)

    assert not consumer.is_alive()
    assert received[0] is not None
    assert received[0].samples.size == 160
