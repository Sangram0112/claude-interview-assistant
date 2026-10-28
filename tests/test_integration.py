"""Integration tests against real components. Run with:  pytest -m integration

* Windows WASAPI device enumeration and a short real loopback/mic capture.
* Real faster-whisper transcription of speech synthesised by Windows SAPI (downloads the
  tiny.en model on first run).
* Optional live Claude call when ANTHROPIC_API_KEY is set (spends a few cents).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import wave

import numpy as np
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform != "win32", reason="Windows only")]

QUESTION = "Can you tell me about a time you handled a conflict in your team?"


def _synthesize(path) -> None:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{QUESTION}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=60)


def _read_wav(path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    assert width == 2
    from app.audio.processing import pcm16_bytes_to_mono

    return pcm16_bytes_to_mono(data, channels), rate


def test_wasapi_devices_enumerate():
    from app.audio.pyaudio_sources import list_input_devices, list_loopback_devices

    loopbacks = list_loopback_devices()
    inputs = list_input_devices()
    print("loopback:", [d.name for d in loopbacks])
    print("inputs:", [d.name for d in inputs])
    assert loopbacks, "no WASAPI loopback device found"


def test_loopback_capture_opens_and_closes():
    from app.audio.pyaudio_sources import LoopbackSource

    frames = []
    src = LoopbackSource()
    src.start(lambda s, samples, rate: frames.append((len(samples), rate)), lambda s, m: None)
    time.sleep(1.0)
    src.stop()
    assert not src.is_running
    # Loopback delivers frames only while something plays; opening/closing is what we verify.
    print(f"captured {len(frames)} buffers")


def test_real_whisper_transcribes_synthesized_question(tmp_path):
    from app.audio.processing import resample
    from app.config import AudioSettings, TranscriptionSettings
    from app.models import Speaker
    from app.questions.detector import QuestionDetector
    from app.transcription.whisper_local import LocalWhisperProvider

    wav = tmp_path / "q.wav"
    _synthesize(wav)
    audio, rate = _read_wav(wav)
    audio = resample(audio, rate, 16000)

    events = []
    provider = LocalWhisperProvider(
        TranscriptionSettings(whisper_model=os.environ.get("CIA_WHISPER_MODEL", "tiny.en"), interim_results=True),
        AudioSettings(),
    )
    started = time.monotonic()
    provider.start(events.append, print, lambda m, f: print("ERR", m))
    load_s = time.monotonic() - started
    # Feed in real-time-sized chunks like the capture callback does.
    chunk = 480
    for i in range(0, len(audio), chunk):
        provider.feed(Speaker.INTERVIEWER, audio[i : i + chunk])
    provider.feed(Speaker.INTERVIEWER, np.zeros(16000, np.float32))
    t_end = time.monotonic()
    deadline = t_end + 60
    while time.monotonic() < deadline and not any(e.is_final for e in events):
        time.sleep(0.05)
    latency = time.monotonic() - t_end
    provider.stop()
    finals = [e for e in events if e.is_final]
    assert finals, "no final transcript"
    text = " ".join(e.text for e in finals)
    print(f"model load {load_s:.1f}s, final after {latency:.2f}s: {text!r}")
    assert "conflict" in text.lower()
    questions = QuestionDetector().process(finals[0])
    assert questions and questions[0].category.value == "behavioral"


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_live_claude_answer():
    from app.ai.client import ClaudeService
    from app.config import AISettings, AnswerLength
    from app.models import AnswerMode, DetectedQuestion, QuestionCategory, Speaker
    from app.profiles.models import Project, UserProfile
    from app.security.secrets import SecretStore

    profile = UserProfile(
        target_job_title="Junior Data Analyst",
        skills=["SQL", "Python", "Power BI"],
        projects=[Project(name="Sales Dashboard", technologies=["Power BI", "SQL"], description="Weekly KPI dashboard for a retail club")],
    )
    service = ClaudeService(lambda: AISettings(), SecretStore())
    q = DetectedQuestion(text="Walk me through a project you are proud of.", category=QuestionCategory.PROJECT, speaker=Speaker.INTERVIEWER)
    s = asyncio.run(service.stream_answer(q, profile, AnswerMode.CONCISE, AnswerLength.SHORT))
    print(s)
    assert s.spoken_answer and "Sales Dashboard" in s.raw_text
