"""Diagnostics: ``ClaudeInterviewAssistant.exe --self-test`` (or ``python -m app.main --self-test``).

Checks every native layer without opening the UI and writes a report to
``<data dir>\\selftest.txt`` (windowed builds have no console). Makes no API calls and
captures no audio content (devices are only opened and closed).
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable

from app.paths import data_dir


def run_self_test() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> None:
        started = time.monotonic()
        try:
            detail = fn()
            results.append((name, True, f"{detail} ({time.monotonic() - started:.1f}s)"))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"))

    def qt() -> str:
        from PySide6 import __version__ as v
        from PySide6.QtWidgets import QApplication  # noqa: F401

        return f"PySide6 {v}"

    def sdk() -> str:
        import anthropic

        anthropic.AsyncAnthropic(api_key="sk-ant-selftest-not-a-key")
        return f"anthropic {anthropic.__version__}"

    def devices() -> str:
        from app.audio.pyaudio_sources import list_input_devices, list_loopback_devices

        return f"{len(list_loopback_devices())} loopback, {len(list_input_devices())} input devices"

    def loopback_open_close() -> str:
        from app.audio.pyaudio_sources import LoopbackSource

        src = LoopbackSource()
        src.start(lambda *_: None, lambda *_: None)
        src.stop()
        return "opened and closed"

    def whisper_runtime() -> str:
        import ctranslate2
        import numpy as np

        from app.config import Settings
        from app.paths import models_dir

        settings = Settings.load()
        cached = any(p.name.endswith(settings.transcription.whisper_model) for p in models_dir().glob("models--*"))
        if not cached:
            return f"ctranslate2 {ctranslate2.__version__} loads; model '{settings.transcription.whisper_model}' not downloaded yet (skipped)"
        from faster_whisper import WhisperModel

        model = WhisperModel(settings.transcription.whisper_model, device="cpu", compute_type="int8", download_root=str(models_dir()))
        list(model.transcribe(np.zeros(16000, np.float32), language="en")[0])
        return f"ctranslate2 {ctranslate2.__version__}; '{settings.transcription.whisper_model}' loaded and ran"

    def credential_store() -> str:
        import keyring

        return f"backend {type(keyring.get_keyring()).__name__}"

    check("Qt GUI library", qt)
    check("Claude SDK", sdk)
    check("Credential store", credential_store)
    check("Audio devices (WASAPI)", devices)
    check("System-audio capture", loopback_open_close)
    check("Speech-to-text runtime", whisper_runtime)

    ok = all(passed for _, passed, _ in results)
    lines = [f"{'PASS' if passed else 'FAIL'}  {name}: {detail}" for name, passed, detail in results]
    lines.append("RESULT: " + ("all checks passed" if ok else "some checks failed"))
    report = "\n".join(lines)
    (data_dir() / "selftest.txt").write_text(report, encoding="utf-8")
    try:
        print(report)
    except Exception:  # noqa: BLE001 - no console in windowed builds
        pass
    return 0 if ok else 1
