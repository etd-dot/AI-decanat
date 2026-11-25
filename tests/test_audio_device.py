import asyncio
import sys
import threading
import time
from contextlib import suppress
from types import SimpleNamespace

import pytest
from decanat.audio_device import (
    AudioDeviceAgent,
    device_inventory,
    stream_devices,
    validate_audio_selection,
)
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


def test_explicit_endpoint_uses_default_peer_from_the_same_driver(monkeypatch):
    def query_device(name, _kind):
        return {"hostapi": 1 if name == "Speaker, MME" else 0}

    sd = SimpleNamespace(
        query_devices=query_device,
        query_hostapis=lambda _: {"default_input_device": 7, "default_output_device": 8},
        check_input_settings=lambda **_kw: None,
        check_output_settings=lambda **_kw: None,
        PortAudioError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    assert stream_devices("USB mic, WASAPI", "", sd) == ("USB mic, WASAPI", 8)
    assert stream_devices("", "Speaker, WASAPI", sd) == (7, "Speaker, WASAPI")
    validate_audio_selection("USB mic, WASAPI", "")
    with pytest.raises(ValueError, match="одним аудиодрайвером"):
        validate_audio_selection("USB mic, WASAPI", "Speaker, MME")


def test_inventory_flags_indistinguishable_devices_without_opening_stream(monkeypatch):
    mic = {"name": "USB", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0,
           "default_samplerate": 48000}
    module = SimpleNamespace(
        query_hostapis=lambda: [{"name": "WASAPI"}, {"name": "MME"}],
        query_devices=lambda: [mic, mic, {**mic, "hostapi": 1}],
    )
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    devices = device_inventory()["devices"]
    assert [device["selectable_input"] for device in devices] == [False, False, True]
    assert not any(device["selectable_output"] for device in devices)


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
    agent._input_peak = None
    agent._output_test = None
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
    before = time.monotonic()
    agent._callback(bytes(960), bytearray(960), 480, timing, None)
    after = time.monotonic()
    started = [args for callback, args in agent.loop.soon if callback == controller.playback_clock]
    assert len(started) == 1
    turn, starts_at = started[0]
    assert turn == "turn"
    assert before + .049 <= starts_at <= after + .051
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


async def test_staff_audio_diagnostics_use_same_stream_and_clear_buffers(controller):
    agent = agent_for(controller)
    aec = []

    def echo(captured, rendered, _delay):
        aec.append(rendered)
        return bytes(320)

    agent.loop = asyncio.get_running_loop()
    agent.apm = SimpleNamespace(process=echo)
    agent._pcm = b""
    agent._cursor = 0
    agent._turn_id = None
    timing = SimpleNamespace(outputBufferDacTime=10, currentTime=10, inputBufferAdcTime=10)
    output = bytearray(960)
    microphone = asyncio.create_task(agent.test_input(0.05))
    await asyncio.sleep(0)
    agent._callback(b"\x00\x80" * 480, output, 480, timing, None)
    assert await microphone == {"peak": 1, "signal_detected": True}
    assert agent._input_peak is None
    assert bytes(output) == bytes(960)

    tone = b"\x01\x00" * 480
    speaker = asyncio.create_task(agent.test_output(tone))
    await asyncio.sleep(0)
    agent._callback(bytes(960), output, 480, timing, None)
    assert bytes(output) == aec[-1] == tone
    assert not speaker.done()
    await asyncio.wait_for(speaker, 1)
    assert agent._pcm == b""
    assert controller.journal.list() == []

    speaker = asyncio.create_task(agent.test_output(tone))
    await asyncio.sleep(0)
    speaker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await speaker
    assert agent._pcm == b""
