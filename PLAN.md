# Local French ↔ English Live Captions

## Goal

Build a Windows desktop application that runs locally, listens to a microphone or computer output, captions French and English speech, translates between them, identifies speakers when possible, and saves conversations for later reading.

Core rules:

- Work offline after models are downloaded.
- Keep audio, captions, translations, and voice signatures local.
- Do not save raw audio by default.
- Save every final caption to local history; the user may delete it afterward.
- Build and verify one stage at a time.
- Keep packages, environments, models, caches, and helper tools inside the project whenever possible.

## User experience

The main window uses a familiar chat layout:

```text
Conversations                 Selected conversation
-------------------------     --------------------------------
Live conversation             Person A · 14:32
Today                         FR: Bonjour
  French lesson               EN: Hello
  Team meeting
Yesterday                     Person B · 14:33
  Game dialogue               EN: How are you?
                              FR: Comment allez-vous ?
```

- Saved conversations appear on the left, newest first, with search, date groups, preview, and status.
- The selected live or saved conversation appears on the right.
- Completed turns appear as message bubbles with time, speaker, original text, and translation.
- New messages scroll into view only when the user is already near the bottom; **To bottom** jumps to the newest message.
- **Clear screen** clears only the visible captions, never saved history.
- The conversation sidebar can be hidden and resized.
- A separate movable, resizable, always-on-top overlay shows the latest caption. Its font, colours, opacity, click-through mode, size, and position are saved.
- Display modes are original, translation, or both, and apply to existing bubbles too.

All speakers stay left-aligned by default. Two-sided alternating bubbles do not scale to many speakers. A later microphone-only action may let the user mark one speaker as **You** and place only that speaker on the right. The app must never guess that the first voice is the user.

## Technology and local storage

| Part | Choice |
|---|---|
| Interface | PySide6 |
| Microphone | sounddevice |
| Computer output | SoundCard with Windows WASAPI loopback |
| Voice activity | Silero VAD |
| Recognition | faster-whisper small, CPU INT8 |
| Translation | Marian OPUS-MT through CTranslate2, CPU INT8 |
| Speaker detection | sherpa-onnx with local segmentation and embedding models |
| History | SQLite with WAL and FTS5 |
| Packaging | PyInstaller, later |

Project-local layout:

```text
.venv/                 Python environment
.cache/                Download and test caches
models/speech/         Speech-recognition models
models/translation/    Translation models
models/speakers/       Speaker models and evaluation sample
vendor/ or tools/      Future bundled native helpers
data/history.sqlite3   Local conversation history
logs/application.log   Rotating diagnostics
```

Do not install packages globally. Use `.venv/`; only use a project-local `.conda/` if Conda becomes necessary. Large environments, models, caches, logs, data, and generated binaries stay out of Git.

The Git executable supplied by GitHub Desktop on this development computer was recorded as:

```text
C:\Users\duong\AppData\Local\GitHubDesktop\app-3.6.3\resources\app\git\cmd\git.exe
```

GitHub Desktop may change the `app-*` directory after an update, so locate its newest copy if that path no longer exists. Do not build this machine-specific path into the application.

## Audio and processing design

Only one source is active at a time:

- **Microphone:** any currently connected Windows input device.
- **Computer audio:** the full selected speaker or headphone output through WASAPI loopback. This covers most browsers, media players, calls, and games. Protected media or unusual drivers may require a virtual audio cable. Per-application capture is deferred.

Current caption path:

```text
Microphone or computer output
  → bounded native-audio queue
  → mono 16 kHz conversion
  → bounded processed-audio queue
  → Silero VAD and completed speech chunk
  → one faster-whisper worker
  → French/English language gate
  → bounded Marian translation queue
  → display and SQLite history
```

Target speaker path for Stage 9:

```text
Timestamped audio
  ├→ VAD → Whisper ──────────────┐
  └→ speaker analysis in parallel ├→ timestamp alignment → message
                                  ┘
```

