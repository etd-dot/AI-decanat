"""Read-only inspection of the actual MetaHuman's face animation graphs."""

import json
from pathlib import Path


def inspect(unreal):
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/avatar-face-graphs.json"
    blueprint = unreal.load_asset("/Game/Decanat/Characters/BP_DeanOffice")
    report = {"character": json.loads(unreal.DecanatMetaHumanTools.inspect_blueprint(blueprint)), "animations": {}}
    for component in report["character"]["components"]:
        for key in ("animation_class", "post_process_class"):
            path = component.get(key, "")
            if path and path not in report["animations"]:
                asset = unreal.load_asset(path.removesuffix("_C"))
                report["animations"][path] = json.loads(unreal.DecanatMetaHumanTools.inspect_blueprint(asset))
    mapping = unreal.load_asset("/NV_ACE_Reference/mh_arkit_mapping_pose_A2F")
    report["ace_mapping"] = json.loads(unreal.DecanatMetaHumanTools.inspect_pose_asset(mapping))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import unreal

    inspect(unreal)
