# Local French ↔ English Live Captions

## 1. Goal

Build a Windows desktop application that listens to live audio and displays captions in French and English.

The application should:

- Run on the local computer.
- Work offline after its models have been downloaded.
- Capture sound from a microphone.
- Capture sound playing through the computer, such as a web video, media player, or game.
- Detect whether speech is French or English.
- Translate French into English and English into French.
- Show the original text, the translation, or both.
- Identify different speakers when possible.
- Save completed conversation history so it can be read again later.
- Keep audio private and avoid sending it to an online service.

## 2. Expected Result

The user starts the application, selects an audio source, and presses **Start**.

The captions appear in a small window:

```text
Person A
FR: Bonjour, comment allez-vous ?
EN: Hello, how are you?

Person B
EN: I'm doing well, thank you.
FR: Je vais bien, merci.
```

Each person's caption appears as a chat message bubble with a visible label, avatar letter, and stable colour. All speakers appear on the left by default because the application cannot safely guess which voice belongs to the user. For microphone sessions, the user may manually mark one session speaker as `You`; only that speaker then appears on the right. The caption window can stay above a video, game, or other application.

The main application uses a familiar chat layout:

```text
┌──────────────────────┬──────────────────────────────────────────┐
│ Search conversations │ Current or selected conversation         │
│                      │                                          │
│ Today                │ Person A                                 │
│  French lesson       │ ┌──────────────────────────────────────┐ │
│  Team meeting        │ │ FR: Bonjour                           │ │
│                      │ │ EN: Hello                             │ │
│ Yesterday            │ └──────────────────────────────────────┘ │
│  Game dialogue       │                                          │
│  Video captions      │                              You         │
│                      │      ┌─────────────────────────────────┐ │
│                      │      │ EN: How are you?                │ │
│                      │      │ FR: Comment allez-vous ?        │ │
│                      │      └─────────────────────────────────┘ │
└──────────────────────┴──────────────────────────────────────────┘
```

Saved conversations appear in the left sidebar. Selecting one opens its complete message history on the right.

## 3. Recommended Technology

Use Python for the first version because it makes local speech and translation models easier to integrate.

| Part | Recommended tool | Purpose |
|---|---|---|
| Desktop interface | PySide6 | Settings window and caption overlay |
| Microphone capture | sounddevice | Read microphone audio |
| Computer-output capture | SoundCard 0.4.6 / Windows WASAPI loopback | Capture sound sent to speakers or headphones |
| Speech detection | Silero VAD | Detect when someone starts and stops speaking |
| Speech recognition | faster-whisper | Convert French or English speech into text locally |
| Translation | MarianMT or NLLB | Translate French ↔ English locally |
| Speaker labels | Diart with compatible local segmentation and embedding models (first candidate) | Assign and track Person A, Person B, and onward in a live stream |
| Packaging | PyInstaller | Create a Windows application |

The exact model sizes should be configurable so the app can work on both slower and faster computers. The Diart choice is provisional until it passes the speaker-stage performance and accuracy tests on the target computer.

## 4. Audio Sources

The main window will use one clear radio-button choice for the active source: **Microphone** or **Computer audio**. Only one source can be active at a time. Keep **Computer audio** disabled or hidden until WASAPI loopback capture is implemented and tested.

### Microphone

The user can select any microphone detected by Windows.

### Computer sound

Use Windows WASAPI loopback to capture the sound sent to a selected speaker or headphone device. This should work with normal browser videos, local media players, voice calls, and many games.

Some applications, protected media, or unusual audio drivers may block or bypass normal capture. A virtual audio cable can be offered as a fallback.

The first release should capture the full output device. Capturing only one specific application can be considered later because it is more complex and is not supported equally by every program.

## 5. Processing Flow

```text
Microphone or computer output
              ↓
        Audio buffering
              ├──────────────────────────────┐
              ↓                              ↓
     Detect active speech          Streaming speaker analysis
              ↓                              ↓
 Rolling preview and final ASR       Speaker timeline/clusters
              └──────────────┬───────────────┘
                             ↓
                  Align text with speaker
                             ↓
               Translate the final caption
                             ↓
                  Display and save caption
```

Speaker analysis and speech recognition should receive the same timestamped audio and run in parallel. Speaker identification must not wait for recognition to finish. Their results are joined by audio timestamps before the final message is displayed and saved.

Audio should be processed in small rolling pieces with a short overlap. VAD opens a speech-turn buffer when speech starts. While speech continues, Whisper periodically reads a snapshot of that buffer to update one provisional caption. When VAD detects the end of the turn, Whisper performs the final recognition pass. Start with a two-to-four-second preview interval, then tune it during testing to balance delay and CPU use.

### Bounded processing queues

Every background processing queue must have a fixed maximum size. A slow model must not cause audio, captions, or database work to accumulate forever.

```text
Audio capture
    ↓ bounded audio buffer
    ├→ bounded VAD/recognition queue → provisional and final ASR ─┐
    └→ bounded diarization queue    → speaker timeline ───────────┤
                                                                  ↓
                                                timestamp alignment
                                                                  ↓
                                              bounded translation queue
                                                                  ↓
                                                    final message creation
    ↓ reliable database-write queue
SQLite history
```

Queue rules:

- The real-time audio callback must never wait for Whisper, translation, the database, or the interface.
- Measure queue size in seconds of waiting audio as well as item count.
- Show a warning before a queue becomes full.
- Discard optional partial-caption work first when the computer falls behind.
- Never silently discard a completed caption or database write.
- If the audio queue still becomes full, drop the oldest unprocessed live audio so the application can catch up and insert a visible `[caption skipped: processing too slow]` event.
- On **Pause**, stop accepting audio at the pause boundary. If the current speech-turn buffer contains enough valid speech, close it at that boundary and run the final recognition pass; otherwise discard its provisional bubble.
- On **Stop**, stop capture, discard provisional-only work, and drain accepted final recognition, speaker alignment, translation, and database writes before completing the session.
- On a device change or error, preserve already accepted final work when safe and record a visible session event; discard only work that cannot be completed reliably.
- Record queue depth and dropped-audio duration in diagnostic logs without recording audio content.

## 6. Language Modes

The application should provide three modes:

1. **Automatic:** Detect French or English for each completed phrase.
2. **French → English:** Always treat the input as French.
3. **English → French:** Always treat the input as English.

Manual modes are important because automatic detection can be unreliable for very short phrases, names, or sentences containing both languages.

### Language-pair architecture

French ↔ English is the first-release language pair, but do not hard-code it throughout the application. Keep the language mode, preferred source language, target language, recognition model, and translation model in configuration.

Use simple language codes such as `fr` and `en` in the processing pipeline and database. The interface may display the friendly names French and English.

