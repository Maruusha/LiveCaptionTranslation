"""Windows computer-output capture through WASAPI loopback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Event, Thread

import soundcard as sc

from app.audio.microphone import MicrophoneCaptureError, MicrophoneLevelMonitor
from app.speech.vad import SpeechEvent, SpeechSegment
from app.translation.translator import TranslationResult


LOOPBACK_SAMPLE_RATE = 48_000
LOOPBACK_CHANNELS = 2
LOOPBACK_BLOCK_FRAMES = 2_048

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutputAudioDevice:
    """One currently connected Windows speaker or headphone endpoint."""

    id: str
    name: str
    is_default: bool = False

    @property
    def display_name(self) -> str:
        default_suffix = " (Default)" if self.is_default else ""
        return f"{self.name}{default_suffix}"


def list_output_devices() -> list[OutputAudioDevice]:
    """Return connected playback endpoints with the Windows default first."""
    try:
        speakers = sc.all_speakers()
        default_speaker = sc.default_speaker()
        default_id = str(default_speaker.id)
    except Exception as error:
        raise MicrophoneCaptureError(
            "Windows speaker and headphone devices could not be read."
        ) from error

    devices = {
        str(speaker.id): OutputAudioDevice(
            id=str(speaker.id),
            name=" ".join(str(speaker.name).split()),
            is_default=str(speaker.id) == default_id,
        )
        for speaker in speakers
    }
    return sorted(
        devices.values(),
        key=lambda device: (
            not device.is_default,
            device.name.casefold(),
            device.id,
        ),
    )


def refresh_output_devices() -> list[OutputAudioDevice]:
    """Query fresh playback endpoint objects after a Windows device change."""
    return list_output_devices()


class LoopbackCaptureMonitor(MicrophoneLevelMonitor):
    """Feed Windows output sound into the existing local caption pipeline."""

    def __init__(
        self,
        output_device: OutputAudioDevice,
        level_callback: Callable[[float], None],
        warning_callback: Callable[[str], None],
        speech_event_callback: Callable[[SpeechEvent], None] | None = None,
        speech_segment_callback: Callable[[SpeechSegment], None] | None = None,
        caption_callback: Callable[[TranslationResult], None] | None = None,
        recognition_language: str | None = None,
        max_segment_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            0,
            level_callback,
            warning_callback,
            speech_event_callback,
            speech_segment_callback,
            caption_callback,
            recognition_language,
            max_segment_seconds,
        )
        self.output_device = output_device
        self._capture_stop = Event()
        self._capture_ready = Event()
        self._pipeline_ready = Event()
        self._capture_thread: Thread | None = None
        self._capture_error: Exception | None = None

    @property
    def is_running(self) -> bool:
        thread = self._capture_thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return

        self._capture_stop.clear()
        self._capture_ready.clear()
        self._pipeline_ready.clear()
        self._capture_error = None
        self._active_sample_rate = LOOPBACK_SAMPLE_RATE
        thread = Thread(
            target=self._capture_loop,
            name="wasapi-loopback-capture",
            daemon=True,
        )
        self._capture_thread = thread
        thread.start()

        if not self._capture_ready.wait(timeout=5.0):
            self._capture_stop.set()
            self._pipeline_ready.set()
            thread.join(timeout=1.0)
            self._capture_thread = None
            self._active_sample_rate = None
            raise MicrophoneCaptureError(
                "Computer audio capture did not start in time."
            )
        if self._capture_error is not None or not thread.is_alive():
            error = self._capture_error
            self._capture_thread = None
            self._active_sample_rate = None
            raise MicrophoneCaptureError(
                "The selected output device does not support WASAPI loopback "
                "capture. Try another connected speaker or headphone device."
            ) from error

        self._start_pipeline()
        self._pipeline_ready.set()
        logger.info(
            "Computer audio capture started: device=%s, sample rate=%d Hz",
            self.output_device.name,
            LOOPBACK_SAMPLE_RATE,
        )

    def stop(self) -> None:
        thread = self._capture_thread
        self._capture_thread = None
        self._active_sample_rate = None
        if thread is None:
            return

        self._capture_stop.set()
        self._pipeline_ready.set()
        thread.join(timeout=3.0)
        stop_error: Exception | None = None
        if thread.is_alive():
            stop_error = RuntimeError("WASAPI loopback capture did not stop.")
        elif self._capture_error is not None:
            stop_error = self._capture_error

        pipeline_error = self._stop_pipeline()
        stop_error = stop_error or pipeline_error
        self._log_capture_summary("Computer audio")
        if stop_error is not None:
            raise MicrophoneCaptureError(
                "Computer audio capture did not stop cleanly."
            ) from stop_error

    def _capture_loop(self) -> None:
        ready = False
        try:
            microphone = sc.get_microphone(
                self.output_device.id,
                include_loopback=True,
            )
            with microphone.recorder(
                samplerate=LOOPBACK_SAMPLE_RATE,
                channels=LOOPBACK_CHANNELS,
                blocksize=LOOPBACK_BLOCK_FRAMES * 2,
            ) as recorder:
                ready = True
                self._capture_ready.set()
                self._pipeline_ready.wait()
                while not self._capture_stop.is_set():
                    samples = recorder.record(numframes=LOOPBACK_BLOCK_FRAMES)
                    self._accept_audio(samples, LOOPBACK_SAMPLE_RATE)
        except Exception as error:
            self._capture_error = error
            if ready and not self._capture_stop.is_set():
                self._warning_callback(
                    f"Computer audio capture stopped unexpectedly: {error}"
                )
        finally:
            self._capture_ready.set()

