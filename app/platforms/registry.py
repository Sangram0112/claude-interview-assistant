"""Meeting platform metadata.

The app does NOT integrate with any meeting platform's API. All three platforms are supported
the same way: Windows loopback capture of whatever the meeting app plays through the selected
output device, plus the user's microphone. This module only records that fact, offers setup
guidance, and detects whether the platform's app appears to be running.
"""

from __future__ import annotations

import csv
import io
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)


class IntegrationStatus(str, Enum):
    VERIFIED_LOOPBACK = "Supported via Windows system-audio capture (platform-independent)"
    NOT_IMPLEMENTED = "Not implemented"


@dataclass(frozen=True, slots=True)
class PlatformInfo:
    id: str
    name: str
    process_names: tuple[str, ...]
    capture: IntegrationStatus
    native_api: IntegrationStatus
    setup_tips: tuple[str, ...]
    native_api_notes: str
    extra: dict = field(default_factory=dict)


PLATFORMS: dict[str, PlatformInfo] = {
    "zoom": PlatformInfo(
        id="zoom",
        name="Zoom (desktop app)",
        process_names=("Zoom.exe",),
        capture=IntegrationStatus.VERIFIED_LOOPBACK,
        native_api=IntegrationStatus.NOT_IMPLEMENTED,
        setup_tips=(
            "In Zoom > Settings > Audio, note which Speaker device Zoom uses.",
            "In this app's Settings > Audio, pick the loopback of that same device (or leave 'Default').",
            "If you use headphones through a USB/Bluetooth adapter, select that adapter's loopback.",
        ),
        native_api_notes=(
            "Zoom's Realtime Media Streams (RTMS) and Meeting SDK can provide audio/transcripts but "
            "require a Zoom developer app, host/admin approval and a server-side component. Claude's "
            "Zoom connector does not provide live audio. Neither is implemented here."
        ),
    ),
    "teams": PlatformInfo(
        id="teams",
        name="Microsoft Teams (desktop app)",
        process_names=("ms-teams.exe", "Teams.exe"),
        capture=IntegrationStatus.VERIFIED_LOOPBACK,
        native_api=IntegrationStatus.NOT_IMPLEMENTED,
        setup_tips=(
            "In Teams > Settings > Devices, note the Speaker device.",
            "Select the loopback of that device in this app's Settings > Audio (or leave 'Default').",
        ),
        native_api_notes=(
            "Microsoft Graph exposes meeting transcripts only after a meeting ends, and real-time "
            "media bots need Azure registration and tenant admin consent. Not implemented here."
        ),
    ),
    "meet": PlatformInfo(
        id="meet",
        name="Google Meet (browser)",
        process_names=("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"),
        capture=IntegrationStatus.VERIFIED_LOOPBACK,
        native_api=IntegrationStatus.NOT_IMPLEMENTED,
        setup_tips=(
            "Meet plays audio through the browser; Windows loopback captures it like any other app.",
            "Browser tab-capture APIs are not used, so no browser extension is needed.",
            "If the browser outputs to a non-default device, choose that device's loopback in Settings.",
        ),
        native_api_notes=(
            "The Google Meet REST API provides transcripts after a meeting (Workspace editions with "
            "transcription). Real-time media access requires the Meet Media API developer preview. "
            "Not implemented here."
        ),
    ),
    "other": PlatformInfo(
        id="other",
        name="Other / in-person (microphone only)",
        process_names=(),
        capture=IntegrationStatus.VERIFIED_LOOPBACK,
        native_api=IntegrationStatus.NOT_IMPLEMENTED,
        setup_tips=("Any app that plays audio on this PC can be captured via system audio.",),
        native_api_notes="",
    ),
}


def running_processes() -> set[str]:
    if sys.platform != "win32":
        return set()
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {row[0].lower() for row in csv.reader(io.StringIO(out)) if row}


def detect_platform_running(platform_id: str, processes: set[str] | None = None) -> bool | None:
    """True/False if detectable, None if the platform has no known process."""
    info = PLATFORMS.get(platform_id)
    if info is None or not info.process_names:
        return None
    processes = processes if processes is not None else running_processes()
    return any(name.lower() in processes for name in info.process_names)
