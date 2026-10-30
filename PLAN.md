# Local French ↔ English Live Captions

Updated: 2026-10-30. Current branch: `feat/packaging`. Packaging has not started.

## Goal and rules

A Windows desktop app for local microphone/computer-audio captions, French ↔ English translation, optional speaker labels, and saved conversations.

- Run offline after models are installed. Keep audio, text, and voice signatures local; do not save raw audio by default.
- Save every final caption and visible skip/error to history; users can delete conversations afterward.
- Work and verify in small stages. Finish major runtime validation before the WhatsApp-inspired UI redesign, then package the app.
- Use project-local environments, packages, models, caches, and helpers. Use `.venv`; use local `.conda` only if needed. Do not install globally.
- Keep one Whisper worker: multiple workers previously harmed game performance on this PC.
- Make Git changes only when requested. Keep the current branch, main, and develop locally.

## Stack and files

| Component | Current choice |
|---|---|
| UI | PySide6 widgets |
| Microphone / computer output | sounddevice / SoundCard WASAPI loopback |
| Conversion / speech detection | soxr to mono 16 kHz / package-bundled Silero ONNX VAD |
| Captions | faster-whisper small, CPU INT8, four CPU threads, one worker |
| Translation | Marian OPUS-MT FR→EN and EN→FR through CTranslate2 INT8 |
| Live speaker labels | sherpa-onnx TitaNet embeddings, one CPU thread |
| Offline speaker diagnostic | sherpa-onnx segmentation + embeddings, two CPU threads |
| History | SQLite, WAL, FTS5, schema version 3 |
| Packaging | PyInstaller planned |

Direct dependencies are pinned in requirements.txt; test tools are in requirements-dev.txt. CTranslate2 is explicitly pinned. Speaker model sources/hashes are in app/speakers/MODELS.md.

```text
.venv/                 Local Python environment
.cache/                Package/download/test caches
models/speech/         faster-whisper-small
models/translation/    OPUS-MT CTranslate2 INT8, both directions
models/speakers/       TitaNet, segmentation model, evaluation sample
data/history.sqlite3   Conversations and speaker data
logs/application.log   Rotating diagnostics
vendor/ or tools/      Future bundled helpers
```

These generated/local directories stay outside Git. UI settings use Qt's application configuration directory unless LIVE_CAPTION_SETTINGS_PATH overrides it; LIVE_CAPTION_HISTORY_PATH overrides history. Tests use in-memory stores.

GitHub Desktop Git on this PC: `C:\Users\duong\AppData\Local\GitHubDesktop\app-3.6.7\resources\app\git\cmd\git.exe`.
Locate the newest app-* copy after Desktop updates; never hardcode it in the application.

Run: `.venv\Scripts\python.exe -m app.main`.
Test: `.venv\Scripts\python.exe -m pytest`.

## Processing and controls

```text
Microphone OR selected computer output
  → bounded native queue → mono 16 kHz → bounded processed queue
  → Silero VAD → completed speech chunks
      ├→ one Whisper worker ──────────┐
      └→ optional speaker embedding ─┤
                       ordered timestamp join
                         → FR/EN language gate
                         → translation → UI + SQLite
```

