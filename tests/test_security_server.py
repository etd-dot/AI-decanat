from datetime import UTC, datetime, timedelta

import pytest
from conftest import EXAMPLES, package
from decanat.contracts import AnswerStatus, GeneratedAnswer
from decanat.providers import ProviderError, validate_answer
from decanat.security import AdminAuth
from decanat.server import create_app
from fastapi.testclient import TestClient

TOKEN = "local-desktop-token-that-is-long-enough"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
SETUP_TOKEN = "one-launch-native-setup-token-that-is-long-enough"
SETUP_HEADERS = {**HEADERS, "X-Setup-Token": SETUP_TOKEN}


@pytest.mark.parametrize(
    ("origin", "marker", "token", "accepted"),
    [
        (None, None, TOKEN, True),
        ("http://127.0.0.1", "unreal-desktop", TOKEN, True),
        ("http://127.0.0.1", None, TOKEN, False),
        ("http://127.0.0.1", "browser", TOKEN, False),
        ("https://other.site", "unreal-desktop", TOKEN, False),
        ("null", "unreal-desktop", TOKEN, False),
        ("http://127.0.0.1", "unreal-desktop", "wrong-token", False),
    ],
)
def test_native_unreal_origin_requires_marker_and_private_token(
    controller, tmp_path, origin, marker, token, accepted,
):
    from starlette.websockets import WebSocketDisconnect

    headers = {"Authorization": f"Bearer {token}"}
    if origin is not None:
        headers["Origin"] = origin
    if marker is not None:
        headers["X-Decanat-Client"] = marker
    with TestClient(create_app(tmp_path, TOKEN, conversation=controller), base_url="http://127.0.0.1") as client:
        if accepted:
            with client.websocket_connect("ws://127.0.0.1/v1/conversation", headers=headers) as ws:
                assert ws.receive_json()["kind"] == "snapshot"
        else:
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect("ws://127.0.0.1/v1/conversation", headers=headers):
                    pytest.fail("Untrusted WebSocket handshake accepted")
            assert rejected.value.code == 1008


def test_native_marker_does_not_bypass_http_origin_guard(controller, tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, conversation=controller), base_url="http://127.0.0.1") as client:
        headers = {**HEADERS, "Origin": "http://127.0.0.1", "X-Decanat-Client": "unreal-desktop"}
        assert client.get("/v1/health", headers=headers).status_code == 403


