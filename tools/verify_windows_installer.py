"""Install/check/uninstall our development bundle in a new workspace directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/windows-installer-verification.json")
    args = parser.parse_args()
    installer = args.installer.resolve(strict=True)
    workspace = (ROOT / ".runtime").resolve()
    evidence = Path(tempfile.mkdtemp(prefix="f04-install-", dir=workspace))
    target = (evidence / "Пробная установка с пробелами").resolve()
    data = evidence / "профиль"
    if not target.is_relative_to(workspace) or target.exists():
        raise ValueError("The installation test must use a new directory inside the workspace")
    if shutil.disk_usage(workspace).free < 16 * 1024**3:
        raise ValueError("At least 16 GiB free is required for the temporary installation")
    data.mkdir()
    marker = data / "preserve-on-uninstall.txt"
    marker.write_text("User data belongs to the user, not the uninstaller.\n", encoding="utf-8")
    report = {"started_at_utc": datetime.now(UTC).isoformat(), "passed": False,
              "installer": str(installer), "test_directory": str(evidence),
              "clean_windows_verified": False, "microphone_opened": False}
    uninstaller = target / "unins000.exe"
    try:
        subprocess.run([
            str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/DIR={target}", f"/LOG={evidence / 'install.log'}",
        ], check=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
        report["installation_verified"] = True
        if not (target / "Decanat.exe").is_file() or not uninstaller.is_file():
            raise ValueError("Installer did not create the application and uninstaller")
        process = subprocess.Popen([
            str(target / "Decanat.exe"), "--check", "--data-dir", str(data),
        ], cwd=evidence, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            code = process.wait(timeout=240)
        except subprocess.TimeoutExpired:
            process.kill()  # Its Windows job closes and cleans up descendants.
            process.wait()
            raise
        report["launcher_exit_code"] = code
        log = Path(os.environ["LOCALAPPDATA"]) / "AI-decanat-development" / f"launcher-{process.pid}.log"
        if code != 0 or not log.is_file():
            raise ValueError("The installed native launcher failed its offline check")
        last_line = log.read_bytes().splitlines()[-1]
        try:
            summary = json.loads(last_line.decode("utf-8"))
        except UnicodeDecodeError:
            # Windows redirected stdio can use ACP when the isolated embedded
            # interpreter ignores PYTHONIOENCODING. Decode only the JSON line.
            summary = json.loads(last_line.decode("mbcs"))
        if not summary["passed"] or not Path(summary["package_file"]).is_relative_to(target / "runtime"):
            raise ValueError("Installed runtime did not use its own packaged code")
        report.update(installed_runtime_verified=True, runtime_checks=summary)
        subprocess.run([
            str(target / "runtime/python.exe"), "-I", "-B", "-c",
            "import sounddevice; assert sounddevice.query_hostapis()",
        ], check=True, timeout=20, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        report["portaudio_loaded_without_capture"] = True
        print("Installed native runtime passed in a path with spaces and Cyrillic characters", flush=True)
    except Exception as error:
        report["issue"] = str(error)
        raise
    finally:
        try:
            user_files = {path.relative_to(data): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in data.rglob("*") if path.is_file()}
            if uninstaller.exists():
                subprocess.run([
                    str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={evidence / 'uninstall.log'}",
                ], check=True, timeout=240, creationflags=subprocess.CREATE_NO_WINDOW)
                deadline = time.monotonic() + 30
                while (target / "Decanat.exe").exists() and time.monotonic() < deadline:
                    time.sleep(.2)
            report["uninstallation_verified"] = not (target / "Decanat.exe").exists()
            report["user_data_preserved"] = bool(marker.is_file() and (data / "settings.json").is_file()
                and all((data / name).is_file() and hashlib.sha256((data / name).read_bytes()).hexdigest() == digest
                        for name, digest in user_files.items()))
            report["preserved_user_file_count"] = len(user_files)
            report["passed"] = bool(report.get("installed_runtime_verified")
                                    and report["uninstallation_verified"] and report["user_data_preserved"])
        except Exception as error:
            report["cleanup_issue"] = str(error)
            report["passed"] = False
            raise
        finally:
            report["finished_at_utc"] = datetime.now(UTC).isoformat()
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["passed"]:
        raise ValueError("Installer lifecycle verification did not pass")


if __name__ == "__main__":
    main()
