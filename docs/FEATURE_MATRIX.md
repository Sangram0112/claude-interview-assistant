# Feature matrix

Status legend:
* **Implemented ✅** – built and verified (automated test and/or run on real Windows hardware).
* **Implemented, needs your credentials 🔑** – built and tested against a faithful fake of the
  external service; not exercised live because no key was available during development.
* **Partial 🟡** – works with stated limitations.
* **Not implemented / unsupported ❌** – deliberately not built (reason given).

Verification environment: Windows 11, Python 3.12.7, Realtek speakers + Intel Smart Sound mic array.

## Desktop app & UI

| Feature | Status | Verification |
|---|---|---|
| App launches on Windows | ✅ | Real launch of `app.main`, screenshots of every tab, clean exit |
| Start / Stop / Pause / Resume | ✅ | Controller tests + live hardware run (stop → idle in ~40 ms) |
| Platform selector (Zoom / Teams / Meet / Other) + setup guidance + process detection | ✅ | Unit tests, screenshot |
| Microphone & system-audio status chips with live level meters | ✅ | Live hardware run (peak meter data) |
| Visible LISTENING indicator (pulsing), listening state in window title/taskbar | ✅ | UI test; layout guarantees it is never truncated |
| Live transcript with interim text | ✅ | Live hardware run |
| Question cards: question, spoken answer, key points, profile example, context-quality badge, needs-personalisation list, copy, regenerate, mode picker | ✅ | Qt tests (fake Claude); answer content from Claude 🔑 |
| Loading indicator, error banner, usage stats & cost estimate, session timer | ✅ | Qt tests |
| Settings (API keys, model, effort, length, mode, audio devices, STT, detection, privacy, UI) | ✅ | Qt tests; device lists from real WASAPI |
| Compact always-on-top mode, resizable, width-adaptive header | ✅ | Screenshots at 520–1100 px |
| Global shortcuts Ctrl+Alt+P / R / H / C (RegisterHotKey, no keyboard hook) | ✅ | All 4 registered on real Windows; in-window fallback |
| Type-a-question ("Ask") without audio | ✅ | Qt test |

## Audio & transcription

| Feature | Status | Verification |
|---|---|---|
| Provider interface for audio sources | ✅ | Fake-source tests |
| Windows loopback (system audio) capture | ✅ | Real capture of played speech, RMS 0.23 |
| Microphone capture | ✅ | Device enumeration + open/close verified; mic speech path not exercised by automated test (would need someone speaking) |
| Resample/mono conversion, chunked processing | ✅ | Unit tests |
| Energy VAD with adaptive noise floor, silence/noise/click handling | ✅ | Unit tests incl. regression for noise-at-start bug |
| Finalize when loopback audio stops arriving | ✅ | Unit test |
| Local Whisper (offline) | ✅ | Real `tiny.en` on Windows TTS speech: exact transcript, final ~0.8–2 s after speech ends |
| Deepgram streaming provider | 🔑 | Message assembly/URL/key handling tested; live socket not exercised (no key) |
| No capture before Start; devices closed on Pause/Stop; no audio processed after Stop | ✅ | Pipeline tests |
| Per-session consent dialog with data-flow disclosure | ✅ | Implemented; shown on every Start |
| Raw audio never stored | ✅ | No code path writes audio |

## Question detection

| Feature | Status | Verification |
|---|---|---|
| Sentence assembly, question recognition (?, interrogatives, imperative prompts) | ✅ | Unit tests + live run |
| Category classification (intro, behavioural, technical, project, SQL, Python, React/API, data analyst, scenario, career) | ✅ | Unit tests; word-boundary regression from live run |
| Lead-in context ("I see you built X. How did you test it?") | ✅ | Unit tests + live run |
| Duplicate suppression (fuzzy, time-windowed) | ✅ | Unit + controller tests |
| Only interviewer audio triggers suggestions (configurable) | ✅ | Unit tests |

## Claude

| Feature | Status | Verification |
|---|---|---|
| Official SDK (`anthropic` 2.3.0), async, streaming | ✅ / 🔑 | Request shape verified against installed SDK signatures and fake client; live call needs your key |
| Key from env or Windows Credential Manager; never in files/logs/UI | ✅ | Tests + secret scanner |
| Model selection (Opus 5.5 default, Sonnet 5.5, Haiku 5.5, custom) | ✅ | Tests |
| Rate limit / network / timeout / auth / billing / permission / not-found / server errors | ✅ | Every SDK error class mapped and tested; SDK retries 429/5xx |
| Missing credentials → actionable message | ✅ | Tested against the real SDK behaviour |
| Response length + answer modes (Concise, Detailed, STAR, Technical, Beginner) | ✅ | Prompt tests |
| Configurable temperature | ❌ → replaced by **reasoning effort** | The Claude API (version 2026-10-21) rejects `temperature`/`top_p`/`top_k` on every model |
| Personalised, no-fabrication answers with placeholders | ✅ prompt / 🔑 output | Prompt rules tested; output quality requires live calls |
| Prompt caching of profile | ✅ / 🔑 | Cache breakpoint in request verified; savings visible in stats with a key |
| Server-side refusal fallback | ✅ / 🔑 | `fallbacks="default"` on Opus/Sonnet 5.5, toggleable |

## Profiles, resume, practice

| Feature | Status | Verification |
|---|---|---|
| Multiple profiles: create / edit / delete / switch | ✅ | Repository + Qt tests |
| Resume import PDF/TXT, local extraction, preview & correction | ✅ | Tests incl. real PDF parsing |
| Scanned (image-only) PDFs | 🟡 | Detected and the user is told to paste text; no OCR |
| Job description, skills, projects, experience, achievements, length, style | ✅ | Qt tests |
| Auto-fill profile from resume with Claude (asks before uploading) | 🔑 | Structured-output call + merge logic tested with fake |
| Practice mode: question generation, typed answers, evaluation scores, feedback, improved answer | 🔑 | Logic and UI tested with fake Claude |
| Practice: verbal answers via microphone | ✅ | Uses the same verified pipeline (mic only) |

## Privacy & security

| Feature | Status |
|---|---|
| Transcript retention (off by default; 1/7/30 days/forever) with purge at start-up | ✅ |
| Delete saved transcripts; delete all data & keys | ✅ |
| Logs exclude transcript text by default; key redaction filter | ✅ |
| No stealth: no hidden window, no capture-exclusion (`SetWindowDisplayAffinity`), no keyboard hooks, hide = minimise | ✅ |
| `.gitignore` excludes secrets; secret scanner for repo and build output | ✅ |

## Platforms

| Platform | Status |
|---|---|
| Zoom desktop | ✅ via Windows system-audio capture (platform-independent) |
| Microsoft Teams desktop | ✅ via Windows system-audio capture |
| Google Meet in browsers | ✅ via Windows system-audio capture (no extension/tab capture needed) |
| Zoom RTMS / Meeting SDK, Teams Graph / media bots, Meet REST / Media API | ❌ Not implemented – need developer apps, admin consent and server components |
| Claude's Zoom connector as an audio source | ❌ It does not provide live audio |
| macOS / Linux | ❌ Capture layer is Windows-only (WASAPI) |

## Architecture choices

| Item | Status |
|---|---|
| FastAPI local service | ❌ Deliberately not used – everything runs in-process; a local server would add an open port without benefit (see ARCHITECTURE.md) |
| Packaging with PyInstaller | see PACKAGING.md |