Separate the language-dependent parts behind small interfaces:

```text
SpeechRecognizer.transcribe(audio, language)
Translator.translate(text, source_language, target_language)
LanguageDetector.detect(audio_or_text)
```

Store downloaded models by purpose and language pair:

```text
models/
├── speech/
├── translation/
│   └── fr-en/
├── vad/
└── speakers/
```

## 7. Caption Display

Provide these display choices:

- Original text only.
- Translation only.
- Original and translation together.
- Automatic person labels such as `Person A`, `Person B`, `Person C`, `Person D`, and onward. The application should not impose a three-person cap, but the practical speaker count depends on the selected model, audio quality, and computer performance.
- A chat-style view with one message bubble for each completed speech turn.
- A different colour for each detected person.
- All detected speakers appear on the left by default. In a microphone session, the user may manually mark one speaker as `You`; that speaker then appears on the right. Do not guess that the first detected voice is the user. System-audio speakers stay on the left.
- Optional time shown beside each message.
- Adjustable font size.
- Adjustable background color and transparency.
- Always-on-top mode.
- Movable and resizable window.
- A button to pause or clear captions.
- Pause and resume the same live session at any time.
- A local conversation history screen.

Partial text may be shown while someone is speaking. Completed text should replace it when the phrase is finished, without creating duplicate lines.

### Temporary and final captions

Temporary captions are automatic working text, not a user-editing mode:

- While a person is still speaking, show one temporary message bubble with a subtle `Listening…` or provisional indicator.
- When VAD reports speech start, open one rolling speech-turn buffer and one temporary bubble.
- While speech continues, run preview recognition periodically on a snapshot of that buffer. Whisper may replace words inside the same temporary bubble as more audio provides context.
- Do not translate, save, search, or export every temporary revision.
- When VAD reports speech end, close the buffer, run one final recognition pass, and replace the temporary bubble with the final caption.
- Translate and save only the final caption.
- Give every speech turn a stable internal message ID so a temporary bubble becomes its final bubble instead of producing duplicates.
- A later speaker result may update the final bubble's Person label without changing its message ID.
- If final recognition fails, remove the temporary bubble or replace it with a clear failure marker rather than saving invented text.

Manual correction of saved captions is a separate distant-future feature. Live provisional text is revised by the recognition pipeline, not typed or edited by the user.

A message bubble should contain the original caption and its translation:

```text
Person A                                      14:32
┌─────────────────────────────────────────────────┐
│ FR: Est-ce que tu peux m'entendre ?             │
│ EN: Can you hear me?                            │
└─────────────────────────────────────────────────┘

                                           You  14:32
                ┌─────────────────────────────────┐
                │ EN: Yes, I can hear you.        │
                │ FR: Oui, je peux t'entendre.    │
                └─────────────────────────────────┘
```

The newest message should appear at the bottom. The view should scroll automatically unless the user has moved upward to read older captions.

### Main conversation sidebar

The left side of the main application should behave like the conversation list in a normal chat application:

- Keep the active live session at the top.
- List saved conversations below it, newest first.
- Show each conversation's title, last-message preview, and date or time.
- Group conversations by useful periods such as Today, Yesterday, and Older.
- Provide a search box above the list.
- Open the selected conversation in the main chat area on the right.
- Clearly mark live, paused, incomplete, and completed sessions.
- Allow a session to be renamed or deleted from its context menu.
- Allow the sidebar to collapse when the user wants more room for captions.

The small always-on-top overlay remains a separate compact view. It does not need to show the saved-conversation sidebar.

For system-output audio, all detected speakers normally appear on the left because none of them represents the local user. Do not alternate left and right for Person A/B/C/D; two sides cannot represent an unlimited number of people consistently. Always show the visible person label or name in addition to colour.

### Pause and resume

The user can pause and resume one caption session as many times as needed:

- **Pause** immediately stops accepting new microphone or computer-output audio.
- Close the current speech turn at the pause boundary. Finalize it if it contains enough valid speech; otherwise discard its provisional bubble.
- Do not record or buffer audio while paused.
- Keep all existing messages, translations, Person labels, colours, and voice profiles in memory.
- Keep the local models loaded when practical so resume is quick.
- Show a clear `Paused` state in the main window and overlay.
- **Resume** continues appending new messages to the same session.
- Add a small pause divider or event time to the saved history so long gaps are understandable.
- Only **Stop** completes the session. Pausing must not create a second conversation.
- If the app closes or crashes while paused, keep the session as incomplete and allow it to be reopened or completed later.

### Application state machine

Use one explicit runtime state machine to control audio capture, queue handling, buttons, and recovery. Keep durable database session status separate so saved history does not depend on the current screen state:

| State | Meaning |
|---|---|
| `Idle` | No active session |
| `Starting` | Devices and models are being prepared |
| `Live` | Audio is accepted and processed |
| `Pausing` | Capture is stopping and accepted work is being settled |
| `Paused` | No audio is captured or buffered; the session remains open |
| `Resuming` | Audio capture and workers are restarting |
| `Stopping` | Accepted work is being finalized before completion |
| `Error` | The active operation failed and requires recovery or a safe stop |

Normal transitions:

```text
Idle → Starting → Live
Live → Pausing → Paused
Paused → Resuming → Live
Live or Paused → Stopping → Idle
Live or Paused → unexpected shutdown
Starting, Live, Pausing, Resuming, or Stopping → Error
```

Only enable controls that are valid for the current runtime state. `Clear overlay` hides current visible bubbles but does not delete saved messages. `Delete conversation` is a separate confirmed action. Stopping stops capture, discards provisional-only work, finishes already accepted final work, marks the database session `completed`, and then returns the application to `Idle`. An unexpected shutdown has no chance to run a transition; on the next launch, recovery changes any database session still marked `live` or `paused` to `incomplete`. The history interface may display `completed` and `incomplete` as saved-session badges, but they are not live processing states.

Stopping or closing must never wait for recognition on the UI thread. If accepted captions are still processing, show a responsive progress dialog with the remaining-caption count and an estimate based on recent recognition speed. The user may keep waiting or skip queued captions; a caption already inside the model is allowed to finish safely. Closing continues automatically when cleanup completes.

## 7.1 Saved Conversation History

Save every caption session locally so the user can read it again later.

Each saved session should include:

- Session title.
- Start and end date and time.
- All automatically assigned person labels and colours used during the session.
- Time of each message.
- Original detected language and caption text.
- Translated language and text.
- Marks for uncertain or overlapping speech.

The history screen should:

- List sessions with the newest session first.
- Open a saved session in the same chat-style view used for live captions.
- Allow searching caption and translation text.
- Allow changing the session title.
- Allow deleting one selected session after confirmation.
- Provide a clear way to delete all history after confirmation.

