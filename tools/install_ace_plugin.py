"""Install an official ACE base or Mark UE 5.6 plugin without replacing existing files.

The archive's own MIT notice is checked and retained. Local A2F model plugins
are a separate install; this script never enables a cloud provider or downloads models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def add_timing_diagnostic(plugin):
    """Add a read-only Development getter; do not change playback or inference."""
    header = plugin / "Source/ACERuntime/Public/ACEAudioCurveSourceComponent.h"
    original = header.read_bytes()
    text = original.decode("utf-8-sig")
    marker = "GetAnimationPlaybackTimeForDiagnostics"
    if marker not in text:
        needle = "\tvoid Stop();"
        if text.count(needle) != 1:
            raise ValueError("Unexpected ACE timing interface; no patch applied")
        replacement = needle + (
            "\n#if !UE_BUILD_SHIPPING\n"
            "\t// Read-only timing evidence for the single audible backend owner.\n"
            "\tfloat GetAnimationPlaybackTimeForDiagnostics() const { return CurrentPlaybackTime; }\n"
            "#endif"
        )
        header.write_text(text.replace(needle, replacement), encoding="utf-8", newline="\n")
    return {"file": str(header.relative_to(ROOT)), "scope": "read_only_development_timing_getter",
            "modified_sha256": hashlib.sha256(header.read_bytes()).hexdigest()}


def install(archive, plugin_name="NV_ACE_Reference"):
    plugins = ROOT / "desktop/Decanat/Plugins"
    if plugin_name not in {"NV_ACE_Reference", "NvAudio2FaceMark"}:
        raise ValueError("Unsupported plugin")
    target = plugins / plugin_name
    if target.exists():
        raise ValueError("ACE plugin already exists; inspect it before any replacement")
    with archive.open("rb") as stream:
        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    with zipfile.ZipFile(archive) as source:
        descriptor = json.loads(source.read(f"{plugin_name}/{plugin_name}.uplugin"))
        if not descriptor["EngineVersion"].startswith("5.6."):
            raise ValueError("The archive is not the UE 5.6 plugin")
        license_text = source.read(f"{plugin_name}/LICENSE (Plugin).txt").decode("utf-8-sig")
        if "SPDX-License-Identifier: MIT" not in license_text:
            raise ValueError("Unexpected plugin license; inspect the actual archive")
        size = sum(item.file_size for item in source.infolist())
        if shutil.disk_usage(ROOT).free < size + 1024**3:
            raise ValueError("Insufficient workspace space for the plugin")
        staging = ROOT / ".tools" / f"ace-stage-{uuid.uuid4().hex}"
        # Validate every destination before creating or extracting any entry.
        for item in source.infolist():
            relative = PurePosixPath(item.filename.replace("\\", "/"))
            if relative.is_absolute() or not relative.parts or relative.parts[0] != plugin_name:
                raise ValueError("Unexpected plugin archive root")
            if ".." in relative.parts or ":" in str(relative) or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Unsafe plugin archive entry")
            if not (staging / str(relative)).resolve().is_relative_to(staging.resolve()):
                raise ValueError("Plugin archive path escapes staging")
        staging.mkdir(parents=True)
        source.extractall(staging)  # ZipFile verifies each extracted member's CRC.
    plugins.mkdir(exist_ok=True)
    extracted = (staging / plugin_name).resolve()
    destination = target.resolve()
    if not extracted.is_relative_to(ROOT.resolve()) or not destination.is_relative_to(ROOT.resolve()):
        raise ValueError("The verified move must remain within the project workspace")
    extracted.rename(destination)  # Same-volume rename; no recursive replacement.
    staging.rmdir()
    timing_patch = add_timing_diagnostic(destination) if plugin_name == "NV_ACE_Reference" else None
    report = {
        "installed_at_utc": datetime.now(UTC).isoformat(), "archive": str(archive),
        "archive_sha256": sha256, "plugin": plugin_name, "version": descriptor["VersionName"],
        "engine_version": descriptor["EngineVersion"], "uncompressed_bytes": size,
        "license": "MIT for plugin source; third-party components/models have separate notices",
        "license_retained": True, "source": "User download from the official authenticated NVIDIA URL",
        "build_verified": False, "local_model_installed": plugin_name == "NvAudio2FaceMark", "face_sync_verified": False,
        "model_redistribution_cleared": False,
        "bundled_model_license": "NVIDIA Models Community License (2025.04.15); development install, production qualification remains open",
        "development_timing_patch": timing_patch,
    }
    report_name = "ace-model-install.json" if plugin_name == "NvAudio2FaceMark" else "ace-plugin-install.json"
    output = ROOT / ".runtime/unreal" / report_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Installed {descriptor['VersionName']} at {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--plugin", choices=("NV_ACE_Reference", "NvAudio2FaceMark"), default="NV_ACE_Reference")
    args = parser.parse_args()
    install(args.archive.resolve(strict=True), args.plugin)


if __name__ == "__main__":
    main()
