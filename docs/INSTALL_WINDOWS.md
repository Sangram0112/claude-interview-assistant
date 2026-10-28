# Installation on Windows

## Requirements

* Windows 10 (21H2+) or Windows 11, 64-bit
* Python **3.12** (3.13 also works; 3.12 is what the project is tested on)
* ~1.5 GB disk for dependencies + 75–500 MB for a Whisper speech model
* Internet access for installation, for the first-run Whisper model download, and for Claude

## From source

Run these in PowerShell from the project folder:

```powershell
# 1. Create a virtual environment with Python 3.12
py -3.12 -m venv .venv

# 2. Install dependencies
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. (dev only) install test tools
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 4. Start the app
.\.venv\Scripts\python.exe -m app.main
```

On slow or unstable connections add `--retries 10 --timeout 60` to the pip command.

## First run

1. **Settings (⚙) > Claude API** – paste your Anthropic API key, click **Save key**, then
   **Test connection**. See [CLAUDE_API_SETUP.md](CLAUDE_API_SETUP.md).
2. **Profile tab** – import your resume (PDF/TXT), review the extracted text, paste the job
   description, fill skills/projects/experience, **Save profile**.
3. **Practice tab** – try a mock interview (no meeting needed).
4. **Live copilot** – choose the platform, press **Start session**, accept the consent
   dialog. The first start downloads the Whisper model (`base.en`, ~145 MB).

## Where data is stored

| What | Where |
|---|---|
| Settings, SQLite DB, logs, Whisper models | `%LOCALAPPDATA%\ClaudeInterviewAssistant\` |
| API keys | Windows Credential Manager → *Windows Credentials* → `ClaudeInterviewAssistant` |
| Window position | `HKCU\Software\ClaudeInterviewAssistant` |

Set `CIA_DATA_DIR` to use a different (e.g. portable) data folder.

## Uninstall

Delete the project/app folder, then in the app use **Privacy > Delete ALL local data & keys**
first (or delete `%LOCALAPPDATA%\ClaudeInterviewAssistant` and the Credential Manager entries).
