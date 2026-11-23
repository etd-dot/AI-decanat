"""Fetch the pinned standalone Windows Codex package; never copy account data."""

from __future__ import annotations

import base64
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.166.0"
INTEGRITY = "NS8Ps5oG5r19pCrMCUXwUqOui6odnUIoBlQkrjCJ+h1k6KiwCzC/+mI9d3nSZCqFoVONPBTmqyq8NYq3YaFGyA=="
URL = f"https://registry.npmjs.org/@openai/codex/-/codex-{VERSION}-win32-x64.tgz"
LICENSE_URL = f"https://raw.githubusercontent.com/openai/codex/rust-v{VERSION}/LICENSE"
LICENSE_SHA256 = "d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc"


def install():
    archive = ROOT / ".cache" / f"codex-{VERSION}-win32-x64.tgz"
    target = ROOT / ".tools" / f"codex-{VERSION}"
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        temporary = archive.with_suffix(".partial")
        with urllib.request.urlopen(URL, timeout=120) as source, temporary.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(archive)
    with archive.open("rb") as stream:
        actual = base64.b64encode(hashlib.file_digest(stream, "sha512").digest()).decode()
    if actual != INTEGRITY:
        raise ValueError("Standalone Codex package integrity mismatch")
    if target.exists():
        raise ValueError(f"Runtime exists; inspect it rather than replacing: {target}")
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or ":" in member.name or path.parts[0] != "package":
                raise ValueError("Unsafe package path")
            if not member.isfile() and not member.isdir():
                raise ValueError("Package links/special files are not allowed")
        source.extractall(target, filter="data")
    executables = list(target.rglob("codex.exe"))
    if len(executables) != 1:
        raise ValueError("Expected exactly one standalone Codex executable")
    descriptor = json.loads((target / "package/package.json").read_text(encoding="utf-8"))
    if descriptor["version"] != f"{VERSION}-win32-x64" or descriptor["license"] != "Apache-2.0":
        raise ValueError("Unexpected standalone package identity/license")
    with urllib.request.urlopen(LICENSE_URL, timeout=30) as source:
        license_text = source.read()
    if hashlib.sha256(license_text).hexdigest() != LICENSE_SHA256:
        raise ValueError("Codex source license differs from the pinned notice")
    (target / "package/LICENSE").write_bytes(license_text)
    report = {
        "version": VERSION, "url": URL, "integrity": "sha512-" + INTEGRITY,
        "executable": str(executables[0].relative_to(ROOT)).replace("\\", "/"),
        "license": "Apache-2.0", "account_data_copied": False,
        "license_source": LICENSE_URL, "license_sha256": LICENSE_SHA256,
    }
    output = ROOT / ".runtime/codex-runtime-install.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    install()