Create the database session immediately and write every final message while captioning is active so a crash does not lose the full conversation. When captioning stops normally, mark the session as complete. History cannot be disabled; the user removes it afterward with the confirmed delete-one or delete-all actions. Do not save raw audio unless the user separately enables a future recording feature.

### Local database design

Use SQLite with related tables instead of storing a whole session as one large text value.

#### `persons`

Stores a reusable person profile:

| Field | Purpose |
|---|---|
| `id` | Internal unique person ID |
| `name` | User-defined name; empty until the user names the person |
| `preferred_colour` | Chat colour chosen automatically or by the user |
| `recognition_enabled` | Whether the app may currently compare new voices with this profile |
| `created_at` | When the profile was created |
| `updated_at` | When the profile was last changed |

Create a `persons` row only after the user explicitly selects **Remember this person**. Do not use `Person A` as the permanent name. `Person A`, `Person B`, and later letters are temporary labels assigned inside each session. A saved person can have a user-defined name such as `Alice` or `Teacher`. The user may turn `recognition_enabled` off without deleting the person's name or old conversation links.

#### `person_voice_signatures`

Stores signatures that the user has explicitly approved for recognizing a saved person in future sessions. One person needs several good examples:

| Field | Purpose |
|---|---|
| `id` | Unique signature ID |
| `person_id` | Related row in `persons` |
| `embedding` | The numerical voice signature, stored as binary data |
| `model_name` | Model that created the signature |
| `model_version` | Model version, needed for compatibility checks |
| `dimensions` | Number of values in the signature |
| `quality_score` | Whether this was a clear and useful voice sample |
| `created_at` | When the signature was created |
| `source_session_person_id` | Optional provenance link to the session person from which it was copied; set this to empty if that conversation is deleted |

Never store the raw speech audio inside this table. Keep only a small number of the clearest approved signatures for each saved person and replace weak or redundant ones.

#### `sessions`

Stores the title, start time, end time, current database state (`live`, `paused`, `incomplete`, or `completed`), audio source, `language_mode`, `preferred_source_language`, `target_language`, and model settings for each caption session. In automatic mode, `preferred_source_language` may be empty because the detected source language belongs to each message.

#### `session_events`

Stores important session events such as pause, resume, audio-device changes, and stop. Each row contains the session ID, event type, and event time. This makes pauses and long gaps understandable when the conversation is read later.

#### `session_persons`

Maps people to their temporary labels in one session:

| Field | Purpose |
|---|---|
| `id` | Internal unique session-person ID |
| `session_id` | Related session |
| `person_id` | Saved person, when known; otherwise empty |
| `session_label` | `Person A`, `Person B`, `Person C`, and onward |
| `session_display_name` | Optional name used only in this conversation; empty by default |
| `session_colour` | Colour used in this conversation |
| `confidence` | Confidence that the session voice matches the saved person |
| `is_local_user` | Whether the user manually marked this microphone-session speaker as `You` |

This allows the same saved person to be `Person A` in one session and `Person D` in another.

#### `session_voice_signatures`

Stores the session-scoped signatures used to keep Person A/B/C labels stable inside one session:

| Field | Purpose |
|---|---|
| `id` | Unique signature ID |
| `session_person_id` | Related row in `session_persons` |
| `embedding` | Numerical voice signature stored as binary data |
| `model_name` | Model that created the signature |
| `model_version` | Model version used for compatibility checks |
| `dimensions` | Number of values in the signature |
| `quality_score` | Whether the sample is clear enough to help matching |
| `created_at` | When the signature was created |

These signatures allow an incomplete session to resume with its existing Person labels. Keep them with the saved conversation so its speaker grouping can be reopened and inspected. Delete them when that conversation is deleted. They are not used to recognize somebody in a different session unless the user selects **Remember this person**. That action creates or links a row in `persons` and copies selected high-quality signatures into `person_voice_signatures`; those approved copies remain until the saved person is forgotten or deleted.

#### `messages`

Stores one chat bubble per completed speech turn, including its session, session person, start and end offsets, timestamp, detected source language, language confidence, original text, translated language, translated text, recognition confidence, and overlap status. Start and end offsets are stored now so future subtitle export does not require changing old message records.

#### Person privacy and control

- Keep all person data and voice signatures on the local machine.
- Keep automatic Person A/B/C voice matching inside the current session by default.
- A session-only display name stays in `session_persons`; it does not create a reusable person profile or enable cross-session recognition.
- Only compare a voice across sessions after the user explicitly selects **Remember this person**.
- Let the user name, rename, merge, forget, or delete a saved person.
- Turning `recognition_enabled` off stops future comparisons but keeps the saved profile, approved signatures, and old conversation links so it can be enabled again.
- **Forget voice** deletes the saved person's approved voice signatures, disables recognition, and removes saved-identity links from old sessions while preserving their message text, session names, and original Person labels.
- **Delete profile** also deletes the saved person row. Its approved signatures are deleted and old `session_persons.person_id` links use `SET NULL`; message text and session labels remain.
- Do not treat a voice match as proof of identity; display it as a best match when confidence is not high.

#### Database maintenance

- Store a schema version and apply ordered migrations when the database structure changes.
- Back up the database before a migration that changes stored data.
- Enable SQLite foreign-key checks and define explicit cascade or `SET NULL` behavior for every relationship.
- Deleting a conversation must cascade to its session events, messages, session people, and session-scoped voice signatures. A remembered signature's optional provenance link uses `SET NULL` so the approved signature remains with its saved person.
- Use transactions so a final message and its related speaker or session updates are saved together.
- Use WAL mode if testing confirms that it improves safe background writes while the interface reads history.
- Add indexes for session date, message time, session-person links, and saved-person links.
- Use SQLite full-text search for original and translated message text when history search is implemented.
- Store timestamps in UTC and convert them to the user's local timezone for display.
- Run a lightweight integrity check at safe times and show a recovery message if the database is damaged.
- Keep a recoverable backup before repair; never silently replace a damaged history database.
- Track database size and provide a maintenance action for old temporary data and unused space.
- Test migrations, interrupted writes, deletion rules, large histories, and recovery from a copied backup.

## 8. Multiple Speakers

### People speaking one after another

A local speaker-detection model should automatically separate them and assign labels such as `Person A`, `Person B`, `Person C`, `Person D`, and onward. The first confirmed voice becomes Person A, the next clearly different voice becomes Person B, and so on. Do not impose an application-level three-person cap. The practical number of speakers will depend on the diarization model, available CPU and RAM, audio quality, and acceptable delay. After Person Z, continue with Person AA, Person AB, and so on if necessary.

The application should try to keep the same label and colour for each person throughout the current session. The labels do not identify anyone's real name and reset in a new session by default. Stage 10 adds optional user-approved naming and cross-session recognition through **Remember this person**.

