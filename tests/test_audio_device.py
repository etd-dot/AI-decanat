import asyncio
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest
from decanat.audio_device import AudioDeviceAgent, stream_devices
from decanat.speech import Audio, EndpointDetector


def test_windows_defaults_use_same_host_wasapi_endpoints_without_capture(monkeypatch):
    monkeypatch.setattr("decanat.audio_device.sys.platform", "win32")
    checks = []
    sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "MME"}, {"name": "Windows WASAPI",
                                                "default_input_device": 18, "default_output_device": 16}],
        check_input_settings=lambda **kw: checks.append(("input", kw)),
        check_output_settings=lambda **kw: checks.append(("output", kw)),
        PortAudioError=RuntimeError,
    )
    assert stream_devices(None, None, sd) == (18, 16)
    assert [kind for kind, _ in checks] == ["input", "output"]
    assert all(kw["samplerate"] == 48000 and kw["channels"] == 1 for _, kw in checks)
    # Explicit staff selection must not be remapped to another endpoint/host.
    assert stream_devices("Staff microphone, MME", "Staff speaker, MME", sd) == (
        "Staff microphone, MME", "Staff speaker, MME",
    )


def test_unsupported_wasapi_format_keeps_portaudio_default(monkeypatch):
    monkeypatch.setattr("decanat.audio_device.sys.platform", "win32")

    def unsupported(**_):
        raise RuntimeError("48 kHz mono not supported")

    sd = SimpleNamespace(
        query_hostapis=lambda: [{"name": "Windows WASAPI", "default_input_device": 18,
                                "default_output_device": 16}],
        check_input_settings=unsupported, PortAudioError=RuntimeError,
    )
    assert stream_devices(None, None, sd) is None


class VAD:
    def __init__(self):
        self.frames = []

    def reset(self):
        pass

    def probability(self, frame):
        self.frames.append(frame.copy())
        return 0.0


def agent_for(controller):
    # No microphone or inference model is opened by logic tests.
    agent = object.__new__(AudioDeviceAgent)
    agent.controller = controller
    agent._frames = asyncio.Queue(maxsize=200)
    agent._closing = agent._failed = False
    agent._lock = threading.Lock()
    agent._pcm = b"queued audio"
    agent._turn_id = "old-turn"
    agent.ready = True
    agent.detector = EndpointDetector()
    agent.vad = VAD()
    return agent


async def test_speech_preparation_cannot_reach_audio_device_before_face_ready(controller):
    class Speech:
        async def synthesize(self, text):
            return Audio(bytes(960), 48000)

    controller.speech = Speech()
    controller.settings.value.speech_enabled = True
    agent = agent_for(controller)
    agent._pcm = b""
    agent._turn_id = None
    agent._queue = controller.subscribe()
    events = controller.subscribe()
    task = asyncio.create_task(agent._events())
    try:
        controller.start()
        controller.avatar_ready()
        await controller.submit("Сколько делают справку?")
        async with asyncio.timeout(5):
            while (prepared := await events.get()).kind != "speech":
                pass
        await asyncio.sleep(0)
        assert agent._pcm == b""
        assert agent._turn_id is None
        assert controller.face_ready(prepared.turn_id)
        await controller._task
        await asyncio.sleep(0)
        assert agent._pcm == bytes(960)
        assert agent._turn_id == prepared.turn_id
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        controller.unsubscribe(agent._queue)
        controller.unsubscribe(events)
        await controller.finish()


async def test_audio_captured_for_previous_student_is_discarded(controller):
    controller.start()
    controller.avatar_ready()
    agent = agent_for(controller)
    agent._enqueue("previous-student", bytes(1024))
    agent._enqueue(controller.session_id, bytes(1024))
    task = asyncio.create_task(agent._recognition())
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert len(agent.vad.frames) == 1
    await controller.finish()


async def test_overflow_discards_broken_utterance_instead_of_splicing_audio(controller):
    agent = agent_for(controller)
    for _ in range(200):
        agent._enqueue("session", b"old")
    agent._enqueue("session", b"new")
    assert agent._frames.qsize() == 2
    assert await agent._frames.get() is None
    assert await agent._frames.get() == ("session", b"new")


async def test_audio_failure_mutes_pending_playback_and_ends_session(controller):
    controller.start()
    controller.avatar_ready()
    agent = agent_for(controller)
    agent._fail()
    agent._fail()
    await asyncio.sleep(0)
    assert not agent.ready
    assert agent._pcm == b""
    assert controller.state.value == "sitting_down"


def test_completed_text_is_reported_after_dac_time_not_when_pcm_is_queued(controller):
    class Loop:
        def __init__(self):
            self.soon, self.later = [], []

        def call_soon_threadsafe(self, callback, *args):
            self.soon.append((callback, args))

        def call_later(self, delay, callback, *args):
            self.later.append((delay, callback, args))

    agent = agent_for(controller)
    agent._pcm = bytes(1920)
    agent._cursor = agent._mark_cursor = 0
    agent._marks = [(480, 9), (960, 20)]
    agent._turn_id = "turn"
    agent.loop = Loop()
    agent.apm = SimpleNamespace(process=lambda *args: bytes(320))
    timing = SimpleNamespace(outputBufferDacTime=10.05, currentTime=10.0, inputBufferAdcTime=9.95)
    agent._callback(bytes(960), bytearray(960), 480, timing, None)
    schedule = [
        (callback, args)
        for callback, args in agent.loop.soon
        if callback == agent._schedule_progress
    ]
    assert len(schedule) == 1
    callback, args = schedule[0]
    callback(*args)
    delay, callback, args = agent.loop.later[0]
    assert delay == pytest.approx(0.06)
    assert callback == controller.playback_progress
    assert args == ("turn", 9)
