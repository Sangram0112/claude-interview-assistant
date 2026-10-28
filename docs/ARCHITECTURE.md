# Architecture

## Component diagram

```mermaid
flowchart LR
    subgraph Windows["Windows audio (WASAPI)"]
        MIC["Microphone"]
        LOOP["Loopback of output device<br/>(Zoom / Teams / Meet audio)"]
    end

    subgraph Audio["app/audio"]
        SRC["AudioSource interface<br/>MicrophoneSource · LoopbackSource"]
        DSP["processing.py<br/>mono · resample 16 kHz · RMS"]
    end

    subgraph STT["app/transcription"]
        PIPE["TranscriptionPipeline<br/>start · pause · resume · stop"]
        PROV["TranscriptionProvider interface"]
        WHISPER["LocalWhisperProvider<br/>(faster-whisper, offline)<br/>+ UtteranceSegmenter VAD"]
        DG["DeepgramProvider<br/>(cloud WebSocket)"]
    end

    subgraph Core["Session logic"]
        CTRL["SessionController (QObject)<br/>signals only"]
        DET["QuestionDetector<br/>+ QuestionDeduplicator"]
    end

    subgraph AI["app/ai"]
        CS["ClaudeService<br/>AsyncAnthropic · streaming · errors"]
        PROMPTS["prompts.py<br/>cached system + profile"]
        PARSE["answer_parser.py"]
        PRACT["PracticeCoach<br/>structured outputs"]
    end

    subgraph Data["app/storage · app/profiles · app/security"]
        DB[("SQLite<br/>profiles · sessions ·<br/>retention-gated content")]
        KEYS["SecretStore<br/>env var → Windows Credential Manager"]
    end

    subgraph UI["app/ui (PySide6)"]
        MW["MainWindow<br/>indicator · transcript · answer cards"]
        TABS["Practice · Profile · Privacy · Setup"]
        HK["GlobalHotkeys (RegisterHotKey)"]
    end

    MIC --> SRC
    LOOP --> SRC
    SRC --> DSP --> PIPE --> PROV
    PROV --- WHISPER
    PROV --- DG
    PROV -- TranscriptEvent --> CTRL
    CTRL --> DET -- DetectedQuestion --> CTRL
    CTRL -- question + profile + recent lines --> CS
    CS --- PROMPTS
    CS --- PARSE
    CS -- HTTPS --> ANTH["Anthropic Claude API"]
    DG -- WSS --> DGAPI["Deepgram API"]
    CS --> KEYS
    CTRL --> DB
    CTRL -- Qt signals --> MW
    TABS --> PRACT --> CS
    HK --> MW
```

## Threads

| Thread | Work | Talks to UI via |
|---|---|---|
| GUI (main) | Widgets, controller state | – |
| PortAudio callback (one per device) | Convert frames → mono float32, hand to pipeline | – (never touches Qt) |
| `whisper-worker` | Speech-to-text inference; stale interim jobs are skipped | `AsyncRunner.on_gui` (queued signal) |
| `stt-poll` | Finalizes utterances when loopback audio stops arriving | – |
| `asyncio-loop` | All Claude API calls (`AsyncAnthropic`), Deepgram uses its own loop thread | `AsyncRunner.submit` callbacks (queued) |
| short-lived workers | Pipeline start (model load) / stop / pause | `AsyncRunner.run_in_thread` callbacks |

The UI thread never blocks on audio, model loading, or network I/O. Streaming answer
updates are throttled to ~12/s.

## Data flow for one question

1. Interviewer speaks → loopback frames → resampled to 16 kHz → `UtteranceSegmenter`.
2. Interim segments (every 1.5 s) give live text; a final segment is emitted after 700 ms of
   silence, after 15 s, or when audio stops arriving.
3. Final `TranscriptEvent` → `QuestionDetector` splits sentences, recognises questions,
   attaches the preceding sentence as lead-in context, classifies the category, and drops
   duplicates (Jaccard / sequence similarity ≥ 0.82 within 10 min).
4. `ClaudeService.stream_answer` sends: fixed system prompt (cached) + profile block
   (cache breakpoint) + recent transcript lines + question + mode/length instructions.
5. Streamed text is parsed incrementally into the card: answer, key points, profile
   example, context quality, and missing-personalisation list.

## Design decisions

* **No FastAPI service.** Everything runs in one process. A local HTTP service would add an
  open port and attack surface without enabling anything the in-process design cannot do.
  The provider interfaces are where a future service boundary would go.
* **Platform-independent capture.** Zoom, Teams and Meet all play audio through a Windows
  output device; WASAPI loopback captures it with no platform API, account, or bot. No
  meeting-platform integration is claimed (see `FEATURE_MATRIX.md`).
* **Speaker attribution by source.** Loopback = interviewer, microphone = you. This is
  reliable without diarization and lets question detection ignore your own speech.
* **Local STT by default.** Audio stays on the PC unless the user selects Deepgram.
* **Temperature → effort.** Since API version `2026-10-21` the Messages API rejects
  `temperature`/`top_p`/`top_k` on every model; the configurable knob is reasoning effort.
* **Prompt caching.** The static instructions and the session-stable profile come first so
  repeated questions read the profile from cache (`cache_read_input_tokens`).
* **Refusal fallback.** On Opus 5.5 / Sonnet 5.5 the request includes `fallbacks: "default"`
  (beta `server-side-fallback-2026-07-01`); it can be disabled in Settings.