Speaker analysis must not wait for recognition. Join the two results by audio timestamps.

### Bounded queues and stopping

- The audio callback must never wait for a model, database, or interface.
- Every background queue has a fixed item or duration limit.
- If audio overflows, drop the oldest unprocessed live audio so the app can catch up.
- If final recognition overloads, show `[caption skipped: processing too slow]`; never fail silently.
- Optional provisional work is the first work discarded under load.
- **Pause** stops new audio, finishes already accepted final work, records a pause event, and keeps the same session open.
- **Resume** appends to that session. The current lower-memory design reloads processing models on resume.
- **Stop** drains accepted final work before completing the session.
- Stop and close remain responsive and show remaining work plus an estimated finish time. The user may skip queued captions; a model call already running finishes safely.
- Capture queues hold at most five seconds. Speech chunks have a user-selected 5–60 second maximum, default 30 seconds. Silence may finish them earlier.
- A distant-future option may adapt the chunk limit to available RAM and model size.

The explicit runtime states are `idle`, `starting`, `live`, `pausing`, `paused`, `resuming`, `stopping`, and `error`. SQLite session statuses are separate: `live`, `paused`, `incomplete`, and `completed`. On startup, sessions left `live` or `paused` by a crash become `incomplete`.

## Language behavior

The active modes are:

1. **Automatic:** Whisper detects the language for each final phrase.
2. **French:** treat input as French and translate to English.
3. **English:** treat input as English and translate to French.

Only normalized `fr` and `en` results enter translation. Other or unknown languages remain visible as original-only captions and count as unsupported, not failed. Manual modes help with short phrases, names, and mixed-language speech.

Language-dependent components use language codes and separate recognition and translation interfaces so another pair does not require rebuilding audio, chat, history, or speaker code. French ↔ Vietnamese is explicitly distant future; do not add its models or interface yet.

## Temporary and final captions

The current implementation shows final VAD chunks only. A later slice may add rolling previews:

- Open one temporary bubble at speech start.
- Update that same bubble from periodic recognition snapshots.
- Do not translate, save, search, or export temporary revisions.
- At speech end, replace it with one final recognition, then translate and save it.
- Use a stable message ID so preview and final text never create duplicate bubbles.
- A later speaker result may update the final bubble’s label without changing its ID.
- If final recognition fails, show a clear failure marker rather than invented text.

This is automatic correction during recognition, not user editing. Manual caption editing is distant future.

## Speakers

Session labels are `Person A` through `Person Z`, then `Person AA`, `Person AB`, and onward. There is no application-level speaker cap; practical accuracy and capacity depend on audio and hardware.

Recommended session rule:

1. VAD provides clear speech.
2. The speaker model creates a voice embedding.
3. Compare it with several good signatures for current-session speakers.
4. Reuse an existing label only for a strong match.
5. Keep uncertain speech as `Unknown`.
6. Create a new person only after enough clear speech supports a new voice.
7. Do not update profiles from very short, noisy, musical, or overlapping audio.

Use separate match and new-person thresholds to prevent rapid label switching. Labels and colours remain stable inside a session but reset next session by default.

Overlapping voices are much harder because they are mixed. The first release should flag overlap and use `Unknown / overlapping` when assignment is uncertain. Perfect separation is not required. Local voice separation is deferred.

### Remembered people

Cross-session recognition is opt-in only:

- Create a permanent person only after **Remember this person**.
- A user may name, rename, merge, forget, disable, or delete that profile.
- A session-only name does not create a permanent identity.
- Keep several approved high-quality signatures, not raw audio.
- A voice match is a best guess, never proof of identity.
- **Forget voice** removes approved signatures and old identity links while preserving messages and original session labels.
- Deleting a profile also preserves old conversation text and labels.

## History database

SQLite stores timestamps in UTC; the UI converts them to local time.

