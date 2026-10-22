"""Live microphone monitoring without recording or retaining audio."""

from __future__ import annotations

from collections.abc import Callable
import logging

import numpy as np
import sounddevice as sd

from app.audio.processing import AudioProcessingError, AudioProcessingWorker
from app.core.performance import (
    PerformanceReport,
    build_performance_report,
    format_mebibytes,
    take_process_snapshot,
)
from app.core.queues import BoundedAudioFrameQueue
from app.speech.recognition import (
    RecognitionError,
    RecognitionResult,
    RecognitionStats,
    RecognitionWorker,
)
from app.speech.vad import (
    SpeechEvent,
    SpeechSegment,
    VoiceActivityError,
    VoiceActivityStats,
    VoiceActivityWorker,
)
from app.translation.translator import (
    TranslationError,
    TranslationResult,
    TranslationStats,
    TranslationWorker,
)


logger = logging.getLogger(__name__)


class MicrophoneCaptureError(RuntimeError):
    """Raised when a microphone stream cannot start or stop safely."""


def calculate_input_level(samples: np.ndarray) -> int:
    """Convert float audio samples to a 0-100 meter value using dBFS."""
    if samples.size == 0:
        return 0

    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    decibels = 20.0 * np.log10(max(rms, 1e-6))
    return int(round(np.clip((decibels + 60.0) / 60.0 * 100.0, 0.0, 100.0)))


