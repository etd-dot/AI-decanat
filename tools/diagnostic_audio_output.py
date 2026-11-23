"""Output-only adapter for the real AudioDeviceAgent; never opens a microphone."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from decanat.audio_device import AudioDeviceAgent, stream_devices


class DiagnosticAudioOutput:
    def __init__(self, controller, root: Path):
        import sounddevice as sd

        controller.settings.value.vad_model_path = str(root / "models/silero_vad.onnx")
        self.agent = AudioDeviceAgent(controller, root / ".tools/native/bin/decanat_audio.dll")
        self.agent.loop = asyncio.get_running_loop()
        self.agent._queue = controller.subscribe()
        self.agent.ready = True
        self.first_dac = []
        devices = stream_devices(None, None, sd)
        render = devices[1] if devices else None
        self.device = dict(sd.query_devices(render, kind="output"))

        def callback(outdata, frames, timing, status):
            with self.agent._lock:
                first = bool(self.agent._pcm) and self.agent._cursor == 0
                turn = self.agent._turn_id
            now = time.perf_counter()
            if first and turn:
                self.first_dac.append({
                    "turn_id": turn,
                    "qpc_time": now + timing.outputBufferDacTime - timing.currentTime,
                    "output_latency_seconds": timing.outputBufferDacTime - timing.currentTime,
                })
            # Feed generated silence into the capture path; no input device is opened.
            translated = SimpleNamespace(
                currentTime=timing.currentTime, outputBufferDacTime=timing.outputBufferDacTime,
                inputBufferAdcTime=timing.currentTime,
            )
            self.agent._callback(bytes(frames * 2), outdata, frames, translated, status)

        self.agent._stream = sd.RawOutputStream(
            samplerate=48000, blocksize=480, dtype="int16", channels=1,
            device=render, latency="low", callback=callback,
        )

    async def start(self):
        async def discard_generated_capture():
            while True:
                await self.agent._frames.get()

        self.agent._tasks = [asyncio.create_task(self.agent._events()),
                             asyncio.create_task(discard_generated_capture())]
        self.agent._stream.start()

    async def close(self):
        await self.agent.close()
