import numpy as np

from app.diagnostics.vad_evaluation import build_evaluation_cases, evaluate_audio


def test_evaluation_reports_boundaries_with_an_injected_detector() -> None:
    results = iter(
        ({"start": 0}, None, None, None, None, None, {"end": 3_500}, None)
    )
    result, segments = evaluate_audio(
        "test",
        np.ones(4_096, dtype=np.float32),
        detector_factory=lambda: lambda _samples: next(results),
    )

    assert result.audio_seconds == 4_096 / 16_000
    assert result.speech_starts == 1
    assert result.speech_ends == 1
    assert result.segments == 1
    assert segments[0].samples.size == 3_500
    assert result.filtered_short_segments == 0
    assert result.warnings == ()


def test_evaluation_cases_are_deterministic_and_do_not_change_source() -> None:
    source = np.linspace(-0.5, 0.5, 16_000, dtype=np.float32)
    original = source.copy()

    first = build_evaluation_cases(source)
    second = build_evaluation_cases(source)

    assert [name for name, _samples in first] == [
        "normal speech",
        "quiet speech (-20 dB)",
        "speech with strong noise",
        "music-like tones",
        "background noise",
    ]
    assert np.array_equal(source, original)
    for (_first_name, first_samples), (_second_name, second_samples) in zip(
        first,
        second,
        strict=True,
    ):
        assert np.array_equal(first_samples, second_samples)