- Only one source is active. Computer audio covers the selected speaker/headphone endpoint: browsers, players, calls, games. Protected media/unusual drivers may need a virtual cable. Per-app capture is future work.
- Audio queues hold five seconds each; recognition, speaker, and translation queues hold eight jobs each. Overflow drops old unprocessed audio or creates a visible skipped-caption/translation warning.
- Speech chunks: user maximum 5–60 seconds, default 30; silence can finish sooner. Silero defaults: threshold 0.5, 400 ms end silence, 100 ms padding, 200 ms minimum segment. Sensitivity adjusts the threshold.
- Caption results preserve audio order through speaker joining and translation, including warnings/unsupported languages. Drain worker/UI handoff before closing a session.
- Automatic mode detects each chunk's language. Manual French/English modes force the source. Translate only normalized fr/en; keep other languages original-only and count them as unsupported.
- Pause stops capture and finalizes VAD audio. Caption queues keep processing. Resume reuses the session, models, and speaker clusters without waiting for caption queues. Cold VAD loading can still delay the initial pause boundary.
- Stop drains work in the background. Show queue counts and an approximate time. **Hide progress** or X hides the dialog; **Skip remaining** cancels queued work while an active model call finishes safely.
- Shutdown waits for actual pipeline completion instead of releasing the session after a fixed model timeout. Audio/VAD failures mark it incomplete. Stop keeps the app open; accepted main-window close requests exit.
- Runtime states: idle, starting, live, pausing, paused, resuming, stopping, error. Database statuses: live, paused, incomplete, completed. Startup recovers interrupted live/paused sessions as incomplete.

## UI and speaker behavior

- Saved conversations on the left: newest first, search, date/preview/status, rename, delete/delete-all, maintenance, manual Refresh. Messages on the right show speaker, time, original, and translation.
- Refresh preserves selection, search, and both scroll positions. Follow new messages only when already near the bottom; **To bottom** jumps to the latest. Clear screen affects visible captions only.
- Persist source/device, language/display modes, chunk/sensitivity/voice-match controls, speaker detection, window/sidebar layout, and overlay appearance/visibility/geometry. Never start capture automatically; missing devices fall back to available ones.
- Overlay: movable, resizable, always on top; configurable font, colours, opacity, click-through. DPI/multiple-monitor/game checks remain open.
- Keep speakers left-aligned. Labels continue Person A…Z, AA, AB… without an application-level cap. A future explicit **You** action may right-align one speaker; never assume the first voice is the user.
- Detection is optional, initially off, and restored if previously enabled. Compare each chunk with up to five signatures per session speaker and approved remembered profiles. Voice match 1–9 maps to similarity 0.49–0.81; default 5 = 0.65. Speech shorter than 0.8 seconds stays Unknown.
- **Live limitation:** one embedding/label per chunk. Live mode does not reliably split speaker changes within a chunk or flag overlap. The separate offline diagnostic supports timeline analysis. Mixed-chunk handling, overlap flags, quality filtering, candidate confirmation, and false-match tuning remain unfinished.
- Create a permanent person only through **Remember or rename speaker…**. Manager actions: rename, recognition enable/disable, merge, Forget voice, delete. Voice matching is a guess, never identity proof.
- Management works idle or after paused caption work drains. Workers reload approved changes before the next chunk. Forget/delete keep conversation text and restore Person labels. Merge keeps up to five distinct signatures. Session signatures can refresh an approved profile; stronger quality filtering remains pending.
- Current captions are final chunks only. Future previews must update one stable bubble, avoid saving/translating revisions, and replace it with final text. Manual editing is separate future work.

## History, logging, and performance

- Tables: sessions, session_events, messages, session_persons, session_voice_signatures, persons, person_voice_signatures. Store UTC; display local time. Pause/resume preserve continuous audio offsets.
- FTS5 searches caption/translation text alongside title search. Closed-session deletion cascades through session data. Approved signatures survive conversation deletion with null provenance; delete-all preserves active sessions.
- Use transactions, foreign keys, indexes, ordered migrations, pre-migration backups, and quick integrity checks. Never silently replace damaged history. Maintain checks/optimizes/vacuums healthy databases.
- Log to terminal and logs/application.log: four files total (current + three backups), 2 MiB each, about 8 MiB total. Native crash tracing follows the current log across rotation. Include lifecycle, device changes, manual refresh, drops, warnings, timings. Never log raw audio, caption text, signatures, or secrets.
- Simple stats cover wall duration, average process CPU, memory/change/process peak, VAD timing, queues, captions, translations, speakers. CPU above 100% reflects multiple cores; peak memory belongs to the entire process.
- Review improvements: remove full saved-history scans per live caption; batch history rendering; limit meter updates to 20 Hz; remove old pause-dialog/offset/fallback code; clean up Qt test windows on the GUI thread after each test.

