"""Cloud streaming speech-to-text via Deepgram's live WebSocket API.

Requires a Deepgram API key (Settings > API keys, or ``DEEPGRAM_API_KEY``). Audio for the
selected sources is streamed to Deepgram while a session is listening.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

import numpy as np

from app.audio.processing import TARGET_RATE, float_to_pcm16
from app.config import TranscriptionSettings
from app.models import Speaker, TranscriptEvent
from app.transcription.base import (
    ErrorCallback,
    EventCallback,
    StatusCallback,
    TranscriptionError,
    TranscriptionProvider,
)

log = logging.getLogger(__name__)

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"
_KEEPALIVE_S = 5.0
_MAX_RECONNECTS = 5


@dataclass
class UtteranceAssembler:
    """Maps Deepgram's result stream onto our interim/final utterance model.

    Deepgram sends ``is_final`` results for consecutive pieces of speech and marks the end of
    an utterance with ``speech_final`` (or a separate ``UtteranceEnd`` message).
    """

    speaker: Speaker
    _final_parts: list[str] = field(default_factory=list)
    _utterance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def handle(self, message: dict) -> TranscriptEvent | None:
        kind = message.get("type")
        if kind == "UtteranceEnd":
            return self._close()
        if kind != "Results":
            return None
        try:
            transcript = message["channel"]["alternatives"][0].get("transcript", "")
        except (KeyError, IndexError, TypeError):
            return None
        transcript = transcript.strip()
        if message.get("is_final"):
            if transcript:
                self._final_parts.append(transcript)
            if message.get("speech_final"):
                return self._close()
            if not transcript:
                return None
            return TranscriptEvent(self.speaker, " ".join(self._final_parts), False, self._utterance_id)
        if not transcript:
            return None
        text = " ".join([*self._final_parts, transcript])
        return TranscriptEvent(self.speaker, text, False, self._utterance_id)

    def _close(self) -> TranscriptEvent | None:
        text = " ".join(self._final_parts).strip()
        event = TranscriptEvent(self.speaker, text, True, self._utterance_id) if text else None
        self._final_parts = []
        self._utterance_id = uuid.uuid4().hex[:12]
        return event


def build_url(settings: TranscriptionSettings, silence_ms: int) -> str:
    params = {
        "model": settings.deepgram_model,
        "language": settings.language or "en",
        "encoding": "linear16",
        "sample_rate": TARGET_RATE,
        "channels": 1,
        "punctuate": "true",
        "smart_format": "true",
        "interim_results": "true",
        "endpointing": max(100, silence_ms // 2),
        "utterance_end_ms": max(1000, silence_ms + 300),
    }
    return f"{DEEPGRAM_URL}?{urlencode(params)}"


class DeepgramProvider(TranscriptionProvider):
    display_name = "Deepgram (cloud streaming)"
    sends_audio_externally = True

    def __init__(self, settings: TranscriptionSettings, api_key: str | None, silence_ms: int = 700) -> None:
        self._settings = settings
        self._api_key = api_key
        self._silence_ms = silence_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queues: dict[Speaker, asyncio.Queue[bytes | None]] = {}
        self._tasks: list[asyncio.Task] = []
        self._stopping = threading.Event()
        self._on_event: EventCallback | None = None
        self._on_status: StatusCallback | None = None
        self._on_error: ErrorCallback | None = None

    def start(self, on_event: EventCallback, on_status: StatusCallback, on_error: ErrorCallback) -> None:
        if not self._api_key:
            raise TranscriptionError(
                "Deepgram is selected but no Deepgram API key is configured. Add it in "
                "Settings > API keys, or switch to Local Whisper."
            )
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise TranscriptionError("The 'websockets' package is required for Deepgram.") from exc
        self._on_event, self._on_status, self._on_error = on_event, on_status, on_error
        self._stopping.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="deepgram-loop", daemon=True)
        self._thread.start()
        on_status("Connecting to Deepgram…")

    def feed(self, speaker: Speaker, samples: np.ndarray) -> None:
        if self._loop is None or self._stopping.is_set():
            return
        payload = float_to_pcm16(samples)
        self._loop.call_soon_threadsafe(self._enqueue, speaker, payload)

    def _enqueue(self, speaker: Speaker, payload: bytes | None) -> None:
        q = self._queues.get(speaker)
        if q is None:
            q = asyncio.Queue(maxsize=500)
            self._queues[speaker] = q
            self._tasks.append(asyncio.ensure_future(self._stream(speaker, q)))
        if q.full():  # connection stalled: drop oldest audio rather than grow unbounded
            q.get_nowait()
        q.put_nowait(payload)

    async def _stream(self, speaker: Speaker, q: asyncio.Queue[bytes | None]) -> None:
        import websockets

        url = build_url(self._settings, self._silence_ms)
        headers = {"Authorization": f"Token {self._api_key}"}
        assembler = UtteranceAssembler(speaker)
        attempts = 0
        while not self._stopping.is_set():
            try:
                async with websockets.connect(url, additional_headers=headers, max_size=2**22) as ws:
                    attempts = 0
                    if self._on_status:
                        self._on_status(f"Deepgram connected ({speaker.value})")
                    await self._pump(ws, q, assembler)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network, auth, protocol
                attempts += 1
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (401, 403):
                    self._report("Deepgram rejected the API key (unauthorized). Check Settings > API keys.", True)
                    return
                if attempts > _MAX_RECONNECTS:
                    self._report("Lost connection to Deepgram and could not reconnect.", True)
                    return
                delay = min(2**attempts, 15)
                log.warning("Deepgram connection error (%s); retrying in %ss", type(exc).__name__, delay)
                self._report(f"Deepgram connection problem; reconnecting in {delay}s…", False)
                await asyncio.sleep(delay)

    async def _pump(self, ws, q: asyncio.Queue[bytes | None], assembler: UtteranceAssembler) -> None:
        async def sender() -> None:
            while True:
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_S)
                except TimeoutError:
                    await ws.send(json.dumps({"type": "KeepAlive"}))
                    continue
                if chunk is None:
                    await ws.send(json.dumps({"type": "CloseStream"}))
                    return
                await ws.send(chunk)

        async def receiver() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                event = assembler.handle(message)
                if event and self._on_event:
                    self._on_event(event)

        send_task = asyncio.ensure_future(sender())
        try:
            await receiver()
        finally:
            send_task.cancel()

    def _report(self, message: str, fatal: bool) -> None:
        if self._on_error:
            self._on_error(message, fatal)

    def stop(self) -> None:
        if self._loop is None:
            return
        self._stopping.set()
        loop = self._loop

        async def shutdown() -> None:
            for q in self._queues.values():
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            if self._tasks:
                _done, pending = await asyncio.wait(self._tasks, timeout=3)
                for task in pending:
                    task.cancel()

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=5)
        except Exception:  # noqa: BLE001
            log.warning("Deepgram shutdown timed out")
        loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        loop.close()
        self._loop = None
        self._thread = None
        self._queues.clear()
        self._tasks.clear()
