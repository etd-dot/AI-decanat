import asyncio
import json
import queue
import threading

import httpx
import pytest

from tools.voice_service import VoiceService


@pytest.mark.asyncio
async def test_2d_switches_provider_and_sends_the_key_only_to_credential_storage():
    service = VoiceService.__new__(VoiceService)
    service.events = queue.SimpleQueue()
    service.commands = queue.SimpleQueue()
    service.stopped = threading.Event()
    original = {"provider": "codex", "api_model": "", "api_fallback": False,
                "input_device": "portable USB microphone", "silero_speaker": "aidar"}
    settings = original.copy()
    calls = []

    def respond(request):
        path = request.url.path
        if path == "/v1/health":
            return httpx.Response(200, json={"setup_step": "ready"})
        if path == "/v1/admin/login":
            return httpx.Response(200, json={"admin_token": "staff-test-session"})
        assert request.headers["X-Admin-Token"] == "staff-test-session"
        if path == "/v1/admin/settings":
            if request.method == "GET":
                return httpx.Response(200, json=settings)
            settings.update(json.loads(request.content))
            calls.append(settings["provider"])
            return httpx.Response(200, json={"settings": settings})
        if path == "/v1/admin/api-key":
            assert request.content == b"test-credential"
            calls.append("secure-key-endpoint")
            return httpx.Response(200, json={"stored": True})
        if path == "/v1/admin/provider-check":
            calls.append("checked-" + settings["provider"])
            return httpx.Response(200, json={"ok": True})
        assert path == "/v1/admin/logout"
        service.stopped.set()
        return httpx.Response(200, json={"ok": True})

    service.setup("login", "test-staff-password")
    service.setup("save_provider", {"provider": "api", "api_model": "sample-model", "api_fallback": False})
    service.setup("api_key", "test-credential")
    service.setup("probe")
    service.setup("save_provider", {"provider": "codex", "api_model": "sample-model", "api_fallback": False})
    service.setup("probe")
    service.setup("logout")
    reader = asyncio.create_task(asyncio.Event().wait())
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                    base_url="http://127.0.0.1") as http:
            await asyncio.wait_for(service._commands(http, None, reader, "setup-test"), 2)
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    assert calls == ["api", "secure-key-endpoint", "checked-api", "codex", "checked-codex"]
    assert settings == {**original, "api_model": "sample-model"}
    events = []
    while not service.events.empty():
        events.append(service.events.get_nowait())
    assert events[-1] == ("health", ({"setup_step": "ready"}, False))
    assert "test-credential" not in repr(events)
