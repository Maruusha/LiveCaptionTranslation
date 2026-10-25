import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LIVE_CAPTION_HISTORY_PATH", ":memory:")
os.environ.setdefault("LIVE_CAPTION_SETTINGS_PATH", ":memory:")


@pytest.fixture(scope="session", autouse=True)
def clean_up_qt_objects_after_tests():
    """Destroy Qt windows before native audio libraries unload at exit."""

    yield

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is None:
        return
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    application.quit()

