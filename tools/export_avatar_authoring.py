"""Export the actual assembled meshes and reference bones for local authoring."""

import json
from pathlib import Path

BODY = "/Game/MetaHumans/Assembled/Decanat/Body/SKM_MC_Decanat_BodyMesh"
OUTFIT = "/Game/MetaHumans/Assembled/Decanat/Clothing/MC_Decanat_Outfits"


def export(unreal):
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/authoring"
    output.mkdir(parents=True, exist_ok=True)
    body = unreal.load_asset(BODY)
    if not isinstance(body, unreal.SkeletalMesh):
        raise RuntimeError("The actual runtime body mesh is missing")
    bones = json.loads(unreal.DecanatMetaHumanTools.export_skeleton(body))
    (output / "skeleton.json").write_text(json.dumps(bones, indent=2), encoding="utf-8")
    for name, asset in (("body", body), ("outfit", unreal.load_asset(OUTFIT))):
        destination = output / f"{name}.fbx"
        if destination.exists():
            continue
        task = unreal.AssetExportTask()
        task.object = asset
        task.filename = str(destination)
        task.automated = True
        task.prompt = False
        task.replace_identical = False
        task.exporter = unreal.SkeletalMeshExporterFBX()
        options = unreal.FbxExportOption()
        options.set_editor_property("level_of_detail", False)
        options.set_editor_property("export_morph_targets", False)
        task.options = options
        if not unreal.Exporter.run_asset_export_task(task) or not destination.exists():
            raise RuntimeError(f"Cannot export the actual {name} mesh")


if __name__ == "__main__":
    import unreal

    export(unreal)
