import asyncio

import pytest
from decanat.contracts import State
from decanat.providers import ProviderError


async def complete_turn(controller, question="Сколько делают справку?"):
    events = controller.subscribe()
    try:
        await controller.submit(question)
        if controller.speech and controller.settings.value.speech_enabled:
            async with asyncio.timeout(5):
                while (event := await events.get()).kind != "speech":
                    pass
            assert controller.face_ready(event.turn_id)
        await controller._task
    finally:
        controller.unsubscribe(events)


async def test_new_session_stands_before_first_answer(controller):
    controller.start()
    assert controller.state == State.STANDING_UP
    assert not controller.history
    controller.avatar_ready()
    await complete_turn(controller)
    assert controller.answer.source_ids
    await controller.finish()


async def test_new_student_never_gets_previous_context(controller):
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller)
    await controller.finish()
    assert controller.history == []
    assert controller.sources == []
    controller.avatar_ready()
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller, "Когда работает деканат?")
    assert controller.providers.adapters["codex"].calls[-1][1] == []
    assert len(controller.journal.list()) == 2
    await controller.finish()


async def test_60_plus_15_timer_excludes_thinking(controller):
    controller.start()
    controller.avatar_ready()
    controller.clock.value += 60
    await controller.tick()
    assert controller.warning
    controller.activity()
    assert not controller.warning
    controller._state(State.THINKING)
    controller.clock.value += 1000
    await controller.tick()
    assert controller.state == State.THINKING
    controller._state(State.LISTENING)
    controller.activity()
    controller.clock.value += 75
    await controller.tick()
    assert controller.state == State.SITTING_DOWN


async def test_known_limit_keeps_avatar_seated(controller):
    controller.providers.unavailable["codex"] = ProviderError("quota_exhausted", "Лимит исчерпан")
    with pytest.raises(ValueError):
        controller.start()
    assert controller.state == State.IDLE
    assert controller.session_id is None


async def test_limit_during_dialogue_sits_and_clears(controller):
    class Limited:
        async def generate(self, *args):
            raise ProviderError("quota_exhausted", "Лимит исчерпан")

    controller.providers.adapters["codex"] = Limited()
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller)
    assert controller.state == State.SITTING_DOWN
    assert not controller.history
    assert controller.journal.list()[0]["status"] == "quota_exhausted"


async def test_fallback_disabled_by_default_and_opt_in_preserves_context(controller):
    class Limited:
        async def generate(self, *args):
            raise ProviderError("quota_exhausted", "Лимит")

    assert not controller.settings.value.api_fallback
    controller.settings.value.api_fallback = True
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller)
    controller.providers.adapters["codex"] = Limited()
    await complete_turn(controller, "А когда работает деканат?")
    assert controller.state == State.LISTENING
    assert controller.journal.list()[0]["provider"] == "api"
    assert controller.providers.adapters["api"].calls[-1][1]
    await controller.finish()


async def test_late_provider_result_cannot_revive_cancelled_turn(controller):
    started = asyncio.Event()

    class Slow:
        async def generate(self, *args):
            started.set()
            try:
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                # Model an uncooperative adapter returning after cancellation.
                return await original.generate(*args)

    original = controller.providers.adapters["codex"]
    controller.providers.adapters["codex"] = Slow()
    controller.start()
    controller.avatar_ready()
    await controller.submit("Сколько делают справку?")
    await asyncio.wait_for(started.wait(), 5)
    await controller.finish()
    assert controller.answer is None
    assert controller.history == []
    assert controller.journal.list() == []


async def test_interruption_tracks_only_spoken_prefix(controller):
    from decanat.speech import Audio

    class Speech:
        async def synthesize(self, text):
            return Audio(b"\x00\x00" * 160, 16000)

    controller.speech = Speech()
    controller.settings.value.speech_enabled = True
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller)
    assert controller.state == State.SPEAKING
    old_turn = controller.turn_id
    controller.playback_progress(old_turn, 7)
    await controller.interrupt()
    assert controller.history[-1].interrupted
    assert controller.history[-1].spoken_text == controller.history[-1].text[:7]
    assert controller.journal.list()[0]["interrupted"]
    controller.playback_finished(old_turn)
    assert controller.state == State.LISTENING
    await controller.finish()


async def test_missing_evidence_never_invokes_provider(controller):
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller, "квазипротонные сверхструны")
    assert controller.answer.status.value == "insufficient_evidence"
    assert not controller.providers.adapters["codex"].calls
    await controller.finish()


async def test_unrecognised_question_is_clarified_by_local_voice_without_provider(controller):
    from decanat.speech import Audio

    class Speech:
        async def transcribe(self, pcm, rate):
            return ""

        async def synthesize(self, text):
            return Audio(bytes(4800 * 2), 48000)

    controller.speech = Speech()
    controller.settings.value.speech_enabled = True
    controller.start()
    controller.avatar_ready()
    events = controller.subscribe()
    await controller.audio(bytes(16000 * 2), 16000)
    async with asyncio.timeout(5):
        while (event := await events.get()).kind != "speech":
            pass
    assert controller.face_ready(event.turn_id)
    await controller._task
    controller.unsubscribe(events)
    assert controller.state == State.SPEAKING
    assert controller.answer.status.value == "clarify"
    assert controller.answer.source_ids == []
    assert not controller.providers.adapters["codex"].calls
    assert controller.journal.list()[0]["provider"] == "local_clarification"
    assert controller.journal.list()[0]["question"] == ""
    await controller.finish()


