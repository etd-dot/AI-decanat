"""Run the copied application in isolation, offline, without opening audio devices."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/windows-distribution-verification.json")
    args = parser.parse_args()
    payload = args.payload.resolve(strict=True)
    evidence = Path(tempfile.mkdtemp(prefix="f04-runtime-", dir=ROOT / ".runtime"))
    environment = dict(os.environ)
    environment.update({
        "PATH": str(payload / "runtime") + os.pathsep + str(Path(os.environ["SYSTEMROOT"]) / "System32"),
        "PYTHONPATH": str(ROOT / "backend/src"),  # -I must ignore this deliberately poisoned path.
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8",
    })
    report = {"started_at_utc": datetime.now(UTC).isoformat(), "passed": False,
              "payload": str(payload), "developer_tools_removed_from_path": True,
              "offline_models_only": True, "microphone_opened": False}
    try:
        result = subprocess.run([
            str(payload / "runtime/python.exe"), "-I", "-B", str(payload / "tools/installed_desktop.py"),
            "--check", "--data-dir", str(evidence / "profile"),
        ], env=environment, cwd=evidence, capture_output=True, timeout=240,
            creationflags=subprocess.CREATE_NO_WINDOW)
        (evidence / "stdout.log").write_bytes(result.stdout)
        (evidence / "stderr.log").write_bytes(result.stderr)
        report["exit_code"] = result.returncode
        report["logs"] = str(evidence)
        result.check_returncode()
        summary = json.loads(result.stdout.decode("utf-8").splitlines()[-1])
        if not summary["passed"] or not Path(summary["package_file"]).is_relative_to(payload / "runtime"):
            raise ValueError("The runtime imported code outside the installed application")
        report.update(passed=True, runtime_checks=summary)
        print("Self-contained offline runtime verification passed", flush=True)
    except Exception as error:
        report["issue"] = str(error)
        raise
    finally:
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
