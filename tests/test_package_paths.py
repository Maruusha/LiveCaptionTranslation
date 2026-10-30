from app.core import paths
from app.diagnostics.package_check import MODEL_FILES, check_models, model_manifest


def test_packaged_paths_keep_user_data_outside_program_files(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "Program Files" / "LiveCaptionTranslation.exe"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("LIVE_CAPTION_DATA_DIR", raising=False)
    monkeypatch.delenv("LIVE_CAPTION_MODELS_PATH", raising=False)
    assert paths.application_directory() == executable.parent
    assert paths.models_directory() == executable.parent / "models"
    assert paths.writable_directory() == tmp_path / "LocalAppData" / "LiveCaptionTranslation"


def test_path_overrides_work_independently_of_current_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LIVE_CAPTION_MODELS_PATH", str(tmp_path / "models"))
    monkeypatch.setenv("LIVE_CAPTION_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.chdir(tmp_path)
    assert paths.models_directory() == tmp_path / "models"
    assert paths.writable_directory() == tmp_path / "user-data"


def test_model_fingerprint_detects_changed_files_with_the_same_size(tmp_path) -> None:
    for filenames in MODEL_FILES.values():
        for filename in filenames:
            path = tmp_path / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")
    manifest = model_manifest(tmp_path)
    assert all(item["ok"] for item in check_models(tmp_path, manifest))
    first = tmp_path / MODEL_FILES["speech"][0]
    first.write_bytes(b"other")
    failed = [item for item in check_models(tmp_path, manifest) if not item["ok"]]
    assert [item["path"] for item in failed] == [MODEL_FILES["speech"][0]]


def test_missing_optional_speaker_model_does_not_hide_required_model_status(tmp_path) -> None:
    checks = check_models(tmp_path)
    assert all(not item["ok"] for item in checks)
    assert sum(item["required"] for item in checks) == 14
