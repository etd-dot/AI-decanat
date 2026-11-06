"""Run inside UE 5.6: save a real editable MetaHuman, optionally rig and assemble it.

DECANAT_CHARACTER_ACTION=complete requires the full editor (-ExecutePythonScript)
and a valid Epic MetaHuman service login. Runtime speech/animation stays local.
Existing character edits and assembled output are never overwritten.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

PRESET = "/MetaHumanCharacter/Optional/Presets/Bruce.Bruce"
CHARACTER = "/Game/MetaHumans/Decanat/MC_Decanat"
ASSEMBLED = "/Game/MetaHumans/Assembled/Decanat"


def is_character(asset):
    # UMetaHumanCharacter is a native UCLASS without a generated Python type in
    # UE 5.6. Check its actual UObject class rather than unreal.MetaHumanCharacter.
    return (
        asset is not None
        and hasattr(asset, "get_class")
        and asset.get_class().get_path_name() == "/Script/MetaHumanCharacter.MetaHumanCharacter"
    )


def prepare(unreal):
    assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    if not hasattr(unreal, "DecanatMetaHumanTools"):
        raise RuntimeError("Build DecanatEditorTools and enable MetaHumanCharacter first")
    if assets.does_asset_exist(CHARACTER):
        character = unreal.load_asset(CHARACTER)
    else:
        source = unreal.load_asset(PRESET)
        if not is_character(source):
            raise RuntimeError("The real Bruce MetaHuman preset/Core Data is unavailable")
        assets.make_directory(CHARACTER.rsplit("/", 1)[0])
        character = assets.duplicate_asset(PRESET, CHARACTER)
        if not character or not assets.save_loaded_asset(character):
            raise RuntimeError("Cannot save the editable MetaHuman character")
    if not is_character(character):
        raise RuntimeError("Character path is occupied by another asset type")
    report_path = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal/metahuman.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tools = unreal.DecanatMetaHumanTools

    def record(stage, issue=""):
        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "editor_process_id": os.getpid(),
            "source_preset": PRESET,
            "character_asset": CHARACTER,
            "stage": stage,
            "issue": issue,
            "status": json.loads(tools.inspect_character(character)),
            "appearance_approved": False,
            "runtime_animations_verified": False,
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report["status"]

    status = record("editable_character_saved")
    unreal.log(f"Decanat: real editable MetaHuman saved: {CHARACTER}; status={status}")
    if os.environ.get("DECANAT_CHARACTER_ACTION", "prepare") != "complete":
        return
    if unreal.EditorAssetLibrary.does_directory_exist(ASSEMBLED):
        raise RuntimeError("Assembled output already exists; inspect it before rebuilding")

    # Service requests and assembly need editor ticks. A Python commandlet does
    # not run this workflow; launch the full editor with -ExecutePythonScript.
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    if os.environ.get("DECANAT_CHARACTER_INTERACTIVE") == "1":
        unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([character])
    started = time.monotonic()
    workflow = {"stage": "rig", "requested": False, "handle": None, "last_poll": 0.0, "busy": False}

    def finish(stage, issue=""):
        assets.save_loaded_asset(character)
        record(stage, issue)
        if workflow["handle"] is not None:
            unreal.unregister_slate_post_tick_callback(workflow["handle"])
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        if issue:
            unreal.log_warning(f"Decanat MetaHuman: {issue}")

    def advance(_delta):
        if time.monotonic() - workflow["last_poll"] < 1:
            return
        workflow["last_poll"] = time.monotonic()
        try:
            if time.monotonic() - started > 900:
                finish("incomplete", "MetaHuman workflow timed out; inspect the editor log")
                return
            current = record(workflow["stage"])
            if workflow["stage"] in {"rig", "textures"}:
                is_rig = workflow["stage"] == "rig"
                ready = current["face_rig" if is_rig else "texture_sources"]
                pending = current["rig_pending" if is_rig else "textures_pending"]
                # Downloaded data can become visible while Epic is still
                # processing its textures/meshes. CanBuild rejects that state.
                if ready and not pending:
                    assets.save_loaded_asset(character)
                    workflow.update(stage="textures" if is_rig else "assembly", requested=False)
                    return
                if pending:
                    return
                if not workflow["requested"]:
                    workflow["requested"] = True
                    accepted = (
                        tools.begin_face_rig(character) if is_rig
                        else tools.begin_texture_download(character)
                    )
                    if not accepted:
                        finish("incomplete", "Epic request did not start; inspect login/service errors in the editor log")
                elif not pending:
                    finish("incomplete", "Epic request ended without the required data; inspect the editor log")
            elif not workflow["requested"]:
                workflow["requested"] = True
                if not tools.begin_assembly(character):
                    finish("incomplete", "Assembly did not start; inspect character status and the editor log")
            else:
                generated = assets.list_assets(ASSEMBLED, recursive=True, include_folder=False)
                blueprints = [path for path in generated if isinstance(unreal.load_asset(path), unreal.Blueprint)]
                if blueprints:
                    if not assets.save_directory(ASSEMBLED, only_if_is_dirty=True, recursive=True):
                        finish("incomplete", "Cannot save all assembled MetaHuman assets")
                        return
                    finish("assembly_output_found_inspection_required")
        except Exception as error:
            finish("incomplete", str(error))

    def tick(delta):
        # MetaHuman initialization can pump Slate while loading meshes/textures.
        # Do not interpret this nested tick as a completed service request.
        if workflow["busy"]:
            return
        workflow["busy"] = True
        try:
            advance(delta)
        finally:
            workflow["busy"] = False

    workflow["handle"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    import unreal

    prepare(unreal)
