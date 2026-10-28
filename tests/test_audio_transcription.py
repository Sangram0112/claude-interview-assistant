import threading
import time

import numpy as np
import pytest

from app.audio.base import AudioCaptureError, AudioSource
from app.audio.processing import (
    UtteranceSegmenter,
    float_to_pcm16,
    interleaved_to_mono,
    pcm16_bytes_to_mono,
    resample,
    rms,
)
from app.config import AudioSettings, TranscriptionSettings
from app.models import PipelineState, Speaker, TranscriptEvent
from app.transcription.base import TranscriptionError, TranscriptionProvider
from app.transcription.deepgram import DeepgramProvider, UtteranceAssembler, build_url
from app.transcription.pipeline import PipelineError, TranscriptionPipeline
from app.transcription.whisper_local import LocalWhisperProvider

SR = 16000


def tone(seconds: float, amp: float = 0.2, rate: int = SR) -> np.ndarray:
    """Speech-like test signal: a 220 Hz voice-band tone with a ~4 Hz syllable envelope."""
    t = np.arange(int(seconds * rate)) / rate
    envelope = 0.15 + 0.85 * np.abs(np.sin(2 * np.pi * 4 * t))
    return (amp * envelope * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float, rate: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * rate), np.float32)


# --- processing --------------------------------------------------------------------
def test_mono_and_pcm_conversion():
    stereo = np.array([0.5, -0.5, 0.25, 0.25], np.float32)
    assert np.allclose(interleaved_to_mono(stereo, 2), [0.0, 0.25])
    pcm = float_to_pcm16(np.array([0.5, -0.5], np.float32))
    assert np.allclose(pcm16_bytes_to_mono(pcm, 1), [0.5, -0.5], atol=1e-3)


def test_resample_lengths_and_energy():
    x = tone(1.0, rate=48000)
    y = resample(x, 48000, SR)
    assert len(y) == SR
    assert abs(rms(y) - rms(x)) < 0.02
    assert resample(x, SR, SR) is not None


def test_segmenter_finalizes_after_silence():
    seg = UtteranceSegmenter(silence_ms=300, interim_interval_s=None)
    events = seg.feed(silence(0.5)) + seg.feed(tone(1.0)) + seg.feed(silence(0.5))
    finals = [e for e in events if e.is_final]
    assert len(finals) == 1
    assert 0.9 * SR < len(finals[0].audio) < 1.8 * SR


def test_segmenter_emits_interims_with_same_id():
    seg = UtteranceSegmenter(silence_ms=300, interim_interval_s=0.5)
    events = seg.feed(silence(0.3)) + seg.feed(tone(1.6)) + seg.feed(silence(0.4))
    interims = [e for e in events if not e.is_final]
    finals = [e for e in events if e.is_final]
    assert len(interims) >= 2 and len(finals) == 1
    assert {e.utterance_id for e in events} == {finals[0].utterance_id}


def test_segmenter_ignores_short_clicks_and_noise_floor():
    seg = UtteranceSegmenter(silence_ms=300, interim_interval_s=None, min_rms=0.001)
    noise = (np.random.default_rng(0).standard_normal(SR) * 0.003).astype(np.float32)
    assert seg.feed(noise) == []  # steady noise adapts the floor
    events = seg.feed(tone(0.06, amp=0.3)) + seg.feed(silence(0.5))
    assert [e for e in events if e.is_final] == []  # 60 ms click is not speech


def test_steady_noise_above_min_rms_is_not_speech():
    """Regression: noise louder than min_rms at session start must not open an utterance."""
    seg = UtteranceSegmenter(silence_ms=300, interim_interval_s=None, min_rms=0.001)
    hum = (np.random.default_rng(1).standard_normal(SR * 20) * 0.01).astype(np.float32)
    events = []
    for i in range(0, len(hum), 480):
        events += seg.feed(hum[i : i + 480])
    assert events == [] and not seg.in_speech


def test_speech_detected_over_background_noise():
    seg = UtteranceSegmenter(silence_ms=300, interim_interval_s=None, min_rms=0.001)
    rng = np.random.default_rng(2)
    noise = lambda s: (rng.standard_normal(int(s * SR)) * 0.005).astype(np.float32)  # noqa: E731
    events = seg.feed(noise(2.0)) + seg.feed(tone(1.5, amp=0.2) + noise(1.5)) + seg.feed(noise(1.0))
    finals = [e for e in events if e.is_final]
    assert len(finals) == 1 and 1.4 * SR < len(finals[0].audio) < 2.4 * SR