| Table | Purpose |
|---|---|
| `persons` | Opt-in reusable profiles and user-defined names |
| `person_voice_signatures` | Approved cross-session embeddings and model metadata |
| `sessions` | Title, times, status, source, language mode, and model settings |
| `session_events` | Pause, resume, device changes, and stop |
| `session_persons` | Temporary label, colour, optional profile link, and `You` flag |
| `session_voice_signatures` | Session-only embeddings for stable labels |
| `messages` | Timing, speaker, language, caption, translation, warning, and overlap data |

Rules:

- Create the session when capture starts and save every final or warning caption immediately.
- Never place raw audio in the database.
- Search titles, original captions, and translations with FTS5.
- A conversation delete cascades through its messages, events, session people, and session signatures.
- An approved person signature survives conversation deletion; only its provenance link becomes null.
- Delete-all preserves an active live or paused session.
- Use schema versions, ordered migrations, transactions, indexes, foreign keys, and a pre-migration backup.
- Run a lightweight integrity check and never silently replace or repair a damaged database.
- The **Maintain** action checks integrity, optimizes, and vacuums only when healthy.

## Diagnostics and performance

The simple performance view shows duration, average process CPU, current memory, memory change, process peak, VAD timing, queue counts, failures, and translation totals. Graphs and detailed historical metrics are deferred.

Diagnostic logging rules:

- Write to the terminal and `logs/application.log`.
- Rotate each file at 2 MiB.
- Keep four files total: current plus `.1`, `.2`, and `.3` backups, about 8 MiB maximum.
- Log startup, shutdown, device changes, queue drops, model warnings, and errors.
- Never log raw audio, caption text, conversation text, voice signatures, or secrets.

Keep one Whisper worker for now. A two-worker experiment harmed game performance on this computer and cannot be validated safely here. Revisit parallel workers and automatic scaling only on stronger hardware.

Future manual presets may be **Fast**, **Balanced**, and **Accurate**. Automatic PC inspection and model selection by CPU, RAM, and GPU are distant-future work.

## Development stages

Each stage needs automated tests plus proportional live CPU, RAM, disk, latency, and behavior checks before it is closed.

### Stage 0 — Local foundation

Implemented: project-local environment, application shell, logging, and test setup. A formal idle baseline can be refreshed when packaging work begins.

### Stage 1 — Microphone capture

Implemented: device discovery, selection preservation, capture, mono conversion, level meter, bounded queues, disconnection handling, and performance summary.

Remaining: a 30-minute live stability run without growing memory, freezing, or unexpected device loss.

### Stage 2 — Voice activity detection

**Complete for the basic milestone (2026-10-22).** Silero VAD runs locally with a sensitivity slider, 400 ms end silence, 100 ms padding, 200 ms minimum segment, and safe chunk splitting. A 429-second live run produced 43 starts, 43 ends, and 43 chunks with no audio-queue loss. VAD used about 3.53% of real time.

Later tuning: compare 400 ms with about 700 ms end silence. Aim for useful 3–8 second turns without confusing this with the maximum chunk length.

### Stage 3 — French and English recognition

**Complete for the basic milestone (2026-10-22).** faster-whisper 1.2.1 uses the project-local multilingual small model, CPU INT8, four CPU threads, one worker, and an eight-item queue. The 429-second live run completed 41 of 43 jobs; two were visibly skipped during a dense burst. Recognition took about 5–12 seconds per caption on this computer. This completion rate is not transcription accuracy.

### Stage 4 — French ↔ English translation

**Implementation complete; live quality testing pending.** Project-local OPUS-MT English→French and French→English models run through CTranslate2 INT8. Model loading measured about 0.36–0.41 seconds and a small smoke translation about 0.04 seconds. Test accents, names, numbers, punctuation, longer speech, direction switching, automatic-language mistakes, offline use, and live resource cost.

### Stage 5 — Computer-output capture

**In progress.** WASAPI loopback uses the same caption pipeline as the microphone. Initial browser/game-style runs proved capture and device handling, but the game test averaged about 244% process CPU, ended near 689 MiB, peaked near 1.29 GiB, sometimes reduced the game to 15 FPS, and lost processed audio during slow model startup. One active recognition also exceeded the 60-second stop timeout, so that session correctly became incomplete.