async def test_audio_waits_for_matching_face_ready_before_speaking(controller):
    from decanat.speech import Audio

    class Speech:
        async def synthesize(self, text):
            return Audio(bytes(960), 48000)

    controller.speech = Speech()
    controller.settings.value.speech_enabled = True
    controller.start()
    controller.avatar_ready()
    events = controller.subscribe()
    await controller.submit("Сколько делают справку?")
    async with asyncio.timeout(5):
        while (prepared := await events.get()).kind != "speech":
            pass
    assert controller.state == State.THINKING
    assert not controller.face_ready("previous-turn")
    assert events.empty()
    assert controller.face_ready(prepared.turn_id)
    assert not controller.face_ready(prepared.turn_id)
    await controller._task
    delivered = []
    while not events.empty():
        delivered.append(events.get_nowait())
    assert controller.state == State.SPEAKING
    assert [e.kind for e in delivered].count("playback_start") == 1
    assert next(e for e in delivered if e.kind == "playback_start").turn_id == prepared.turn_id
    await controller.finish()


async def test_cancelled_face_preparation_never_plays_or_unlocks_next_turn(controller):
    from decanat.speech import Audio

    class Speech:
        async def synthesize(self, text):
            return Audio(bytes(960), 48000)

    controller.speech = Speech()
    controller.settings.value.speech_enabled = True
    controller.start()
    controller.avatar_ready()
    events = controller.subscribe()
    await controller.submit("Сколько делают справку?")
    async with asyncio.timeout(5):
        while (old := await events.get()).kind != "speech":
            pass
    await controller.interrupt()
    assert controller.history[-1].interrupted
    assert controller.history[-1].spoken_text == ""
    assert not controller.face_ready(old.turn_id)
    await controller.submit("Где получить справку?")
    async with asyncio.timeout(5):
        while (new := await events.get()).kind != "speech":
            assert new.kind != "playback_start"
    assert not controller.face_ready(old.turn_id)
    assert controller.state == State.THINKING
    assert controller.face_ready(new.turn_id)
    await controller._task
    playback = []
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "playback_start":
            playback.append(event.turn_id)
    assert playback == [new.turn_id]
    await controller.finish()


async def test_face_timeout_finishes_without_audible_playback(controller):
    from decanat.speech import Audio

    class Speech:
        async def synthesize(self, text):
            return Audio(bytes(960), 48000)

    controller.speech = Speech()
    controller.settings.value.speech_enabled = True
    controller.settings.value.face_prepare_timeout = 1
    controller.start()
    controller.avatar_ready()
    events = controller.subscribe()
    await controller.submit("Сколько делают справку?")
    await controller._task
    delivered = []
    while not events.empty():
        delivered.append(events.get_nowait())
    assert controller.state == State.SITTING_DOWN
    assert not any(e.kind == "playback_start" for e in delivered)
    assert any(e.kind == "error" and e.payload["code"] == "face_timeout" for e in delivered)
    assert controller.journal.list()[0]["status"] == "face_timeout"


async def test_known_codex_quota_uses_opted_in_api_without_retrying_codex(controller):
    class MustNotCall:
        async def generate(self, *args):
            pytest.fail("Known exhausted allowance must not be retried until reset")

    controller.providers.adapters["codex"] = MustNotCall()
    controller.providers.unavailable["codex"] = ProviderError("quota_exhausted", "Лимит")
    controller.settings.value.api_fallback = True
    controller.start()
    controller.avatar_ready()
    await complete_turn(controller)
    assert controller.answer is not None
    assert controller.journal.list()[0]["provider"] == "api"
    await controller.finish()


async def test_old_submit_waiting_for_cancellation_cannot_enter_new_session(controller):
    started = asyncio.Event()
    cancelled, release = asyncio.Event(), asyncio.Event()
    original = controller.providers.adapters["codex"]

    class SlowCleanup:
        async def generate(self, *args):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return await original.generate(*args)

    controller.providers.adapters["codex"] = SlowCleanup()
    controller.start()
    controller.avatar_ready()
    await controller.submit("Сколько делают справку?")
    await asyncio.wait_for(started.wait(), 5)
    stale = asyncio.create_task(controller.submit("Мой предыдущий вопрос"))
    await asyncio.wait_for(cancelled.wait(), 5)
    await controller.finish()
    controller.avatar_ready()
    controller.start()
    new_session = controller.session_id
    release.set()
    with pytest.raises(ValueError, match="отменена"):
        await stale
    assert controller.session_id == new_session
    assert controller.state == State.STANDING_UP
    assert controller.history == []
    await controller.finish()


async def test_finish_cleanup_never_clears_a_new_student(controller):
    started = asyncio.Event()
    cancelled, release = asyncio.Event(), asyncio.Event()
    original = controller.providers.adapters["codex"]

    class SlowCleanup:
        async def generate(self, *args):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return await original.generate(*args)

    controller.providers.adapters["codex"] = SlowCleanup()
    controller.start()
    controller.avatar_ready()
    await controller.submit("Сколько делают справку?")
    await asyncio.wait_for(started.wait(), 5)
    finishing = asyncio.create_task(controller.finish())
    await asyncio.wait_for(cancelled.wait(), 5)
    assert controller.state == State.SITTING_DOWN
    assert controller.history == []
    controller.avatar_ready()
    controller.start()
    new_session = controller.session_id
    release.set()
    await finishing
    assert controller.state == State.STANDING_UP
    assert controller.session_id == new_session
    await controller.finish()
