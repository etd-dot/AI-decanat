"""Render actual endpoints and transition poses before saving office motion bindings."""

import json
import os
import time
from pathlib import Path


def preview(unreal):
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal"
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Decanat/Office"):
        raise RuntimeError("Office is unavailable")
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = subsystem.get_all_level_actors()
    wrappers = [actor for actor in actors if actor.get_class().get_path_name() == "/Script/Decanat.DecanatAvatar"]
    installed_class = wrappers[0].get_editor_property("character_asset") if len(wrappers) == 1 else None
    installed = installed_class is not None and "BP_DeanOffice" in installed_class.get_path_name()
    camera = next(actor for actor in actors if isinstance(actor, unreal.CameraActor))
    for actor in actors:
        if isinstance(actor, unreal.RectLight):
            actor.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
        if not installed and actor.get_actor_label().startswith("Chair"):
            location = actor.get_actor_location()
            actor.set_actor_location(unreal.Vector(location.x + 30, location.y, location.z), False, False)
    cls = installed_class if installed else unreal.EditorAssetLibrary.load_blueprint_class("/Game/MetaHumans/Assembled/Decanat/BP_Decanat")
    character = subsystem.spawn_actor_from_class(cls, unreal.Vector(45, 0, 0))
    character.set_actor_location(unreal.Vector(45, 0, 0), False, False)
    character.set_actor_rotation(unreal.Rotator(pitch=0, yaw=-90, roll=0), False)
    outfit = unreal.load_asset("/Game/Decanat/Wardrobe/office_v4/SKM_OfficeOutfit")
    meshes = character.get_components_by_class(unreal.SkeletalMeshComponent)
    body = next(mesh for mesh in meshes if mesh.get_name() == "Body")
    if outfit:
        clothing = next(mesh for mesh in meshes if mesh.get_name() == "SkeletalMesh")
        clothing.set_skeletal_mesh_asset(outfit)
        clothing.set_editor_property("override_materials", [])
        clothing.set_leader_pose_component(body, True, False)
    version = os.environ.get("DECANAT_MOTION_VERSION", "office_v5")
    clips = {name: unreal.load_asset(f"/Game/Decanat/Animations/{version}/AS_{name}") for name in ("SitIdle", "StandIdle", "Stand", "Sit")}
    if not all(clips.values()):
        raise RuntimeError("All four real animation assets are required")
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    shots = [("seated", "SitIdle", 0.0), ("standing", "StandIdle", 0.0), ("rising", "Stand", 1.2), ("sitting", "Sit", 1.2)]
    state = {"index": 0, "task": None, "handle": None, "busy": False, "started": time.monotonic()}
    report = {"map_edits_saved": False, "appearance_approved": False, "poses": []}
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)

    def tick(_delta):
        if state["busy"]:
            return
        state["busy"] = True
        try:
            if time.monotonic() - state["started"] > 300:
                raise RuntimeError("Motion screenshots timed out")
            if state["task"] is not None:
                if not state["task"].is_task_done():
                    return
                state["index"] += 1
                state["task"] = None
            if state["index"] == len(shots):
                (output / "motion-preview.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                unreal.unregister_slate_post_tick_callback(state["handle"])
                unreal.EditorPythonScripting.set_keep_python_script_alive(False)
                return
            label, name, position = shots[state["index"]]
            if not unreal.DecanatMetaHumanTools.apply_preview_animation(character, clips[name], position):
                raise RuntimeError("Cannot evaluate a motion pose")
            report["poses"].append({"label": label, "animation": name, "seconds": position, "bones_world_cm": json.loads(unreal.DecanatMetaHumanTools.inspect_pose(character))})
            state["task"] = unreal.AutomationLibrary.take_high_res_screenshot(1280, 720, str(output / f"motion-{label}.png"), camera=camera, delay=2.0)
            if not state["task"] or not state["task"].is_valid_task():
                raise RuntimeError("Cannot start actual motion screenshot")
        except Exception as error:
            unreal.log_error(str(error))
            unreal.unregister_slate_post_tick_callback(state["handle"])
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        finally:
            state["busy"] = False

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    import unreal

    preview(unreal)
