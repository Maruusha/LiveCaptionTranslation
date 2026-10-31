"""Small local logging setup for development and troubleshooting."""

from __future__ import annotations

import logging
import faulthandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.paths import writable_directory


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class DiagnosticFileHandler(RotatingFileHandler):
    """Keep native crash tracing attached to the current rotating log file."""

    def enable_crash_tracing(self) -> None:
        faulthandler.enable(file=self.stream, all_threads=True)

    def doRollover(self) -> None:
        faulthandler.disable()
        super().doRollover()
        self.enable_crash_tracing()

    def close(self) -> None:
        faulthandler.disable()
        super().close()


def configure_logging() -> Path:
    """Log to the console when available and a bounded writable file."""
    log_directory = writable_directory() / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_file = log_directory / "application.log"

    root_logger = logging.getLogger()
    if getattr(root_logger, "_live_caption_configured", False):
        return log_file

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    import sys

    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    file_handler = DiagnosticFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.enable_crash_tracing()

    root_logger.addHandler(file_handler)
    root_logger._live_caption_configured = True
    return log_file

