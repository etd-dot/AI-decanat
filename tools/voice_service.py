"""Own the same local voice backend used by the desktop application."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import numpy as np
from decanat.config import RuntimeLease
from websockets.asyncio.client import connect


def mouth_track(pcm: bytes, sample_rate: int) -> tuple[list[int], float]:
    """Use 40 ms PCM windows to distinguish silence and three degrees of articulation."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    step = round(sample_rate * .04)
    if samples.size == 0:
        return [], step / sample_rate
    blocks = np.pad(samples, (0, (-samples.size) % step)).reshape(-1, step)
    power = (blocks * blocks).mean(axis=1)
    level = np.sqrt(power)
    reference = max(float(np.percentile(level, 96)), 650.0)
    high_frequency = (np.diff(blocks, axis=1) ** 2).mean(axis=1) / np.maximum(power, 1)
    shape = np.where(level < max(135.0, reference * .09), 0,
                     np.where(level < reference * .38, 1,
                              np.where((high_frequency < .016) & (level < reference * .72), 3, 2)))
    return shape.tolist(), step / sample_rate


class VoiceService:
    def __init__(self, data: Path, demo: Path) -> None:
        self.data = data
        self.demo = demo
        self.events = queue.SimpleQueue()
        self.commands = queue.SimpleQueue()
        self.stopped = threading.Event()
        self.loop = None
        self.stop_request = None
        self.thread = threading.Thread(target=self._worker, name="voice-service")
        self.thread.start()

    def send(self, command: dict) -> None:
        self.commands.put(("conversation", command))

    def setup(self, action: str, value=None) -> None:
        self.commands.put((action, value))

    def close(self) -> None:
        self.stopped.set()
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop_request.set)
        self.thread.join()

    def _worker(self) -> None:
        try:
            with RuntimeLease(self.data, name="launcher.lock"):
                asyncio.run(self._run())
        except Exception as error:
            print(f"Voice service stopped: {error}", file=sys.stderr)
            if not self.stopped.is_set():
                self.events.put(("offline", "Связь с голосовым сервисом прервалась. Перезапустите приложение."))

    async def _run(self) -> None:
        self.stop_request = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        if self.stopped.is_set():
            return
        setup_token = secrets.token_urlsafe(32)
        connection_file = self.data / f"voice-{secrets.token_hex(8)}.json"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-X", "utf8", "-m", "decanat.cli", "--data-dir",
            str(self.data), "serve", "--port", str(port), "--desktop-setup",
            "--demo-source", str(self.demo), "--connection-file", str(connection_file),
            env={**os.environ, "DECANAT_SETUP_TOKEN": setup_token, "HF_HUB_OFFLINE": "1",
                 "TRANSFORMERS_OFFLINE": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            async with asyncio.timeout(180):
                while not self.stopped.is_set() and not connection_file.exists():
                    if process.returncode is not None:
                        raise RuntimeError("Backend exited at startup")
                    await asyncio.sleep(.25)
            if self.stopped.is_set():
                return
            info = json.loads(connection_file.read_text(encoding="utf-8"))
            url = f"http://127.0.0.1:{port}"
            if info["url"] != url:
                raise RuntimeError("Unexpected backend address")
            headers = {"Authorization": "Bearer " + info["token"]}
            async with (
                httpx.AsyncClient(base_url=url, headers=headers, trust_env=False, timeout=120) as http,
                connect(f"ws://127.0.0.1:{port}/v1/conversation", additional_headers=headers,
                        proxy=None, close_timeout=2, max_size=128 * 1024 * 1024) as ws,
            ):
                reader = asyncio.create_task(self._read(ws))
                commands = asyncio.create_task(self._commands(http, ws, reader, setup_token))
                stopping = asyncio.create_task(self.stop_request.wait())
                try:
                    await asyncio.wait((stopping, commands), return_when=asyncio.FIRST_COMPLETED)
                    if commands.done():
                        await commands
                finally:
                    for task in (reader, commands, stopping):
                        task.cancel()
                    await asyncio.gather(reader, commands, stopping, return_exceptions=True)
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            connection_file.unlink(missing_ok=True)

    async def _read(self, ws) -> None:
        async for message in ws:
            event = json.loads(message)
            kind = event["kind"]
            if kind == "speech":
                data = event["payload"]
                shapes, step = mouth_track(base64.b64decode(data["audio_b64"]), data["sample_rate"])
                self.events.put(("mouth_track", {**event, "payload": {"shapes": shapes, "step": step}}))
                await ws.send(json.dumps({
                    "type": "face_ready", "session_id": event["session_id"], "turn_id": event["turn_id"],
                }))
            elif kind != "playback_start":
                self.events.put((kind, event))

    async def _commands(self, http, ws, reader, setup_token: str) -> None:
        admin_token = ""

        async def request(method, path, **options):
            nonlocal admin_token
            headers = options.pop("headers", {})
            if admin_token:
                headers["X-Admin-Token"] = admin_token
            response = await http.request(method, path, headers=headers, **options)
            if response.is_error:
                if response.status_code == 401:
                    admin_token = ""
                detail = response.json().get("detail")
                raise ValueError(detail if isinstance(detail, str) else "Операция не выполнена.")
            return response.json()

        async def health():
            self.events.put(("health", (await request("GET", "/v1/health"), bool(admin_token))))

        def publish_provider(settings):
            self.events.put(("provider_settings", {
                field: settings[field] for field in ("provider", "api_model", "api_fallback")
            }))

        await health()
        while not self.stopped.is_set():
            if reader.done():
                await reader
                raise RuntimeError("Voice connection closed")
            try:
                action, value = self.commands.get_nowait()
            except queue.Empty:
                await asyncio.sleep(.05)
                continue
            if action == "conversation":
                await ws.send(json.dumps(value))
                continue
            try:
                if action == "create":
                    await request("POST", "/v1/setup/password", json={"password": value},
                                  headers={"X-Setup-Token": setup_token})
                if action in {"create", "login"}:
                    result = await request("POST", "/v1/admin/login", json={"password": value})
                    admin_token = result["admin_token"]
                    publish_provider(await request("GET", "/v1/admin/settings"))
                    self.events.put(("notice", "Вход сотрудника выполнен."))
                elif action == "logout":
                    if admin_token:
                        try:
                            await request("POST", "/v1/admin/logout")
                        finally:
                            admin_token = ""
                elif action == "save_provider":
                    settings = await request("GET", "/v1/admin/settings")
                    for field in ("provider", "api_model", "api_fallback"):
                        settings[field] = value[field]
                    result = await request("PUT", "/v1/admin/settings", json=settings)
                    publish_provider(result["settings"])
                    self.events.put(("notice", "Настройки сохранены. При смене сервиса выполните его проверку."))
                elif action == "load_audio":
                    self.events.put(("audio_settings", await request("GET", "/v1/admin/audio-devices")))
                elif action == "save_audio":
                    settings = await request("GET", "/v1/admin/settings")
                    for field in ("input_device", "output_device"):
                        settings[field] = value[field]
                    result = await request("PUT", "/v1/admin/settings", json=settings)
                    self.events.put(("audio_settings", await request("GET", "/v1/admin/audio-devices")))
                    message = ("Выбор сохранён. Перезапустите приложение, чтобы применить его."
                               if result["restart_required"] else "Устройства сохранены и готовы к проверке.")
                    self.events.put(("notice", message))
                elif action == "journal":
                    result = await request("GET", "/v1/admin/journal", params={"limit": 20, "offset": value})
                    self.events.put(("journal_page", {**result, "offset": value}))
                elif action == "api_key":
                    await request("POST", "/v1/admin/api-key", content=value.encode("utf-8"))
                    self.events.put(("notice", "Ключ сохранён в защищённом хранилище Windows."))
                elif action == "demo":
                    await request("POST", "/v1/admin/knowledge/demo")
                    self.events.put(("notice", "Демонстрационная база загружена."))
                elif action == "import":
                    if value.stat().st_size > 50 * 1024**2:
                        raise ValueError("Архив превышает 50 МБ.")
                    await request("POST", "/v1/admin/knowledge", content=value.read_bytes())
                    self.events.put(("notice", "База знаний загружена."))
                elif action == "probe":
                    await request("POST", "/v1/admin/provider-check")
                    self.events.put(("notice", "Сервис ответил на проверочный запрос."))
                elif action == "codex":
                    await request("POST", "/v1/admin/codex-login")
                    self.events.put(("notice", "Вход в Codex выполнен. Отправьте проверочный запрос."))
                elif action in {"speaker", "microphone"}:
                    kind = "output" if action == "speaker" else "input"
                    result = await request("POST", f"/v1/admin/audio-test/{kind}")
                    self.events.put(("notice", "Динамики проверены." if action == "speaker" else
                                     "Микрофон улавливает звук." if result.get("signal_detected")
                                     else "Микрофон доступен. Говорите во время проверки."))
            except (httpx.HTTPError, ValueError, OSError) as error:
                self.events.put(("notice", str(error)))
            finally:
                await health()
