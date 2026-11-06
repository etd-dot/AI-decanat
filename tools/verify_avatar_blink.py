"""Observe actual face curves and eyelid bone movement in the game's PIE instance."""

import json
import math
import time
from pathlib import Path


def verify(unreal):
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/avatar-blink-verification.json"
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    level.load_level("/Game/Decanat/Office")
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    state = {"started": time.monotonic(), "last": 0, "handle": None, "samples": [], "open": False, "closed": False}
    level.editor_play_simulate()

    def finish(issue=""):
        samples = state["samples"]
        peak = max((s["postprocess_blink"] for s in samples), default=0)
        base = min(samples, key=lambda s: s["idle_blink"], default={})
        closed = max(samples, key=lambda s: s["idle_blink"], default={})
        bones = base.get("eyelid_bones", {})
        movement = max((math.dist(xyz, closed["eyelid_bones"][bone]) for bone, xyz in bones.items()), default=0)
        if not issue and (peak < .5 or movement < .02):
            issue = f"No actual blink deformation: peak curve {peak}, max eyelid displacement {movement} cm"
        report = {"passed": not bool(issue), "issue": issue, "samples": samples,
                  "peak_postprocess_blink": peak, "max_eyelid_displacement_cm": movement,
                  "local_inference_verified": False, "face_sync_verified": False}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        unreal.unregister_slate_post_tick_callback(state["handle"])
        level.editor_request_end_play()
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        if issue:
            unreal.log_error(issue)

    def tick(_delta):
        if time.monotonic() - state["last"] < .025:
            return
        state["last"] = time.monotonic()
        try:
            sample = json.loads(unreal.DecanatMetaHumanTools.inspect_pie_face())
            if sample.get("native_face_instance") and sample.get("animation_clock", 0) > .2:
                state["samples"].append(sample)
                if sample["idle_blink"] == 0 and not state["open"]:
                    state["open"] = unreal.DecanatMetaHumanTools.capture_pie_face(False)
                if sample["idle_blink"] > .8 and not state["closed"]:
                    state["closed"] = unreal.DecanatMetaHumanTools.capture_pie_face(True)
            if time.monotonic() - state["started"] > 12:
                finish("" if state["samples"] else "The project face instance did not initialize")
        except Exception as error:
            finish(str(error))

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    import unreal

    verify(unreal)
