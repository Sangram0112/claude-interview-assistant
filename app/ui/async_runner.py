"""Run asyncio coroutines on a background loop and deliver results on the Qt GUI thread."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger(__name__)


class _GuiInvoker(QObject):
    """Emitting from any thread queues the call onto the thread that owns this object."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("GUI callback failed")


class AsyncRunner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="asyncio-loop", daemon=True)
        self._thread.start()
        self._invoker = _GuiInvoker()

    def on_gui(self, fn: Callable[[], None]) -> None:
        """Schedule ``fn`` on the GUI thread (safe from any thread)."""
        self._invoker.call.emit(fn)

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> Future:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def done(f: Future) -> None:
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                if on_error:
                    self.on_gui(lambda: on_error(exc))
                else:
                    log.error("Background task failed: %s", type(exc).__name__)
            elif on_success:
                result = f.result()
                self.on_gui(lambda: on_success(result))

        future.add_done_callback(done)
        return future

    def run_in_thread(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        name: str = "worker",
    ) -> None:
        """Run a blocking function on a plain worker thread (e.g. loading a speech model)."""

        def target() -> None:
            try:
                result = fn()
            except BaseException as exc:  # noqa: BLE001
                if on_error:
                    # Bind now: `exc` is unset when the except block exits.
                    self.on_gui(lambda e=exc: on_error(e))
                else:
                    log.exception("Worker %s failed", name)
                return
            if on_success:
                self.on_gui(lambda: on_success(result))

        threading.Thread(target=target, name=name, daemon=True).start()

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)
