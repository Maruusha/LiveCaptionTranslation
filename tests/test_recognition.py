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
            assert options["language"] is None
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

    # The overload result waits behind the older caption to preserve time order.
    assert results == []
    assert worker.cancel_pending() == 1
    assert results[1].skipped
    assert results[1].text == "[caption skipped: processing too slow]"
    assert "behind" in warnings[0]


def test_pending_recognition_can_be_cancelled_during_shutdown() -> None:
    results: list[RecognitionResult] = []
    worker = RecognitionWorker(
        result_callback=results.append,
        warning_callback=lambda _warning: None,
        model_factory=lambda: None,
        max_queued_segments=2,
    )
    worker.submit(speech_segment())
    worker.submit(speech_segment(start=2_000))

    assert worker.cancel_pending() == 2
    assert worker.stats.queued_segments == 0
    assert worker.stats.failed_segments == 2
    assert all(result.skipped for result in results)
    assert all("processing cancelled" in result.text for result in results)
    assert not worker.submit(speech_segment(start=4_000))
    assert worker.stats.queued_segments == 0
    assert results[-1].skipped


def test_recognition_worker_uses_a_manual_language() -> None:
    results: list[RecognitionResult] = []

    class FakeModel:
        def transcribe(self, _audio, **options):
            assert options["language"] == "fr"
            return iter([SimpleNamespace(text=" Bonjour")]), SimpleNamespace(
                language="en",
                language_probability=0.25,
            )

    worker = RecognitionWorker(
        result_callback=results.append,
        warning_callback=lambda _warning: None,
        model_factory=FakeModel,
        language="fr",
    )
    worker.start()
    worker.submit(speech_segment())
    worker.stop()

    assert results[0].language == "fr"


def test_model_load_failure_also_rejects_later_chunks_without_stranding_them() -> None:
    results = []

    def fail_to_load():
        raise RuntimeError("Missing local model")

    worker = RecognitionWorker(results.append, lambda _warning: None,
                               model_factory=fail_to_load)
    worker.submit(speech_segment())
    worker.start()
    worker.stop()
    assert not worker.submit(speech_segment(start=2_000))

    assert len(results) == 2
    assert all(result.error for result in results)
    assert worker.stats.queued_segments == 0
    assert worker.stats.failed_segments == 2