If the app is not confident that a voice belongs to an existing person, it should temporarily show `Unknown` instead of immediately creating many incorrect people. It can update the label later when it has enough voice information.

### Detecting an existing or new voice

Use voice clustering, not name or identity recognition:

1. VAD finds a section that contains speech.
2. A speaker-embedding model converts the voice into a numerical voice signature.
3. Compare that signature with the saved signatures for all people in the current session.
4. If it closely matches an existing profile, use that person's existing label and colour.
5. If it does not match closely enough, keep it as an `Unknown` candidate.
6. Create a new person only after enough clear speech supports the new voice. Assign the next label and colour at that point.
7. Update an existing person's profile only with clear, confident, non-overlapping speech.

Keep several good voice signatures for each person instead of only one. This helps when the same person later speaks more quietly, loudly, or with a slightly different tone.

Use separate match and new-person thresholds so labels do not rapidly switch. Very short phrases, noisy audio, music, and overlapping voices should not create a new person. The uncertain message can be relabelled later if later speech makes the match clear.

By default, this process distinguishes voices only within the current session. If the user explicitly selects **Remember this person**, future sessions may compare new speech with that saved person's approved signatures. It still does not discover a real name or prove a person's identity.

### Suggested real-time speaker engine

Use **Diart** as the first candidate for live speaker diarization. Diart combines a speaker-segmentation model, a speaker-embedding model, and incremental clustering. It is designed for streaming audio, can improve its speaker clusters as the conversation continues, and includes overlap-aware processing.

For the first Diart experiment:

- Install Diart and its compatible models only inside the project-local environment.
- Pin the exact Diart, PyTorch, pyannote, segmentation-model, and embedding-model versions that pass testing.
- Download all model files into the project `models/speakers/` folder.
- Test it separately before running it together with Whisper and translation.
- Measure CPU use, RAM use, speaker-label delay, incorrect speaker merges, and incorrect speaker splits.
- Test two, three, four, and more sequential speakers, plus short overlapping speech.

Diart's default pyannote models require accepting model conditions and using a Hugging Face access token for the first download. After download they can operate locally. Before distribution, check whether those model terms and the account requirement fit the promised installation experience.

If Diart is too slow, difficult to package, or unsuitable for distribution, the fallback is a lighter custom online cluster using local ONNX speaker embeddings, such as a compatible WeSpeaker model through sherpa-onnx. That fallback needs more application code and may initially have weaker overlap handling.

Do not use NVIDIA Streaming Sortformer as the first choice for this project. Its standard local configuration is CUDA-focused and documented checkpoints impose a speaker limit, which does not fit the current CPU-only target computer or the goal of avoiding a small application-level speaker cap.

### People speaking at the same time

Overlapping speech is much harder because the voices are mixed into one audio signal.

The first version should:

- Detect that voices overlap when possible.
- Display an `[overlapping speech]` warning.
- Use separate labelled message bubbles only when the speaker assignment is reasonably confident.
- Display an `Unknown / overlapping` message when the app cannot reliably assign the words.
- Produce the best available caption without promising perfect voice separation.

A later version may use a local voice-separation model to split two voices before recognition. This will require more processing power and can still make mistakes, especially with noise, music, or several similar voices.

## 9. Application Components

Keep the code divided into small parts:

```text
app/
├── main.py                 Starts the application
├── core/
│   ├── state.py            Controls session state transitions
│   ├── pipeline.py         Coordinates timestamped processing branches
│   └── queues.py           Owns bounded queues and overload policy
├── audio/
│   ├── devices.py          Lists microphones and output devices
│   ├── microphone.py       Captures microphone input
│   └── loopback.py         Captures Windows output audio
├── speech/
│   ├── vad.py              Finds active speech
│   ├── recognition.py      Runs local speech recognition
│   ├── diarization.py      Builds the streaming speaker timeline
│   └── embeddings.py       Compares approved voice signatures
├── translation/
│   └── translator.py       Runs French ↔ English translation
├── ui/
│   ├── main_window.py      Main chat and conversation sidebar
│   ├── history.py          Saved conversation viewer
│   ├── settings.py         Settings controls
│   └── overlay.py          Always-on-top caption window
├── storage/
│   ├── settings.py         Saves user choices
│   ├── history.py          Saves and loads conversation history
│   ├── database.py         Stores people, sessions, events, and messages locally
│   └── migrations/         Ordered database schema changes
└── model_manager/          Finds and loads files from the root models/ folder
```

The root `models/` folder contains downloaded model files. `app/model_manager/` contains only the code that manages them. Model files should not be committed to source control because they can be very large. Overlapping-voice separation is deferred, so do not create a separation component until that feature is scheduled.

## 10. Development Stages

Build the complete roadmap, but do it one verified step at a time. Do not implement several untested stages together. After every stage, run its functional tests and measure its added CPU use, RAM use, disk use, and latency on the target computer. Fix or simplify that stage before continuing.

### Stage 0 — Local project foundation

- Create the project-local `.venv/` and cache folders.
- Add configuration, structured logging, and a basic test command.
- Create a minimal PySide6 window without speech models.
- Record the computer's baseline CPU and RAM use.
- Confirm that nothing is installed globally.

**Finished when:** The empty application launches from the local environment, tests run, and its baseline resource use is recorded.

### Stage 1 — Microphone audio capture

- List available microphones.
- Capture one selected microphone continuously.
- Convert audio to the internal mono sample rate and format.
- Display a simple input-level meter.
- Handle start, stop, and device disconnection.
- Measure CPU and memory use before continuing.

**Current verification:** When microphone capture stops, the diagnostic log records its duration, average process CPU use, memory before and after capture, process peak memory, processed audio duration, and queue drops. The final 30-minute live-microphone stability run is still required before Stage 1 is considered complete.

**Finished when:** Microphone audio runs for at least 30 minutes without growing memory, freezing, or losing the selected device unexpectedly.

### Stage 2 — Voice activity detection

**Status: Complete for the basic local-caption milestone (2026-10-22).**

- Add Silero VAD locally.
- Mark speech start and speech end without transcription.
- Test quiet voices, background noise, music, and short phrases.
- Tune speech padding and silence duration without cutting words.
- Measure VAD delay and CPU use.

