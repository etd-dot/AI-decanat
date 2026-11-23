"""Inspect the assembled actor and render it in Office without saving map edits."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BLUEPRINT = "/Game/MetaHumans/Assembled/Decanat/BP_Decanat"


def preview(unreal):
    # Script execution inside Unreal does not reliably add tools/ to sys.path.
    tool_directory = str(Path(__file__).resolve().parent)
    if tool_directory not in sys.path:
        sys.path.insert(0, tool_directory)
    from capture_unreal_scene import capture

    character_class = unreal.EditorAssetLibrary.load_blueprint_class(BLUEPRINT)
    if character_class is None:
        raise RuntimeError(f"The assembled MetaHuman Blueprint is unavailable: {BLUEPRINT}")

    def setup():
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        character = actors.spawn_actor_from_class(character_class, unreal.Vector(0, 0, 0))
        if character is None:
            raise RuntimeError("Cannot spawn the assembled MetaHuman")
        character.set_actor_location(unreal.Vector(25, 0, 0), False, False)
        character.set_actor_rotation(unreal.Rotator(pitch=0, yaw=-90, roll=0), False)
        character.set_actor_label("MetaHuman technical preview")
        meshes = character.get_components_by_class(unreal.SkeletalMeshComponent)
        components = []
        for component in meshes:
            mesh = component.get_skeletal_mesh_asset()
            components.append({
                "name": component.get_name(),
                "mesh": mesh.get_path_name() if mesh else None,
                "skeleton": mesh.get_editor_property("skeleton").get_path_name() if mesh else None,
            })
        body = next((part for part in components if part["name"] == "Body" and part["mesh"]), None)
        preview_animation = None
        if body:
            for animation_path in (
                "/MetaHumanCharacter/Optional/Animation/UEFNAnimPreset/Locomotion/AS_MH_Neutral_Stand_Idle_Loop",
                "/MetaHumanCharacter/Optional/Animation/TemplateAnimations/Technical_Loops/Idle/mhc_mh001_fmn_b_idle",
            ):
                animation = unreal.load_asset(animation_path)
                if (isinstance(animation, unreal.AnimSequence)
                        and animation.get_editor_property("skeleton").get_path_name() == body["skeleton"]):
                    if unreal.DecanatMetaHumanTools.apply_preview_animation(character, animation):
                        preview_animation = animation_path
                        break
        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "blueprint": BLUEPRINT,
            "generated_class": character_class.get_path_name(),
            "skeletal_components": components,
            "body_component_present": body is not None,
            "appearance_approved": False,
            "runtime_animations_verified": False,
            "map_edits_saved": False,
            "preview_rotation_degrees": {
                axis: getattr(character.get_actor_rotation(), axis) for axis in ("pitch", "yaw", "roll")
            },
            "preview_location_cm": [25, 0, 0],
            "preview_animation": preview_animation,
            "preview_animation_is_runtime_configuration": False,
        }
        report_file = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/metahuman-runtime.json"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if body is None:
            raise RuntimeError("The assembled actor has no configured Body component; inspect the runtime report")

    capture(unreal, output_name="metahuman-preview.png", setup=setup)


if __name__ == "__main__":
    import unreal

    preview(unreal)
