"""Bind the verified runtime actor to the existing Office without replacing it."""

import json
from pathlib import Path

BLUEPRINT = "/Game/MetaHumans/Assembled/Decanat/BP_Decanat"


def bind(unreal):
    character_class = unreal.EditorAssetLibrary.load_blueprint_class(BLUEPRINT)
    if character_class is None:
        raise RuntimeError("The assembled MetaHuman Blueprint is unavailable")
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Decanat/Office"):
        raise RuntimeError("Cannot load the existing Office map")
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    avatars = [actor for actor in actors if actor.get_class().get_path_name() == "/Script/Decanat.DecanatAvatar"]
    if len(avatars) != 1:
        raise RuntimeError("Office must have exactly one DecanatAvatar actor")
    avatar = avatars[0]
    current = avatar.get_editor_property("character_asset")
    if current is not None and current != character_class:
        raise RuntimeError("The avatar already uses a different character; preserve the edited binding")
    if current is None:
        avatar.set_editor_property("character_asset", character_class)
        avatar.set_actor_location(unreal.Vector(25, 0, 0), False, False)
        avatar.set_actor_rotation(unreal.Rotator(pitch=0, yaw=-90, roll=0), False)
    if not level.save_current_level():
        raise RuntimeError("Cannot save the character reference in Office")
    report = {
        "map": "/Game/Decanat/Office",
        "character_class": character_class.get_path_name(),
        "character_reference_saved": True,
        "runtime_animations_verified": False,
        "avatar_assets_verified": False,
    }
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/scene-binding.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import unreal

    bind(unreal)
