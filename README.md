# Claude Interview Assistant

A Windows desktop app (Python 3.12 + PySide6) that helps you **prepare for and work through
interviews**: it transcribes meeting audio you have permission to capture, detects interview
questions, and drafts personalised answer suggestions with Claude, grounded strictly in your
own resume and the job description. A separate **practice mode** runs mock interviews and
scores your answers.

> **Use responsibly.** Many interview processes and employers do not allow AI assistance during
> live interviews, and recording/transcribing a conversation may require everyone's consent.
> The app is deliberately visible: explicit Start/Stop, a consent step for each session, a
> pulsing LISTENING indicator, a listening title in the taskbar, and no stealth, hidden
> recording, or screen-capture evasion. Practice mode is the intended use when assistance
> isn't permitted.

## Features

* **Live copilot** – platform-independent capture (Windows loopback for Zoom / Teams / Meet,
  plus microphone), live transcript, automatic question detection with duplicate
  suppression, streamed answer cards (spoken answer, key points, a resume example,
  context-quality badge, missing-detail warnings, copy, regenerate in 5 modes).
* **Personalisation** – profiles with resume import (PDF/TXT, local extraction with preview),
  job description, skills, projects, experience, achievements, answer length and speaking
  style. The prompts forbid inventing facts; missing details become `[placeholders]`.
* **Practice mode** – Claude writes role-specific questions; answer by typing or speaking;
  get 1–5 scores (relevance, clarity, technical accuracy, structure), feedback, and an
  improved example answer.
* **Privacy** – local Whisper transcription by default, transcript retention off by default,
  keys in Windows Credential Manager, redacted logs, one-click data deletion.
* Small always-on-top compact mode and global shortcuts (`Ctrl+Alt+P` pause/resume,
  `Ctrl+Alt+R` regenerate, `Ctrl+Alt+H` show/minimise, `Ctrl+Alt+C` copy latest answer).

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

Then: ⚙ Settings → Claude API → paste your key → **Test connection**; fill in the **Profile**
tab; try **Practice**; or press **Start session** in Live copilot.

## Documentation

| Guide | |
|---|---|
| [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) | Installation, first run, data locations |
| [docs/CLAUDE_API_SETUP.md](docs/CLAUDE_API_SETUP.md) | API key, models, effort, costs, what is sent |
| [docs/AUDIO_AND_TRANSCRIPTION.md](docs/AUDIO_AND_TRANSCRIPTION.md) | Permissions, per-platform setup, Whisper vs Deepgram |
| [docs/TESTING_AND_TROUBLESHOOTING.md](docs/TESTING_AND_TROUBLESHOOTING.md) | Running tests, common problems |
| [docs/PACKAGING.md](docs/PACKAGING.md) | Building the Windows executable |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture diagram, threads, design decisions |
| [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) | What is implemented, partial, or unsupported |

## Project layout

```
app/
  main.py            entry point
  controller.py      session orchestration (Qt signals)
  config.py          settings model, supported models, pricing
  audio/             capture interface, WASAPI mic + loopback, DSP + VAD segmenter
  transcription/     provider interface, local Whisper, Deepgram, pipeline, factory
  questions/         question detection + deduplication
  ai/                Claude service, prompts, parser, practice coach, profile extraction
  profiles/          profile model, resume import, editing format, repository
  storage/           SQLite with retention
  security/          secret store (Credential Manager), log redaction
  platforms/         Zoom / Teams / Meet metadata and process detection
  ui/                PySide6 windows, tabs, widgets, hotkeys, asyncio bridge
tests/               pytest suite (unit, Qt integration, opt-in hardware/network)
docs/                guides
packaging/           PyInstaller spec
scripts/             secret scanner
```
