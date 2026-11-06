"""Entry point for the self-contained Windows development distribution."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from decanat.cli import make_kb
from decanat.config import RuntimeLease, Settings, SettingsStore, write_private
from decanat.security import AdminAuth
from decanat.speech import LocalSpeech

ROOT = Path(__file__).resolve().parents[1]


def verify_models():
    sources = json.loads((ROOT / "tools/model-sources.json").read_text(encoding="utf-8"))
    expected = {
        "ggml-small.bin": sources["whisper"]["sha256"]["small"],
        "silero_v5_ru.pt": sources["tts"]["sha256"],
        "silero_vad.onnx": sources["vad"]["sha256"],
        **{"bge-m3/" + name: digest for name, digest in sources["embedding"]["files"].items()},
    }
    for name, digest in expected.items():
        with (ROOT / "models" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"Повреждена локальная модель {name}. Переустановите приложение")


def initialize(data: Path):
    with RuntimeLease(data):
        store = SettingsStore(data)
        if not store.path.exists():
            verify_models()
            metadata = json.loads((ROOT / "distribution.json").read_text(encoding="utf-8"))
            store.save(Settings(
                codex_binary=str(ROOT / metadata["codex_executable"]),
                whisper_model_path=str(ROOT / "models/ggml-small.bin"),
                silero_model_path=str(ROOT / "models/silero_v5_ru.pt"),
                vad_model_path=str(ROOT / "models/silero_vad.onnx"),
                apm_library_path=str(ROOT / "native/decanat_audio.dll"),
                embedding_model=str(ROOT / "models/bge-m3"),
                embedding_revision=metadata["embedding_revision"],
                speech_enabled=True, dense_enabled=True,
            ))
        auth = AdminAuth(data)
        if not auth.configured:
            password = secrets.token_urlsafe(18)
            auth.set_password(password)
            write_private(data / "initial-password.txt", password + "\n")
        kb = make_kb(data, store)
        if not kb.installed:
            archive = BytesIO()
            with zipfile.ZipFile(archive, "w") as package:
                for name in ("manifest.json", "documents.jsonl"):
                    package.write(ROOT / "examples/knowledge" / name, name)
            kb.install(archive.getvalue())
        kb.ensure_compatible()
        return store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ["LOCALAPPDATA"]) / "AI-decanat-development")
    parser.add_argument("--check", action="store_true", help="Offline runtime check; no capture or playback")
    args = parser.parse_args()
    data = args.data_dir.resolve()
    store = initialize(data)
    if args.check:
        speech = LocalSpeech(store)
        diagnostics = speech.diagnostics()
        if not all(value for key, value in diagnostics.items() if key != "whisper_gpu_requested"):
            raise RuntimeError("Не найдены компоненты локальной речи")
        result = subprocess.run([store.value.codex_binary, "--version"], capture_output=True,
                                check=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        from decanat.audio_device import EchoCanceller

        apm = EchoCanceller(Path(store.value.apm_library_path))
        try:
            assert len(apm.process(bytes(960), bytes(960), 0)) == 320
        finally:
            apm.close()
        # Exercise native inference/DLLs too; this opens no audio device.
        asyncio.run(speech.warm_up())
        import numpy as np
        from decanat.speech import SileroVAD

        vad = SileroVAD(Path(store.value.vad_model_path))
        probability = vad.probability(np.zeros(512, dtype=np.float32))
        if not 0 <= probability <= 1:
            raise RuntimeError("Локальная модель определения речи вернула неверный результат")
        print(json.dumps({"passed": True, "runtime": sys.version.split()[0],
                          "codex": result.stdout.strip(), "speech": diagnostics,
                          "native_aec_loaded": True, "tts_asr_vad_warmup_verified": True,
                          "package_file": str(Path(__import__("decanat").__file__).resolve()),
                          "microphone_opened": False,
                          "data_dir": str(data)}, ensure_ascii=False))
        return
    # This directory and its imports belong to the immutable installed payload.
    sys.path.insert(0, str(ROOT / "tools"))
    import launch

    sys.argv = [str(ROOT / "tools/launch.py"), str(ROOT / "Avatar/Decanat/Binaries/Win64/Decanat.exe"),
                "--data-dir", str(data), "--windowed"]
    launch.main()


if __name__ == "__main__":
    main()