**Current implementation:** The project-local `.venv/` contains `silero-vad 6.2.3`, CPU-only `torch 2.14.1`, and `onnxruntime 1.31.0`. The model is bundled with the installed Silero package, so starting VAD does not download anything. The live 16 kHz audio queue now feeds a background ONNX VAD worker, and the interface shows speech or silence when it receives boundary events. Initial settings are a `0.5` speech threshold, `400 ms` minimum silence, `100 ms` speech padding, and a `200 ms` minimum completed segment. A **Speech sensitivity** slider maps levels `1–9` to safe Silero thresholds `0.8–0.2`, defaults to the balanced `0.5` threshold, and locks during capture. Higher sensitivity can accept quieter speech but may also accept more noise. The short-segment filter reduces clicks and brief music tones becoming caption jobs; its count appears in the simple Performance view and diagnostic log. Completed speech turns become temporary, read-only in-memory audio chunks for the later recognition stage; they are not saved to disk. A **Maximum speech chunk** slider allows a safe `5–60` second limit, defaults to `30` seconds, and is locked while capture is active. This limit splits uninterrupted speech; detected silence may finish a chunk earlier. The Performance view and diagnostic log also report model load time, processed VAD audio, average inference time per window, speech boundary counts, and completed chunk counts.

**Automated evaluation on 2026-10-19:** A repeatable local command, `.venv\Scripts\python.exe -m app.diagnostics.vad_evaluation`, runs the production VAD against a project-local 60-second speech sample plus deterministic quiet-speech, strong-noise, music-tone, background-noise, and short-phrase variants. Normal and quiet speech each produced 10 useful segments. The noisy variant produced 12 useful segments and one ignored short event, so strong noise can still split one speaker turn more often. Background noise produced no speech. A music tone triggered one 164 ms boundary pair, which the 200 ms filter correctly prevented from becoming a speech chunk. The short phrase remained detected. Average inference time was about `0.17 ms` per 32 ms window, or about `0.53%` of real time, after model load. All 30 automated tests passed. These generated cases are useful regression tests but do not replace testing real French and English voices through the selected microphone.

**Preliminary live-device check on 2026-10-19:** A 20-second run processed 19.936 seconds of Realtek microphone input across 623 VAD windows with no processing warnings or false speech events. The working Realtek backend returned exact silence during the test. A separate Razer headset backend check returned only a very small background-level signal. This confirmed device fallback and silent-input handling; the later live completion run below replaced the missing spoken-voice check.

**Live completion test on 2026-10-22:** A 429-second microphone run produced 43 speech starts, 43 speech ends, and 43 completed chunks with no audio-queue drops and no ignored short events. VAD processed 428.832 seconds of audio across 13,401 windows. Average inference time was 1.129 ms per 32 ms window, about 3.53% of real time. The matching start/end counts and bounded queues are sufficient to conclude the basic Stage 2 milestone.

**Tuning note for later testing:** The current `400 ms` end-of-speech silence sometimes creates many 1–3 second chunks during short pauses. Compare it with about `700 ms`, aiming for typical final chunks around 3–8 seconds while retaining a safe user-selected maximum. A longer maximum affects only uninterrupted speech and can increase recognition delay; it does not force normal speech turns to reach that length. Silence-boundary tuning remains a separate future experiment.

**Finished when:** Speech boundaries are reliable enough to create useful audio chunks and silence does not cause constant false speech events.

### Stage 3 — French and English speech recognition

**Status: Complete for the basic local-caption milestone (2026-10-22).**

- Add `faster-whisper` with a small CPU INT8 model first.
- When VAD ends the turn, run final recognition on the completed speech chunk.
- Keep the recognition queue bounded and show skipped work visibly.
- Prevent duplicate final captions.
- Test basic French and English speech locally without internet access.
- Measure recognition delay, queue size, CPU, and RAM.

**Current implementation:** `faster-whisper 1.2.1` is installed inside the project `.venv/`. PyAV is pinned to `16.0.1` because the released faster-whisper decoder is not compatible with PyAV 19 yet. The multilingual `Systran/faster-whisper-small` model is pinned to revision `2ec96c5472da50d38d40c0cfe0602af2e94b4c8a` and stored under `models/speech/faster-whisper-small/` (about 486 MB). It loads from that local path with networking disabled, using CPU INT8. The current release deliberately runs exactly one recognition worker with a four-thread CPU budget. The worker-count slider and parallel-worker path were removed after game testing showed that the target computer is not suitable for validating higher concurrency. Completed VAD chunks enter a bounded eight-item background recognition queue and produce final local captions. Queue overload creates a visible skipped-caption result instead of silently losing work. Stop and window-close processing now remains responsive, displays the remaining-caption count and estimated time, and allows queued captions to be skipped safely.

**Live completion test on 2026-10-22:** The same 429-second run created 43 recognition jobs. Whisper completed 41 captions and the bounded queue skipped two during a dense burst, giving a 95.3% processing-completion rate with no captured-audio loss. The 41 model calls used 283.576 seconds in total, with individual captions commonly taking about 5–12 seconds. This confirms offline French/English recognition and bounded-delay behavior, but it is not a 95.3% transcription-accuracy measurement.

**Known limitations deferred beyond this basic milestone:** Short or noisy chunks can produce low-confidence guesses for other languages because the model is multilingual. French/English-only language controls, broader accent/name accuracy evaluation, rolling preview captions, and stable chat bubbles remain later work in their relevant language-control and interface stages.

**Finished when:** A user can speak French or English and receive readable local captions without an internet connection or an ever-growing processing delay.

### Stage 4 — French ↔ English translation

- Compare suitable local French ↔ English translation models on the target computer.
- Translate final captions only, not every unstable partial result.
- Add automatic and manual language modes.
- Show original and translated text.
- Measure translation quality and added delay in both directions.

**Current implementation (2026-10-21):** Use the official directional `Helsinki-NLP/opus-mt-en-fr` and `Helsinki-NLP/opus-mt-fr-en` Marian models instead of a much larger multilingual model. The source revisions are pinned to `dd7f6540a7a48a7f4db59e5c0b9c42c8eea67f18` and `c4aed37b318c763fd177aa449b44e3b783cc6c02`. Both were converted locally to CTranslate2 INT8 and stored under `models/translation/`; each installed direction is about 75.7 MiB. Runtime translation uses the existing CPU CTranslate2 library plus project-local `sentencepiece 0.2.2`, loads only the direction needed, and does not access the network.

Final recognition results now enter a bounded eight-caption translation queue. Recognition and audio capture are not blocked while translation runs. Queue overload or model errors preserve the original caption and show a visible translation warning. The interface provides **Automatic**, **French**, and **English** spoken-language modes and can show both texts, only the original, or only the translation. Translation queue counts, failures, and total processing time appear in the simple Performance view and diagnostic log.

An offline smoke check translated “Hello, how are you today?” to French and “Bonjour, comment allez-vous aujourd’hui ?” to English. Model loading took about `0.36–0.41 seconds`; translations took about `0.04 seconds` on this computer. These are development measurements, not a quality benchmark. All 34 automated tests passed after integration.