def test_segmenter_poll_finalizes_when_audio_stops():
    seg = UtteranceSegmenter(silence_ms=300, interim_interval_s=None)
    seg.feed(silence(0.3), now=99.9)
    seg.feed(tone(1.0), now=100.0)
    assert seg.in_speech
    assert seg.poll(now=100.1) is None
    ev = seg.poll(now=100.5)
    assert ev is not None and ev.is_final


def test_segmenter_max_length():
    seg = UtteranceSegmenter(max_utterance_s=1.0, interim_interval_s=None)
    finals = [e for e in seg.feed(silence(0.3)) + seg.feed(tone(2.5)) if e.is_final]
    assert len(finals) == 2


# --- fakes -------------------------------------------------------------------------
class FakeSource(AudioSource):
    def __init__(self, speaker=Speaker.INTERVIEWER, fail=False):
        super().__init__(speaker, f"fake-{speaker.value}")
        self.fail = fail
        self._running = False
        self.starts = 0
        self.cb = None

    @property
    def is_running(self):
        return self._running

    def start(self, on_frames, on_error):
        if self.fail:
            raise AudioCaptureError("device missing")
        self.starts += 1
        self._running = True
        self.cb = on_frames

    def stop(self):
        self._running = False

    def emit(self, samples, rate=48000):
        if self._running:
            self.cb(self, samples, rate)


class FakeProvider(TranscriptionProvider):
    def __init__(self, fail_start=False):
        self.fed: list[tuple[Speaker, int]] = []
        self.started = self.stopped = self.flushed = 0
        self.fail_start = fail_start
        self.on_event = None

    def start(self, on_event, on_status, on_error):
        if self.fail_start:
            raise TranscriptionError("model missing")
        self.started += 1
        self.on_event = on_event

    def feed(self, speaker, samples):
        self.fed.append((speaker, len(samples)))

    def flush(self):
        self.flushed += 1

    def stop(self):
        self.stopped += 1


# --- pipeline ----------------------------------------------------------------------
def test_pipeline_lifecycle_and_privacy_guarantees():
    src, prov = FakeSource(), FakeProvider()
    states, events = [], []
    p = TranscriptionPipeline([src], prov, on_event=events.append, on_state=states.append)
    src.cb = None
    assert not src.is_running  # nothing opened before start
    p.start()
    assert p.state == PipelineState.LISTENING and src.is_running
    src.emit(tone(0.03, rate=48000))
    assert prov.fed and prov.fed[0][1] == 480  # resampled 48k -> 16k
    p.pause()
    assert not src.is_running and prov.flushed == 1  # device closed while paused
    p.resume()
    assert src.is_running and src.starts == 2
    p.stop()
    assert not src.is_running and prov.stopped == 1
    n = len(prov.fed)
    src.cb(src, tone(0.03), 48000)  # late callback from audio thread after stop
    assert len(prov.fed) == n
    assert states[-1] == PipelineState.IDLE
    prov.on_event(TranscriptEvent(Speaker.INTERVIEWER, "late", True, "x"))
    assert events == []  # no events delivered after stop


def test_pipeline_requires_a_source():
    with pytest.raises(PipelineError):
        TranscriptionPipeline([], FakeProvider(), on_event=lambda e: None)


def test_pipeline_start_failure_cleans_up():
    good, bad = FakeSource(Speaker.INTERVIEWER), FakeSource(Speaker.CANDIDATE, fail=True)
    prov = FakeProvider()
    p = TranscriptionPipeline([good, bad], prov, on_event=lambda e: None)
    with pytest.raises(PipelineError, match="device missing"):
        p.start()
    assert not good.is_running and prov.stopped == 1 and p.state == PipelineState.ERROR

    p2 = TranscriptionPipeline([FakeSource()], FakeProvider(fail_start=True), on_event=lambda e: None)
    with pytest.raises(PipelineError, match="model missing"):
        p2.start()


# --- whisper provider (fake model) ---------------------------------------------------
class FakeSegment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=-0.2):
        self.text, self.no_speech_prob, self.avg_logprob = text, no_speech_prob, avg_logprob


