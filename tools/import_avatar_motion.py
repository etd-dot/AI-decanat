"""Create real AnimSequence assets without changing the shared MetaHuman skeleton."""

import os
from pathlib import Path


def create(unreal):
    body = unreal.load_asset("/Game/MetaHumans/Assembled/Decanat/Body/SKM_MC_Decanat_BodyMesh")
    assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    version = os.environ.get("DECANAT_MOTION_VERSION", "office_v5")
    source = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/authoring" / version
    for name in ("SitIdle", "StandIdle", "Stand", "Sit"):
        path = f"/Game/Decanat/Animations/{version}/AS_{name}"
        if assets.does_asset_exist(path):
            unreal.log(f"Preserving existing animation {path}")
            continue
        sequence = unreal.DecanatMetaHumanTools.create_animation(body, str(source / f"{name}.json"))
        if not sequence or not assets.save_loaded_asset(sequence):
            raise RuntimeError(f"Cannot create/save real animation {name}")


if __name__ == "__main__":
    import unreal

    create(unreal)
