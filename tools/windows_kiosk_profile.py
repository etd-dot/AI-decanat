"""Export an Assigned Access profile for a dedicated Windows 11 kiosk account."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

NAMESPACE = "http://schemas.microsoft.com/AssignedAccess/2017/config"
RS5 = "http://schemas.microsoft.com/AssignedAccess/201810/config"
WINDOWS_11 = "http://schemas.microsoft.com/AssignedAccess/2022/config"


def build_profile(installation: Path, account: str, shortcut: str) -> ET.Element:
    if not account.startswith(".\\") or not account[2:].strip():
        raise ValueError("Use a dedicated local user in the form .\\Account")
    installation = installation.resolve(strict=True)
    manifest = json.loads((installation / "distribution.json").read_text(encoding="utf-8"))
    executables = [
        installation / "Decanat.exe",
        installation / "runtime/python.exe",
        installation / "Avatar/Decanat/Binaries/Win64/Decanat.exe",
        installation / manifest["codex_executable"],
    ]
    for executable in executables:
        if (not executable.resolve().is_relative_to(installation)
                or executable.suffix.lower() != ".exe" or not executable.is_file()):
            raise ValueError(f"Missing or invalid bundled executable: {executable}")
    ET.register_namespace("", NAMESPACE)
    ET.register_namespace("rs5", RS5)
    ET.register_namespace("v5", WINDOWS_11)

    def add(parent, tag, **attributes):
        return ET.SubElement(parent, f"{{{NAMESPACE}}}{tag}", attributes)

    root = ET.Element(f"{{{NAMESPACE}}}AssignedAccessConfiguration")
    profile_id = "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-decanat/{account.casefold()}")).upper() + "}"
    profile = add(add(root, "Profiles"), "Profile", Id=profile_id, Name="AI-decanat")
    allowed = add(add(profile, "AllAppsList"), "AllowedApps")
    for executable in executables:
        app = add(allowed, "App", DesktopAppPath=str(executable))
        if executable == executables[0]:
            app.set(f"{{{RS5}}}AutoLaunch", "true")
            app.set(f"{{{RS5}}}AutoLaunchArguments", "--kiosk")
    pins = ET.SubElement(profile, f"{{{WINDOWS_11}}}StartPins")
    pins.text = json.dumps({"pinnedList": [{"desktopAppLink": shortcut}]}, ensure_ascii=False)
    add(profile, "Taskbar", ShowTaskbar="false")
    config = add(add(root, "Configs"), "Config")
    add(config, "Account").text = account
    add(config, "DefaultProfile", Id=profile_id)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installation", type=Path)
    parser.add_argument("account", help=r"Local kiosk account, e.g. .\DecanatKiosk")
    parser.add_argument("output", type=Path)
    parser.add_argument("--shortcut", default=r"%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\AI-decanat.lnk")
    args = parser.parse_args()
    root = build_profile(args.installation, args.account, args.shortcut)
    ET.indent(root)
    ET.ElementTree(root).write(args.output, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
