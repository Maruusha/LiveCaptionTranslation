from types import SimpleNamespace

import numpy as np

from app.speech.recognition import RecognitionResult, RecognitionWorker
from app.speech.vad import SpeechSegment


def speech_segment(start: int = 0, size: int = 1_600) -> SpeechSegment:
    samples = np.ones(size, dtype=np.float32)
    samples.setflags(write=False)
    return SpeechSegment(samples, start, start + size)


def test_recognition_worker_transcribes_a_completed_chunk() -> None:
    results: list[RecognitionResult] = []
    warnings: list[str] = []

    class FakeModel:
        def transcribe(self, audio, **options):
            assert audio.size == 1_600
            assert options["beam_size"] == 1
            return iter(
                [SimpleNamespace(text=" Bonjour "), SimpleNamespace(text="le monde")]
            ), SimpleNamespace(language="fr", language_probability=0.98)

    worker = RecognitionWorker(
        result_callback=results.append,
        warning_callback=warnings.append,
        model_factory=FakeModel,
    )
    worker.start()
    worker.submit(speech_segment())
    worker.stop()

    assert len(results) == 1
    assert results[0].text == "Bonjour le monde"
    assert results[0].language == "fr"
    assert results[0].language_probability == 0.98
    assert results[0].error is None
    assert worker.stats.completed_segments == 1
    assert warnings == []


def test_recognition_queue_reports_visible_overload() -> None:
    results: list[RecognitionResult] = []
    warnings: list[str] = []
    worker = RecognitionWorker(
        result_callback=results.append,
        warning_callback=warnings.append,
        model_factory=lambda: None,
        max_queued_segments=1,
    )

    assert worker.submit(speech_segment())
    assert not worker.submit(speech_segment(start=2_000))

    assert results[0].skipped
    assert results[0].text == "[caption skipped: processing too slow]"
    assert "behind" in warnings[0]
