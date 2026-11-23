"""Check engine-evaluated contacts and exercise the application's real PIE controller."""

import json
import math
import time
from pathlib import Path


def distance(a, b):
    return math.sqrt(sum((x - y)**2 for x, y in zip(a, b, strict=True)))


def verify(unreal):
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/office-motion-verification.json"
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Decanat/Office"):
        raise RuntimeError("Office is unavailable")
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    cls = unreal.EditorAssetLibrary.load_blueprint_class("/Game/Decanat/Characters/BP_DeanOffice")
    preview = actors.spawn_actor_from_class(cls, unreal.Vector(45, 0, 0))
    preview.set_actor_location(unreal.Vector(45, 0, 0), False, False)
    preview.set_actor_rotation(unreal.Rotator(pitch=0, yaw=-90, roll=0), False)
    tools = unreal.DecanatMetaHumanTools
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    report = {"appearance_approved": False, "ace_verified": False, "packaged_build_verified": False, "geometry": {}, "pie": []}

    def evaluate(name, position):
        clip = unreal.load_asset(f"/Game/Decanat/Animations/office_v5/AS_{name}")
        if not tools.apply_preview_animation(preview, clip, position):
            raise RuntimeError("Cannot evaluate the actual motion")
        return json.loads(tools.inspect_pose(preview))

    endpoints = {}
    for name in ("SitIdle", "StandIdle", "Stand", "Sit"):
        duration = 4 if name.endswith("Idle") else 2.4
        poses = [evaluate(name, frame / 30) for frame in range(round(duration * 30) + 1)]
        drift = max(distance(pose[foot], poses[0][foot]) for pose in poses for foot in ("foot_l", "foot_r"))
        step, culprit_frame, culprit_bone = max((distance(a[bone], b[bone]),frame,bone) for frame,(a,b) in enumerate(zip(poses,poses[1:],strict=False)) for bone in a)
        report["geometry"][name] = {"samples": len(poses), "max_ankle_drift_cm": drift, "max_sample_step_cm": step, "largest_step_frame": culprit_frame, "largest_step_bone": culprit_bone}
        if drift > .1 or step > 8:
            output.write_text(json.dumps(report,indent=2),encoding="utf-8")
            raise RuntimeError(f"Contact/continuity failure: {name}, feet={drift}, step={step}")
        endpoints[name] = (poses[0], poses[-1])
    for a, ai, b, bi in (("Stand",0,"SitIdle",0),("Stand",1,"StandIdle",0),("Sit",0,"StandIdle",0),("Sit",1,"SitIdle",0),("SitIdle",0,"SitIdle",1),("StandIdle",0,"StandIdle",1)):
        error = max(distance(endpoints[a][ai][bone], endpoints[b][bi][bone]) for bone in endpoints[a][ai])
        if error > .05:
            raise RuntimeError(f"Motion boundary discontinuity: {a}/{b}: {error} cm")
    report["engine_contact_and_boundary_checks_passed"] = True
    actors.destroy_actor(preview)
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    state = {"stage": "initial", "started": time.monotonic(), "handle": None, "last": 0.0, "observed_transition": False}
    level.editor_play_simulate()

    def finish(error=""):
        report["pie_passed"] = not bool(error)
        report["issue"] = error
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        unreal.unregister_slate_post_tick_callback(state["handle"])
        level.editor_request_end_play()
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        if error:
            unreal.log_error(error)

    def tick(_delta):
        if time.monotonic() - state["last"] < .1:
            return
        state["last"] = time.monotonic()
        try:
            if time.monotonic() - state["started"] > 60:
                finish("PIE motion verification timed out")
                return
            current = json.loads(tools.inspect_pie_motion())
            animation = current.get("animation", "").rsplit("/", 1)[-1].split(".")[0]
            if state["stage"] == "initial" and animation == "AS_SitIdle" and current["position"] > .1:
                # Placement uses stable contact/pelvis landmarks. The head is
                # intentionally moving with the idle breath at this game time.
                spatial_error = max(distance(current["bones"][bone],endpoints["SitIdle"][0][bone]) for bone in ("pelvis","foot_l","foot_r"))
                report["runtime_initial"] = current
                report["expected_initial_bones"] = endpoints["SitIdle"][0]
                if spatial_error > .15:
                    raise RuntimeError(f"Runtime actor placement differs from Office preview: {spatial_error} cm")
                report["runtime_placement_error_cm"] = spatial_error
                report["pie"].append({"stage": "initial", **current})
                if not tools.send_pie_motion("stand"):
                    raise RuntimeError("Cannot send the isolated stand stimulus")
                state.update(stage="standing", observed_transition=False)
            elif state["stage"] == "standing":
                if animation == "AS_Stand" and current["position"] > .1:
                    state["observed_transition"] = True
                elif animation == "AS_StandIdle":
                    if not state["observed_transition"]:
                        raise RuntimeError("Standing transition was not observed")
                    report["pie"].append({"stage": "standing", **current})
                    if not tools.send_pie_motion("sit"):
                        raise RuntimeError("Cannot send the isolated sit stimulus")
                    state.update(stage="seated", observed_transition=False)
            elif state["stage"] == "seated":
                if animation == "AS_Sit" and current["position"] > .1:
                    state["observed_transition"] = True
                elif animation == "AS_SitIdle":
                    if not state["observed_transition"]:
                        raise RuntimeError("Seated transition was not observed")
                    report["pie"].append({"stage": "seated", **current})
                    finish()
        except Exception as error:
            finish(str(error))

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    import unreal

    verify(unreal)
