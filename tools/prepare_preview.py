"""Prepare an isolated desktop preview with fictional KB data and the real Codex provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import zipfile
from io import BytesIO
from pathlib import Path

from decanat.config import RuntimeLease, Settings, SettingsStore, write_private
from decanat.knowledge import KnowledgeStore
from decanat.providers import CodexProvider
from decanat.security import AdminAuth
from decanat.speech import LocalSpeech

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / ".runtime/interactive-preview"


async def prepare(codex_binary: Path, verify_provider: bool):
    with RuntimeLease(PREVIEW):
        settings = SettingsStore(PREVIEW)
        if not settings.path.exists():
            settings.save(Settings(
                codex_binary=str(codex_binary),
                whisper_model_path=str(ROOT / "models/ggml-small.bin"),
                silero_model_path=str(ROOT / "models/silero_v5_ru.pt"),
                vad_model_path=str(ROOT / "models/silero_vad.onnx"),
                apm_library_path=str(ROOT / ".tools/native/bin/decanat_audio.dll"),
                silero_speaker="aidar", speech_enabled=True,
                dense_enabled=False, provider="codex", api_fallback=False,
            ))
        diagnostics = LocalSpeech(settings).diagnostics()
        if not all(value for name, value in diagnostics.items() if name != "whisper_gpu_requested"):
            raise RuntimeError("Install and verify the local speech models/native AEC first")
        auth = AdminAuth(PREVIEW)
        if not auth.configured:
            password = secrets.token_urlsafe(18)
            auth.set_password(password)
            write_private(PREVIEW / "initial-password.txt", password + "\n")
        knowledge = KnowledgeStore(PREVIEW)
        if not knowledge.installed:
            archive = BytesIO()
            with zipfile.ZipFile(archive, "w") as package:
                for name in ("manifest.json", "documents.jsonl"):
                    package.write(ROOT / "examples/knowledge" / name, name)
            knowledge.install(archive.getvalue())
        print("Preview uses FICTIONAL example knowledge; production settings are untouched.", flush=True)
        if verify_provider:
            question = "Сколько делают справку об обучении?"
            async with asyncio.timeout(settings.value.provider_timeout):
                answer = await CodexProvider(settings).generate(question, [], knowledge.search(question))
            print(json.dumps({"real_provider": "codex", "answer": answer.model_dump()},
                             ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--verify-provider", action="store_true")
    args = parser.parse_args()
    asyncio.run(prepare(args.codex_binary.resolve(strict=True), args.verify_provider))


if __name__ == "__main__":
    main()
