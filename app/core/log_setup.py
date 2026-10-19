"""Small local logging setup for development and troubleshooting."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging() -> Path:
    """Log to the terminal and a bounded file inside the project."""
    project_root = Path(__file__).resolve().parents[2]
    log_directory = project_root / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_file = log_directory / "application.log"

    root_logger = logging.getLogger()
    if getattr(root_logger, "_live_caption_configured", False):
        return log_file

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger._live_caption_configured = True
    return log_file

