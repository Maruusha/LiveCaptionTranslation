# Windows package — development build

Stage 11's first slice creates a folder containing LiveCaptionTranslation.exe
and its `_internal` dependencies. Keep the entire folder together. Python and
Conda are not required to run it. The build is unsigned and is not yet an installer.
The intermittent native audio-start defect remains under investigation.

## Models and offline use

Place the existing `models` folder beside the executable, or launch with:

```powershell
.\LiveCaptionTranslation.exe --models 'E:\Projects\LiveCaptionTranslation\models'
```

Required models: faster-whisper-small, OPUS-MT en-fr and fr-en CTranslate2 INT8.
Speaker detection additionally needs nemo_en_titanet_small.onnx.
Silero's ONNX model is included with the application dependencies.
No automatic downloads occur in this build. First-run download/setup is a later slice.

## Private data and updates

Packaged conversations are stored in
`%LOCALAPPDATA%\LiveCaptionTranslation\data\history.sqlite3`.
Logs and caches use the same root. Existing development-project history is not
automatically copied or modified. Qt UI preferences use its existing app configuration
directory. `--data-dir PATH` changes the history/log/cache root for testing;
`LIVE_CAPTION_HISTORY_PATH` and `LIVE_CAPTION_SETTINGS_PATH` can override individual stores.

Close the application before replacing its program folder. Preserve the external
models and user-data directories when upgrading. Removing the program folder does
not delete saved conversations. Automated upgrades/uninstall are not implemented yet.

## Diagnostics

Run a no-audio self-check, including model hashes and actual model loading:

```powershell
.\LiveCaptionTranslation.exe --self-check --verify-models --load-models --models 'PATH\TO\models' --report '.\package-check.json'
```

Reports contain file/library status and errors, never caption text or audio.
The build manifest detects missing/changed model files; it is not a signed authenticity
guarantee. Omit `--verify-models` for other compatible model revisions.

`--smoke-test --report PATH` opens and closes the UI with an in-memory history/settings
store, without recording. `--data-dir PATH` can isolate diagnostic logs too.
For a windowed executable, use PowerShell `Start-Process -Wait -PassThru` to obtain
the final exit code. Native crash traces are written into the rotating application log.

## Rebuild from source

```powershell
.\.venv\Scripts\python.exe -m pip install --cache-dir .cache\pip -r requirements-build.txt
.\.venv\Scripts\python.exe scripts\build_windows.py
# Optionally add --include-models, if there is enough free disk space.
# Or add models to an existing matching build without rebuilding:
.\.venv\Scripts\python.exe scripts\build_windows.py --stage-models
```

Builds/caches remain inside the project. Do not distribute source history, saved
settings, logs, caches, or personal voice profiles. Before a public release, review
third-party library/model licences, test a clean Windows PC without Python/Conda,
and complete the remaining live quality/stability tests.

Build reference: [PyInstaller runtime paths](https://pyinstaller.org/en/stable/runtime-information.html).
