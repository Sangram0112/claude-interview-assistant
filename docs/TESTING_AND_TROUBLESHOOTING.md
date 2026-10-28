# Testing and troubleshooting

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# Unit + Qt integration tests (no network, no audio hardware, no API key) - ~15 s
.\.venv\Scripts\python.exe -m pytest

# Hardware / real-model tests: WASAPI devices, loopback capture, real Whisper on
# speech synthesised by Windows (downloads tiny.en on first run)
.\.venv\Scripts\python.exe -m pytest -m integration -o addopts="" -s

# Lint and secret scan
.\.venv\Scripts\ruff.exe check app tests scripts
.\.venv\Scripts\python.exe scripts\check_secrets.py
```

`test_live_claude_answer` in the integration suite runs only when `ANTHROPIC_API_KEY` is set and
spends a few cents.

| Suite | What it covers |
|---|---|
| `test_config_security` | settings persistence, env overrides, credential store precedence, key masking, log redaction |
| `test_storage_profiles` | SQLite CRUD, retention purge, delete-all, profile prompt rendering, resume TXT/PDF import, edit format |
| `test_audio_transcription` | resampling, VAD segmenter (noise floor, clicks, interims, max length, stalled audio), pipeline start/pause/resume/stop guarantees, Whisper provider with a fake model, Deepgram message assembly |
| `test_questions` | question recognition, fillers, categories, word-boundary matching, dedup, lead-in context |
| `test_ai` | prompt content (no-fabrication rules, cache breakpoint), streaming parser, request shape (effort, fallbacks, no sampling params), error mapping for every SDK error class, refusal, missing credentials, practice scoring |
| `test_controller_ui` | full session flow with fake audio/STT/Claude: detection → dedup → answer, retention on/off, pause/resume, failures, regenerate cancellation, main window, settings/profile/practice widgets |
| `test_integration` (opt-in) | real devices and real Whisper model |

## Troubleshooting

### "Claude: no key" / "No Claude API key is configured"
Add the key in ⚙ Settings → Claude API → **Save key**, then **Test connection**. If you set
`ANTHROPIC_API_KEY` with `setx`, restart the app (and the terminal that launches it).

### "Claude rejected the API key" / "no available credit" / "not allowed to use the selected model"
Check the key in the Anthropic Console, add credits under Plans & Billing, or select another
model. The message shown always comes from the actual API response category.

### Transcript stays empty while others talk
1. Watch the **System audio** meter in the header. If it doesn't move, the meeting is playing
   through a different device: pick its loopback in Settings → Audio (headsets in "Hands-Free"
   mode appear as a separate device).
2. If the meter moves but nothing is transcribed, lower *Speech threshold* in Settings → Audio.
3. Check the log (below) for "Could not load speech model".

### "Could not open the microphone"
Windows Settings → Privacy & security → Microphone → allow desktop apps. Close apps that hold
the mic in exclusive mode (Sound Control Panel → device → Advanced → uncheck *Allow
applications to take exclusive control*).

### "Could not load speech model … path-length limit"
Your profile path is long. Set a short models folder and restart:
`setx CIA_MODELS_DIR C:\cia-models` (or enable Windows long-path support).

### First session takes a long time to start
The Whisper model downloads once (~75 MB `tiny.en`, ~145 MB `base.en`). Later starts load it in
1–3 s. Offline? Pre-download on another connection or switch to `tiny.en`.

### Transcription lags behind speech
Use `tiny.en`, lower *End-of-sentence silence* to ~500 ms, turn off interim text, or use
Deepgram. On an NVIDIA GPU with CUDA 12 + cuDNN, choose device *CUDA*.

### Junk transcripts such as "Thank you." during silence
Known Whisper behaviour on noise; common phrases are filtered. Raise *Speech threshold*.

### The same question appears twice
Lower the *Duplicate similarity threshold* (Settings → Detection), e.g. 0.75.

### A global shortcut doesn't work
Another app already registered it; the status bar names which ones. The shortcut still works
while this window is focused.

### Logs
`%LOCALAPPDATA%\ClaudeInterviewAssistant\logs\app.log` (rotating, 3 × 2 MB). Keys are redacted
and transcript text is excluded unless enabled in Settings → Privacy. Start with `CIA_DEBUG=1`
for verbose logs.
