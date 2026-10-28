# Packaging for Windows

The app is packaged as a PyInstaller **one-folder** build (faster start-up than one-file, and
antivirus tools flag it less often).

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\pyinstaller.exe packaging\interview_assistant.spec --noconfirm --clean
# Verify no credentials were bundled
.\.venv\Scripts\python.exe scripts\check_secrets.py dist\ClaudeInterviewAssistant
# Run it
.\dist\ClaudeInterviewAssistant\ClaudeInterviewAssistant.exe
```

Output: `dist\ClaudeInterviewAssistant\` – zip this folder to distribute it.

## What is and isn't bundled

| Bundled | Not bundled |
|---|---|
| Python runtime, Qt (Core/Gui/Widgets), app code | `.env`, settings, database, logs |
| CTranslate2 / ONNX Runtime DLLs, faster-whisper VAD assets | API keys (they live in Windows Credential Manager) |
| PortAudio (PyAudioWPatch) | Whisper models (downloaded on first use to `%LOCALAPPDATA%`) |

The spec refuses to build if a `.env`, `.db`, `.key` or `.pem` file would be bundled, and
`.env` is never read by the frozen app.

## Notes

* **Code signing:** unsigned executables trigger SmartScreen ("Windows protected your PC").
  Sign with `signtool sign /fd SHA256 /tr <timestamp-url> /td SHA256 /f cert.pfx ...` for
  distribution.
* **Pre-bundling a model:** to ship fully offline, copy a downloaded model folder next to the
  exe and set `CIA_MODELS_DIR` in a launcher script.
* **Installer:** wrap the folder with Inno Setup or WiX if an installer/Start-menu entry is needed.
