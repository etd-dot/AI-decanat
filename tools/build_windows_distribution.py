"""Build an application-local Windows runtime from the verified desktop and locked dependencies.

The development bundle is separate from the project and never includes account
files, runtime settings, conversation journals or developer tools.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dependency_closure():
    pending = deque([("ai-decanat", {"speech", "models"})])
    contexts = {}
    distributions = {}
    while pending:
        name, extras = pending.popleft()
        distribution = importlib.metadata.distribution(name)
        key = distribution.metadata["Name"].lower().replace("_", "-")
        previous = contexts.setdefault(key, set())
        unseen = ({""} | set(extras)) - previous
        if not unseen:
            continue
        previous.update(unseen)
        distributions[key] = distribution
        for raw in distribution.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or any(
                requirement.marker.evaluate({**default_environment(), "extra": extra})
                for extra in unseen
            ):
                pending.append((requirement.name, requirement.extras))
    return distributions


def copy_dependencies(destination: Path):
    source = Path(sys.prefix) / "Lib/site-packages"
    versions = {}
    for name, distribution in sorted(dependency_closure().items()):
        versions[name] = distribution.version
        if name == "ai-decanat":
            continue  # Editable checkout references must never enter the bundle.
        for item in distribution.files or []:
            path = Path(distribution.locate_file(item)).resolve()
            if not path.is_relative_to(source.resolve()) or not path.is_file():
                continue  # Entry point scripts outside site-packages are not needed.
            if path.suffix == ".pyc" or path.name == "direct_url.json":
                continue
            target = destination / path.relative_to(source.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    shutil.copytree(ROOT / "backend/src/decanat", destination / "decanat",
                    ignore=shutil.ignore_patterns("__pycache__"))
    metadata = destination / "ai_decanat-0.1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: ai-decanat\nVersion: 0.1.0\n", encoding="utf-8")
    return versions


def copy_models(destination: Path):
    sources = json.loads((ROOT / "tools/model-sources.json").read_text(encoding="utf-8"))
    expected = {
        "ggml-small.bin": sources["whisper"]["sha256"]["small"],
        "silero_v5_ru.pt": sources["tts"]["sha256"],
        "silero_vad.onnx": sources["vad"]["sha256"],
        **{"bge-m3/" + name: digest for name, digest in sources["embedding"]["files"].items()},
    }
    for name, digest in expected.items():
        source = ROOT / "models" / name
        if sha(source) != digest:
            raise ValueError(f"Model differs from its pinned source: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return sources["embedding"]["revision"]


def copy_avatar(source: Path, destination: Path):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        "*.pdb", "*.debug", "*.sym", "Saved", "Intermediate", "DerivedDataCache"))


def build(args):
    if os.name != "nt":
        raise ValueError("Build this distribution on Windows x64")
    evidence = json.loads((ROOT / "assets/manifest/windows-runtime-checks.json").read_text(encoding="utf-8"))
    if not args.development:
        raise ValueError("Production release qualification is incomplete; only --development can be built")
    source_avatar = ROOT / ".runtime/unreal/package-with-ace/Windows"
    source_exe = source_avatar / "Decanat/Binaries/Win64/Decanat.exe"
    if sha(source_exe) != evidence["executable_sha256"] or not evidence["face_sync_verified"]:
        raise ValueError("Use the exact Windows/ACE build that passed the recorded runtime checks")
    if args.output.exists():
        raise ValueError("Output exists; choose a new directory instead of replacing a build")
    if not args.crt_dir.is_dir():
        raise ValueError("Provide the verified Visual C++ x64 runtime directory")
    codex_record = json.loads((ROOT / ".runtime/codex-runtime-install.json").read_text(encoding="utf-8"))
    codex_source = ROOT / ".tools" / f"codex-{codex_record['version']}" / "package"
    codex_exe = ROOT / codex_record["executable"]
    codex_relative = "codex/" + codex_exe.relative_to(codex_source).as_posix()
    if not codex_exe.is_file() or not (codex_source / "LICENSE").is_file():
        raise ValueError("Install the pinned standalone Codex package with its source license notice")
    output = args.output.resolve()
    output.mkdir(parents=True)
    print("Copying the application-local Python runtime and locked runtime dependencies", flush=True)
    shutil.copytree(Path(sys.base_prefix), output / "runtime",
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "Scripts", "include", "libs"))
    versions = copy_dependencies(output / "runtime/Lib/site-packages")
    for path in args.crt_dir.glob("*.dll"):
        shutil.copy2(path, output / "runtime" / path.name)
    (output / "native").mkdir()
    shutil.copy2(ROOT / ".tools/native/bin/decanat_audio.dll", output / "native/decanat_audio.dll")
    shutil.copytree(codex_source, output / "codex")
    (output / "tools").mkdir()
    for name in ("installed_desktop.py", "launch.py", "install_models.py", "model-sources.json"):
        shutil.copy2(ROOT / "tools" / name, output / "tools" / name)
    shutil.copytree(ROOT / "examples/knowledge", output / "examples/knowledge")
    print("Verifying and copying pinned local speech/search models", flush=True)
    embedding_revision = copy_models(output / "models")
    print("Copying the verified Unreal desktop without debug symbols or authoring tools", flush=True)
    copy_avatar(source_avatar, output / "Avatar")
    notices = output / "notices"
    notices.mkdir()
    for name in ("third-party.md", "avatar-authoring.md"):
        shutil.copy2(ROOT / "docs" / name, notices / name)
    shutil.copy2(ROOT / ".tools/inno-7.1.1/License.txt", notices / "Inno-Setup-License.txt")
    for name in ("COPYING", "COPYRIGHT"):
        source = ROOT / "native/audio/subprojects/webrtc-audio-processing-1.3" / name
        if source.exists():
            shutil.copy2(source, notices / ("WebRTC-" + name))
    shutil.copy2(ROOT / "native/audio/subprojects/abseil-cpp-20230125.1/LICENSE", notices / "Abseil-LICENSE")
    for plugin in ("NV_ACE_Reference", "NvAudio2FaceMark"):
        directory = ROOT / "desktop/Decanat/Plugins" / plugin
        for source in directory.rglob("*"):
            if not source.is_file() or not any(word in source.name.upper() for word in ("LICENSE", "COPYING", "NOTICE")):
                continue
            target = notices / plugin / source.relative_to(directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (output / "DEVELOPMENT-PREVIEW.txt").write_text(
        "ИИ деканата — демонстрационная версия\n\n"
        "В комплекте вымышленная база: справки и часы приёма. Это не правила реального вуза.\n"
        "Для ответов требуется вход сотрудника в Codex через ChatGPT. API-fallback выключен.\n"
        "Настройки, база и журнал хранятся отдельно в профиле пользователя и сохраняются при удалении программы.\n"
        "Право production-поставки старых моделей NVIDIA и окончательная приёмка ещё не установлены.\n",
        encoding="utf-8-sig")
    build_dir = Path(tempfile.mkdtemp(prefix="windows-launcher-", dir=ROOT / ".runtime"))
    subprocess.run([
        args.cxx, "/nologo", "/std:c++17", "/EHsc", "/MT", "/O2", "/utf-8",
        str(ROOT / "installer/windows/launcher.cpp"), f"/Fo{build_dir / 'launcher.obj'}",
        f"/Fe{output / 'Decanat.exe'}", "/link", "/SUBSYSTEM:WINDOWS",
        "user32.lib", "shell32.lib", "ole32.lib",
    ], check=True, cwd=build_dir)
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(), "development_only": True,
        "production_qualification_complete": False, "python": sys.version.split()[0],
        "dependencies": versions, "codex_executable": codex_relative, "codex_source": codex_record,
        "embedding_revision": embedding_revision, "avatar_executable_sha256": sha(source_exe),
        "runtime_check_manifest_sha256": sha(ROOT / "assets/manifest/windows-runtime-checks.json"),
        "account_data_copied": False, "production_settings_copied": False,
        "development_tools_required_on_target": False,
    }
    (output / "distribution.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Self-contained development payload: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--crt-dir", type=Path, required=True)
    parser.add_argument("--cxx", default="cl")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
