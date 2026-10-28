"""System-wide hotkeys via the Win32 ``RegisterHotKey`` API.

RegisterHotKey only notifies us when the exact registered combination is pressed; it does
not observe any other keystrokes (unlike a keyboard hook), so no typing is ever read.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000

# id -> (action, modifiers, virtual key, human label)
HOTKEYS: dict[int, tuple[str, int, int, str]] = {
    1: ("toggle_pause", MOD_CONTROL | MOD_ALT, ord("P"), "Ctrl+Alt+P"),
    2: ("regenerate", MOD_CONTROL | MOD_ALT, ord("R"), "Ctrl+Alt+R"),
    3: ("toggle_window", MOD_CONTROL | MOD_ALT, ord("H"), "Ctrl+Alt+H"),
    4: ("copy_latest", MOD_CONTROL | MOD_ALT, ord("C"), "Ctrl+Alt+C"),
}


class GlobalHotkeys(QObject, QAbstractNativeEventFilter):
    triggered = Signal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self._hwnd: int | None = None
        self._registered: list[int] = []
        self.failed: list[str] = []

    def register(self, hwnd: int) -> None:
        if sys.platform != "win32":
            return
        self.unregister()
        self._hwnd = hwnd
        user32 = ctypes.windll.user32
        for hotkey_id, (_action, mods, vk, label) in HOTKEYS.items():
            if user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, mods | MOD_NOREPEAT, vk):
                self._registered.append(hotkey_id)
            else:
                self.failed.append(label)  # combination already taken by another app
        if self.failed:
            log.warning("Hotkeys unavailable (in use by another app): %s", ", ".join(self.failed))

    def unregister(self) -> None:
        if sys.platform != "win32" or self._hwnd is None:
            return
        for hotkey_id in self._registered:
            ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(self._hwnd), hotkey_id)
        self._registered.clear()
        self.failed.clear()

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt override
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in HOTKEYS:
                self.triggered.emit(HOTKEYS[msg.wParam][0])
                return True, 0
        return False, 0