Latest completed live run (2026-10-30): 67 chunks, 65 English→French captions/translations, one overload skip and one user skip; zero translation failures/audio-queue drops. Speaker results: 63 labelled, four Unknown, zero worker failures. Pause/resume and manual refresh continued processing in one session. Average CPU about 331%, final memory 372 MiB, process peak about 1.50 GiB. Counts show processing, not accuracy. Earlier FR→EN runs also completed; cold starts previously lost processed audio, and gaming was affected.

Automated baseline: **118 passing tests** plus successful source compilation. Synthetic tests do not close the live checks below. No new long stability recording was performed during review.

Startup investigation (2026-10-30): Windows confirmed a native Python access violation just after Start computer audio. Eight subsequent short starts completed with captions, translations, speaker labels and clean shutdown; the original failure has not been reproduced. Removed temporary self-capturing chat timer callbacks in favour of one owned timer and enabled persistent native traces. Treat this as an open intermittent defect until further live testing confirms stability.

The updated build then captured 11 seconds of computer audio, completed two captions/translations/speaker results with zero failures or dropped audio, and stopped while leaving the app open.

## Stages and remaining work

| Stage | Status | Remaining |
|---|---|---|
| 0 — Foundation | Implemented | Refresh idle baseline for packaging |
| 1 — Microphone | Implemented | 30-minute stability, memory, device-loss test |
| 2 — VAD | Basic milestone complete | Optional 400 vs 700 ms silence tuning |
| 3 — Recognition | Basic milestone complete | Quality/overload tuning on this CPU |
| 4 — Translation | Both directions run | Human quality review: names, numbers, accents, long speech, direction switching; offline test |
| 5 — Computer audio | Browser runs pass; intermittent Start crash open | Capture native failure trace/retest; player/call, device switch/disconnect, long run, cold-start loss, game CPU/FPS and clean Stop |
| 6 — Controls | Recent Pause/Resume/Stop runs pass | Live retest after review; queued Close/Hide progress and loaded-game Stop |
| 7 — Chat/overlay | Foundation implemented | Live refresh/settings restoration, DPI/multi-monitor/game/resizing; planned WhatsApp-style redesign |
| 8 — History | Current milestone complete | Normal-use checks and packaged migration validation |
| 9 — Session speakers | Live labels implemented | Ground-truth 2/3/4+ speakers, merges/splits, similar voices, short turns, overlap/mixed chunks |
| 10 — Remembered people | Management implemented; initial reuse observed | Confirm known/unknown voices across sessions; manager actions, quality filtering, false accepts/rejects |
| 11 — Packaging | Not started | Windows package, first-run downloader, hash/file verification, disk/progress UI, clean-PC/offline tests, upgrades/uninstall preserving history |

Finish runtime quality/device tests before the full UI redesign, then display/stability checks and packaging. Include quiet/noisy/music/silence inputs, short/overlapping speech, deliberate overload, queued Stop/Close, crash recovery, deletion/migration/backups, and large history where relevant.

## Deferred work

- Local voice separation; per-app capture.
- Caption/speaker/translation corrections; temporary previews; speaker colours; explicit You alignment.
- Export text/Markdown/SRT/VTT, shortcuts, consent-based recording.
- Settings import/export and user-managed history backup/restore.
- Advanced model management/rollback, portable builds, graphs/history metrics, terminology lists, macOS/Linux.
- Fast/Balanced/Accurate presets; automatic hardware/model choice, RAM-based chunk sizes, multiple Whisper workers/scaling on stronger hardware.
- French ↔ Vietnamese only after FR↔EN is stable: reuse the pipeline and add vi models, language choices, font checks, language-specific tests later.
