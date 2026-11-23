"""Exercise the real Windows renderer and IPC with an isolated Conversation fixture.

Answers and the inactivity clock are controlled test inputs. By default speech
and face readiness are controlled too. --real-face requires native ACE; adding
--play-output measures a real output stream without opening a microphone.
This does not verify LLM quality or the student UI readiness gate.
No test endpoint is added to the production server. No real account is contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import secrets
import socket
import tempfile
import time
import wave
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import uvicorn
from decanat.config import SettingsStore, write_private
from decanat.contracts import AnswerStatus, GeneratedAnswer, State
from decanat.journal import Journal
from decanat.knowledge import KnowledgeStore
from decanat.providers import ProviderError, Providers
from decanat.server import create_app
from decanat.session import Conversation, SessionError
from decanat.speech import Audio

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


class ControlledAnswer:
    quota = False

    async def generate(self, question, history, evidence):
        if self.quota:
            raise ProviderError("quota_exhausted", "Controlled quota exhaustion")
        return GeneratedAnswer(
            status=AnswerStatus.ANSWER,
            answer_text="Справка готовится три рабочих дня.",
            source_ids=[evidence[0].id],
        )


class SilentSpeech:
    def __init__(self, real_face=False):
        self.real_face = real_face

    async def synthesize(self, text):
        if self.real_face:
            with wave.open(str(ROOT / "assets/preview/voice/aidar-ru.wav"), "rb") as source:
                pcm = source.readframes(source.getnframes())
                return Audio(pcm, source.getframerate(), ((source.getnframes(), len(text)),))
        return Audio(bytes(16000 * 2 * 5), 16000, ((16000 * 5, len(text)),))


class WireTap:
    """Observe the actual native client's acknowledgements without changing commands."""

    def __init__(self, app, controller):
        self.app, self.controller = app, controller
        self.acknowledgements = []
        self.face_acknowledgements = []
        self.connected = False
        self.close_send = None
        self.origins = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.app(scope, receive, send)
        self.connected = True
        self.origins.append(dict(scope["headers"]).get(b"origin", b"").decode("ascii", "replace"))
        self.close_send = send

        async def observed_receive():
            message = await receive()
            if message["type"] == "websocket.receive" and message.get("text"):
                command = json.loads(message["text"])
                if command.get("type") == "avatar_ready":
                    self.acknowledgements.append({
                        "session_id": command.get("session_id"),
                        "backend_state": self.controller.state.value,
                    })
                elif command.get("type") == "face_ready":
                    self.face_acknowledgements.append({
                        "session_id": command.get("session_id"), "turn_id": command.get("turn_id"),
                        "backend_state": self.controller.state.value,
                        "pending_turn": self.controller._face_pending[0] if self.controller._face_pending else None,
                        "received_monotonic": time.monotonic(),
                    })
            return message

        try:
            return await self.app(scope, observed_receive, send)
        finally:
            self.connected = False
            self.close_send = None


