"""Exercise local Audio2Face on the real MetaHuman; generated speech stays muted.

This verifies GPU inference/deformation/cancellation, not physical audio synchrony.
"""

import base64
import json
import math
import time
import wave
from pathlib import Path


def verify(unreal):
    root = Path(unreal.Paths.project_dir()).resolve().parents[1]
    output = root / ".runtime/unreal/avatar-face-verification.json"
    payload = root / ".runtime/unreal/face-speech-payload.json"
    with wave.open(str(root / "assets/preview/voice/aidar-ru.wav"), "rb") as source:
        pcm = source.readframes(source.getnframes())
        rate = source.getframerate()
    payload.write_text(json.dumps({"audio_b64": base64.b64encode(pcm).decode("ascii"),
                                   "sample_rate": rate}), encoding="utf-8")
    tools = unreal.DecanatMetaHumanTools
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    level.load_level("/Game/Decanat/Office")
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    state = {"started": time.monotonic(), "stage": "initial", "stage_started": time.monotonic(),
             "last": 0, "handle": None, "samples": [], "neutral_jaw": None,
             "jaw_seen": False, "complete_seen": False}
    if not tools.begin_face_game():
        raise RuntimeError("Cannot start an actual PIE game for the audio clock")

    def stage(name):
        state.update(stage=name, stage_started=time.monotonic())
        unreal.log(f"Decanat face probe stage: {name}")

    def finish(issue=""):
        samples = state["samples"]
        report = {"passed": not bool(issue), "issue": issue, "samples": samples,
                  "provider": "LocalA2F-Mark", "real_generated_speech": True,
                  "inference_and_deformation_verified": not bool(issue),
                  "cancellation_verified": not bool(issue), "physical_audio_sync_verified": False,
                  "production_license_qualification_complete": False}
        if samples:
            report["peak_jaw_curve"] = max(s.get("riglogic_jaw_open", 0) for s in samples)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        unreal.unregister_slate_post_tick_callback(state["handle"])
        tools.stop_pie_face_audio()
        level.editor_request_end_play()
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        if issue:
            unreal.log_error(issue)

    def send():
        if not tools.send_pie_face_audio(str(payload)):
            raise RuntimeError("Cannot send the isolated real speech payload")

    def tick(_delta):
        now = time.monotonic()
        if now - state["last"] < .04:
            return
        state["last"] = now
        try:
            if now - state["started"] > 180:
                raise TimeoutError(f"Local face check timed out in {state['stage']}")
            sample = json.loads(tools.inspect_pie_face())
            if not sample.get("native_face_instance") or sample.get("animation_clock", 0) < .2:
                return
            sample.update(stage=state["stage"], wall_seconds=now - state["started"])
            state["samples"].append(sample)
            jaw = sample.get("riglogic_jaw_open", 0)
            if state["stage"] == "initial":
                motion = json.loads(tools.inspect_pie_motion())
                if not motion.get("avatar_configured"):
                    raise RuntimeError(motion.get("configuration_issue", "Avatar is not configured"))
                if "LocalA2F-Mark" not in sample.get("providers", []):
                    raise RuntimeError("The local Mark provider is not registered on this GPU")
                if sample.get("consumer_volume") != 0:
                    raise RuntimeError("ACE must remain muted; backend owns audible playback")
                state["neutral_jaw"] = sample["jaw_bone"]
                if not tools.send_pie_motion("stand"):
                    raise RuntimeError("Cannot request standing before the speaking probe")
                stage("standing")
            elif state["stage"] == "standing":
                motion = json.loads(tools.inspect_pie_motion())
                if motion.get("animation", "").endswith("AS_StandIdle.AS_StandIdle"):
                    send()
                    stage("first_speech")
            elif state["stage"] == "first_speech" and jaw > .05 and sample.get("ace_jaw_open", 0) > .03:
                displacement = math.dist(state["neutral_jaw"], sample["jaw_bone"])
                if displacement < .01:
                    raise RuntimeError("Audio2Face curves have no actual jaw deformation")
                stage("first_playback")
            elif state["stage"] == "first_playback" and now - state["stage_started"] > 8:
                # The actual product's audio owner sends listening after the
                # final PCM sample. Exercise that same terminal state here.
                tools.finish_pie_face_audio()
                stage("answer_finished")
            elif state["stage"] == "answer_finished" and now - state["stage_started"] > .5:
                if jaw < .03:
                    send()
                    stage("before_cancel")
                else:
                    raise RuntimeError("Finished answer did not return the face to neutral")
            elif state["stage"] == "before_cancel" and jaw > .05 and sample.get("ace_jaw_open", 0) > .03:
                tools.stop_pie_face_audio()
                stage("cancel_settle")
            elif state["stage"] == "cancel_settle" and now - state["stage_started"] > .5:
                if jaw > .03:
                    raise RuntimeError("Cancellation did not reset the actual jaw curve")
                stage("cancel_hold")
            elif state["stage"] == "cancel_hold":
                if jaw > .03:
                    raise RuntimeError("Cancelled facial animation resumed")
                if now - state["stage_started"] > 1.5:
                    send()
                    stage("after_cancel_speech")
            elif state["stage"] == "after_cancel_speech" and jaw > .05 and sample.get("ace_jaw_open", 0) > .03:
                finish()
        except Exception as error:
            finish(str(error))

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    import unreal

    verify(unreal)
