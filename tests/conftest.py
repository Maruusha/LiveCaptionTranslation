import os
import gc
from time import monotonic, sleep

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LIVE_CAPTION_HISTORY_PATH", ":memory:")
os.environ.setdefault("LIVE_CAPTION_SETTINGS_PATH", ":memory:")


@pytest.fixture(autouse=True)
def clean_up_qt_objects_after_tests():
    """Destroy test windows on the GUI thread before another worker starts."""

    yield

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    application = QApplication.instance()
    if application is None:
        gc.collect()
        return
    widgets = application.topLevelWidgets()
    for widget in widgets:
        if not isValid(widget):
            continue
        widget.close()
    deadline = monotonic() + 3.0
    while any(
        isValid(widget) and getattr(widget, "_stop_thread", None) is not None
        for widget in widgets
    ):
        application.processEvents()
        if monotonic() >= deadline:
            pytest.fail("A test left an audio-stop worker running")
        sleep(0.01)
    for widget in widgets:
        if isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    gc.collect()

