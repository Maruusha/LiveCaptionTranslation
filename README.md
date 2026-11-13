# Live Caption Translation

A Windows desktop app that creates live captions from a microphone or computer audio and translates between French and English. Speech and translation run locally after the models are installed. This is the first prototype version.

## Features

- Capture a microphone or the sound playing through a selected output device.
- Show captions and translations as chat messages or in an overlay.
- Pause and resume a recording, and revisit saved conversations.
- Optionally label speakers and remember voices across conversations.

The live speaker detector assigns one label per speech chunk. It does not reliably separate people speaking at the same time.

## Packages

| Package | Version | Purpose |
| --- | --- | --- |
| PySide6 | 6.12.0 | Desktop interface and overlay |
| NumPy | 2.5.4 | Audio data processing |
| sounddevice | 0.5.6 | Microphone capture |
| SoundCard | 0.4.6 | Computer audio capture |
| soxr | 1.1.0 | Audio sample-rate conversion |
| PyTorch (`torch`) | 2.14.1 | Tensor support for speech detection |
| ONNX Runtime | 1.31.0 | Runs the Silero speech detection model |
| silero-vad | 6.2.3 | Detects speech and silence |
| faster-whisper | 1.2.1 | Converts speech to captions |
| CTranslate2 | 4.8.3 | Runs speech recognition and translation models |
| PyAV (`av`) | 16.0.1 | Audio support for faster-whisper |
| SentencePiece | 0.2.2 | Prepares text for translation |
| sherpa-onnx | 1.12.33 | Speaker identification and offline diagnostics |

The development dependency **pytest 9.1.1** runs the automated tests. Python's built-in `sqlite3` module stores conversation history. The complete pinned package lists are in [requirements.txt](requirements.txt) and [requirements-dev.txt](requirements-dev.txt).

## Run the app

Use Windows and Python 3.12. From the project folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

If `.venv` is already set up, run only the last command.

The app also needs local model files in `models/`. They are not included in Git and must be installed separately; there is no automatic model downloader yet. The expected locations are:

```text
models/speech/faster-whisper-small/
models/translation/opus-mt-en-fr-ct2-int8/
models/translation/opus-mt-fr-en-ct2-int8/
models/speakers/nemo_en_titanet_small.onnx  # optional speaker detection
```

See speaker model details and [PLAN.md](PLAN.md) for more information.

## Data and tests

Conversation history and voice signatures are stored locally in `data/history.sqlite3`. Raw audio is processed in memory and is not saved as a recording. Logs are stored in `logs/`.

To install test tools and run the tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

See the project diagram for the technology stack and data flow.