class FakeWhisperModel:
    def __init__(self, text="Can you tell me about yourself?"):
        self.text = text
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((len(audio), kwargs))
        return [FakeSegment(self.text)], None


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_whisper_provider_end_to_end_with_fake_model():
    model = FakeWhisperModel()
    provider = LocalWhisperProvider(
        TranscriptionSettings(interim_results=False), AudioSettings(silence_ms=300), model_factory=lambda: model
    )
    events: list[TranscriptEvent] = []
    lock = threading.Lock()

    def on_event(e):
        with lock:
            events.append(e)

    provider.start(on_event, lambda m: None, lambda m, f: None)
    provider.feed(Speaker.INTERVIEWER, silence(0.3))
    provider.feed(Speaker.INTERVIEWER, tone(1.2))
    provider.feed(Speaker.INTERVIEWER, silence(0.5))
    assert _wait_for(lambda: any(e.is_final for e in events))
    provider.stop()
    final = [e for e in events if e.is_final][0]
    assert final.text == "Can you tell me about yourself?" and final.speaker == Speaker.INTERVIEWER
    assert model.calls[0][1]["vad_filter"] is False


def test_whisper_filters_hallucinations():
    provider = LocalWhisperProvider(TranscriptionSettings(), AudioSettings(), model_factory=lambda: FakeWhisperModel("Thank you."))
    provider._model = FakeWhisperModel("Thank you.")
    assert provider._transcribe(tone(1.0), final=True) == ""
    provider._model = FakeWhisperModel("noise")
    provider._model.transcribe = lambda a, **k: ([FakeSegment("noise", no_speech_prob=0.9, avg_logprob=-1.5)], None)
    assert provider._transcribe(tone(1.0), final=True) == ""


def test_whisper_long_path_error_is_specific():
    def too_long():
        err = OSError("The filename or extension is too long")
        err.winerror = 206
        raise err

    provider = LocalWhisperProvider(TranscriptionSettings(), AudioSettings(), model_factory=too_long)
    with pytest.raises(TranscriptionError, match="CIA_MODELS_DIR"):
        provider.start(lambda e: None, lambda m: None, lambda m, f: None)


def test_models_dir_override(monkeypatch, tmp_path):
    from app.paths import models_dir

    monkeypatch.setenv("CIA_MODELS_DIR", str(tmp_path / "m"))
    assert models_dir() == tmp_path / "m" and (tmp_path / "m").is_dir()


def test_whisper_load_failure_is_user_friendly():
    def boom():
        raise RuntimeError("no internet")

    provider = LocalWhisperProvider(TranscriptionSettings(), AudioSettings(), model_factory=boom)
    with pytest.raises(TranscriptionError, match="internet"):
        provider.start(lambda e: None, lambda m: None, lambda m, f: None)


# --- deepgram ------------------------------------------------------------------------
def _result(text, is_final=False, speech_final=False):
    return {"type": "Results", "is_final": is_final, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


def test_deepgram_assembler():
    a = UtteranceAssembler(Speaker.INTERVIEWER)
    e1 = a.handle(_result("tell me"))
    assert e1 and not e1.is_final and e1.text == "tell me"
    e2 = a.handle(_result("tell me about", is_final=True))
    assert e2 and not e2.is_final
    e3 = a.handle(_result("yourself"))
    assert e3.text == "tell me about yourself" and e3.utterance_id == e1.utterance_id
    e4 = a.handle(_result("yourself?", is_final=True, speech_final=True))
    assert e4.is_final and e4.text == "tell me about yourself?"
    e5 = a.handle(_result("next"))
    assert e5.utterance_id != e1.utterance_id
    assert a.handle({"type": "Metadata"}) is None
    a.handle(_result("why us", is_final=True))
    end = a.handle({"type": "UtteranceEnd"})
    assert end.is_final and end.text == "why us"


def test_deepgram_url_and_missing_key():
    url = build_url(TranscriptionSettings(), 700)
    assert "encoding=linear16" in url and "sample_rate=16000" in url and "interim_results=true" in url
    with pytest.raises(TranscriptionError, match="API key"):
        DeepgramProvider(TranscriptionSettings(), None).start(lambda e: None, lambda m: None, lambda m, f: None)