def test_new_profile_bootstrap_requires_both_secrets_and_cannot_replace_password(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    app = create_app(tmp_path, TOKEN, setup_token=SETUP_TOKEN)
    route = "/v1/setup/password"
    password = {"password": "new-employee-password"}
    with TestClient(app) as client:
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "password"
        assert client.post(route, headers={"X-Setup-Token": SETUP_TOKEN}, json=password).status_code == 401
        assert client.post(route, headers=HEADERS, json=password).status_code == 403
        assert client.post(route, headers={**SETUP_HEADERS, "Origin": "https://example.com"},
                           json=password).status_code == 403
        invalid = "too-long-secret" * 30
        response = client.post(route, headers=SETUP_HEADERS, json={"password": invalid})
        assert response.status_code == 422
        assert invalid not in response.text
        assert not app.state.auth.configured

        with ThreadPoolExecutor(max_workers=2) as workers:
            def initialize(_):
                return client.post(route, headers=SETUP_HEADERS, json=password).status_code
            assert sorted(workers.map(initialize, range(2))) == [200, 409]
        assert client.post(route, headers=SETUP_HEADERS,
                           json={"password": "different-password"}).status_code == 409
        assert client.post("/v1/admin/login", headers=HEADERS, json=password).status_code == 200
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "knowledge"
    assert not (tmp_path / "initial-password.txt").exists()
    with TestClient(create_app(tmp_path, TOKEN)) as client:
        assert client.post(route, headers=SETUP_HEADERS, json=password).status_code == 403


def test_setup_requires_real_provider_probe_and_preserves_progress(controller, tmp_path, monkeypatch):
    from decanat.speech import LocalSpeech

    async def warm_up(_):
        pass

    class ReadyAudio:
        def __init__(self, *_):
            self.ready = False

        async def start(self):
            self.ready = True

        async def close(self):
            self.ready = False

    class WorkingProvider:
        async def generate(self, question, history, evidence):
            assert question == "Назовите код проверки."
            assert history == []
            return GeneratedAnswer(status=AnswerStatus.ANSWER, answer_text="3572",
                                   source_ids=[evidence[0].id])

    monkeypatch.setattr(LocalSpeech, "warm_up", warm_up)
    monkeypatch.setattr("decanat.audio_device.AudioDeviceAgent", ReadyAudio)
    monkeypatch.setattr(controller.settings, "set_api_key", lambda key: None)
    controller.settings.save(controller.settings.value.model_copy(update={
        "speech_enabled": True, "api_fallback": True, "api_model": "test-model",
    }))
    app = create_app(tmp_path, TOKEN, conversation=controller, setup_token=SETUP_TOKEN)
    app.state.auth.set_password("current-employee-password")
    with TestClient(app) as client:
        assert client.post("/v1/admin/provider-check", headers=HEADERS).status_code == 401
        staff = {**HEADERS, "X-Admin-Token": app.state.auth.login("current-employee-password")}
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "provider"
        controller.providers.adapters["api"] = WorkingProvider()
        assert client.post("/v1/admin/provider-check", headers=staff).status_code == 503
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "provider"
        with client.websocket_connect("/v1/conversation", headers=HEADERS) as ws:
            assert ws.receive_json()["kind"] == "snapshot"
            ws.send_json({"type": "start", "avatar_configured": True})
            assert ws.receive_json()["kind"] == "command_error"
        settings = client.get("/v1/admin/settings", headers=staff).json()
        settings["provider"] = "api"
        assert client.put("/v1/admin/settings", headers=staff, json=settings).status_code == 200
        assert client.post("/v1/admin/provider-check", headers=staff).status_code == 200
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "ready"
        assert controller.journal.list() == []

    restarted = create_app(tmp_path, TOKEN, conversation=controller, setup_token=SETUP_TOKEN)
    with TestClient(restarted) as client:
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "ready"
        with client.websocket_connect("/v1/conversation", headers=HEADERS) as ws:
            assert ws.receive_json()["kind"] == "snapshot"
            ws.send_json({"type": "start"})
            assert ws.receive_json()["kind"] == "command_error"
            ws.send_json({"type": "start", "avatar_configured": True})
            assert ws.receive_json()["payload"]["state"] == "standing_up"
        staff = {**HEADERS, "X-Admin-Token": restarted.state.auth.login("current-employee-password")}
        assert client.post("/v1/admin/api-key", headers=staff, content="new-key").status_code == 200
        assert client.get("/v1/health", headers=HEADERS).json()["setup_step"] == "provider"


def test_demo_knowledge_requires_staff_and_explicit_selection(tmp_path):
    app = create_app(tmp_path, TOKEN, setup_token=SETUP_TOKEN, demo_source=EXAMPLES)
    app.state.auth.set_password("current-employee-password")
    with TestClient(app) as client:
        demo = "/v1/admin/knowledge/demo"
        assert not client.get("/v1/health", headers=HEADERS).json()["kb_installed"]
        assert client.post(demo, headers=HEADERS).status_code == 401
        staff = {**HEADERS, "X-Admin-Token": app.state.auth.login("current-employee-password")}
        assert client.post(demo, headers=staff).status_code == 200
        assert client.get("/v1/health", headers=HEADERS).json()["demo_knowledge"]
        assert client.post(demo, headers=staff).status_code == 409


def test_hash_password_expiry_and_rate_limit(tmp_path):
    from conftest import Clock

    clock = Clock()
    auth = AdminAuth(tmp_path, clock)
    auth.set_password("long-test-password")
    assert "long-test-password" not in auth.path.read_text()
    token = auth.login("long-test-password")
    assert auth.check(token)
    clock.value += 1801
    assert not auth.check(token)
    for _ in range(5):
        assert auth.login("wrong") is None
    with pytest.raises(ValueError, match="пять"):
        auth.login("long-test-password")


def test_journal_retention_is_not_session_context(controller):
    old = datetime.now(UTC) - timedelta(days=31)
    controller.journal.record(
        turn_id="old",
        session_id="session",
        question="вопрос",
        answer="ответ",
        status="answer",
        provider="api",
        kb_id="kb",
        release_id="one",
        sources=[],
        created_at=old,
    )
    assert controller.journal.purge() == 1
    assert controller.journal.list() == []


def test_journal_pagination_preserves_interruption_and_sources(controller, tmp_path):
    now = datetime.now(UTC)
    evidence = controller.kb.search("справка")
    for turn_id, timestamp in (("old", now - timedelta(days=31)), ("a", now), ("b", now)):
        controller.journal.record(
            turn_id=turn_id, session_id="student", question="Где взять справку?",
            answer="Полный ответ", status="answer", provider="codex", kb_id="kb",
            release_id="first", sources=evidence, created_at=timestamp,
        )
    controller.journal.playback("b", "Полный", interrupted=True)
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        staff = {**HEADERS, "X-Admin-Token": app.state.auth.login("employee-password")}
        route = "/v1/admin/journal?limit=1"
        assert client.get(route, headers=HEADERS).status_code == 401
        page = client.get(route, headers=staff).json()
        row, = page["items"]
        assert row["turn_id"] == "b"
        assert row["spoken_text"] == "Полный" and row["interrupted"]
        assert row["sources"] == [item.model_dump(mode="json") for item in evidence]
        assert page["has_more"]
        page = client.get(route + "&offset=1", headers=staff).json()
        assert [row["turn_id"] for row in page["items"]] == ["a"]
        assert not page["has_more"]


async def test_audio_test_is_staff_only_and_logout_cancels_it(controller, tmp_path, monkeypatch):
    import asyncio

    from decanat.speech import LocalSpeech
    from httpx import ASGITransport, AsyncClient

    entered = asyncio.Event()

    async def warm_up(_):
        pass

    class Audio:
        def __init__(self, *_):
            self.ready = False
            self.testing = False

        async def start(self):
            self.ready = True

        async def close(self):
            self.ready = False

        async def test_input(self):
            self.testing = True
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.testing = False

    monkeypatch.setattr(LocalSpeech, "warm_up", warm_up)
    monkeypatch.setattr("decanat.audio_device.AudioDeviceAgent", Audio)
    controller.settings.value.speech_enabled = True
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    staff = {**HEADERS, "X-Admin-Token": app.state.auth.login("employee-password")}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        route = "/v1/admin/audio-test/input"
        assert (await client.post(route, headers=HEADERS)).status_code == 401
        pending = asyncio.create_task(client.post(route, headers=staff))
        await asyncio.wait_for(entered.wait(), 2)
        assert controller.maintenance
        assert (await client.post(route, headers=staff)).status_code == 409
        assert (await client.post("/v1/admin/logout", headers=staff)).status_code == 200
        assert (await asyncio.wait_for(pending, 2)).status_code == 409
        assert not controller.maintenance
        assert controller.journal.list() == []


def test_provider_cannot_invent_citations(kb):
    answer = GeneratedAnswer(
        status=AnswerStatus.ANSWER, answer_text="Утверждение", source_ids=["imaginary/source"]
    )
    with pytest.raises(ProviderError, match="источники"):
        validate_answer(answer, kb.search("справка"))


def test_provider_factual_answer_requires_citation(kb):
    answer = GeneratedAnswer(status=AnswerStatus.ANSWER, answer_text="Утверждение", source_ids=[])
    with pytest.raises(ProviderError):
        validate_answer(answer, kb.search("справка"))


def test_http_auth_admin_and_maintenance(controller, tmp_path):
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 401
        assert (
            client.get(
                "/v1/health", headers={**HEADERS, "Origin": "https://other.site"}
            ).status_code
            == 403
        )
        assert client.get("/v1/admin/journal", headers=HEADERS).status_code == 401
        response = client.post(
            "/v1/admin/login", headers=HEADERS, json={"password": "employee-password"}
        )
        admin = {**HEADERS, "X-Admin-Token": response.json()["admin_token"]}
        assert client.get("/v1/admin/journal", headers=admin).status_code == 200
        controller.start()
        assert (
            client.post("/v1/admin/knowledge", headers=admin, content=package()).status_code == 409
        )
        controller.history.clear()
        controller.session_id = None
        from decanat.contracts import State

        controller.state = State.IDLE
        assert (
            client.post("/v1/admin/knowledge", headers=admin, content=b"broken").status_code == 422
        )
        assert not controller.maintenance
        assert (
            client.post("/v1/admin/knowledge", headers=admin, content=package()).status_code == 200
        )
        assert client.post("/v1/admin/logout", headers=admin).status_code == 200
        assert client.get("/v1/admin/settings", headers=admin).status_code == 401


def test_websocket_session_protocol_and_clear(controller, tmp_path):
    app = create_app(tmp_path, TOKEN, conversation=controller)
    with (
        TestClient(app) as client,
        client.websocket_connect("/v1/conversation", headers=HEADERS) as ws,
    ):
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "snapshot"
        ws.send_json({"type": "start"})
        standing = ws.receive_json()
        assert standing["payload"]["state"] == "standing_up"
        session = standing["session_id"]
        ws.receive_json()  # stand animation command
        ws.send_json({"type": "avatar_ready", "session_id": session})
        assert ws.receive_json()["payload"]["state"] == "listening"
        ws.send_json({"type": "text", "session_id": "old-student", "text": "справка"})
        assert ws.receive_json()["kind"] == "command_error"
        ws.send_json({"type": "finish", "session_id": session})
        events = [ws.receive_json() for _ in range(4)]
        assert any(e["kind"] == "cleared" for e in events)


def test_pending_audio_configuration_blocks_new_session_until_restart(controller, tmp_path):
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        token = client.post(
            "/v1/admin/login", headers=HEADERS, json={"password": "employee-password"}
        ).json()["admin_token"]
        admin = {**HEADERS, "X-Admin-Token": token}
        body = client.get("/v1/admin/settings", headers=admin).json()
        body["input_device"] = "new microphone"
        response = client.put("/v1/admin/settings", headers=admin, json=body)
        assert response.status_code == 200
        assert response.json()["restart_required"]
        assert client.get("/v1/health", headers=HEADERS).json()["restart_required"]
        with client.websocket_connect("/v1/conversation", headers=HEADERS) as ws:
            ws.receive_json()
            ws.send_json({"type": "start"})
            assert "перезапустить" in ws.receive_json()["payload"]["message"]
            assert controller.session_id is None
        # Saving another setting must not clear an outstanding restart requirement.
        body["provider"] = "api"
        assert client.put("/v1/admin/settings", headers=admin, json=body).json()["restart_required"]


def test_diagnostics_requires_staff_and_omits_secrets(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "decanat.audio_device.device_inventory", lambda: {"available": True, "devices": []}
    )
    monkeypatch.setattr(controller.settings, "api_key", lambda: "not-for-diagnostics")
    app = create_app(tmp_path, TOKEN, conversation=controller)
    app.state.auth.set_password("employee-password")
    with TestClient(app) as client:
        assert client.get("/v1/admin/diagnostics", headers=HEADERS).status_code == 401
        token = client.post(
            "/v1/admin/login", headers=HEADERS, json={"password": "employee-password"}
        ).json()["admin_token"]
        response = client.get("/v1/admin/diagnostics", headers={**HEADERS, "X-Admin-Token": token})
        assert response.json()["api_key_configured"]
        assert "not-for-diagnostics" not in response.text


def test_login_rate_limit_survives_concurrent_http_workers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    auth = AdminAuth(tmp_path)
    auth.set_password("employee-password")

    def attempt(_):
        try:
            assert auth.login("wrong-password") is None
            return "rejected"
        except ValueError:
            return "limited"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(12)))
    assert results.count("rejected") == 5
    assert results.count("limited") == 7


async def test_cancelled_upload_keeps_maintenance_until_mutating_thread_finishes(
    controller, tmp_path, monkeypatch
):
    import asyncio
    import threading
    from types import SimpleNamespace

    entered, release = threading.Event(), threading.Event()
    original = controller.kb.install

    def slow_install(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    async def stream():
        yield package()

    monkeypatch.setattr(controller.kb, "install", slow_install)
    queue = controller.subscribe()
    app = create_app(tmp_path, TOKEN, conversation=controller)
    endpoint = next(
        r.endpoint for r in app.routes if r.path == "/v1/admin/knowledge" and "POST" in r.methods
    )
    request = asyncio.create_task(endpoint(SimpleNamespace(stream=stream)))
    assert await asyncio.to_thread(entered.wait, 1)
    request.cancel()
    await asyncio.sleep(0)
    assert controller.maintenance
    with pytest.raises(ValueError, match="обслуживании"):
        controller.start()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not controller.maintenance
    assert controller.kb.search("справка")
    events = []
    while not queue.empty():
        events.append(queue.get_nowait().kind)
    assert "knowledge_updated" in events
    controller.unsubscribe(queue)
