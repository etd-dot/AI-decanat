import hashlib
import json
from types import SimpleNamespace

import pytest
from decanat.config import Settings, SettingsStore
from decanat.security import AdminAuth

from tools import build_windows_distribution, installed_desktop


def test_new_avatar_package_requires_matching_verified_ace_report(tmp_path):
    game = tmp_path / "Decanat.exe"
    game.write_bytes(b"test fixture")
    report_path = tmp_path / "verification.json"
    executable_sha = hashlib.sha256(game.read_bytes()).hexdigest()
    report = {
        "executable_sha256": executable_sha,
        "passed": True,
        "avatar_configured": True,
        "native_face_handshake_verified": True,
        "ace_local_inference_and_curves_verified": True,
        "preparation_cancellation_verified": True,
        "output_dac_and_face_clock_verified": True,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert build_windows_distribution.verify_avatar_release(
        game, report_path, new_report=True
    ) == executable_sha
    for change in (
        {"passed": False},
        {"native_face_handshake_verified": False},
        {"executable_sha256": "different executable"},
    ):
        report_path.write_text(json.dumps({**report, **change}), encoding="utf-8")
        with pytest.raises(ValueError, match="exact Windows/ACE build"):
            build_windows_distribution.verify_avatar_release(game, report_path, new_report=True)


def test_runtime_dependency_selection_preserves_nested_extras_and_platform_markers(monkeypatch):
    requirements = {
        "ai-decanat": ["uvicorn[standard]", "speechlib; extra == 'speech'", "pytest; extra == 'dev'"],
        "uvicorn": ["websockets; extra == 'standard'"],
        "websockets": ["winlib; sys_platform == 'win32'"],
        "speechlib": [], "winlib": [],
    }
    monkeypatch.setattr(build_windows_distribution.importlib.metadata, "distribution",
                        lambda name: SimpleNamespace(metadata={"Name": name}, requires=requirements[name]))
    environment = build_windows_distribution.default_environment()
    monkeypatch.setattr(build_windows_distribution, "default_environment",
                        lambda: {**environment, "sys_platform": "win32"})
    assert set(build_windows_distribution.dependency_closure()) == {
        "ai-decanat", "uvicorn", "websockets", "speechlib", "winlib",
    }


def test_installed_startup_keeps_existing_staff_password_settings_and_knowledge(kb):
    settings = SettingsStore(kb.root)
    settings.save(Settings(provider="api", api_model="staff-selected-model"))
    auth = AdminAuth(kb.root)
    auth.set_password("existing-staff-password")
    paths = [settings.path, auth.path, kb.path]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result = installed_desktop.initialize(kb.root)
    assert result.value.api_model == "staff-selected-model"
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == before
    assert not (kb.root / "initial-password.txt").exists()


def test_first_installed_startup_rejects_corrupt_model_before_creating_profile(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    (payload / "tools").mkdir(parents=True)
    (payload / "models").mkdir()
    sources = {
        "whisper": {"sha256": {"small": "0" * 64}}, "tts": {"sha256": "0" * 64},
        "vad": {"sha256": "0" * 64}, "embedding": {"files": {}},
    }
    (payload / "tools/model-sources.json").write_text(json.dumps(sources), encoding="utf-8")
    (payload / "models/ggml-small.bin").write_bytes(b"damaged")
    monkeypatch.setattr(installed_desktop, "ROOT", payload)
    data = tmp_path / "profile"
    with pytest.raises(ValueError, match="ggml-small.bin"):
        installed_desktop.initialize(data)
    assert not any((data / name).exists() for name in ("settings.json", "admin.hash", "knowledge.sqlite3"))


def test_first_installed_startup_creates_only_settings(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "distribution.json").write_text(
        json.dumps({"codex_executable": "codex/codex.exe", "embedding_revision": "local"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(installed_desktop, "ROOT", payload)
    monkeypatch.setattr(installed_desktop, "verify_models", lambda: None)
    profile = tmp_path / "profile"
    assert installed_desktop.initialize(profile).value.speech_enabled
    assert {path.name for path in profile.iterdir()} == {"settings.json", "runtime.lock"}


def test_avatar_distribution_excludes_saved_settings_logs_and_build_cache(tmp_path):
    source = tmp_path / "source"
    public = source / "Decanat/Content/Paks/Decanat.pak"
    public.parent.mkdir(parents=True)
    public.write_bytes(b"cooked public content")
    for name in ("Decanat/Saved/Config/settings.ini", "Engine/Saved/Logs/private.log",
                 "Decanat/Intermediate/build.cache", "Decanat/DerivedDataCache/shaders.cache",
                 "Decanat/Binaries/Win64/Decanat.pdb"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"must not enter distribution")
    destination = tmp_path / "payload"
    build_windows_distribution.copy_avatar(source, destination)
    assert [path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()] == [
        "Decanat/Content/Paks/Decanat.pak",
    ]