**Remaining before completion:** The separate testing branch should check real recognized captions in both directions, accents, names, numbers, punctuation, longer speech, automatic-language mistakes, repeated direction switching, offline use, and live CPU/RAM/latency. Add and test an explicit language gate before the translation queue: send a caption to translation only when its normalized detected language is French (`fr`) or English (`en`); keep other-language captions as original-only results and count them as unsupported/skipped rather than translation failures. Consider a minimum language-confidence rule for short or uncertain captions during later tuning. Tune or replace the models only if those checks show unacceptable quality.

**Finished when:** Final French captions receive useful English translations and final English captions receive useful French translations without preventing live transcription.

### Stage 5 — Computer-output capture

- Add Windows WASAPI loopback capture.
- List available speaker and headphone devices.
- Allow switching between microphone and computer output.
- Test a browser video, local media player, voice call, and game.
- Handle device changes and unsupported capture clearly.
- Repeat the VAD, recognition, translation, and resource tests using system audio.

**Current implementation (2026-10-22):** `SoundCard 0.4.6` is installed only inside the project `.venv/` and pinned in `requirements.txt`. It enumerates the currently connected Windows playback endpoints and opens the selected endpoint as a WASAPI loopback recorder. The interface now provides the planned **Microphone** or **Computer audio** radio choice, a separate speaker/headphone selector, refresh behavior that keeps the current selection when possible, and clear startup errors for unavailable loopback devices. Only one source can run at a time.

Computer audio is captured at 48 kHz in stereo, downmixed to mono, and sent into the same bounded resampling, VAD, recognition, translation, performance, and responsive-shutdown pipeline used by the microphone. Stereo capture is intentional because the SoundCard Windows backend documents a single-channel recording problem. No captured audio is written to disk.

**Initial live smoke test on 2026-10-22:** The default `Headphones (Razer Barracuda X (BT))` loopback endpoint opened without warnings and delivered 24 blocks / 1.024 seconds of computer audio, with a non-zero input level. This verifies device opening and live data flow, but not caption or translation quality.

**First end-to-end computer-audio test on 2026-10-22:** The selected Razer headphones captured for 820.5 seconds and shut down cleanly with application exit code `0`. The pipeline processed 814.4 seconds of audio, detected 36 speech segments, completed 35 recognitions, and produced 23 French/English translations. The 10 reported translation failures were expected unsupported-language results: eight Vietnamese, one Japanese, and one Russian. Two English recognition results were empty and therefore were not sent to translation. Recognition averaged about 8.65 seconds per completed caption. The raw capture queue lost no audio; the processed-audio queue dropped 3.701 seconds (about 0.45%), and one speech segment was not recognized, most likely because the bounded recognition queue filled during a burst of short segments. The diagnostic log did not preserve the matching UI warning during this run, so the exact reason could not be proven afterward. VAD used about 3.25% of real time. The reported process CPU value can exceed 100% because 100% represents one fully used CPU core. Memory values were unavailable during this run.

**Follow-up fixes on 2026-10-22:** Pipeline warnings are now written to the rotating diagnostic log as well as shown in the status bar, with the correct Microphone or Computer audio source name. Windows process-memory collection now uses the correctly typed `K32GetProcessMemoryInfo` API and returns current and peak memory in a direct check. Automated tests now also cover preservation of a selected output across refresh, active-output disconnection and clean capture shutdown, source-specific warning logging, and Windows memory collection. All 46 automated tests pass. Repeat a live capture to confirm the repaired memory figures and to capture the exact warning if the bounded recognition queue fills again.

**Game-audio test on 2026-10-25:** Razer-headphone loopback captured game audio successfully and processed 456.7 seconds of input with no raw capture-queue loss. VAD created 47 speech segments; Whisper completed 44, the bounded recognition queue visibly skipped two, and one recognition was still active when stopping. Thirty-four English captions received French translations, while ten short sound or speech fragments produced no text. Recognition averaged about 6.5 seconds per completed segment. The application averaged 244.3% process CPU (about 2.4 fully used cores), used 689.3 MiB at the end, and peaked at 1287.3 MiB. Game performance sometimes fell to about 15 FPS, so the current CPU configuration is not acceptable for gaming use on this target computer. VAD model startup took about 35 seconds and contributed to 29.972 seconds of processed-queue loss near startup. The one remaining recognition did not finish within the 60-second stop timeout, so the saved session was correctly marked `incomplete` rather than `completed`.

**Remaining before completion:** Run real browser-video, local-player, voice-call, and game tests. Confirm device switching and disconnection behavior, French and English VAD boundaries, caption/translation quality, long-run queue behavior, CPU/RAM use, and stop/close behavior with queued work. Stage 5 remains in progress until these live checks pass.

**Finished when:** Normal sound playing through the selected Windows output device produces stable translated captions.

### Stage 6 — Session controls

- Implement explicit idle, starting, live, pausing, paused, resuming, stopping, and error runtime states.
- Store `live`, `paused`, `incomplete`, and `completed` as separate durable session statuses in SQLite.
- Add pause, resume, stop, and clear-overlay controls.
- Keep pause and resume inside the same session.
- Confirm that no audio is captured or buffered while paused.
- Flush already accepted speech safely when stopping.
- Test repeated pause and resume operations.

**Current implementation (first slice, 2026-10-22):** The application now has one explicit runtime state machine with `idle`, `starting`, `live`, `pausing`, `paused`, `resuming`, `stopping`, and `error` states. Invalid transitions raise a clear internal error instead of silently creating contradictory UI and capture state. The existing start, successful live capture, asynchronous stop, and capture-start failure paths now report through this state machine. Pause and resume transitions are defined and tested at the core level but are not connected to buttons or audio control yet. All 50 automated tests pass.

**Game-audio stop result on 2026-10-25:** The interface stayed responsive while stopping, but one active Whisper call exceeded the 60-second finishing limit while the game was competing for CPU. Shutdown reported a clear error and preserved the conversation as `incomplete`. Stage 6 does not yet pass its clean-stop requirement under this load.

**Next slice:** Add separate Pause/Resume and Stop controls, stop accepting audio immediately at the pause boundary, settle already accepted work in the background, and resume the same in-memory session without resetting its captions or counters.

**Finished when:** Session controls behave consistently without losing finished messages, duplicating audio, or creating unintended sessions.

### Stage 7 — Main chat interface and overlay

- Build the chat-message layout and conversation sidebar shell.
- Let the user manually mark one speaker as `You` in microphone sessions; keep all speakers on the left by default.
- Add the always-on-top overlay.
- Add size, position, font, colour, transparency, and click-through controls.
- Save interface settings between launches.
- Test high-DPI screens, multiple monitors, and windowed or borderless games.
- Measure UI responsiveness while the full caption pipeline is busy.

