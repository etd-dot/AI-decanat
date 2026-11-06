"""Create a project-specific ACE/ARKit face graph without editing Epic assets."""

import json
from pathlib import Path


def install(unreal):
    root = Path(unreal.Paths.project_dir()).resolve().parents[1]
    output = root / ".runtime/unreal/avatar-face-install.json"
    tools = unreal.DecanatMetaHumanTools
    target = "/Game/Decanat/Characters/ABP_DecanatFace"
    if unreal.EditorAssetLibrary.does_asset_exist(target):
        raise RuntimeError("A project face graph exists; inspect before replacing")
    character = unreal.load_asset("/Game/Decanat/Characters/BP_DeanOffice")
    inspection = json.loads(tools.inspect_blueprint(character))
    face = next(c for c in inspection["components"] if c["name"] == "Face")
    mesh = unreal.load_asset(face["mesh"])
    mapping = unreal.load_asset("/NV_ACE_Reference/mh_arkit_mapping_pose_A2F")
    blueprint = tools.create_face_animation(mesh, mapping)
    if not blueprint:
        raise RuntimeError("Cannot create the face graph")
    report = {"graph": json.loads(tools.inspect_blueprint(blueprint)), "installed": False,
              "local_inference_verified": False, "face_sync_verified": False}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["graph"]["compile_error"]:
        raise RuntimeError("Face graph did not compile; inspect report before binding or saving")
    if not tools.install_face_animation(character, blueprint):
        raise RuntimeError("Cannot bind the face graph and muted ACE consumer")
    for asset in (blueprint, unreal.load_asset("/Game/Decanat/Animations/Face/PA_DecanatArkit"), character):
        if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
            raise RuntimeError(f"Cannot save {asset.get_path_name()}")
    report["installed"] = True
    report["character"] = json.loads(tools.inspect_blueprint(character))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import unreal

    install(unreal)
