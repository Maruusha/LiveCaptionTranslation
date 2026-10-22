import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LIVE_CAPTION_HISTORY_PATH", ":memory:")
os.environ.setdefault("LIVE_CAPTION_SETTINGS_PATH", ":memory:")