**Current implementation (first chat slice, 2026-10-22):** The single latest-caption label has been replaced by a scrollable WhatsApp-style live conversation area. Every completed speech turn creates one left-aligned bubble containing its source label, local time, original language and text, and translated language and text. Warning or skipped captions use a separate warning colour. The existing Original only, Translation only, and Original and translation choices update all visible bubbles, not only new ones. New messages scroll into view only when the user was already near the bottom. **To bottom** jumps directly to the newest message, while **Clear screen** removes visible bubbles without deleting saved history. The lower interface now uses the familiar chat layout: conversations on the left and the selected conversation's message bubbles on the right. The live controls and simple performance panel remain above it. All 61 automated tests pass.

**Current implementation (overlay foundation, 2026-10-22):** A **Show overlay** control opens a separate frameless, always-on-top caption window. It displays the newest live result, including source label, time, original text, translation, and warning colour, and follows the main Original, Translation, or combined display choice. The overlay can be dragged to another screen position, hidden without stopping capture, and cleared with the main caption screen.

**Current implementation (overlay customization slice, 2026-10-22):** An **Overlay settings** dialog now controls caption font size, text colour, background colour, background opacity, and mouse click-through mode. A resize handle changes the frameless overlay size. Its appearance, size, and screen position are saved locally and restored on the next launch. Invalid saved values are repaired to safe limits, and an overlay restored from a disconnected monitor is moved back onto the primary screen. **Reset defaults** restores the original appearance. When click-through is active, the main window remains the way to hide or configure the overlay.

**Current implementation (resizable chat layout and saved choices, 2026-10-22):** The conversation sidebar and caption area now use a draggable divider. **Hide conversations** collapses the sidebar to provide more caption space, and **Show conversations** restores it. The application locally remembers the main window geometry, sidebar visibility and width, caption display mode, spoken-language choice, maximum speech-chunk duration, and speech sensitivity. Saved numeric values are constrained by the existing safe control ranges when restored.

**Live layout check and fix (2026-10-25):** Testing on the target Windows display found that the 48-pixel Start/Stop button could extend below the Live audio group and overlap the conversation title and controls. The maximum speech-chunk slider was moved to the processing panel, the speech-status labels were combined into one row, and excess vertical spacing was removed. A geometry regression check now requires the Live audio panel to end before the conversation panel begins.

**Current automated verification:** All 69 tests pass, including the fixed single-Whisper-worker pipeline, overlay formatting, safe appearance limits, click-through flags, settings reset, saved appearance and geometry, collapsible-sidebar behavior, saved main-interface choices, window flags, show/hide integration, and live-caption updates.

Automatic Person labels and colours, temporary-to-final bubble replacement, and right alignment for a manually selected `You` remain in later stages. Stage 7 still needs real high-DPI, multiple-monitor, windowed-game, borderless-game, and busy-pipeline responsiveness tests.

**Finished when:** Live captions remain readable and the interface stays responsive while audio processing runs in the background.

### Stage 8 — Local history and crash recovery

- Create the SQLite schema for sessions, session events, session people, temporary session voice signatures, and messages.
- Save every final message during the active session.
- Mark interrupted live or paused sessions as incomplete after restart.
- Add the left conversation list, history reader, search, rename, and deletion controls.
- Keep raw audio out of the database.
- Test database recovery, long histories, and deletion rules.

**Current implementation (first history slice, 2026-10-22):** A project-local SQLite database at `data/history.sqlite3` now stores sessions, session events, and every final or warning caption as it arrives; raw audio is never stored. The schema also creates the planned `persons`, `person_voice_signatures`, `session_persons`, and `session_voice_signatures` tables so later speaker stages can extend the data without replacing the history design. A session is created when capture starts, marked `completed` after a clean stop, marked `incomplete` after a stop error, and any `live` or `paused` rows left by an earlier process are recovered as `incomplete` on the next launch.

The left conversation list shows newest sessions first with a title, time, latest-caption preview, and status. Selecting a saved session loads its messages into the same chat view on the right. A **Live conversation** entry stays at the top during capture. The user can read an older session while new live captions continue saving in the background, then return to Live to see the newest messages. `data/` is excluded from Git. Search, rename, confirmed deletion, pause-event storage, database maintenance controls, and long-history performance testing remain to be implemented.

**Finished when:** Completed and interrupted sessions can be reopened with their captions, translations, labels, timestamps, and pause events intact.

### Stage 9 — Multiple speakers inside one session

- Test Diart by itself before connecting it to the full application.
- Pin the model and dependency versions that pass testing.
- Add automatic Person A, Person B, Person C, Person D, and onward labels.
- Run diarization in parallel with VAD and recognition, then align the speaker timeline with captions by timestamp.
- Store temporary embeddings in `session_voice_signatures`.
- Keep returning voices on their existing session labels.
- Add `Unknown` and overlapping-speech states.
- Test two, three, four, and more speakers.
- Measure added latency, CPU, RAM, incorrect merges, and incorrect splits.

**Finished when:** Sequential speakers usually retain stable labels during one session, uncertain audio is not turned into many false people, and the computer can keep up with the combined pipeline.

### Stage 10 — Remembered people across sessions

- Add the explicit **Remember this person** action.
- Create `persons` and `person_voice_signatures` records only with user approval.
- Compare future voices only with approved saved profiles.
- Allow naming, renaming, merging, forgetting, and deleting saved people.
- Preserve old conversation text when a saved person is forgotten.
- Display uncertain identity results as a best match rather than a fact.
- Test false acceptance and false rejection with similar voices.

**Finished when:** An approved saved person can be matched in a later session with useful accuracy, while unnamed automatic speakers remain session-only.

### Stage 11 — Packaging and offline setup

- Create a Windows application package.
- Add a first-run model downloader.
- Keep models outside the executable and inside the approved local model folder.
- Show required disk space and download progress.
- Verify model files before loading them.
- Verify that the application runs after the internet is disconnected.
- Test installation, upgrade, uninstall, and history preservation.

**Finished when:** A normal user can install, configure, and run the application without development tools, and all promised local features continue to work offline.

## 11. Performance Levels

Offer simple quality choices:

| Mode | Expected result |
|---|---|
| Fast | Manual preset with the lowest delay and memory use, but more caption mistakes |
| Balanced | Manual preset with the recommended mix of speed and accuracy |
| Accurate | Manual preset with better recognition, but it needs a fast GPU or accepts a longer delay |

The target delay for the balanced mode should be about one to three seconds on a suitable computer. CPU-only systems may be slower.

These are user-selected presets in the first releases. Automatic PC inspection and automatic model selection remain distant-future work. The application should show its current delay and warn the user when processing cannot keep up with incoming audio.

