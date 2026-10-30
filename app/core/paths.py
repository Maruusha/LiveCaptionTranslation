"""Separate packaged program resources, external models, and writable data."""

from __future__ import annotations

import os
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[2]


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_directory() -> Path:
    return Path(sys.executable).resolve().parent if is_packaged() else SOURCE_ROOT


def resource_directory() -> Path:
    return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))


def writable_directory() -> Path:
    override = os.environ.get("LIVE_CAPTION_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_packaged():
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "LiveCaptionTranslation"
    return SOURCE_ROOT


def models_directory() -> Path:
    override = os.environ.get("LIVE_CAPTION_MODELS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return application_directory() / "models"


def prepare_runtime_environment() -> None:
    """Keep caches writable and prevent accidental model network downloads."""
    cache = writable_directory() / ".cache"
    os.environ.setdefault("HF_HOME", str(cache / "huggingface"))
    os.environ.setdefault("TORCH_HOME", str(cache / "torch"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
