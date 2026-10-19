import numpy as np

from app.core.queues import BoundedAudioFrameQueue
from app.speech.vad import SpeechEvent, VoiceActivityWorker


def test_vad_worker_emits_speech_boundaries() -> None:
    source = BoundedAudioFrameQueue(max_seconds=2.0)
    source.put(np.ones(1_536, dtype=np.float32), sample_rate=16_000)
    events: list[SpeechEvent] = []
    warnings: list[str] = []
    results = iter(({"start": 412}, None, {"end": 1_124}))

    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=events.append,
        warning_callback=warnings.append,
        detector_factory=lambda: lambda _samples: next(results),
    )
    worker.start()
    worker.stop()

    assert [event.kind for event in events] == ["speech_start", "speech_end"]
    assert [event.sample_index for event in events] == [412, 1_124]
    assert events[0].audio_seconds == 412 / 16_000
    assert worker.stats.processed_windows == 3
    assert worker.stats.processed_audio_seconds == 1_536 / 16_000
    assert worker.stats.speech_starts == 1
    assert worker.stats.speech_ends == 1
    assert worker.stats.average_inference_milliseconds >= 0.0
    assert warnings == []


def test_vad_worker_closes_active_speech_when_capture_stops() -> None:
    source = BoundedAudioFrameQueue()
    source.put(np.ones(512, dtype=np.float32), sample_rate=16_000)
    events: list[SpeechEvent] = []

    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=events.append,
        warning_callback=lambda _warning: None,
        detector_factory=lambda: lambda _samples: {"start": 0},
    )
    worker.start()
    worker.stop()

    assert [event.kind for event in events] == ["speech_start", "speech_end"]
    assert events[-1].sample_index == 512
    assert worker.stats.speech_starts == 1
    assert worker.stats.speech_ends == 1
