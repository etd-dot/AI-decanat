from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from decanat.config import RuntimeLease, Settings, SettingsStore, write_private
from decanat.contracts import ClientCommand
from decanat.journal import Journal
from decanat.knowledge import (
    MAX_ARCHIVE,
    KnowledgeStore,
    PackageError,
    SentenceEncoder,
    pack_directory,
)
from decanat.providers import ProviderError, Providers, codex_login
from decanat.security import AdminAuth
from decanat.session import Conversation, SessionError
from decanat.speech import LocalSpeech


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


async def complete_mutation(operation, *args, on_success=None, **kwargs):
    """Do not leave a mutating worker running after releasing maintenance on cancellation.

    run_in_executor returns a Future (not another Task cancelled at loop shutdown).
    Defer caller cancellation until the atomic install has actually finished.
    """
    future = asyncio.get_running_loop().run_in_executor(None, partial(operation, *args, **kwargs))
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        result = future.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if on_success:
        on_success(result)
    if cancelled:
        raise asyncio.CancelledError
    return result


def create_app(
    root: Path,
    ipc_token: str,
    *,
    conversation: Conversation | None = None,
    setup_token: str | None = None,
    demo_source: Path | None = None,
    connection_file: Path | None = None,
    port: int = 8765,
) -> FastAPI:
    if len(ipc_token) < 32:
        raise ValueError("IPC token must contain at least 32 characters")
    if setup_token is not None and len(setup_token) < 32:
        raise ValueError("Setup token must contain at least 32 characters")
    settings = conversation.settings if conversation else SettingsStore(root)
    encoder = (
        SentenceEncoder(settings.value.embedding_model, settings.value.embedding_revision)
        if settings.value.dense_enabled
        else None
    )
    kb = conversation.kb if conversation else KnowledgeStore(root, encoder)
    journal = conversation.journal if conversation else Journal(root)
    speech = LocalSpeech(settings)
    controller = conversation or Conversation(
        settings, kb, Providers(settings), journal, speech=speech
    )
    auth = AdminAuth(root)
    controller_lock = asyncio.Lock()
    client_connected = False
    audio_agent = None
    audio_error = "Локальная речь не включена"
    models_ready = False
    knowledge_error = ""
    demo_knowledge = False
    restart_required = False
    provider_proof = root / "provider-check.json"
    active_audio_test: tuple[str, asyncio.Task] | None = None
    runtime_fields = (
        "whisper_model_path",
        "whisper_use_gpu",
        "silero_model_path",
        "silero_speaker",
        "vad_model_path",
        "apm_library_path",
        "speech_enabled",
        "input_device",
        "output_device",
    )
    startup_settings = settings.snapshot()

    def provider_identity() -> dict:
        value = settings.value
        return (
            {"provider": "codex", "binary": value.codex_binary, "model": value.codex_model}
            if value.provider == "codex"
            else {"provider": "api", "model": value.api_model}
        )

    def provider_ready() -> bool:
        if controller.providers.known_error():
            return False
        try:
            return json.loads(provider_proof.read_text(encoding="utf-8")) == provider_identity()
        except (OSError, ValueError):
            return False

    @asynccontextmanager
    async def lifespan(app):
        nonlocal audio_agent, audio_error, knowledge_error, models_ready, demo_knowledge

        async def housekeeping():
            ticks = 0
            while True:
                await asyncio.sleep(1)
                async with controller_lock:
                    await controller.tick()
                ticks += 1
                if ticks % 3600 == 0:
                    await asyncio.to_thread(journal.purge)

        with RuntimeLease(root):
            task = None
            published = False
            try:
                try:
                    await asyncio.to_thread(kb.warm_up)
                    metadata = kb.metadata()
                    demo_knowledge = bool(metadata and metadata["kb_id"] == "fictional-dean-office")
                except Exception:
                    knowledge_error = "Проверьте локальную модель поиска и перестройте индекс в даунтайме"
                if settings.value.speech_enabled:
                    try:
                        from decanat.audio_device import AudioDeviceAgent

                        await speech.warm_up()
                        audio_agent = AudioDeviceAgent(controller, Path(settings.value.apm_library_path))
                        models_ready = True
                        await audio_agent.start()
                        audio_error = ""
                    except Exception:
                        if audio_agent:
                            await audio_agent.close()
                        audio_agent = None
                        audio_error = (
                            "Не удалось открыть микрофон и динамики"
                            if models_ready else "Проверьте модели речи и библиотеку WebRTC AEC"
                        )
                if connection_file is not None:
                    write_private(connection_file, json.dumps({
                        "url": f"http://127.0.0.1:{port}", "token": ipc_token,
                    }))
                    published = True
                task = asyncio.create_task(housekeeping())
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                try:
                    await controller.finish("shutdown")
                finally:
                    try:
                        if audio_agent:
                            await audio_agent.close()
                    finally:
                        if published:
                            await asyncio.to_thread(connection_file.unlink, missing_ok=True)

    app = FastAPI(
        title="ИИ-деканат: локальный backend",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.controller = controller
    app.state.auth = auth
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def redact_password_error(request: Request, exc: RequestValidationError):
        if request.url.path in {
            "/v1/setup/password", "/v1/admin/login", "/v1/admin/password",
        }:
            return JSONResponse({"detail": "Некорректный формат пароля"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        # The native client does not use a browser origin. Reject CSRF/DNS-rebinding traffic.
        if request.headers.get("origin") or request.url.hostname not in {
            "127.0.0.1",
            "localhost",
            "testserver",
        }:
            return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
        return await call_next(request)

    def ipc(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization.encode(), f"Bearer {ipc_token}".encode()):
            raise HTTPException(401, "Требуется локальное подключение приложения")

    def admin(x_admin_token: str = Header(default=""), _=Depends(ipc)):
        if not auth.check(x_admin_token):
            raise HTTPException(401, "Требуется вход сотрудника")
        return x_admin_token

    @asynccontextmanager
    async def maintenance(message: str):
        async with controller_lock:
            if controller.state.value != "idle" or controller.maintenance:
                raise HTTPException(409, message)
            controller.maintenance = True
            controller.emit("maintenance", enabled=True)
        try:
            yield
        finally:
            controller.maintenance = False
            controller.emit("maintenance", enabled=False)

    def audio_ready() -> bool:
        return bool(audio_agent and audio_agent.ready and not restart_required)

    async def cancel_audio_test(token: str | None = None):
        if active_audio_test is None or (token is not None and active_audio_test[0] != token):
            return
        task = active_audio_test[1]
        task.cancel()
        # The owning request reports the outcome and releases maintenance.
        await asyncio.gather(task, return_exceptions=True)

    def setup_status() -> tuple[str, str]:
        if not auth.configured:
            return "password", "Задайте пароль сотрудника"
        if not kb.installed or knowledge_error:
            return "knowledge", knowledge_error or "Установите подготовленный ZIP-пакет"
        if not provider_ready():
            return "provider", "Проверьте ответ выбранного сервиса"
        if restart_required:
            return "devices", "Перезапустите приложение, чтобы применить настройки звука"
        if not models_ready:
            return "models", audio_error
        if not audio_ready():
            return "devices", audio_error or "Аудиоустройства недоступны"
        return "ready", ""

    @app.get("/v1/health", dependencies=[Depends(ipc)])
    def health():
        step, issue = setup_status() if setup_token is not None else ("disabled", "")
        return {
            "version": "0.1.0",
            "state": controller.state.value,
            "kb_installed": kb.installed,
            "knowledge_ready": kb.installed and not knowledge_error,
            "knowledge_error": knowledge_error,
            "admin_configured": auth.configured,
            "speech": speech.diagnostics(),
            "mode": "voice" if settings.value.speech_enabled else "text_diagnostics",
            "audio_device_ready": audio_ready(),
            "audio_error": audio_error,
            "restart_required": restart_required,
            "avatar_assets_verified": False,
            "setup_step": step,
            "setup_issue": issue,
            "demo_available": demo_source is not None,
            "demo_knowledge": demo_knowledge,
            "models_ready": models_ready,
        }

    @app.post("/v1/setup/password", dependencies=[Depends(ipc)])
    def setup_password(body: Login, x_setup_token: str = Header(default="")):
        if setup_token is None or not secrets.compare_digest(
            x_setup_token.encode(), setup_token.encode()
        ):
            raise HTTPException(403, "Первичная настройка доступна из запущенного приложения")
        try:
            created = auth.initialize_password(body.password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not created:
            raise HTTPException(409, "Пароль сотрудника уже задан")
        return {"created": True}

    @app.post("/v1/admin/login", dependencies=[Depends(ipc)])
    def login(body: Login):
        try:
            token = auth.login(body.password)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        if not token:
            raise HTTPException(401, "Неверный пароль или сотрудник ещё не настроен")
        return {"admin_token": token, "expires_in": 1800}

    @app.post("/v1/admin/logout")
    async def logout(token=Depends(admin)):
        auth.logout(token)
        await cancel_audio_test(token)
        return {"ok": True}

    @app.get("/v1/admin/settings", dependencies=[Depends(admin)])
    def get_settings():
        return settings.snapshot()

    @app.put("/v1/admin/settings", dependencies=[Depends(admin)])
    async def put_settings(body: Settings):
        nonlocal restart_required
        async with controller_lock:
            if controller.state.value != "idle" or controller.maintenance:
                raise HTTPException(409, "Настройки меняются только без активного разговора")
            # Changing an indexed model requires reimport before the next student session.
            new_encoder = (
                SentenceEncoder(body.embedding_model, body.embedding_revision)
                if body.dense_enabled
                else None
            )
            metadata = kb.metadata()
            fingerprint = new_encoder.fingerprint if new_encoder else "lexical-only"
            if metadata and metadata["encoder"] != fingerprint:
                raise HTTPException(
                    409,
                    "Остановите приложение и перестройте индекс с новой моделью через CLI",
                )
            audio = (body.input_device, body.output_device)
            if (body.speech_enabled and any(audio)
                    and audio != (settings.value.input_device, settings.value.output_device)):
                from decanat.audio_device import validate_audio_selection

                try:
                    validate_audio_selection(*audio)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            previous_provider = provider_identity()
            settings.save(body)
            if provider_identity() != previous_provider:
                provider_proof.unlink(missing_ok=True)
            restart_required = any(
                getattr(body, key) != startup_settings[key] for key in runtime_fields
            )
            if (kb.encoder.fingerprint if kb.encoder else "lexical-only") != fingerprint:
                kb.encoder = new_encoder
            controller.providers.unavailable.clear()
            return {"settings": settings.snapshot(), "restart_required": restart_required}

    @app.get("/v1/admin/audio-devices", dependencies=[Depends(admin)])
    def audio_devices():
        from decanat.audio_device import device_inventory

        return {**device_inventory(), "input_device": settings.value.input_device,
                "output_device": settings.value.output_device}

    @app.get("/v1/admin/diagnostics", dependencies=[Depends(admin)])
    def diagnostics():
        import platform
        import shutil

        from decanat.audio_device import device_inventory

        return {
            "health": health(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "audio": device_inventory(),
            "codex_found": bool(shutil.which(settings.value.codex_binary)),
            "api_key_configured": bool(settings.api_key()),
            "api_model_configured": bool(settings.value.api_model),
            "knowledge": kb.metadata(),
        }

    @app.post("/v1/admin/audio-test/{kind}")
    async def audio_test(kind: Literal["input", "output"], token=Depends(admin)):
        nonlocal active_audio_test
        async with maintenance("Проверка звука возможна только без активного разговора"):
            if not audio_ready():
                raise HTTPException(409, "Примените настройки и проверьте, что устройства подключены")
            agent = audio_agent

            async def run():
                if kind == "input":
                    return await agent.test_input()
                audio = await speech.synthesize("Проверка динамиков. Вы слышите голос деканата.")
                if audio.sample_rate != 48000:
                    raise RuntimeError("Модель вернула неверный формат")
                await agent.test_output(audio.pcm)
                return {"played": True}

            task = asyncio.create_task(run())
            active_audio_test = (token, task)
            try:
                return await task
            except asyncio.CancelledError as exc:
                if asyncio.current_task().cancelling():
                    raise
                raise HTTPException(409, "Проверка остановлена") from exc
            except Exception as exc:
                raise HTTPException(503, "Проверка звука не завершилась. Проверьте устройство") from exc
            finally:
                active_audio_test = None

    @app.post("/v1/admin/audio-test-cancel")
    async def stop_audio_test(token=Depends(admin)):
        await cancel_audio_test(token)
        return {"ok": True}

    @app.post("/v1/admin/api-key", dependencies=[Depends(admin)])
    async def set_api_key(request: Request):
        data = await request.body()
        if len(data) > 1024:
            raise HTTPException(413, "Слишком длинный ключ")
        try:
            key = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "Некорректный формат ключа") from exc
        if not key:
            raise HTTPException(400, "Ключ пуст")
        async with maintenance("Ключ меняется только без активного разговора"):
            if settings.value.provider == "api":
                provider_proof.unlink(missing_ok=True)
            try:
                await complete_mutation(settings.set_api_key, key)
            except Exception as exc:
                raise HTTPException(503, "Защищённое хранилище ОС недоступно") from exc
            controller.providers.unavailable.pop("api", None)
        return {"stored": True}

    @app.post("/v1/admin/password", dependencies=[Depends(admin)])
    async def password(body: Login):
        try:
            await asyncio.to_thread(auth.set_password, body.password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await cancel_audio_test()
        return {"ok": True, "login_required": True}

    @app.get("/v1/admin/journal", dependencies=[Depends(admin)])
    def get_journal(limit: int = 100, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(422, "Некорректные параметры страницы")
        items = journal.list(limit + 1, offset)
        return {"items": items[:limit], "has_more": len(items) > limit}

    @app.get("/v1/admin/knowledge", dependencies=[Depends(admin)])
    def knowledge_info():
        return {"knowledge": kb.metadata()}

    def knowledge_installed(metadata):
        nonlocal knowledge_error, demo_knowledge
        knowledge_error = ""
        demo_knowledge = metadata["kb_id"] == "fictional-dean-office"
        controller.emit("knowledge_updated", knowledge=metadata)

    @app.post("/v1/admin/knowledge", dependencies=[Depends(admin)])
    async def knowledge_upload(request: Request, replace_kb: bool = False):
        async with maintenance("Обновление базы возможно только в даунтайме"):
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_ARCHIVE:
                    raise HTTPException(413, "Архив превышает 50 MiB")
                data.extend(chunk)
            try:
                metadata = await complete_mutation(
                    kb.install, bytes(data), replace_kb=replace_kb, on_success=knowledge_installed
                )
            except PackageError as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"knowledge": metadata}

    @app.post("/v1/admin/knowledge/demo", dependencies=[Depends(admin)])
    async def install_demo():
        if demo_source is None:
            raise HTTPException(404, "Демонстрационный пакет отсутствует")

        def install():
            return kb.install(pack_directory(demo_source))

        async with maintenance("База устанавливается только без активного разговора"):
            if kb.installed:
                raise HTTPException(409, "В профиле уже есть база знаний")
            try:
                metadata = await complete_mutation(install, on_success=knowledge_installed)
            except (OSError, PackageError) as exc:
                raise HTTPException(422, "Не удалось установить демонстрационный пакет") from exc
        return {"knowledge": metadata}

    @app.post("/v1/admin/codex-login", dependencies=[Depends(admin)])
    async def login_codex():
        async with maintenance("Вход в Codex возможен только без активного разговора"):
            if settings.value.provider != "codex":
                raise HTTPException(409, "Сначала выберите Codex в настройках")
            provider_proof.unlink(missing_ok=True)
            try:
                await codex_login(settings.value.codex_binary)
            except ProviderError as exc:
                raise HTTPException(503, str(exc)) from exc
            except TimeoutError as exc:
                raise HTTPException(504, "Истекло время входа в Codex. Повторите попытку") from exc
            except OSError as exc:
                raise HTTPException(503, "Не удалось запустить вход в Codex") from exc
            controller.providers.unavailable.pop("codex", None)
        return {"ok": True}

    @app.post("/v1/admin/provider-check", dependencies=[Depends(admin)])
    async def provider_check():
        async with maintenance("Проверка сервиса возможна только без активного разговора"):
            provider_proof.unlink(missing_ok=True)
            try:
                await controller.providers.probe()
            except ProviderError as exc:
                raise HTTPException(503, str(exc)) from exc
            except TimeoutError as exc:
                raise HTTPException(504, "Сервис не ответил вовремя. Повторите проверку") from exc
            except Exception as exc:
                raise HTTPException(503, "Не удалось выполнить проверку сервиса") from exc
            write_private(provider_proof, json.dumps(provider_identity()))
        return {"ok": True}

    @app.post("/v1/admin/provider-reset", dependencies=[Depends(admin)])
    def provider_reset():
        controller.providers.unavailable.clear()
        return {"ok": True}

    @app.websocket("/v1/conversation")
    async def websocket(ws: WebSocket):
        nonlocal client_connected
        supplied = ws.headers.get("authorization", "")
        origin = ws.headers.get("origin")
        # UE 5.6 libwebsockets unconditionally sets a loopback Origin. Browsers
        # cannot supply the native marker or Authorization in WebSocket().
        # Keep HTTP's strict Origin guard and reject every other WS origin.
        native_loopback = (
            origin == "http://127.0.0.1"
            and ws.url.hostname == "127.0.0.1"
            and ws.headers.get("x-decanat-client") == "unreal-desktop"
        )
        if (origin and not native_loopback) or not secrets.compare_digest(
            supplied.encode(), f"Bearer {ipc_token}".encode()
        ):
            await ws.close(code=1008)
            return
        if client_connected:
            await ws.close(code=1013)
            return
        client_connected = True
        await ws.accept()
        queue = controller.subscribe()

        async def send_events():
            while True:
                event = await queue.get()
                await ws.send_text(event.model_dump_json())
                if event.kind == "resync_required":
                    await ws.close(code=1013)
                    return

        sender = asyncio.create_task(send_events())
        try:
            controller.emit("snapshot", **controller.snapshot())
            while True:
                raw = await ws.receive_text()
                if len(raw) > 4_010_000:
                    await ws.close(code=1009)
                    break
                try:
                    command = ClientCommand.model_validate_json(raw)
                    async with controller_lock:
                        if command.type == "face_ready" and command.session_id != controller.session_id:
                            continue  # A late renderer callback must not affect the next student.
                        if (
                            command.type not in {"start"}
                            and command.session_id != controller.session_id
                        ):
                            raise SessionError("Устаревшая сессия")
                        if command.type == "start":
                            if restart_required:
                                raise SessionError(
                                    "Настройки изменены. Сотруднику нужно перезапустить приложение"
                                )
                            if knowledge_error:
                                raise SessionError("База знаний требует настройки сотрудником")
                            if settings.value.speech_enabled and not (
                                audio_agent and audio_agent.ready
                            ):
                                raise SessionError(audio_error or "Аудиоустройство недоступно")
                            if setup_token is not None:
                                step, issue = setup_status()
                                if step != "ready":
                                    raise SessionError(issue)
                                if not command.avatar_configured:
                                    raise SessionError("Локальный аватар требует настройки")
                            controller.start()
                        elif command.type == "finish":
                            await controller.finish()
                        elif command.type == "avatar_ready":
                            controller.avatar_ready()
                        elif command.type == "face_ready":
                            controller.face_ready(command.turn_id)
                        elif command.type == "text":
                            await controller.submit(command.text)
                        elif command.type == "speech_start":
                            await controller.interrupt()
                        elif command.type == "audio_end":
                            pcm = base64.b64decode(command.audio_b64, validate=True)
                            await controller.audio(pcm, command.sample_rate)
                        elif command.type == "playback_progress":
                            controller.playback_progress(command.turn_id, command.played_characters)
                        elif command.type == "playback_finished":
                            controller.playback_finished(command.turn_id)
                        elif command.type == "activity":
                            controller.activity()
                        elif command.type == "sources":
                            controller.activity()
                            controller.emit(
                                "sources", items=[s.model_dump() for s in controller.sources]
                            )
                except (ValidationError, SessionError, binascii.Error) as exc:
                    message = (
                        str(exc)
                        if isinstance(exc, SessionError)
                        else "Некорректная команда приложения"
                    )
                    controller.emit("command_error", message=message)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await sender
            controller.unsubscribe(queue)
            try:
                await cancel_audio_test()
                async with controller_lock:
                    await controller.finish("client_disconnected")
                    controller.avatar_ready()
            finally:
                client_connected = False

    return app
