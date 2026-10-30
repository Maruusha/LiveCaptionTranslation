"""Build the Windows folder distribution using this project's .venv."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import APP_VERSION
from app.diagnostics.package_check import model_manifest, write_report


def copy_models(destination: Path, manifest: dict) -> None:
    for item in manifest["files"]:
        target = destination / "models" / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "models" / item["path"], target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    model_options = parser.add_mutually_exclusive_group()
    model_options.add_argument("--include-models", action="store_true",
                               help="Copy runtime models after building the executable")
    model_options.add_argument("--stage-models", action="store_true",
                               help="Add runtime models to the existing matching build")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The Windows package must be built on Windows.")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        parser.error("Run this with .venv\\Scripts\\python.exe.")
    if version("pyinstaller") != "6.22.3":
        parser.error("Install the versions in requirements-build.txt first.")

    manifest = model_manifest(ROOT / "models")
    model_bytes = sum(item["size"] for item in manifest["files"])
    destination = ROOT / "dist" / "LiveCaptionTranslation"
    estimate = (
        model_bytes + 64 * 1024**2 if args.stage_models
        else 1536 * 1024**2 + (model_bytes if args.include_models else 0)
    )
    if shutil.disk_usage(ROOT).free < estimate:
        parser.error(f"At least {estimate / 1024**3:.1f} GiB free is needed for this build.")
    if args.stage_models:
        built_manifest = destination / "_internal" / "app_resources" / "model-manifest.json"
        if not (destination / "LiveCaptionTranslation.exe").is_file() or not built_manifest.is_file():
            parser.error("Build the executable before staging models.")
        if json.loads(built_manifest.read_text(encoding="utf-8")) != manifest:
            parser.error("The existing build fingerprints differ; rebuild before staging models.")
        copy_models(destination, manifest)
        metadata_path = destination / "BUILDINFO.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["models_included"] = True
        write_report(metadata, metadata_path)
        shutil.copy2(ROOT / "packaging" / "README.md", destination / "README.md")
        print(f"Models staged: {destination / 'models'}")
        return 0
    resources = ROOT / "build" / "package_resources"
    write_report(manifest, resources / "model-manifest.json")
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / ".cache" / "pyinstaller")
    result = subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
        str(ROOT / "packaging" / "LiveCaptionTranslation.spec"),
    ], cwd=ROOT, env=environment, check=False)
    if result.returncode:
        return result.returncode
    if args.include_models:
        copy_models(destination, manifest)
    shutil.copy2(ROOT / "packaging" / "README.md", destination / "README.md")
    write_report({
        "app_version": APP_VERSION, "built_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "pyinstaller": version("pyinstaller"),
        "models_included": args.include_models,
        "runtime_versions": {
            name: version(name) for name in (
                "PySide6", "torch", "numpy", "faster-whisper", "ctranslate2",
                "silero-vad", "sherpa-onnx", "SoundCard", "sounddevice",
            )
        },
    }, destination / "BUILDINFO.json")
    print(f"Built: {destination / 'LiveCaptionTranslation.exe'}")
    print("Distribute the whole folder, including _internal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