The first performance view uses simple live text statistics: capture duration, average process CPU use, current memory, memory change, and process peak memory. A selectable graph view and more detailed historical metrics are deferred until the basic audio and caption pipeline is stable.

## 12. Privacy and Storage

- Process audio locally.
- The finished application should not require an account or API key for normal use. If a candidate model requires an account or token for its initial download, either obtain redistribution permission and bundle it correctly or choose a different default model before release.
- Do not save raw audio by default.
- Save every final caption and translation to local history. History saving is mandatory so sessions can be recovered and read later.
- Give the user a clear delete-history button.
- Store settings and history in the user's local application-data folder.
- Explain that models require an internet connection only for their first download.

## 12.1 Project-Local Dependencies

Keep everything needed for development inside this project folder whenever possible.

- Put Python packages and virtual environments inside the project, not in the system-wide Python installation.
- Use a project-local `.venv/` for the normal Python setup.
- If Conda is required, create and use the environment inside this project, for example `.conda/`. Do not create it in Conda's global environment directory.
- Store downloaded speech, translation, VAD, and speaker models under `models/`.
- Store package and model caches under `.cache/` when the related tool allows it.
- Store bundled native libraries and helper executables under `vendor/` or `tools/`.
- Do not install packages globally or modify the user's main Python or Conda environment.
- Exclude environments, caches, models, generated files, and downloaded binaries from Git.
- Do not install Conda unless the project actually needs it. Prefer a local Python virtual environment first.

### Git location on this development computer

Git is supplied by GitHub Desktop and is not currently available through the normal command search path. When Git is needed, use this executable directly:

```text
C:\Users\duong\AppData\Local\GitHubDesktop\app-3.6.7\resources\app\git\cmd\git.exe
```

This is a machine-specific development path. GitHub Desktop updates can change the `app-*` version folder, so locate the newest installed copy if this path stops working. Do not build it into the application or assume another computer uses the same GitHub Desktop version.

## 13. Error Handling

### Local diagnostic logs

- Write runtime logs to the terminal and `logs/application.log`.
- Rotate `application.log` when it reaches 2 MB.
- Keep four log files in total: the current file plus three backups named `application.log.1`, `application.log.2`, and `application.log.3`.
- The four files use about 8 MB at most in normal operation. When rotating, delete the oldest backup.
- Keep the `logs/` folder out of Git.
- Log operational information such as application startup and shutdown, device refresh or disconnection, audio queue drops, processing warnings, and errors.
- Do not put raw audio, caption or conversation text, voice signatures, access tokens, or other sensitive data in diagnostic logs.

Show simple messages for common problems:

- The selected audio device was disconnected.
- The output device does not support loopback capture.
- A model has not been downloaded.
- There is not enough memory to load the selected model.
- The application is falling behind the live audio.
- No speech has been detected for a long time.

The application should try to reconnect to an audio device when it becomes available again.

## 14. Testing Checklist

Test the following cases:

- One French speaker using a microphone.
- One English speaker using a microphone.
- French and English speakers taking turns.
- Two speakers talking at the same time.
- Quiet speech and loud speech.
- Background music and environmental noise.
- A French or English web video.
- A local video or audio file.
- Game dialogue and sound effects.
- Speakers and Bluetooth headphones.
- Changing the Windows output device while captions are running.
- Repeated pause and resume operations in one session.
- A deliberately overloaded processing queue and its visible skipped-caption warning.
- Stopping while recognition or translation work is still queued.
- A crash or forced close during live and paused states.
- Database migration, interrupted write, backup recovery, and a large message history.
- Stable left-side labels for conversations with more than two detected people.
- Running without an internet connection.
- A CPU-only computer and a computer with a supported GPU.

## 15. First Release Scope

The complete first-release target is listed below, but it must be built in the order defined in the development stages. Each stage should be individually usable, tested, and measured before work begins on the next one. The features are not implemented all at once.

The complete target should include:

- Windows support.
- Microphone and full computer-output capture.
- French and English recognition.
- French ↔ English local translation.
- Original and translated caption modes.
- An always-on-top caption overlay.
- Pause and resume at any time without leaving the current session.
- Automatic Person A, B, C, D, and onward speaker labels without an application-level three-person cap; practical capacity depends on the model and hardware.
- An explicit **Remember this person** option for approved cross-session recognition.
- A chat-style caption view with separate message bubbles and speaker colours.
- Saved conversation history listed in a left sidebar and reopened in the chat-style viewer.
- An overlapping-speech warning.
- Offline operation after model setup.

Do not make perfect separation of simultaneous voices a requirement for the first release. It is an advanced feature and would delay a stable basic version.

## 16. Deferred and Distant-Future Improvements

The following items are intentionally outside the near-term roadmap. Keep them documented, but do not implement them during the first releases unless priorities are explicitly changed.

- Separate two overlapping voices locally.
- Capture one chosen application instead of all computer sound.
- Manually correct a wrong speaker assignment in saved history.
- Manually edit or correct saved caption and translation text.
- Export saved conversations to Markdown or plain text.
- Export `.srt` and `.vtt` subtitle files.
- Add keyboard shortcuts.
- Add optional recording with clear consent.
- Import and export application settings.
- Add user-managed database backup and restore controls.
- Add an advanced model manager with updates, rollback, and multiple installed versions.
- Offer a portable build in addition to the normal installer.
- Add performance graphs and an advanced diagnostics screen for detailed metrics and long-session queue history.
- Support macOS and Linux.
- Add more languages.
- Add terminology lists for names, games, or technical subjects.
- Automatically inspect the user's PC and select different speech-recognition, translation, and speaker-detection models based on its CPU, RAM, and GPU.
- Provide simple hardware profiles such as **Low Power**, **Balanced**, and **High Accuracy**.
- Revisit multiple parallel Whisper workers and automatic worker scaling only on hardware strong enough to benchmark them without harming normal use or game performance.
- Adapt the maximum in-memory speech-chunk length to available RAM and the selected recognition model. Until then, keep the manual `5–60` second slider with a `30` second default, always enforce its safe upper bound, and never discard later audio merely because one chunk reached that bound.
- Allow an advanced user to override the automatic model choices.
- Do not implement this during the first releases. Initially, provide a small set of manual quality choices and document their hardware requirements.
- In the distant future, add French ↔ Vietnamese recognition and translation after the French ↔ English application is stable. At that time, add the `vi` language code, a `models/translation/fr-vi/` model folder, Vietnamese interface choices, suitable direct or fallback translation models, font checks, and Vietnamese-specific accuracy, punctuation, and mixed-language tests. It should reuse the existing audio capture, VAD, speaker detection, chat, history, pause/resume, and database design. Do not include Vietnamese models, user-interface work, or language-specific testing in near-term releases.

