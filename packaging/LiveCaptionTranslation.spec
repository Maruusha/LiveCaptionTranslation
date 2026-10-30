"""Windows folder build; external models and private data are never frozen in."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "build" / "package_resources" / "model-manifest.json"), "app_resources")]
datas += collect_data_files("soundcard", includes=["*.h"])
datas += collect_data_files("silero_vad", includes=["data/*.onnx"])
binaries = []
for package in ("ctranslate2", "sherpa_onnx", "onnxruntime"):
    binaries += collect_dynamic_libs(package)
for distribution in (
    "faster-whisper", "ctranslate2", "silero-vad", "torch", "sherpa-onnx",
    "sounddevice", "SoundCard", "sentencepiece",
):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(root / "app" / "main.py")],
    pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=["silero_vad.data", "_sounddevice_data", "ctranslate2._ext"],
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "tkinter", "pytest", "IPython",
        "matplotlib", "pandas", "tensorflow", "torchaudio", "torchvision", "tensorboard",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="LiveCaptionTranslation",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LiveCaptionTranslation")