Remaining: browser video, local player, voice call, game, device switch/disconnect, French/English quality, long-run queues, repaired memory reporting, and clean stop/close tests.

### Stage 6 — Session controls

**Implemented; loaded live tests pending.** Pause/resume/stop use the explicit state machine, preserve one history session and continuous offsets, record events, and remain responsive while accepted captions finish. Automated coverage includes repeated transitions and stop from paused. The game-load clean-stop case still needs improvement or a successful retest.

### Stage 7 — Chat interface and overlay

**Implemented foundation; physical-display tests pending.** The chat, saved choice restoration, collapsible sidebar, overlay, styling controls, click-through, and layout regression checks are present.

Remaining: high-DPI, multiple-monitor, windowed/borderless game, and full-pipeline responsiveness tests. Automatic speaker colours, `You`, and temporary-to-final bubble replacement belong to later slices.

### Stage 8 — Local history and recovery

**Complete (2026-10-25).** Schema version 2 provides immediate caption persistence, crash recovery, pause/resume events, sidebar/history reading, indexed search, rename, confirmed deletion, maintenance, migrations, backups, cascade rules, and large-history tests.

### Stage 9 — Multiple speakers in one session

**In progress; isolated model slice complete (2026-10-25).** sherpa-onnx 1.12.33 and project-local INT8 segmentation plus TitaNet embedding models are installed. The isolated wrapper provides unlimited labels, diarization, timestamp assignment, and conservative unknown/overlap results. The 16-second public two-speaker sample found two speakers and four turns in 0.488 seconds after a 0.231-second load, ending near 171 MiB.

Next: add a bounded speaker worker beside Whisper, retain several session signatures, join by sample timestamps, persist session people/signatures, and test combined CPU/RAM, delay, merges, splits, overlap, and 2/3/4+ speakers. Keep it experimental and disabled by default until those checks pass.

### Stage 10 — Remembered people

Planned after stable session diarization: opt-in profiles, approved signatures, naming, merging, forgetting, deleting, uncertain matches, and false-accept/false-reject tests.

### Stage 11 — Packaging and offline setup

Planned: Windows package, first-run model downloader, file verification, disk-space/progress display, offline verification, upgrades, uninstall, and history preservation.

## Test checklist

- French and English microphone speech, accents, names, numbers, and punctuation.
- Quiet/loud speech, short phrases, noise, music, and silence.
- French/English speakers taking turns and overlapping.
- More than two speakers with stable labels.
- Browser, local-player, call, and game output.
- Speaker/headphone changes and disconnections.
- Repeated pause/resume; stop/close with queued work.
- Deliberate overload and visible skipped-caption messages.
- Crash recovery from live and paused sessions.
- Migration, interrupted write, backup copy, deletion rules, and large history.
- High-DPI and multiple-monitor overlay behavior.
- Offline operation, CPU-only operation, and later a supported GPU computer.

Current automated baseline after Stage 9’s isolated slice and queue cleanup: 90 tests. Update this single number only after the full suite passes; do not keep old incremental test-count history.

## Deferred and distant-future work

- Separate overlapping voices locally.
- Capture one chosen application instead of all computer output.
- Manually correct speaker assignments, captions, and translations.
- Export Markdown, plain text, SRT, and VTT.
- Keyboard shortcuts and optional consent-based audio recording.
- Settings import/export and user-managed database backup/restore.
- Advanced model manager, updates, rollback, and portable builds.
- Performance graphs and long-session metrics.
- macOS and Linux support.
- Terminology lists.
- Automatic hardware inspection, model choice, chunk sizing, and Whisper worker scaling.
- French ↔ Vietnamese after the French ↔ English application is stable. Reuse the existing pipeline and add only the `vi` models, language choices, font checks, and Vietnamese-specific tests at that time.
