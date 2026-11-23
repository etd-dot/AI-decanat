"""Create /Game/Decanat/Office using Unreal Editor's -ExecutePythonScript.

Furniture uses engine primitives and our own materials. Real character/animation
references from DefaultGame.ini are saved on the map actor for packaged builds.
An existing map and existing materials are never overwritten.
"""

from __future__ import annotations

import configparser
from pathlib import Path

MAP_PATH = "/Game/Decanat/Office"
MATERIAL_PATH = "/Game/Decanat/Materials"
ANIMATION_KEYS = (
    "SitIdleAnimation", "StandAnimation", "StandIdleAnimation", "SitAnimation",
    "SpeakingIdleAnimation",
)


def configured_avatar(unreal):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(Path(unreal.Paths.project_config_dir()) / "DefaultGame.ini", encoding="utf-8-sig")
    settings = config["Decanat"] if config.has_section("Decanat") else {}
    path = settings.get("AvatarClass", "").strip().strip('"')
    if not path:
        return None, {}
    character = unreal.load_class(None, path)
    if character is None:
        raise RuntimeError("Configured AvatarClass could not be loaded")
    animations = {}
    for key in ANIMATION_KEYS:
        path = settings.get(key, "").strip().strip('"')
        if not path and key == "SpeakingIdleAnimation":
            continue
        animation = unreal.load_asset(path) if path else None
        if not isinstance(animation, unreal.AnimSequence):
            raise RuntimeError(f"Configure a real AnimSequence for {key}")
        animations[key] = animation
    return character, animations


def material(unreal, assets, name, color, roughness=0.65, metallic=0.0, emissive=False):
    path = f"{MATERIAL_PATH}/{name}"
    if assets.does_asset_exist(path):
        existing = unreal.load_asset(path)
        if not isinstance(existing, unreal.MaterialInterface):
            raise RuntimeError(f"Existing material path has another asset type: {path}")
        return existing
    result = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, MATERIAL_PATH, unreal.Material, unreal.MaterialFactoryNew()
    )
    if result is None:
        raise RuntimeError(f"Cannot create material {path}")
    editor = unreal.MaterialEditingLibrary
    tint = editor.create_material_expression(result, unreal.MaterialExpressionConstant3Vector)
    tint.set_editor_property("constant", unreal.LinearColor(*color, 1.0))
    connections = [(tint, unreal.MaterialProperty.MP_BASE_COLOR)]
    if emissive:
        connections.append((tint, unreal.MaterialProperty.MP_EMISSIVE_COLOR))
    for prop, value in (
        (unreal.MaterialProperty.MP_ROUGHNESS, roughness),
        (unreal.MaterialProperty.MP_METALLIC, metallic),
    ):
        expression = editor.create_material_expression(result, unreal.MaterialExpressionConstant)
        expression.set_editor_property("r", value)
        connections.append((expression, prop))
    for expression, prop in connections:
        if not editor.connect_material_property(expression, "", prop):
            raise RuntimeError(f"Cannot connect material property for {path}")
    editor.recompile_material(result)
    if not assets.save_loaded_asset(result):
        raise RuntimeError(f"Cannot save material {path}")
    return result


