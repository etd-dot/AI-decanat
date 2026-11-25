"""Install/check/uninstall our development bundle in a new workspace directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running_process(command, cwd, *, env=None, log=None):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        yield process
    finally:
        if process.poll() is None:
            # Closing the native launcher's job also stops its desktop and backend.
            process.terminate()
        process.wait(timeout=15)


def request(connection, route, *, body=None, headers=None, method=None, timeout=20):
    token_headers = {"Authorization": "Bearer " + connection["token"], **(headers or {})}
    if body is not None:
        token_headers["Content-Type"] = "application/json"
    message = Request(connection["url"] + route, headers=token_headers, method=method,
                      data=json.dumps(body).encode("utf-8") if body is not None else None)
    with build_opener(ProxyHandler({})).open(message, timeout=timeout) as response:
        return json.load(response)


def wait_for_health(process, profile, pattern):
    deadline = time.monotonic() + 180
    while process.poll() is None and time.monotonic() < deadline:
        for filename in profile.glob(pattern):
            try:
                connection = json.loads(filename.read_text(encoding="utf-8"))
                address = urlsplit(connection["url"])
                if (address.scheme, address.hostname) != ("http", "127.0.0.1"):
                    raise ValueError("First-run verification must stay on loopback")
                return connection, request(connection, "/v1/health", timeout=1)
            except (OSError, json.JSONDecodeError):
                continue
        time.sleep(.25)
    raise ValueError(f"The installed backend did not become ready in {profile}")


def verify_first_launch(target, evidence, report, *, check_provider):
    profile = evidence / "Первый запуск"
    launcher = target / "Decanat.exe"
    with running_process([str(launcher), "--data-dir", str(profile)], evidence) as process:
        _, health = wait_for_health(process, profile, "desktop-*.json")
        report["microphone_opened"] = health["audio_device_ready"]
        if health["setup_step"] != "password" or not health["models_ready"]:
            raise ValueError("The new installed profile did not reach password setup with local models ready")
        desktop_log = profile / "desktop.log"
        deadline = time.monotonic() + 120
        while process.poll() is None and time.monotonic() < deadline:
            if desktop_log.is_file():
                text = desktop_log.read_text(encoding="utf-8", errors="replace")
                if ("Bringing World /Game/Decanat/Office.Office up for play" in text
                        and "Allocation of instance of LocalA2F-Mark complete" in text):
                    break
                if "Allocation of instance of LocalA2F-Mark failed" in text:
                    raise ValueError(f"The installed local facial model could not load: {desktop_log}")
            time.sleep(.5)
        else:
            raise ValueError(f"The installed Unreal scene did not finish startup: {desktop_log}")
        report["installed_desktop_first_launch_verified"] = True
        print("Installed desktop reached first-run setup and loaded the Unreal scene", flush=True)

    # Exercise the same installed setup endpoints with an ephemeral capability.
    # This profile and its test password are independent from the operator's account.
    for filename in profile.glob("desktop-*.json"):
        filename.unlink()
    verify_local_setup(target, profile, evidence, report, check_provider=check_provider)


def verify_local_setup(target, profile, evidence, report, *, check_provider):
    setup = secrets.token_urlsafe(32)
    password = {"password": "test-" + secrets.token_urlsafe(24)}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    connection_file = profile / "test-connection.json"
    environment = {**os.environ, "DECANAT_SETUP_TOKEN": setup, "HF_HUB_OFFLINE": "1",
                   "TRANSFORMERS_OFFLINE": "1"}
    command = [str(target / "runtime/python.exe"), "-I", "-B", "-X", "utf8", "-m", "decanat.cli",
               "--data-dir", str(profile), "serve", "--port", str(port), "--desktop-setup",
               "--connection-file", str(connection_file), "--demo-source", str(target / "examples/knowledge")]
    try:
        with (evidence / "first-setup.log").open("wb") as log:
            with running_process(command, evidence, env=environment, log=log) as process:
                connection, health = wait_for_health(process, profile, connection_file.name)
                if health["setup_step"] != "password":
                    raise ValueError("The first-run profile already had a password")
                created = request(connection, "/v1/setup/password", body=password,
                                  headers={"X-Setup-Token": setup})
                if not created["created"]:
                    raise ValueError("The installed backend did not accept the first password")
                login = request(connection, "/v1/admin/login", body=password)
                admin_headers = {"X-Admin-Token": login["admin_token"]}
                request(connection, "/v1/admin/knowledge/demo", method="POST",
                        headers=admin_headers, timeout=120)
                health = request(connection, "/v1/health")
                if health["setup_step"] != "provider" or not health["demo_knowledge"]:
                    raise ValueError("First-run setup did not advance from demo knowledge to the provider")
                report.update(installed_first_run_setup_verified=True, first_run_step="provider",
                              provider_account_verified=False)
                print("Installed first-run setup created a local password and loaded demo knowledge", flush=True)
                if check_provider:
                    provider = request(connection, "/v1/admin/provider-check", method="POST",
                                       headers=admin_headers, timeout=120)
                    report["provider_account_verified"] = provider.get("ok") is True
                    health = request(connection, "/v1/health")
                    report["first_run_step"] = health["setup_step"]
                    if not report["provider_account_verified"] or health["setup_step"] != "ready":
                        raise ValueError(f"The installed first-run profile did not become ready: {health['setup_issue']}")
                    print("Installed first-run setup reached ready with the configured provider", flush=True)
    finally:
        connection_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/windows-installer-verification.json")
    parser.add_argument("--first-launch", action="store_true",
                        help="Launch the installed desktop and verify its local setup flow")
    parser.add_argument("--provider-check", action="store_true",
                        help="Send the built-in setup probe to the configured Codex account")
    args = parser.parse_args()
    if args.provider_check and not args.first_launch:
        parser.error("--provider-check requires --first-launch")
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
        if args.first_launch:
            verify_first_launch(target, evidence, report, check_provider=args.provider_check)
    except Exception as error:
        report["issue"] = str(error)
        raise
    finally:
        try:
            profiles = (data, evidence / "Первый запуск") if args.first_launch else (data,)
            user_files = {path.relative_to(evidence): hashlib.sha256(path.read_bytes()).hexdigest()
                          for profile in profiles for path in profile.rglob("*") if path.is_file()}
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
                and all((evidence / name).is_file()
                        and hashlib.sha256((evidence / name).read_bytes()).hexdigest() == digest
                        for name, digest in user_files.items()))
            report["preserved_user_file_count"] = len(user_files)
            report["passed"] = bool(not report.get("issue") and report.get("installed_runtime_verified")
                                    and report["uninstallation_verified"] and report["user_data_preserved"]
                                    and (not args.first_launch or report.get("installed_first_run_setup_verified"))
                                    and (not args.provider_check or report.get("first_run_step") == "ready"))
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
