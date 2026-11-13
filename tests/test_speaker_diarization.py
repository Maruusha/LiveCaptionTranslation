from types import SimpleNamespace

import numpy as np
import pytest

from app.speakers.diarization import (
    SpeakerDiarizer,
    SpeakerTurn,
    assign_caption_speaker,
    person_index,
    person_label,
)


def test_person_labels_continue_after_z_without_a_fixed_cap() -> None:
    assert person_label(0) == "Person A"
    assert person_label(25) == "Person Z"
    assert person_label(26) == "Person AA"
    assert person_label(27) == "Person AB"
    assert person_label(701) == "Person ZZ"
    assert person_index("Person A") == 0
    assert person_index("Person AA") == 26
    assert person_index("Person ZZ") == 701
    with pytest.raises(ValueError):
        person_label(-1)
    with pytest.raises(ValueError):
        person_index("Alice")


def test_caption_assignment_uses_timeline_overlap_and_marks_uncertainty() -> None:
    turns = [
        SpeakerTurn(0.0, 2.0, 0),
        SpeakerTurn(2.0, 4.0, 1),
    ]

    first = assign_caption_speaker(turns, 0.2, 1.8)
    boundary = assign_caption_speaker(turns, 1.5, 2.5)
    missing = assign_caption_speaker(turns, 5.0, 6.0)

    assert first.label == "Person A"
    assert first.confidence == 1.0
    assert not first.overlap_detected
    assert boundary.label == "Unknown"
    assert boundary.overlap_detected
    assert missing.label == "Unknown"
    assert missing.speaker_index is None


def test_diarizer_wraps_model_turns_and_performance() -> None:
    class FakeResult:
        def sort_by_start_time(self):
            return [
                SimpleNamespace(start=0.1, end=1.0, speaker=0),
                SimpleNamespace(start=1.2, end=2.0, speaker=1),
            ]

    class FakeModel:
        sample_rate = 16_000

        def process(self, samples):
            assert samples.dtype == np.float32
            assert samples.flags.c_contiguous
            return FakeResult()

    diarizer = SpeakerDiarizer(model=FakeModel())
    result = diarizer.process(np.zeros(32_000, dtype=np.float64))

    assert result.audio_seconds == 2.0
    assert result.speaker_count == 2
    assert [turn.label for turn in result.turns] == ["Person A", "Person B"]
    assert result.processing_seconds >= 0.0


def test_diarizer_rejects_invalid_audio() -> None:
    class FakeModel:
        sample_rate = 16_000

        def process(self, samples):
            raise AssertionError("Invalid audio must not reach the model")

    diarizer = SpeakerDiarizer(model=FakeModel())
    with pytest.raises(ValueError, match="one-dimensional"):
        diarizer.process(np.zeros((4, 2), dtype=np.float32))
    with pytest.raises(ValueError, match="non-finite"):
        diarizer.process(np.array([0.0, np.nan], dtype=np.float32))