class Probe:
    def __init__(self, log, process):
        self.log, self.process = log, process
        self.records = []
        self.offset = 0
        self.partial = b""

    def read(self):
        if self.log.exists():
            with self.log.open("rb") as stream:
                stream.seek(self.offset)
                chunk = self.partial + stream.read()
                self.offset = stream.tell()
            lines = chunk.split(b"\n")
            self.partial = lines.pop()
            for line in lines:
                if b"DECANAT_MOTION " in line:
                    self.records.append(json.loads(line.split(b"DECANAT_MOTION ", 1)[1]))

    async def until(self, predicate, description, *, seconds=25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.read()
            if predicate():
                return
            if self.process.returncode is not None:
                raise RuntimeError(f"Game exited ({self.process.returncode}): {description}")
            await asyncio.sleep(0.1)
        raise TimeoutError(description)

    def observed(self, start, animation, *, state=None, connected=None):
        return any(
            r["phase"] == "sample" and r["animation"] == animation
            and r["position"] > 0.1
            and (state is None or r["state"] == state)
            and (connected is None or r["connected"] == connected)
            for r in self.records[start:]
        )


async def run(executable, directory, report, *, real_face=False, play_output=False, cancel_preparation=False):
    settings = SettingsStore(directory)
    # Startup has no physical audio agent. The explicitly controlled speech
    # fixture is enabled only after startup, for body-state coverage.
    settings.save(settings.value.model_copy(update={"dense_enabled": False}))
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        for name in ("manifest.json", "documents.jsonl"):
            package.write(ROOT / "examples/knowledge" / name, name)
    kb = KnowledgeStore(directory)
    kb.install(archive.getvalue())
    answer, clock = ControlledAnswer(), Clock()
    providers = Providers(settings, codex=answer, api=answer)
    controller = Conversation(
        settings, kb, providers, Journal(directory), speech=SilentSpeech(real_face), clock=clock,
    )
    token = secrets.token_urlsafe(48)
    app = create_app(directory, token, conversation=controller)
    wire = WireTap(app, controller)
    report["native_origins"] = wire.origins
    report["native_face_acknowledgements"] = wire.face_acknowledgements
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(wire, log_level="error", access_log=False))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    audio_output = None
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("Test server did not start")
                await asyncio.sleep(0.05)
        settings.value.speech_enabled = True
        if play_output:
            from diagnostic_audio_output import DiagnosticAudioOutput

            audio_output = DiagnosticAudioOutput(controller, ROOT)
            report["output_device"] = audio_output.device
            report["output_dac_starts"] = audio_output.first_dac
            report["microphone_opened"] = False
            await audio_output.start()
        connection = directory / "connection.json"
        write_private(connection, json.dumps({"url": f"http://127.0.0.1:{port}", "token": token}))
        environment = {**os.environ, "DECANAT_CONNECTION_FILE": str(connection)}
        log = directory / "game.log"
        with (directory / "console.log").open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                str(executable), "-unattended", "-windowed", "-ResX=1280", "-ResY=720",
                "-nosplash", "-d3d12", "-corelimit=4", "-DecanatMotionTrace", f"-abslog={log}",
                env=environment, stdout=output, stderr=asyncio.subprocess.STDOUT,
            )
        probe = Probe(log, process)
        report["game_log"] = str(log)
        report["trace"] = probe.records
        report["acknowledgements"] = wire.acknowledgements
        await probe.until(
            lambda: wire.connected and probe.observed(0, "AS_SitIdle", connected=True),
            "Initial seated pose and IPC connection", seconds=120,
        )

        async def stand():
            start = len(probe.records)
            session = controller.start()
            await probe.until(
                lambda: controller.state == State.LISTENING
                and probe.observed(start, "AS_StandIdle", state="listening"),
                "Stand transition acknowledged by the actual renderer",
            )
            if not probe.observed(start, "AS_Stand"):
                raise AssertionError("Standing transition was not sampled")
            return session

        async def seated(start):
            def fresh_idle_after_sit():
                completed = [i for i, r in enumerate(probe.records[start:], start)
                             if r["phase"] == "complete" and r["animation"] == "AS_Sit"]
                return bool(completed) and probe.observed(completed[-1] + 1, "AS_SitIdle", state="idle")

            await probe.until(
                lambda: controller.state == State.IDLE and fresh_idle_after_sit(),
                "Sit transition acknowledged by the actual renderer",
            )
            if not probe.observed(start, "AS_Sit"):
                raise AssertionError("Sitting transition was not sampled")
            if controller.history or controller.session_id:
                raise AssertionError("Previous session was not cleared")

        def passed(name, start):
            probe.read()
            report["scenarios"].append({
                "name": name, "passed": True, "trace_start": start,
                "trace_end": len(probe.records),
            })
            print(f"PASS {name}", flush=True)

        start = len(probe.records)
        await stand()
        await controller.finish()
        await seated(start)
        passed("start_and_user_finish", start)

        start = len(probe.records)
        await stand()
        clock.value += settings.value.idle_seconds
        await controller.tick()
        if not controller.warning or controller.state != State.LISTENING:
            raise AssertionError("Inactivity warning changed the standing state")
        clock.value += settings.value.warning_seconds
        await controller.tick()
        await seated(start)
        passed("inactivity_timeout_60_plus_15", start)

        cancelled_preparations = []
        if cancel_preparation:
            start = len(probe.records)
            await stand()
            for delay in (.01, .05, .2):
                await controller.submit("Сколько делают справку?")
                await probe.until(lambda: controller._face_pending is not None,
                                  "Wait until native facial preparation is requested")
                turn = controller.turn_id
                await asyncio.sleep(delay)
                if controller._face_pending is None:
                    raise AssertionError("Preparation completed before its cancellation probe")
                if not await controller.interrupt():
                    raise AssertionError("Cancellation before face_ready was refused")
                cancelled_preparations.append(turn)
            await asyncio.sleep(1)
            if any(item["turn_id"] in cancelled_preparations for item in audio_output.first_dac):
                raise AssertionError("Cancelled facial preparation reached the audible output")
            await controller.finish()
            await seated(start)
            report["cancelled_preparation_turns"] = cancelled_preparations
            passed("cancel_before_face_ready_has_no_audible_playback", start)

        if play_output:
            start = len(probe.records)
            await stand()
            await controller.submit("Сколько делают справку?")
            async with asyncio.timeout(90):
                await controller._task
            await probe.until(
                lambda: controller.state == State.LISTENING
                and probe.observed(start, "AS_StandIdle", state="listening"),
                "The actual output device finishes the PCM and returns to listening", seconds=20,
            )
            await controller.finish()
            await seated(start)
            passed("full_generated_voice_on_actual_output", start)

        start = len(probe.records)
        await stand()
        await controller.submit("Сколько делают справку?")
        await probe.until(lambda: controller._face_pending is not None,
                          "Controlled body-only fixture waits for facial preparation")
        if not real_face:
            controller.face_ready(controller.turn_id)
        async with asyncio.timeout(90):
            await controller._task
        await probe.until(
            lambda: probe.observed(start, "AS_StandIdle", state="speaking"),
            "Standing while speaking", seconds=10,
        )
        if play_output:
            await asyncio.sleep(2.5)
        interrupted = len(probe.records)
        interrupted_qpc = time.perf_counter()
        if not await controller.interrupt():
            raise AssertionError("Interruption was refused")
        await probe.until(
            lambda: probe.observed(interrupted, "AS_StandIdle", state="listening"),
            "Interruption must leave the character standing", seconds=10,
        )
        await asyncio.sleep(1)
        probe.read()
        if any(r["animation"] != "AS_StandIdle" for r in probe.records[interrupted:]):
            raise AssertionError("Interruption changed the standing pose")
        if real_face:
            await probe.until(
                lambda: sum(r["phase"] == "sample" and r["qpc_time"] > interrupted_qpc + .5
                            for r in probe.records[interrupted:]) >= 3,
                "Observe fresh settled face samples after native log flushing", seconds=10,
            )
            settled = [r for r in probe.records[interrupted:]
                       if r["phase"] == "sample" and r["qpc_time"] > interrupted_qpc + .5]
            if len(settled) < 3 or max(r.get("face_jaw_curve", 1) for r in settled) > .03:
                raise AssertionError("The packaged face did not stay neutral after interruption")
            report["packaged_jaw_cancellation_verified"] = True
        await controller.finish()
        await seated(start)
        passed("interruption_remains_standing", start)

        start = len(probe.records)
        providers.unavailable["codex"] = ProviderError("quota_exhausted", "Controlled known quota")
        try:
            controller.start()
        except SessionError:
            pass
        else:
            raise AssertionError("Known exhausted quota allowed a conversation")
        await asyncio.sleep(1)
        probe.read()
        if not probe.observed(start, "AS_SitIdle") or any(
            r["animation"] != "AS_SitIdle" for r in probe.records[start:]
        ):
            raise AssertionError("Known quota must keep the character seated")
        providers.unavailable.clear()
        passed("known_quota_stays_seated", start)

        start = len(probe.records)
        await stand()
        answer.quota = True
        await controller.submit("Сколько делают справку?")
        await controller._task
        await seated(start)
        if controller.journal.list()[0]["status"] != "quota_exhausted":
            raise AssertionError("The controlled provider quota path was not executed")
        providers.unavailable.clear()
        answer.quota = False
        passed("quota_during_answer_sits", start)

        start = len(probe.records)
        controller.start()
        await probe.until(
            lambda: any(r["phase"] == "sample" and r["animation"] == "AS_Stand"
                        and .4 < r["position"] < 1.8 for r in probe.records[start:]),
            "Observe a partially completed standing animation",
        )
        previous = probe.records[-1]["position"]
        duplicate = len(probe.records)
        controller.emit("snapshot", **controller.snapshot())
        await probe.until(
            lambda: probe.observed(duplicate, "AS_Stand"),
            "Observe repeated snapshot without restarting the clip",
        )
        if probe.records[-1]["position"] < previous:
            raise AssertionError("Repeated snapshot restarted the standing animation")
        await controller.finish()
        await seated(start)
        before = next(r for r in probe.records[start:] if r["phase"] == "before_reverse")
        after = next(r for r in probe.records[start:] if r["phase"] == "after_reverse")
        jump = max(math.dist(before["bones"][bone], after["bones"][bone]) for bone in before["bones"])
        if jump > .1:
            raise AssertionError(f"Reversing the transition jumped {jump} cm")
        report["reversal_max_bone_jump_cm"] = jump
        passed("finish_while_rising_and_duplicate_snapshot", start)

        start = len(probe.records)
        await stand()
        await wire.close_send({"type": "websocket.close", "code": 1001})
        await probe.until(
            lambda: probe.observed(start, "AS_SitIdle", connected=False),
            "Connection loss returns to seated without a stale acknowledgement",
        )
        passed("ipc_disconnect_sits", start)

        probe.read()
        completions = [r for r in probe.records if r["phase"] == "complete" and r["acknowledge"]]
        if len(completions) != len(wire.acknowledgements):
            raise AssertionError("Renderer completion and IPC acknowledgement counts differ")
        for completed, received in zip(completions, wire.acknowledgements, strict=True):
            expected = "standing_up" if completed["animation"] == "AS_Stand" else "sitting_down"
            if received["session_id"] != completed["session_id"] or received["backend_state"] != expected:
                raise AssertionError("Acknowledgement belongs to another transition or session")
            if completed["length"] <= 0 or completed["position"] + .0001 < completed["length"]:
                raise AssertionError("Acknowledgement preceded the actual end of the animation")
        report.update({
            "passed": True, "trace": probe.records, "acknowledgements": wire.acknowledgements,
            "completion_checks": len(completions),
            "avatar_configured": probe.records[-1]["avatar_configured"],
        })
        if real_face:
            valid = [ack for ack in wire.face_acknowledgements
                     if ack["turn_id"] == ack["pending_turn"] and ack["backend_state"] == "thinking"]
            if len(valid) != (2 if play_output else 1):
                raise AssertionError("The actual native face did not acknowledge exactly the pending speeches")
            report["native_face_handshake_verified"] = True
            blink = max(r.get("face_blink_curve", 0) for r in probe.records)
            if blink < .5:
                raise AssertionError("No actual packaged eyelid blink curve was observed")
            report["packaged_blink_verified"] = True
            report["peak_packaged_blink_curve"] = blink
            jaw = max(r.get("face_jaw_curve", 0) for r in probe.records)
            if jaw < .05:
                raise AssertionError("No actual packaged jaw deformation curve was observed")
            report["peak_packaged_jaw_curve"] = jaw
            report["ace_local_inference_and_curves_verified"] = True
        if cancel_preparation:
            late = [item for item in wire.face_acknowledgements
                    if item["turn_id"] in cancelled_preparations]
            if late:
                raise AssertionError("A cancelled facial preparation acknowledged a later callback")
            if any(item["turn_id"] in cancelled_preparations for item in audio_output.first_dac):
                raise AssertionError("Cancelled preparation resumed audible playback")
            report["preparation_cancellation_verified"] = True
        if play_output:
            errors = []
            for start in audio_output.first_dac:
                for record in probe.records:
                    if record.get("face_turn") != start["turn_id"] or record["phase"] != "sample" or record["state"] != "speaking":
                        continue
                    elapsed = record["qpc_time"] - start["qpc_time"]
                    if .2 < elapsed < 6.9:
                        errors.append(record["face_media_position"] - elapsed)
            if len(errors) < 20:
                raise AssertionError("Insufficient samples comparing the real face clock and physical DAC clock")
            absolute = sorted(abs(error) for error in errors)
            report["face_to_dac_timing"] = {
                "samples": len(errors), "maximum_absolute_error_seconds": absolute[-1],
                "p95_absolute_error_seconds": absolute[math.ceil(len(absolute) * .95) - 1],
                "minimum_signed_error_seconds": min(errors), "maximum_signed_error_seconds": max(errors),
                "acceptance_bound_seconds": .15,
            }
            if absolute[-1] > .15:
                raise AssertionError("The face and physical audio output differ by more than 150 ms")
            report["output_dac_and_face_clock_verified"] = True
        report["passed"] = True
    finally:
        if audio_output:
            await audio_output.close()
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.kill()
                await process.wait()
        server.should_exit = True
        with suppress(TimeoutError):
            await asyncio.wait_for(server_task, 15)
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/unreal/packaged-motion-verification.json")
    parser.add_argument("--real-face", action="store_true", help="Require the actual native ACE callback, no controlled face ACK")
    parser.add_argument("--play-output", action="store_true", help="Play generated voice through the system output, never open a microphone")
    parser.add_argument("--cancel-preparation", action="store_true", help="Also cancel native preparation before face_ready at three timings")
    args = parser.parse_args()
    if args.play_output and not args.real_face:
        parser.error("--play-output requires --real-face")
    if args.cancel_preparation and not args.play_output:
        parser.error("--cancel-preparation requires --play-output")
    executable = args.executable.resolve(strict=True)
    report = {
        "started_at_utc": datetime.now(UTC).isoformat(), "executable": str(executable),
        "passed": False, "scenarios": [], "controlled_answer": True,
        "controlled_silent_speech": not args.real_face,
        "approved_voice_fixture": args.real_face,
        "physical_audio_verified": False, "full_ace_acceptance_verified": False,
        "student_ui_verified": False,
        "controlled_face_ready_for_body_only_fixture": not args.real_face,
        "native_face_handshake_verified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep evidence logs on success and failure; credentials are removed separately.
    directory = Path(tempfile.mkdtemp(prefix="f02-", dir=args.output.parent))
    try:
        asyncio.run(run(executable, directory, report, real_face=args.real_face, play_output=args.play_output,
                        cancel_preparation=args.cancel_preparation))
    except Exception as error:
        report["passed"] = False
        report["issue"] = str(error)
        raise
    finally:
        (directory / "connection.json").unlink(missing_ok=True)
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
