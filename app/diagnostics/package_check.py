"""Offline checks usable from either Python or the Windows executable."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys

from app import APP_VERSION
from app.core.paths import (
    application_directory, is_packaged, models_directory, resource_directory,
    writable_directory,
)


MODEL_FILES = {
    "speech": (
        "speech/faster-whisper-small/config.json",
        "speech/faster-whisper-small/model.bin",
        "speech/faster-whisper-small/tokenizer.json",
        "speech/faster-whisper-small/vocabulary.txt",
    ),
    "translation_en_fr": tuple(
        f"translation/opus-mt-en-fr-ct2-int8/{name}"
        for name in ("config.json", "model.bin", "source.spm", "target.spm", "shared_vocabulary.json")
    ),
    "translation_fr_en": tuple(
        f"translation/opus-mt-fr-en-ct2-int8/{name}"
        for name in ("config.json", "model.bin", "source.spm", "target.spm", "shared_vocabulary.json")
    ),
    "speakers": ("speakers/nemo_en_titanet_small.onnx",),
}


def model_manifest(root: Path) -> dict:
    """Build a fingerprint of the known runtime files, excluding user data."""
    files = []
    for component, names in MODEL_FILES.items():
        for name in names:
            source = root / name
            if not source.is_file():
                raise FileNotFoundError(f"Cannot package missing model file: {source}")
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({
                "path": name, "component": component, "size": source.stat().st_size,
                "sha256": digest, "required": component != "speakers",
            })
    return {"format": 1, "app_version": APP_VERSION, "files": files}


def check_models(root: Path, manifest: dict | None = None) -> list[dict]:
    checks = []
    for component, names in MODEL_FILES.items():
        for name in names:
            path = root / name
            present = path.is_file() and path.stat().st_size > 0
            checks.append({"path": name, "required": component != "speakers", "ok": present})
    if manifest is not None:
        by_name = {entry["path"]: entry for entry in manifest["files"]}
        for check in checks:
            expected = by_name.get(check["path"])
            if expected is None:
                check["ok"] = False
                check["error"] = "No fingerprint in the build manifest"
                continue
            if check["ok"]:
                path = root / check["path"]
                if path.stat().st_size != expected["size"]:
                    check["ok"] = False
                else:
                    with path.open("rb") as stream:
                        check["ok"] = (
                            hashlib.file_digest(stream, "sha256").hexdigest() == expected["sha256"]
                        )
                if not check["ok"]:
                    check["error"] = "File does not match the build fingerprint"
    return checks


def run_package_check(*, load_models: bool = False, verify_models: bool = False) -> dict:
    report = {
        "app_version": APP_VERSION, "python": sys.version, "platform": platform.platform(),
        "packaged": is_packaged(), "application_directory": str(application_directory()),
        "models_directory": str(models_directory()), "writable_directory": str(writable_directory()),
        "imports": {}, "model_loads": {}, "errors": [],
    }
    for module in (
        "PySide6.QtWidgets", "PySide6.QtMultimedia", "sounddevice", "soundcard",
        "numpy", "soxr", "av", "torch", "silero_vad", "onnxruntime",
        "ctranslate2._ext", "sentencepiece", "sherpa_onnx",
    ):
        try:
            importlib.import_module(module)
            report["imports"][module] = "ok"
        except Exception as error:
            report["imports"][module] = str(error)
            report["errors"].append(f"{module}: {error}")

    manifest = None
    try:
        if verify_models:
            path = resource_directory() / "app_resources" / "model-manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
        report["models"] = check_models(models_directory(), manifest)
        if any(not item["ok"] and item["required"] for item in report["models"]):
            report["errors"].append("Required model files are missing or invalid")
    except Exception as error:
        report["errors"].append(f"Model verification: {error}")

    try:
        from app.history.database import HistoryDatabase

        database = HistoryDatabase(":memory:")
        try:
            report["sqlite"] = list(database.integrity_check())
            if report["sqlite"] != ["ok"]:
                report["errors"].append("SQLite check failed")
        finally:
            database.close()
    except Exception as error:
        report["errors"].append(f"SQLite: {error}")

    if load_models and not report["errors"]:
        from app.speech.vad import create_silero_detector
        from app.speech.recognition import create_faster_whisper_model
        from app.translation.translator import create_translation_model
        from app.speakers.identification import SherpaSpeakerEmbeddingModel
        import numpy as np

        def load_vad():
            create_silero_detector()(np.zeros(512, dtype=np.float32))

        def load_whisper():
            model = create_faster_whisper_model()
            segments, _info = model.transcribe(np.zeros(16_000, dtype=np.float32), beam_size=1)
            list(segments)

        def load_translation(source, target, text):
            if not create_translation_model(source, target).translate(text):
                raise RuntimeError("Empty translation")

        loaders = {
            "vad": load_vad, "whisper": load_whisper,
            "en_fr": lambda: load_translation("en", "fr", "Hello."),
            "fr_en": lambda: load_translation("fr", "en", "Bonjour."),
        }
        if all(item["ok"] for item in report["models"] if not item["required"]):
            loaders["speakers"] = SherpaSpeakerEmbeddingModel
        for name, loader in loaders.items():
            try:
                loader()
                report["model_loads"][name] = "ok"
            except Exception as error:
                report["model_loads"][name] = str(error)
                report["errors"].append(f"{name}: {error}")
    report["ok"] = not report["errors"]
    return report


def write_report(report: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
