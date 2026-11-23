"""Save a project-specific Blueprint and verified motion references in Office."""

import json
from pathlib import Path

SOURCE = "/Game/MetaHumans/Assembled/Decanat/BP_Decanat"
DESTINATION = "/Game/Decanat/Characters/BP_DeanOffice"


def install(unreal):
    assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    outfit = unreal.load_asset("/Game/Decanat/Wardrobe/office_v4/SKM_OfficeOutfit")
    animations = {key: unreal.load_asset(f"/Game/Decanat/Animations/office_v5/AS_{name}") for key, name in (("SitIdleAnimation", "SitIdle"), ("StandIdleAnimation", "StandIdle"), ("StandAnimation", "Stand"), ("SitAnimation", "Sit"))}
    if not outfit or not all(animations.values()):
        raise RuntimeError("Real outfit and all four motion assets are required")
    if assets.does_asset_exist(DESTINATION):
        raise RuntimeError("Office Blueprint already exists; preserve it and inspect before changing")
    blueprint = assets.duplicate_asset(SOURCE, DESTINATION)
    if not blueprint or not unreal.DecanatMetaHumanTools.install_office_outfit(blueprint, outfit):
        raise RuntimeError("Cannot configure the project-specific MetaHuman Blueprint")
    if not assets.save_loaded_asset(blueprint):
        raise RuntimeError("Cannot save the office Blueprint")
    cls = unreal.EditorAssetLibrary.load_blueprint_class(DESTINATION)
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Decanat/Office"):
        raise RuntimeError("Cannot load Office")
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    avatars = [actor for actor in actors if actor.get_class().get_path_name() == "/Script/Decanat.DecanatAvatar"]
    if len(avatars) != 1:
        raise RuntimeError("Expected one application avatar")
    avatar = avatars[0]
    if avatar.get_editor_property("character_asset").get_path_name() != SOURCE + ".BP_Decanat_C":
        raise RuntimeError("The office character reference was edited; do not replace it")
    avatar.set_editor_property("character_asset", cls)
    avatar.set_editor_property("animation_assets", animations)
    avatar.set_actor_location(unreal.Vector(45, 0, 0), False, False)
    avatar.set_actor_rotation(unreal.Rotator(pitch=0, yaw=-90, roll=0), False)
    for actor in actors:
        if actor.get_actor_label().startswith("Chair"):
            location = actor.get_actor_location()
            actor.set_actor_location(unreal.Vector(location.x + 30, location.y, location.z), False, False)
        if isinstance(actor, unreal.RectLight):
            actor.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    if not level.save_current_level():
        raise RuntimeError("Cannot save the actual character and motion references")
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/office-avatar-installed.json"
    output.write_text(json.dumps({"blueprint": DESTINATION, "animations": {key: clip.get_path_name() for key, clip in animations.items()}, "appearance_approved": False, "ace_verified": False}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import unreal

    install(unreal)
