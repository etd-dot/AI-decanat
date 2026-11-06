"""Guard real character assets against accidental replacement outside Unreal tests."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/prepare_metahuman.py"
spec = importlib.util.spec_from_file_location("decanat_metahuman", SCRIPT)
metahuman = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metahuman)


def environment(tmp_path):
    class Character:
        def get_class(self):
            return SimpleNamespace(
                get_path_name=lambda: "/Script/MetaHumanCharacter.MetaHumanCharacter"
            )

    assets = Mock()
    project = tmp_path / "desktop/Decanat"
    project.mkdir(parents=True)
    env = SimpleNamespace(
        EditorAssetSubsystem=object(),
        MetaHumanCharacter=Character,
        get_editor_subsystem=Mock(return_value=assets),
        DecanatMetaHumanTools=Mock(),
        Paths=SimpleNamespace(project_dir=lambda: str(project)),
        load_asset=Mock(),
        log=Mock(),
    )
    return env, assets


def test_missing_real_preset_does_not_create_a_character(tmp_path):
    env, assets = environment(tmp_path)
    assets.does_asset_exist.return_value = False
    env.load_asset.return_value = None
    with pytest.raises(RuntimeError, match="Core Data"):
        metahuman.prepare(env)
    assets.make_directory.assert_not_called()
    assets.duplicate_asset.assert_not_called()
    assets.save_loaded_asset.assert_not_called()


def test_existing_character_edits_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("DECANAT_CHARACTER_ACTION", "prepare")
    env, assets = environment(tmp_path)
    assets.does_asset_exist.return_value = True
    character = env.MetaHumanCharacter()
    env.load_asset.return_value = character
    env.DecanatMetaHumanTools.inspect_character.return_value = '{"valid_character":true}'
    metahuman.prepare(env)
    assets.duplicate_asset.assert_not_called()
    assets.save_loaded_asset.assert_not_called()
    env.DecanatMetaHumanTools.begin_face_rig.assert_not_called()
    report = json.loads((tmp_path / ".runtime/unreal/metahuman.json").read_text(encoding="utf-8"))
    assert report["stage"] == "editable_character_saved"
    assert report["appearance_approved"] is False
    assert report["runtime_animations_verified"] is False


def test_occupied_asset_path_is_never_replaced(tmp_path):
    env, assets = environment(tmp_path)
    assets.does_asset_exist.return_value = True
    env.load_asset.return_value = object()
    with pytest.raises(RuntimeError, match="another asset type"):
        metahuman.prepare(env)
    assets.duplicate_asset.assert_not_called()
    assets.save_loaded_asset.assert_not_called()


def test_assembly_waits_for_rig_and_texture_processing(tmp_path, monkeypatch):
    monkeypatch.setenv("DECANAT_CHARACTER_ACTION", "complete")
    monkeypatch.setenv("DECANAT_CHARACTER_INTERACTIVE", "0")
    env, assets = environment(tmp_path)
    assets.does_asset_exist.return_value = True
    env.load_asset.return_value = env.MetaHumanCharacter()
    env.EditorAssetLibrary = SimpleNamespace(does_directory_exist=lambda _path: False)
    env.EditorPythonScripting = Mock()
    env.register_slate_post_tick_callback = Mock()
    status = dict(face_rig=True, rig_pending=True, texture_sources=True, textures_pending=True)
    env.DecanatMetaHumanTools.inspect_character.side_effect = lambda _asset: json.dumps(status)
    clock = [0.0]
    monkeypatch.setattr(metahuman.time, "monotonic", lambda: clock[0])
    metahuman.prepare(env)
    tick = env.register_slate_post_tick_callback.call_args.args[0]

    def poll():
        clock[0] += 2
        tick(0.016)

    poll()  # DNA exists, but the rig is still being applied.
    status["rig_pending"] = False
    poll()  # Move to textures only after rig processing ends.
    poll()  # Texture source exists, but derived textures are still processing.
    env.DecanatMetaHumanTools.begin_assembly.assert_not_called()
    status["textures_pending"] = False
    poll()
    poll()
    env.DecanatMetaHumanTools.begin_assembly.assert_called_once()
    env.DecanatMetaHumanTools.begin_face_rig.assert_not_called()
    env.DecanatMetaHumanTools.begin_texture_download.assert_not_called()
