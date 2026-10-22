from app.speech.recognition import RecognitionResult
from app.translation.translator import TranslationResult, TranslationWorker


def recognition(text: str, language: str | None) -> RecognitionResult:
    return RecognitionResult(
        start_sample=0,
        end_sample=16_000,
        text=text,
        language=language,
        language_probability=0.99,
        processing_seconds=0.2,
    )


def test_translation_worker_translates_both_directions() -> None:
    results: list[TranslationResult] = []
    warnings: list[str] = []
    loaded_directions: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, target_language: str) -> None:
            self.target_language = target_language

        def translate(self, text: str) -> str:
            translations = {
                ("fr", "Hello world"): "Bonjour le monde",
                ("en", "Bonjour le monde"): "Hello world",
            }
            return translations[(self.target_language, text)]

    def model_factory(source_language: str, target_language: str) -> FakeModel:
        loaded_directions.append((source_language, target_language))
        return FakeModel(target_language)

    worker = TranslationWorker(
        result_callback=results.append,
        warning_callback=warnings.append,
        model_factory=model_factory,
    )
    worker.start()
    worker.submit(recognition("Hello world", "en"))
    worker.submit(recognition("Bonjour le monde", "fr-FR"))
    worker.stop()

    assert loaded_directions == [("en", "fr"), ("fr", "en")]
    assert [result.translated_text for result in results] == [
        "Bonjour le monde",
        "Hello world",
    ]
    assert [result.target_language for result in results] == ["fr", "en"]
    assert worker.stats.completed_captions == 2
    assert worker.stats.failed_captions == 0
    assert warnings == []


def test_translation_worker_preserves_an_unsupported_language_caption() -> None:
    results: list[TranslationResult] = []
    worker = TranslationWorker(
        result_callback=results.append,
        warning_callback=lambda _warning: None,
        model_factory=lambda _source, _target: None,
    )

    assert not worker.submit(recognition("Guten Tag", "de"))
    assert results[0].recognition.text == "Guten Tag"
    assert results[0].translated_text is None
    assert results[0].error is not None
    assert worker.stats.failed_captions == 1


def test_translation_queue_reports_overload_without_losing_original_text() -> None:
    results: list[TranslationResult] = []
    warnings: list[str] = []
    worker = TranslationWorker(
        result_callback=results.append,
        warning_callback=warnings.append,
        model_factory=lambda _source, _target: None,
        max_queued_captions=1,
    )

    assert worker.submit(recognition("First", "en"))
    assert not worker.submit(recognition("Second", "en"))

    assert results[0].recognition.text == "Second"
    assert results[0].skipped
    assert "behind" in warnings[0]


def test_pending_translation_can_be_cancelled_during_shutdown() -> None:
    results: list[TranslationResult] = []
    worker = TranslationWorker(
        result_callback=results.append,
        warning_callback=lambda _warning: None,
        model_factory=lambda _source, _target: None,
        max_queued_captions=2,
    )
    worker.submit(recognition("First", "en"))
    worker.submit(recognition("Second", "fr"))

    assert worker.cancel_pending() == 2
    assert worker.stats.queued_captions == 0
    assert worker.stats.failed_captions == 2
    assert all(result.skipped for result in results)
    assert all(result.recognition.text in {"First", "Second"} for result in results)
