"""Run installed local speech/search models on synthetic Russian examples.

Writes generated WAV previews, never captures or plays microphone/speaker audio.
This is a smoke check; it does not approve a voice or validate real university facts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
import wave
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import numpy as np
from decanat.config import SettingsStore
from decanat.knowledge import KnowledgeStore, SentenceEncoder
from decanat.speech import LocalSpeech, SileroVAD
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]


async def speech_check(settings, models, output, report):
    speech = LocalSpeech(settings)
    vad = SileroVAD(models / "silero_vad.onnx")
    samples = (
        ("voice-preview", "Здравствуйте. Я виртуальный помощник деканата. Помогу найти информацию об учёбе и документах."),
        ("question", "Сколько дней готовится справка об обучении?"),
    )
    for name, text in samples:
        started = time.perf_counter()
        audio = await speech.synthesize(text)
        synthesis_seconds = time.perf_counter() - started
        path = output / f"{name}.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(audio.sample_rate)
            stream.writeframes(audio.pcm)
        started = time.perf_counter()
        transcript = await speech.transcribe(audio.pcm, audio.sample_rate)
        recognition_seconds = time.perf_counter() - started
        normalized = np.frombuffer(audio.pcm, dtype="<i2").astype(np.float32) / 32768
        normalized = resample_poly(normalized, 16000, audio.sample_rate)
        vad.reset()
        probabilities = [vad.probability(normalized[i:i + 512])
                         for i in range(0, len(normalized) - 511, 512)]
        record = {
            "name": name, "source_text": text, "transcript": transcript, "wav": str(path),
            "audio_seconds": audio.duration, "synthesis_seconds": synthesis_seconds,
            "recognition_seconds": recognition_seconds,
            "peak_vad_probability": max(probabilities, default=0),
        }
        report["speech"].append(record)
        if not audio.pcm or not transcript.strip() or record["peak_vad_probability"] < .6:
            raise RuntimeError(f"Local speech smoke check failed: {name}")
        print(f"PASS speech {name}: {transcript}", flush=True)


def search_check(directory, models, report):
    sources = json.loads((ROOT / "tools/model-sources.json").read_text(encoding="utf-8"))
    encoder = SentenceEncoder(str(models / "bge-m3"), sources["embedding"]["revision"])
    kb = KnowledgeStore(directory, encoder)
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        for name in ("manifest.json", "documents.jsonl"):
            package.write(ROOT / "examples/knowledge" / name, name)
    started = time.perf_counter()
    metadata = kb.install(archive.getvalue())
    indexing_seconds = time.perf_counter() - started
    started = time.perf_counter()
    evidence = kb.search("Сколько делают справку об обучении?")
    report["search"] = {
        "synthetic_kb": True, "metadata": metadata, "indexing_seconds": indexing_seconds,
        "query_seconds": time.perf_counter() - started,
        "evidence_ids": [item.id for item in evidence],
    }
    if not any("трёх рабочих дней" in item.text for item in evidence):
        raise RuntimeError("Local hybrid search did not retrieve the synthetic certificate duration")
    print("PASS local BGE-M3 hybrid retrieval", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=ROOT / "models")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/windows-local-models")
    args = parser.parse_args()
    output, models = args.output.resolve(), args.models.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "started_at_utc": datetime.now(UTC).isoformat(), "passed": False, "speech": [],
        "voice": "aidar", "voice_approved": False, "synthetic_loopback_only": True,
        "physical_audio_verified": False, "real_university_facts_verified": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="model-check-", dir=output) as temporary:
            directory = Path(temporary)
            settings = SettingsStore(directory)
            settings.value.whisper_model_path = str(models / "ggml-small.bin")
            settings.value.silero_model_path = str(models / "silero_v5_ru.pt")
            settings.value.whisper_use_gpu = False
            asyncio.run(speech_check(settings, models, output, report))
            search_check(directory, models, report)
        report["passed"] = True
    except Exception as error:
        report["issue"] = str(error)
        raise
    finally:
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )


if __name__ == "__main__":
    main()