class MicrophoneLevelMonitor:
    """Capture microphone audio and coordinate local processing workers."""

    def __init__(
        self,
        device_indices: int | tuple[int, ...],
        level_callback: Callable[[float], None],
        warning_callback: Callable[[str], None],
        speech_event_callback: Callable[[SpeechEvent], None] | None = None,
        speech_segment_callback: Callable[[SpeechSegment], None] | None = None,
        caption_callback: Callable[[TranslationResult], None] | None = None,
        recognition_language: str | None = None,
        audio_buffer: BoundedAudioFrameQueue | None = None,
    ) -> None:
        self.device_indices = (
            (device_indices,) if isinstance(device_indices, int) else device_indices
        )
        if not self.device_indices:
            raise ValueError("At least one microphone backend index is required.")

        self.device_index = self.device_indices[0]
        self.active_device_index: int | None = None
        self._level_callback = level_callback
        self._warning_callback = warning_callback
        self._speech_segment_callback = speech_segment_callback
        self._caption_callback = caption_callback
        self.audio_buffer = audio_buffer or BoundedAudioFrameQueue(
            max_seconds=5.0,
            max_frames=2_048,
        )
        self.processed_audio_buffer = BoundedAudioFrameQueue(
            max_seconds=5.0,
            max_frames=2_048,
        )
        self.processing_worker = AudioProcessingWorker(
            source_queue=self.audio_buffer,
            output_queue=self.processed_audio_buffer,
            warning_callback=self._warning_callback,
            output_sample_rate=16_000,
        )
        self.recognition_worker = (
            RecognitionWorker(
                result_callback=self._handle_recognition_result,
                warning_callback=self._warning_callback,
                language=recognition_language,
            )
            if caption_callback is not None
            else None
        )
        self.translation_worker = (
            TranslationWorker(
                result_callback=caption_callback,
                warning_callback=self._warning_callback,
            )
            if caption_callback is not None
            else None
        )
        self.voice_activity_worker = (
            VoiceActivityWorker(
                source_queue=self.processed_audio_buffer,
                event_callback=speech_event_callback,
                warning_callback=self._warning_callback,
                segment_callback=self._handle_speech_segment,
            )
            if speech_event_callback is not None
            else None
        )
        self._stream: sd.InputStream | None = None
        self._active_sample_rate: int | None = None
        self._performance_start = None
        self.last_performance_report: PerformanceReport | None = None

    @property
    def is_running(self) -> bool:
        return self._stream is not None

    @property
    def performance_report(self) -> PerformanceReport | None:
        """Return current measurements while running, or the final measurements."""
        start = self._performance_start
        if start is not None:
            return build_performance_report(start, take_process_snapshot())
        return self.last_performance_report

    @property
    def voice_activity_stats(self) -> VoiceActivityStats | None:
        worker = self.voice_activity_worker
        return worker.stats if worker is not None else None

    @property
    def recognition_stats(self) -> RecognitionStats | None:
        worker = self.recognition_worker
        return worker.stats if worker is not None else None

    @property
    def translation_stats(self) -> TranslationStats | None:
        worker = self.translation_worker
        return worker.stats if worker is not None else None

    def start(self) -> None:
        """Open the selected microphone and begin level callbacks."""
        if self.is_running:
            return

        last_error: Exception | None = None
        for device_index in self.device_indices:
            stream: sd.InputStream | None = None
            try:
                stream = sd.InputStream(
                    device=device_index,
                    channels=1,
                    dtype="float32",
                    latency="low",
                    callback=self._handle_audio,
                )
                self._active_sample_rate = int(round(float(stream.samplerate)))
                stream.start()
            except Exception as error:
                last_error = error
                self._active_sample_rate = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue

            self._stream = stream
            self.active_device_index = device_index
            logger.info(
                "Microphone capture started: backend index=%d, sample rate=%d Hz",
                device_index,
                self._active_sample_rate,
            )
            self._performance_start = take_process_snapshot()
            self.last_performance_report = None
            if self.translation_worker is not None:
                self.translation_worker.start()
            if self.recognition_worker is not None:
                self.recognition_worker.start()
            if self.voice_activity_worker is not None:
                self.voice_activity_worker.start()
            self.processing_worker.start()
            return

        raise MicrophoneCaptureError(
            "The selected microphone could not be started through any available "
            "Windows audio backend. Check its connection, Windows microphone "
            "privacy settings, and whether another application is using it."
        ) from last_error

    def stop(self) -> None:
        """Stop and release the microphone stream."""
        stream = self._stream
        self._stream = None
        self.active_device_index = None
        self._active_sample_rate = None
        if stream is None:
            return

        stop_error: Exception | None = None
        try:
            stream.stop()
        except Exception as error:
            stop_error = error
        try:
            stream.close()
        except Exception as error:
            stop_error = stop_error or error

        try:
            self.processing_worker.stop()
        except AudioProcessingError as error:
            stop_error = stop_error or error

        if self.voice_activity_worker is not None:
            try:
                self.voice_activity_worker.stop()
            except VoiceActivityError as error:
                stop_error = stop_error or error

        if self.recognition_worker is not None:
            try:
                self.recognition_worker.stop()
            except RecognitionError as error:
                stop_error = stop_error or error

        if self.translation_worker is not None:
            try:
                self.translation_worker.stop()
            except TranslationError as error:
                stop_error = stop_error or error

        self._log_capture_summary()

        if stop_error is not None:
            raise MicrophoneCaptureError(
                "The microphone did not stop cleanly."
            ) from stop_error

    def cancel_pending_captions(self) -> int:
        """Discard queued recognition and translation jobs."""
        cancelled = 0
        if self.recognition_worker is not None:
            cancelled += self.recognition_worker.cancel_pending()
        if self.translation_worker is not None:
            cancelled += self.translation_worker.cancel_pending()
        return cancelled

    def _handle_audio(self, input_data, _frames, _time_info, status) -> None:
        if status:
            self._warning_callback(str(status))

        level = calculate_input_level(input_data)
        self._level_callback(float(level))

        sample_rate = self._active_sample_rate
        if sample_rate is None:
            return

        samples = np.asarray(input_data, dtype=np.float32)
        mono_samples = samples[:, 0] if samples.ndim == 2 else samples
        self.audio_buffer.put(mono_samples, sample_rate=sample_rate)

    def _handle_speech_segment(self, segment: SpeechSegment) -> None:
        if self._speech_segment_callback is not None:
            self._speech_segment_callback(segment)
        if self.recognition_worker is not None:
            self.recognition_worker.submit(segment)

    def _handle_recognition_result(self, result: RecognitionResult) -> None:
        if self.translation_worker is not None:
            self.translation_worker.submit(result)
        elif self._caption_callback is not None:
            self._caption_callback(
                TranslationResult(result, None, None, 0.0)
            )

    def _log_capture_summary(self) -> None:
        start = self._performance_start
        self._performance_start = None
        if start is None:
            return

        report = build_performance_report(start, take_process_snapshot())
        self.last_performance_report = report
        input_queue = self.audio_buffer.stats
        output_queue = self.processed_audio_buffer.stats
        processing = self.processing_worker.stats
        logger.info(
            "Microphone capture stopped: duration=%.1fs, average CPU=%.1f%%, "
            "memory start=%s, end=%s, change=%s, process peak=%s, "
            "processed input=%.1fs, output=%.1fs, capture queue dropped=%.3fs, "
            "processed queue dropped=%.3fs",
            report.elapsed_seconds,
            report.average_cpu_percent,
            format_mebibytes(report.memory_start_bytes),
            format_mebibytes(report.memory_end_bytes),
            format_mebibytes(report.memory_change_bytes),
            format_mebibytes(report.peak_memory_bytes),
            processing.input_seconds,
            processing.output_seconds,
            input_queue.dropped_seconds,
            output_queue.dropped_seconds,
        )
        vad_stats = self.voice_activity_stats
        if vad_stats is not None:
            logger.info(
                "Voice activity summary: model load=%.3fs, audio=%.3fs, "
                "windows=%d, average inference=%.3fms, real-time use=%.2f%%, "
                "speech starts=%d, speech ends=%d, completed segments=%d, "
                "short segments ignored=%d",
                vad_stats.model_load_seconds,
                vad_stats.processed_audio_seconds,
                vad_stats.processed_windows,
                vad_stats.average_inference_milliseconds,
                vad_stats.realtime_percent,
                vad_stats.speech_starts,
                vad_stats.speech_ends,
                vad_stats.completed_segments,
                vad_stats.filtered_short_segments,
            )
        recognition_stats = self.recognition_stats
        if recognition_stats is not None:
            logger.info(
                "Speech recognition summary: queued=%d, active=%d, completed=%d, "
                "failed=%d, processing time=%.3fs",
                recognition_stats.queued_segments,
                recognition_stats.active_segments,
                recognition_stats.completed_segments,
                recognition_stats.failed_segments,
                recognition_stats.processing_seconds,
            )
        translation_stats = self.translation_stats
        if translation_stats is not None:
            logger.info(
                "Translation summary: queued=%d, active=%d, completed=%d, "
                "failed=%d, processing time=%.3fs",
                translation_stats.queued_captions,
                translation_stats.active_captions,
                translation_stats.completed_captions,
                translation_stats.failed_captions,
                translation_stats.processing_seconds,
            )


