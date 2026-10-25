"""Application entry point."""

from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

# Editors often run this file directly instead of using ``python -m app.main``.
# In that mode, Python only adds app/ to the import path, so add the project
# root before importing the app package.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_NAME, APP_VERSION
from app.core.log_setup import configure_logging
from app.ui.main_window import MainWindow


logger = logging.getLogger(__name__)
_application_instance: QApplication | None = None


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Create the Qt application, or return the existing test instance."""

    global _application_instance
    existing_application = QApplication.instance()
    if existing_application is not None:
        _application_instance = existing_application
        return existing_application

    application = QApplication(list(arguments) if arguments is not None else sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName("LiveCaptionTranslation")
    _application_instance = application
    return application


def main(arguments: Sequence[str] | None = None) -> int:
    """Open the main window and run the desktop application."""
    log_file = configure_logging()
    logger.info("Application starting; log file: %s", log_file)
    application = create_application(arguments)
    try:
        window = MainWindow()
    except sqlite3.DatabaseError as error:
        logger.exception("History database could not be opened")
        QMessageBox.critical(
            None,
            "History database could not be opened",
            "The local history database is damaged or incompatible. It was "
            "not replaced. Restore a known-good backup or move the original "
            f"database aside before restarting.\n\n{error}",
        )
        return 1
    window.show()
    exit_code = application.exec()
    logger.info("Application stopped with exit code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

