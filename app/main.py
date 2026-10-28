"""Application entry point: ``python -m app.main``."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from app import APP_NAME, __version__


def _load_dotenv() -> None:
    """Development convenience: read a .env next to the source tree (never bundled)."""
    if getattr(sys, "frozen", False):
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _load_dotenv()

    from app.config import Settings
    from app.logging_setup import configure_logging

    settings = Settings.load()
    debug = os.environ.get("CIA_DEBUG") == "1"
    configure_logging(logging.DEBUG if debug else logging.INFO, settings.privacy.log_transcripts)
    log = logging.getLogger("app")
    log.info("%s %s starting (Python %s)", APP_NAME, __version__, sys.version.split()[0])

    if "--self-test" in argv:
        from app.selftest import run_self_test

        return run_self_test()

    from PySide6.QtWidgets import QApplication

    from app.ai.client import ClaudeService
    from app.controller import SessionController
    from app.paths import database_path
    from app.security.secrets import SecretStore
    from app.storage.database import Database
    from app.ui.async_runner import AsyncRunner
    from app.ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    # Style before any widget exists so size hints use the final fonts.
    from app.ui.theme import stylesheet

    app.setStyleSheet(stylesheet(settings.ui.font_scale))

    db = Database(database_path())
    removed = db.purge_expired(settings.privacy.retention)
    if removed:
        log.info("Retention: removed %d expired transcript/answer rows", removed)

    secrets = SecretStore()
    runner = AsyncRunner()
    holder: dict = {}
    claude = ClaudeService(lambda: holder["controller"].settings.ai, secrets)
    controller = SessionController(settings, db, secrets, claude, runner)
    holder["controller"] = controller

    window = MainWindow(controller)
    window.show()
    code = app.exec()

    runner.shutdown()
    db.close()
    log.info("Exited with code %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