def configure_camera_exposure(unreal, camera):
    # Fixed physical exposure keeps the 2200-lumen key light from clipping when
    # project-wide automatic exposure is disabled. EV100 is approximately 8.
    settings = camera.camera_component.get_editor_property("post_process_settings")
    for name, value in (
        ("auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL),
        ("auto_exposure_apply_physical_camera_exposure", True),
        ("camera_iso", 400.0),
        ("camera_shutter_speed", 60.0),
        ("depth_of_field_fstop", 4.0),
    ):
        settings.set_editor_property(f"override_{name}", True)
        settings.set_editor_property(name, value)
    camera.camera_component.set_editor_property("post_process_settings", settings)
    camera.camera_component.set_editor_property("post_process_blend_weight", 1.0)


def create_scene(unreal):
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    if assets.does_asset_exist(MAP_PATH):
        raise RuntimeError("Office already exists. This script never overwrites an edited scene.")
    # Missing modules/invalid configured assets must fail before replacing the open level.
    avatar_class = unreal.load_class(None, "/Script/Decanat.DecanatAvatar")
    if avatar_class is None:
        raise RuntimeError("Build the Decanat C++ module before creating the scene")
    cube = unreal.load_asset("/Engine/BasicShapes/Cube.Cube")
    if cube is None:
        raise RuntimeError("The engine's basic Cube mesh is unavailable")
    character, animations = configured_avatar(unreal)
    assets.make_directory(MATERIAL_PATH)
    materials = {
        "desk": material(unreal, assets, "M_Desk", (0.32, 0.20, 0.12), 0.55),
        "metal": material(unreal, assets, "M_Metal", (0.08, 0.10, 0.12), 0.35, 0.75),
        "seat": material(unreal, assets, "M_Seat", (0.045, 0.055, 0.07), 0.9),
        "wall": material(unreal, assets, "M_Wall", (0.52, 0.56, 0.58), 0.95),
        "panel": material(unreal, assets, "M_Panel", (0.11, 0.18, 0.21), 0.8),
        "floor": material(unreal, assets, "M_Floor", (0.20, 0.22, 0.24), 0.8),
        "screen": material(unreal, assets, "M_Screen", (0.055, 0.12, 0.17), 0.3, emissive=True),
        "paper": material(unreal, assets, "M_Paper", (0.78, 0.76, 0.69), 0.9),
    }
    if not level.new_level(MAP_PATH):
        raise RuntimeError("Cannot create Office map")

    def spawn(cls, name, position, folder):
        actor = actors.spawn_actor_from_class(cls, unreal.Vector(*position))
        if actor is None:
            raise RuntimeError(f"Cannot create {name}")
        actor.set_actor_label(name)
        actor.set_folder_path(f"Office/{folder}")
        return actor

    def block(name, position, size_cm, finish):
        actor = spawn(unreal.StaticMeshActor, name, position, "Furniture")
        actor.static_mesh_component.set_static_mesh(cube)
        actor.static_mesh_component.set_material(0, materials[finish])
        actor.set_actor_scale3d(unreal.Vector(*(size / 100 for size in size_cm)))
        return actor

    block("Desk surface", (100, 0, 75), (70, 150, 5), "desk")
    for side in (-65, 65):
        block("Desk support", (100, side, 37), (50, 5, 74), "metal")
    block("Chair seat", (10, 0, 45), (50, 50, 4), "seat")
    block("Chair back", (-14, 0, 70), (4, 50, 50), "seat")
    for x in (-9, 29):
        for y in (-19, 19):
            block("Chair leg", (x, y, 22), (3, 3, 44), "metal")
    block("Laptop base", (90, 0, 79), (25, 35, 2), "metal")
    block("Laptop keyboard", (87, 0, 80.1), (16, 29, 0.3), "seat")
    block("Laptop lid", (104, 0, 91), (1.8, 35, 25), "metal")
    block("Laptop screen", (102.9, 0, 91), (0.2, 31, 21), "screen")
    block("Reference papers", (96, 48, 78), (22, 30, 0.7), "paper")
    block("Floor", (0, 0, -5), (1500, 1500, 10), "floor")
    block("Background wall", (-220, 0, 160), (10, 1500, 320), "wall")
    block("Wall accent panel", (-214, 20, 140), (2, 240, 200), "panel")
    camera = spawn(unreal.CameraActor, "Fixed visitor camera", (430, -390, 170), "Camera")
    camera.set_actor_rotation(
        unreal.MathLibrary.find_look_at_rotation(
            camera.get_actor_location(), unreal.Vector(15, 0, 100)
        ), False,
    )
    camera.camera_component.set_editor_property("field_of_view", 36.0)
    camera.camera_component.set_editor_property("aspect_ratio", 16 / 9)
    camera.camera_component.set_editor_property("constrain_aspect_ratio", True)
    configure_camera_exposure(unreal, camera)
    for name, position, intensity, temperature in (
        ("Key light", (250, -180, 260), 2200.0, 4800.0),
        ("Fill light", (80, 240, 200), 1100.0, 5600.0),
        ("Rim light", (-110, 80, 240), 900.0, 6000.0),
    ):
        light = spawn(unreal.RectLight, name, position, "Lighting")
        light.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
        light.set_actor_rotation(
            unreal.MathLibrary.find_look_at_rotation(
                light.get_actor_location(), unreal.Vector(0, 0, 120)
            ), False,
        )
        for prop, value in (
            ("intensity", intensity), ("use_temperature", True), ("temperature", temperature),
            ("source_width", 100.0), ("source_height", 100.0),
        ):
            light.light_component.set_editor_property(prop, value)
    avatar = spawn(avatar_class, "Dean office avatar", (45, 0, 0), "Character")
    avatar.set_actor_rotation(unreal.Rotator(pitch=0, yaw=-90, roll=0), False)
    if character is not None:
        avatar.set_editor_property("character_asset", character)
        avatar.set_editor_property("animation_assets", animations)
    if not level.save_current_level():
        raise RuntimeError("Cannot save Office map")
    if character is None:
        unreal.log_warning(
            "Office environment saved; MetaHuman and animations are not configured. "
            "Assign Character Asset and Animation Assets on the avatar actor before packaging."
        )
    else:
        unreal.log("Office saved with character/animation references. Local ACE setup is still required.")


if __name__ == "__main__":
    import unreal

    create_scene(unreal)
