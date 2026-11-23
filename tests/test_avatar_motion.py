"""Compile the actual portable motion controller; no Unreal SDK or models are required."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AvatarMotionTests(unittest.TestCase):
    def test_transitions_reject_late_completion_and_previous_sessions(self):
        compiler = shutil.which("clang++") or shutil.which("g++") or shutil.which("cl")
        if not compiler:
            self.skipTest("A C++ compiler is required for the avatar motion check")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="decanat-motion-") as directory:
            executable = Path(directory) / (
                "avatar-motion-test.exe" if sys.platform == "win32" else "avatar-motion-test"
            )
            include = root / "desktop/Decanat/Source/Decanat"
            source = root / "tests/native/test_avatar_motion.cpp"
            if Path(compiler).stem.lower() == "cl":
                arguments = [
                    compiler,
                    "/nologo",
                    "/std:c++17",
                    "/EHsc",
                    "/W4",
                    "/WX",
                    f"/I{include}",
                    str(source),
                    f"/Fe{executable}",
                    f"/Fo{Path(directory) / 'avatar-motion-test.obj'}",
                ]
            else:
                arguments = [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(include),
                    str(source),
                    "-o",
                    str(executable),
                ]
            compilation = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(compilation.returncode, 0, compilation.stdout + compilation.stderr)
            execution = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(execution.returncode, 0, execution.stderr)


if __name__ == "__main__":
    unittest.main()
