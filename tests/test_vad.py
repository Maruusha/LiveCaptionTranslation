import numpy as np

from app.core.queues import BoundedAudioFrameQueue
from app.speech.vad import SpeechEvent, SpeechSegment, VoiceActivityWorker


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


def test_vad_worker_keeps_selected_speech_threshold() -> None:
    source = BoundedAudioFrameQueue(max_seconds=1.0)
    source.put(np.zeros(512, dtype=np.float32), sample_rate=16_000)
    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=lambda _event: None,
        warning_callback=lambda _warning: None,
        detector_factory=lambda: lambda _samples: None,
        speech_threshold=0.3,
    )

    worker.start()
    worker.stop()

    assert worker.speech_threshold == 0.3


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


def test_vad_worker_keeps_timestamps_continuous_after_restart() -> None:
    source = BoundedAudioFrameQueue(max_seconds=2.0)
    events: list[SpeechEvent] = []
    detector_runs = iter(
        (
            iter(({"start": 0},)),
            iter(({"start": 0}, {"end": 800})),
        )
    )

    def detector_factory():
        results = next(detector_runs)
        return lambda _samples: next(results)

    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=events.append,
        warning_callback=lambda _warning: None,
        detector_factory=detector_factory,
        min_segment_seconds=0.0,
    )
    source.put(np.ones(512, dtype=np.float32), sample_rate=16_000)
    worker.start()
    worker.stop()

    source.put(np.ones(1_024, dtype=np.float32), sample_rate=16_000)
    worker.start()
    worker.stop()

    assert [event.sample_index for event in events] == [0, 512, 512, 1_312]
    assert worker.stats.processed_windows == 3


def test_vad_worker_builds_a_read_only_completed_speech_segment() -> None:
    source = BoundedAudioFrameQueue(max_seconds=2.0)
    source.put(np.arange(1_024, dtype=np.float32), sample_rate=16_000)
    segments: list[SpeechSegment] = []
    results = iter(({"start": 100}, {"end": 900}))

    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=lambda _event: None,
        warning_callback=lambda _warning: None,
        segment_callback=segments.append,
        detector_factory=lambda: lambda _samples: next(results),
        min_segment_seconds=0.0,
    )
    worker.start()
    worker.stop()

    assert len(segments) == 1
    assert segments[0].start_sample == 100
    assert segments[0].end_sample == 900
    assert segments[0].samples.size == 800
    assert segments[0].samples[0] == 100
    assert not segments[0].samples.flags.writeable
    assert worker.stats.completed_segments == 1


def test_vad_worker_splits_a_long_speech_segment_at_the_memory_limit() -> None:
    source = BoundedAudioFrameQueue(max_seconds=2.0)
    source.put(np.ones(1_536, dtype=np.float32), sample_rate=16_000)
    segments: list[SpeechSegment] = []
    results = iter(({"start": 0}, None, None))

    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=lambda _event: None,
        warning_callback=lambda _warning: None,
        segment_callback=segments.append,
        detector_factory=lambda: lambda _samples: next(results),
        max_segment_seconds=1_024 / 16_000,
        min_segment_seconds=0.0,
    )
    worker.start()
    worker.stop()

    assert [segment.samples.size for segment in segments] == [1_024, 512]
    assert segments[0].forced_split
    assert not segments[1].forced_split
    assert worker.stats.completed_segments == 2


def test_vad_worker_filters_a_very_short_non_speech_segment() -> None:
    source = BoundedAudioFrameQueue(max_seconds=2.0)
    source.put(np.ones(1_024, dtype=np.float32), sample_rate=16_000)
    segments: list[SpeechSegment] = []
    results = iter(({"start": 100}, {"end": 2_000}))

    worker = VoiceActivityWorker(
        source_queue=source,
        event_callback=lambda _event: None,
        warning_callback=lambda _warning: None,
        segment_callback=segments.append,
        detector_factory=lambda: lambda _samples: next(results),
    )
    worker.start()
    worker.stop()

    assert segments == []
    assert worker.stats.completed_segments == 0
    assert worker.stats.filtered_short_segments == 1
