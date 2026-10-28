# PyInstaller spec - build with:  .venv\Scripts\pyinstaller packaging\interview_assistant.spec --noconfirm
# Produces dist\ClaudeInterviewAssistant\ClaudeInterviewAssistant.exe (one-folder build).
# No .env, settings, database or API keys are included: they live in the user's profile.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

datas = []
datas += collect_data_files("faster_whisper")  # includes the Silero VAD assets
binaries = []
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("pyaudiowpatch")

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "keyring.backends.Windows",
        "app.transcription.whisper_local",
        "app.transcription.deepgram",
        "app.audio.pyaudio_sources",
    ],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "pytest"],
    noarchive=False,
)
# Defence in depth: refuse to bundle anything that looks like a secrets file.
for entry in list(a.datas):
    if Path(entry[0]).name.startswith(".env") or entry[0].endswith((".db", ".key", ".pem")):
        raise SystemExit(f"Refusing to bundle sensitive file: {entry[0]}")

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClaudeInterviewAssistant",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ClaudeInterviewAssistant", upx=False)
