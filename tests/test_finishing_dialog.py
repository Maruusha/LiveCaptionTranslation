import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.main import create_application
from app.ui.finishing_dialog import FinishingCaptionsDialog


def test_hide_progress_closes_view_without_requesting_skip() -> None:
    create_application(["live-caption-translation-test"])
    dialog = FinishingCaptionsDialog()
    skip_requests: list[bool] = []
    dialog.finish_now_requested.connect(lambda: skip_requests.append(True))
    dialog.show()

    dialog.hide_progress_button.click()

    assert not dialog.isVisible()
    assert skip_requests == []


def test_skip_remaining_still_emits_explicit_request() -> None:
    create_application(["live-caption-translation-test"])
    dialog = FinishingCaptionsDialog()
    skip_requests: list[bool] = []
    dialog.finish_now_requested.connect(lambda: skip_requests.append(True))

    dialog.finish_now_button.click()

    assert skip_requests == [True]
