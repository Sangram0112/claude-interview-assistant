"""Windows audio capture using PyAudioWPatch (PortAudio with WASAPI loopback support).

* :class:`MicrophoneSource` - the user's microphone (labelled as the candidate).
* :class:`LoopbackSource` - "what you hear" from an output device, i.e. the other meeting
  participants in Zoom, Teams or a browser tab running Google Meet. This is the same
  mechanism Windows exposes to any recording app; it is platform-independent and requires
  no meeting-platform API.

Windows may block microphone access in Settings > Privacy & security > Microphone
("Let desktop apps access your microphone"); in that case opening the stream fails and
we surface an actionable message.
"""

from __future__ import annotations

import logging
import threading

from app.audio.base import (
    AudioCaptureError,
    AudioDeviceInfo,
    AudioSource,
    ErrorCallback,
    FrameCallback,
)
from app.audio.processing import pcm16_bytes_to_mono
from app.models import Speaker

log = logging.getLogger(__name__)

try:
    import pyaudiowpatch as pyaudio
except ImportError:  # pragma: no cover - only on non-Windows dev machines
    pyaudio = None  # type: ignore[assignment]

_MIC_PRIVACY_HINT = (
    "Check Windows Settings > Privacy & security > Microphone and allow desktop apps to "
    "access the microphone, then try again."
)


def _require_backend() -> None:
    if pyaudio is None:
        raise AudioCaptureError("Audio capture requires PyAudioWPatch (Windows only).")


class _PyAudioManager:
    """One shared PortAudio instance; PortAudio initialisation is process-global."""

    _lock = threading.Lock()
    _instance = None
    _users = 0

    @classmethod
    def acquire(cls):
        _require_backend()
        with cls._lock:
            if cls._instance is None:
                cls._instance = pyaudio.PyAudio()
            cls._users += 1
            return cls._instance

    @classmethod
    def release(cls) -> None:
        with cls._lock:
            cls._users = max(0, cls._users - 1)
            if cls._users == 0 and cls._instance is not None:
                cls._instance.terminate()
                cls._instance = None


def _wasapi_info(pa) -> dict:
    try:
        return pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as exc:
        raise AudioCaptureError("Windows WASAPI audio is not available on this system.") from exc


def list_input_devices() -> list[AudioDeviceInfo]:
    pa = _PyAudioManager.acquire()
    try:
        wasapi = _wasapi_info(pa)
        default_index = wasapi.get("defaultInputDevice", -1)
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("hostApi") != wasapi["index"] or info.get("isLoopbackDevice"):
                continue
            if int(info.get("maxInputChannels", 0)) <= 0:
                continue
            devices.append(
                AudioDeviceInfo(
                    index=i,
                    name=str(info["name"]),
                    sample_rate=int(info["defaultSampleRate"]),
                    channels=int(info["maxInputChannels"]),
                    is_loopback=False,
                    is_default=i == default_index,
                )
            )
        return devices
    finally:
        _PyAudioManager.release()


def list_loopback_devices() -> list[AudioDeviceInfo]:
    pa = _PyAudioManager.acquire()
    try:
        default_name = None
        try:
            default_name = pa.get_default_wasapi_loopback()["name"]
        except (OSError, LookupError, AttributeError):
            pass
        devices = []
        for info in pa.get_loopback_device_info_generator():
            devices.append(
                AudioDeviceInfo(
                    index=int(info["index"]),
                    name=str(info["name"]),
                    sample_rate=int(info["defaultSampleRate"]),
                    channels=max(1, int(info["maxInputChannels"])),
                    is_loopback=True,
                    is_default=info["name"] == default_name,
                )
            )
        return devices
    finally:
        _PyAudioManager.release()


class _PyAudioSource(AudioSource):
    def __init__(self, speaker: Speaker, label: str, device_index: int | None, frames_ms: int = 30) -> None:
        super().__init__(speaker, label)
        self._device_index = device_index
        self._frames_ms = frames_ms
        self._pa = None
        self._stream = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    def _resolve_device(self, pa) -> dict:
        raise NotImplementedError

    def start(self, on_frames: FrameCallback, on_error: ErrorCallback) -> None:
        with self._lock:
            if self._running:
                return
            self._pa = _PyAudioManager.acquire()
            try:
                info = self._resolve_device(self._pa)
                rate = int(info["defaultSampleRate"])
                channels = max(1, int(info["maxInputChannels"]))
                frames_per_buffer = max(64, int(rate * self._frames_ms / 1000))

                def callback(in_data, frame_count, time_info, status):  # PortAudio thread
                    if status:
                        log.debug("%s stream status flag %s", self.label, status)
                    try:
                        on_frames(self, pcm16_bytes_to_mono(in_data, channels), rate)
                    except Exception:  # noqa: BLE001 - never raise into PortAudio
                        log.exception("Frame handler failed for %s", self.label)
                    return (None, pyaudio.paContinue)

                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=int(info["index"]),
                    frames_per_buffer=frames_per_buffer,
                    stream_callback=callback,
                )
                self._stream.start_stream()
                self._running = True
                log.info("Started %s capture on device '%s' (%d Hz, %d ch)", self.label, info["name"], rate, channels)
            except AudioCaptureError:
                self._release()
                raise
            except OSError as exc:
                self._release()
                raise AudioCaptureError(self._open_error_message(exc)) from exc

    def _open_error_message(self, exc: OSError) -> str:
        return f"Could not open {self.label} ({exc})."

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except OSError:
                    log.warning("Error while closing %s stream", self.label)
                self._stream = None
            if self._running:
                log.info("Stopped %s capture", self.label)
            self._running = False
            self._release()

    def _release(self) -> None:
        if self._pa is not None:
            self._pa = None
            _PyAudioManager.release()


class MicrophoneSource(_PyAudioSource):
    def __init__(self, device_index: int | None = None, frames_ms: int = 30) -> None:
        super().__init__(Speaker.CANDIDATE, "microphone", device_index, frames_ms)

    def _resolve_device(self, pa) -> dict:
        if self._device_index is not None:
            return pa.get_device_info_by_index(self._device_index)
        wasapi = _wasapi_info(pa)
        index = wasapi.get("defaultInputDevice", -1)
        if index is None or index < 0:
            raise AudioCaptureError("No microphone found. Connect a microphone or choose one in Settings.")
        return pa.get_device_info_by_index(index)

    def _open_error_message(self, exc: OSError) -> str:
        return f"Could not open the microphone ({exc}). {_MIC_PRIVACY_HINT}"


class LoopbackSource(_PyAudioSource):
    def __init__(self, device_index: int | None = None, frames_ms: int = 30) -> None:
        super().__init__(Speaker.INTERVIEWER, "system audio", device_index, frames_ms)

    def _resolve_device(self, pa) -> dict:
        if self._device_index is not None:
            info = pa.get_device_info_by_index(self._device_index)
            if not info.get("isLoopbackDevice"):
                raise AudioCaptureError("The selected system-audio device is not a loopback device.")
            return info
        try:
            return pa.get_default_wasapi_loopback()
        except (OSError, LookupError) as exc:
            raise AudioCaptureError(
                "No system-audio (loopback) device found. Make sure an output device such as "
                "speakers or headphones is enabled in Windows Sound settings."
            ) from exc
