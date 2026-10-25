from types import SimpleNamespace

import numpy as np

from app.speakers.identification import (
    RememberedSpeakerProfile,
    SessionSpeakerProfile,
    SpeakerIdentificationWorker,
)
from app.speech.vad import SpeechSegment


def segment(start: int, value: float = 1.0) -> SpeechSegment:
    samples = np.full(16_000, value, dtype=np.float32)
    samples.setflags(write=False)
    return SpeechSegment(samples, start, start + samples.size)


def test_worker_reuses_session_label_and_finds_remembered_person() -> None:
    embeddings = iter(
        (
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.99, 0.01], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        )
    )
    model = SimpleNamespace(
        dimension=2,
        compute=lambda _samples: next(embeddings),
    )
    results = []
    worker = SpeakerIdentificationWorker(
        result_callback=results.append,
        warning_callback=lambda _warning: None,
        remembered_profiles=(
            RememberedSpeakerProfile(
                person_id=7,
                name="Alice",
                embeddings=(np.array([0.0, 1.0], dtype=np.float32),),
            ),
        ),
        model_factory=lambda: model,
    )

    worker.start()
    worker.submit(segment(0))
    worker.submit(segment(16_000))
    worker.submit(segment(32_000))
    worker.stop()

    assert [result.label for result in results] == [
        "Person A",
        "Person A",
        "Alice",
    ]
    assert results[0].session_speaker_index == results[1].session_speaker_index
    assert results[2].remembered_person_id == 7
    assert results[2].session_speaker_index == 1
    assert worker.stats.completed_segments == 3
    assert worker.stats.unknown_segments == 0
    assert worker.stats.failed_segments == 0
    assert worker.stats.processing_seconds >= 0.0


def test_short_speech_stays_unknown_and_does_not_create_person() -> None:
    model = SimpleNamespace(
        dimension=2,
        compute=lambda _samples: np.array([1.0, 0.0], dtype=np.float32),
    )
    short = np.ones(4_000, dtype=np.float32)
    result = []
    worker = SpeakerIdentificationWorker(
        result.append,
        lambda _warning: None,
        model_factory=lambda: model,
    )

    worker.start()
    worker.submit(SpeechSegment(short, 0, short.size))
    worker.submit(segment(16_000))
    worker.stop()

    assert result[0].label == "Unknown"
    assert result[1].label == "Person A"
    assert worker.stats.completed_segments == 1
    assert worker.stats.unknown_segments == 1


def test_worker_restores_session_labels_after_pause() -> None:
    embeddings = iter(
        (
            np.array([1.0, 0.01], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        )
    )
    model = SimpleNamespace(
        dimension=2,
        compute=lambda _samples: next(embeddings),
    )
    results = []
    worker = SpeakerIdentificationWorker(
        results.append,
        lambda _warning: None,
        session_profiles=(
            SessionSpeakerProfile(
                speaker_index=3,
                remembered_person_id=9,
                display_name="Alice",
                embeddings=(np.array([1.0, 0.0], dtype=np.float32),),
            ),
        ),
        model_factory=lambda: model,
    )

    worker.start()
    worker.submit(segment(0))
    worker.submit(segment(16_000))
    worker.stop()

    assert results[0].label == "Alice"
    assert results[0].session_speaker_index == 3
    assert results[1].label == "Person E"
    assert results[1].session_speaker_index == 4
