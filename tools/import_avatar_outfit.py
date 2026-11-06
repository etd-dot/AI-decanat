"""Build the fitted wardrobe mesh against the exact MetaHuman bind skeleton."""

import json
from pathlib import Path


def create(unreal):
    root = Path(unreal.Paths.project_dir()).resolve().parents[1]
    source = root / ".runtime/unreal/authoring/office-outfit.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    folder = data["asset"].rsplit("/", 1)[0]
    assets.make_directory(folder)
    for entry in data["materials"]:
        entry["asset"] = f"{folder}/M_{entry['name']}"
        if assets.does_asset_exist(entry["asset"]):
            finish = unreal.load_asset(entry["asset"])
            if not finish.get_editor_property("used_with_skeletal_mesh"):
                finish.set_editor_property("used_with_skeletal_mesh", True)
                unreal.MaterialEditingLibrary.recompile_material(finish)
                if not assets.save_loaded_asset(finish):
                    raise RuntimeError("Cannot save skeletal material usage")
            continue
        factory = unreal.MaterialFactoryNew()
        finish = unreal.AssetToolsHelpers.get_asset_tools().create_asset(f"M_{entry['name']}", folder, unreal.Material, factory)
        finish.set_editor_property("two_sided", True)
        finish.set_editor_property("used_with_skeletal_mesh", True)
        colour = unreal.MaterialEditingLibrary.create_material_expression(finish, unreal.MaterialExpressionConstant3Vector)
        colour.set_editor_property("constant", unreal.LinearColor(*entry["colour"], 1))
        unreal.MaterialEditingLibrary.connect_material_property(colour, "", unreal.MaterialProperty.MP_BASE_COLOR)
        roughness = unreal.MaterialEditingLibrary.create_material_expression(finish, unreal.MaterialExpressionConstant)
        roughness.set_editor_property("r", entry["roughness"])
        unreal.MaterialEditingLibrary.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)
        unreal.MaterialEditingLibrary.recompile_material(finish)
        if not assets.save_loaded_asset(finish):
            raise RuntimeError("Cannot save wardrobe material")
    if assets.does_asset_exist(data["asset"]):
        unreal.log("Preserving the existing wardrobe mesh")
        return
    payload = source.with_name("office-outfit-import.json")
    payload.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    body = unreal.load_asset("/Game/MetaHumans/Assembled/Decanat/Body/SKM_MC_Decanat_BodyMesh")
    mesh = unreal.DecanatMetaHumanTools.create_outfit(body, str(payload))
    if not mesh or not assets.save_loaded_asset(mesh):
        raise RuntimeError("Cannot build/save the authored outfit")


if __name__ == "__main__":
    import unreal

    create(unreal)
