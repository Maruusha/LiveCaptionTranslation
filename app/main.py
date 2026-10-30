"""Application entry point."""

from __future__ import annotations

import logging
import argparse
import os
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
from app.core.paths import prepare_runtime_environment, writable_directory


logger = logging.getLogger(__name__)
_application_instance: QApplication | None = None


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Create the Qt application, or return the existing test instance."""

    global _application_instance
    existing_application = QApplication.instance()
    if existing_application is not None:
        existing_application.setQuitOnLastWindowClosed(False)
        _application_instance = existing_application
        return existing_application

    application = QApplication(list(arguments) if arguments is not None else sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName("LiveCaptionTranslation")
    # Only an accepted MainWindow close may end the process. Transient dialogs
    # closing during Stop must never make Qt quit the application.
    application.setQuitOnLastWindowClosed(False)
    _application_instance = application
    return application


def main(arguments: Sequence[str] | None = None) -> int:
    """Open the main window and run the desktop application."""
    argv = list(sys.argv if arguments is None else arguments)
    parser = argparse.ArgumentParser(description=APP_NAME)
    diagnostics = parser.add_mutually_exclusive_group()
    diagnostics.add_argument("--self-check", action="store_true")
    diagnostics.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--models", type=Path, help="Use an existing external models folder")
    parser.add_argument("--data-dir", type=Path, help="Override the writable data root")
    parser.add_argument("--report", type=Path, help="Diagnostic JSON output")
    parser.add_argument("--load-models", action="store_true", help="Self-check actual model loading")
    parser.add_argument("--verify-models", action="store_true", help="Check model SHA-256 fingerprints")
    options, qt_arguments = parser.parse_known_args(argv[1:])
    if options.models is not None:
        os.environ["LIVE_CAPTION_MODELS_PATH"] = str(options.models.resolve())
    if options.data_dir is not None:
        os.environ["LIVE_CAPTION_DATA_DIR"] = str(options.data_dir.resolve())
    prepare_runtime_environment()
    log_file = configure_logging()
    logger.info("Application starting; log file: %s", log_file)
    if options.self_check:
        from app.diagnostics.package_check import run_package_check, write_report

        report = run_package_check(load_models=options.load_models, verify_models=options.verify_models)
        destination = options.report or writable_directory() / "logs" / "package-check.json"
        write_report(report, destination)
        logger.info("Package self-check: passed=%s; report=%s", report["ok"], destination)
        return 0 if report["ok"] else 1

    application = create_application([argv[0] if argv else APP_NAME, *qt_arguments])
    from app.ui.main_window import MainWindow

    try:
        if options.smoke_test:
            from app.history.database import HistoryDatabase
            from app.ui.overlay_settings import MemorySettings
            window = MainWindow(history_database=HistoryDatabase(":memory:"),
                                interface_settings=MemorySettings())
        else:
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
    window.application_exit_requested.connect(application.quit)
    if options.smoke_test:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, window.close)
    exit_code = application.exec()
    if options.smoke_test and options.report:
        from app.diagnostics.package_check import write_report
        write_report({"ok": exit_code == 0, "gui": "opened and closed", "exit_code": exit_code},
                     options.report)
    logger.info("Application stopped with exit code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

