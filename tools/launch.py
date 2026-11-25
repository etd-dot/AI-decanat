"""Start the local backend and packaged desktop application as one supervised process group."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from decanat.config import RuntimeLease


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("desktop", type=Path, help="Path to the packaged Decanat executable")
    parser.add_argument("--data-dir", type=Path, default=Path(".runtime"))
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--windowed", action="store_true", help="Open a movable preview window")
    display.add_argument("--kiosk", action="store_true", help="Launch full-screen without the Unreal console")
    parser.add_argument("--setup", action="store_true", help="Enable desktop staff setup")
    parser.add_argument("--demo-source", type=Path, help="Optional sample knowledge directory")
    args = parser.parse_args()
    if not args.desktop.is_file():
        parser.error("Packaged desktop executable is not installed")
    root = args.data_dir.resolve()
    with RuntimeLease(root, name="launcher.lock"):
        try:
            launch(args, root)
        except (OSError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(75) from exc  # The native launcher may retry the whole job.


def launch(args, root):
    connection_file = root / f"desktop-{secrets.token_hex(8)}.json"
    environment = dict(os.environ)
    environment["DECANAT_CONNECTION_FILE"] = str(connection_file)
    serve_flags = ["--connection-file", str(connection_file)]
    if args.setup:
        environment["DECANAT_SETUP_TOKEN"] = secrets.token_urlsafe(32)
        serve_flags.append("--desktop-setup")
        if args.demo_source:
            serve_flags.extend(["--demo-source", str(args.demo_source.resolve())])
    python_flags = ["-I"] if sys.flags.isolated else []
    if sys.flags.dont_write_bytecode:
        python_flags.append("-B")
    if os.name == "nt":
        python_flags.extend(["-X", "utf8"])
    backend = subprocess.Popen(
        [sys.executable, *python_flags, "-m", "decanat.cli", "--data-dir", str(root), "serve", *serve_flags],
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    desktop = None
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("Backend failed to start")
            if connection_file.exists():
                try:
                    data = json.loads(connection_file.read_text(encoding="utf-8"))
                    request = urllib.request.Request(
                        data["url"] + "/v1/health",
                        headers={"Authorization": "Bearer " + data["token"]},
                    )
                    with urllib.request.urlopen(request, timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Backend startup timed out")
        display = ["-windowed", "-ResX=1600", "-ResY=900"] if args.windowed else ["-fullscreen"]
        if args.kiosk:
            display.append("-DecanatKiosk")
        desktop = subprocess.Popen(
            [str(args.desktop.resolve()), *display, "-NoSplash", f"-abslog={root / 'desktop.log'}"],
            env=environment,
        )
        while desktop.poll() is None:
            if backend.poll() is not None:
                raise RuntimeError(
                    "Backend stopped; closing desktop to avoid a stale student session"
                )
            time.sleep(0.5)
        if desktop.returncode not in {0, -1073741510, 0xC000013A}:
            raise RuntimeError(f"Desktop stopped unexpectedly (exit code {desktop.returncode})")
    finally:
        for process in (desktop, backend):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        connection_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
