"""Bounded queues shared by real-time processing stages."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from time import monotonic

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One owned block of mono float audio held only in memory."""

    samples: np.ndarray
    sample_rate: int
    captured_at: float

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.sample_rate


@dataclass(frozen=True, slots=True)
class AudioQueueStats:
    queued_frames: int
    queued_seconds: float
    dropped_frames: int
    dropped_seconds: float


class BoundedAudioFrameQueue:
    """Thread-safe FIFO queue bounded by both duration and frame count."""

    def __init__(self, max_seconds: float = 5.0, max_frames: int = 2_048) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be greater than zero.")
        if max_frames <= 0:
            raise ValueError("max_frames must be greater than zero.")

        self.max_seconds = float(max_seconds)
        self.max_frames = int(max_frames)
        self._frames: deque[AudioFrame] = deque()
        self._queued_seconds = 0.0
        self._dropped_frames = 0
        self._dropped_seconds = 0.0
        self._lock = Lock()

    def put(
        self,
        samples: np.ndarray,
        sample_rate: int,
        captured_at: float | None = None,
    ) -> int:
        """Append one frame and return how many old frames were dropped."""
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero.")

        owned_samples = np.asarray(samples, dtype=np.float32)
        if owned_samples.ndim != 1:
            raise ValueError("Audio frames must contain one mono channel.")

        owned_samples = owned_samples.copy()
        owned_samples.setflags(write=False)
        frame = AudioFrame(
            samples=owned_samples,
            sample_rate=int(sample_rate),
            captured_at=monotonic() if captured_at is None else float(captured_at),
        )

        dropped_now = 0
        with self._lock:
            self._frames.append(frame)
            self._queued_seconds += frame.duration_seconds

            while (
                len(self._frames) > self.max_frames
                or self._queued_seconds > self.max_seconds
            ):
                dropped = self._frames.popleft()
                self._queued_seconds -= dropped.duration_seconds
                self._dropped_frames += 1
                self._dropped_seconds += dropped.duration_seconds
                dropped_now += 1

        return dropped_now

    def get_nowait(self) -> AudioFrame | None:
        """Return the oldest frame, or None when the queue is empty."""
        with self._lock:
            if not self._frames:
                return None
            frame = self._frames.popleft()
            self._queued_seconds -= frame.duration_seconds
            return frame

    def clear(self) -> None:
        """Discard queued frames while retaining cumulative drop statistics."""
        with self._lock:
            self._frames.clear()
            self._queued_seconds = 0.0

    @property
    def stats(self) -> AudioQueueStats:
        with self._lock:
            return AudioQueueStats(
                queued_frames=len(self._frames),
                queued_seconds=max(0.0, self._queued_seconds),
                dropped_frames=self._dropped_frames,
                dropped_seconds=self._dropped_seconds,
            )

