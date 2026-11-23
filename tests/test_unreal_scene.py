"""Check failure safety in the editor script, without claiming an Unreal runtime test."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[1] / "tools/create_unreal_scene.py"
spec = importlib.util.spec_from_file_location("decanat_scene", SCRIPT)
scene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scene)


class SceneSafetyTests(unittest.TestCase):
    def environment(self, directory):
        level, actors, assets = Mock(), Mock(), Mock()
        assets.does_asset_exist.return_value = False
        classes = [object(), object(), object()]
        subsystems = dict(zip(classes, (level, actors, assets), strict=True))

        class AnimSequence:
            pass

        env = SimpleNamespace(
            LevelEditorSubsystem=classes[0],
            EditorActorSubsystem=classes[1],
            EditorAssetSubsystem=classes[2],
            get_editor_subsystem=subsystems.__getitem__,
            Paths=SimpleNamespace(project_config_dir=lambda: directory),
            load_class=Mock(return_value=object()),
            load_asset=Mock(return_value=object()),
            AnimSequence=AnimSequence,
        )
        return env, level, actors, assets

    def test_existing_map_is_preserved(self):
        env, level, actors, assets = self.environment("unused")
        assets.does_asset_exist.return_value = True
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            scene.create_scene(env)
        level.new_level.assert_not_called()
        actors.spawn_actor_from_class.assert_not_called()
        assets.make_directory.assert_not_called()
        env.load_class.assert_not_called()

    def test_missing_module_does_not_change_the_open_map(self):
        env, level, actors, assets = self.environment("unused")
        env.load_class.return_value = None
        with self.assertRaisesRegex(RuntimeError, r"C\+\+ module"):
            scene.create_scene(env)
        level.new_level.assert_not_called()
        actors.spawn_actor_from_class.assert_not_called()
        assets.make_directory.assert_not_called()

    def test_invalid_character_does_not_change_the_open_map(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "DefaultGame.ini").write_text(
                "[Decanat]\nAvatarClass=/Game/Missing.Missing_C\n"
            )
            env, level, actors, assets = self.environment(directory)
            env.load_class.side_effect = [object(), None]
            with self.assertRaisesRegex(RuntimeError, "AvatarClass"):
                scene.create_scene(env)
            level.new_level.assert_not_called()
            actors.spawn_actor_from_class.assert_not_called()
            assets.make_directory.assert_not_called()

    def test_wrong_animation_type_is_rejected_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "DefaultGame.ini").write_text(
                "[Decanat]\nAvatarClass=/Game/Person.Person_C\n"
                "SitIdleAnimation=/Game/NotAnimation.NotAnimation\n"
            )
            env, level, actors, assets = self.environment(directory)
            with self.assertRaisesRegex(RuntimeError, "SitIdleAnimation"):
                scene.create_scene(env)
            level.new_level.assert_not_called()
            actors.spawn_actor_from_class.assert_not_called()
            assets.make_directory.assert_not_called()

    def test_real_animation_references_are_resolved_with_optional_speaking_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            config = "[Decanat]\nAvatarClass=/Game/Person.Person_C\n"
            for key in scene.ANIMATION_KEYS:
                config += f'{key}="/Game/Animation/{key}.{key}"\n'
            Path(directory, "DefaultGame.ini").write_text(config)
            env, _, _, _ = self.environment(directory)
            clips = {f"/Game/Animation/{key}.{key}": env.AnimSequence() for key in scene.ANIMATION_KEYS}
            env.load_asset.side_effect = clips.get
            character, animations = scene.configured_avatar(env)
            self.assertIs(character, env.load_class.return_value)
            self.assertEqual(set(animations), set(scene.ANIMATION_KEYS))
            for key, animation in animations.items():
                self.assertIs(animation, clips[f"/Game/Animation/{key}.{key}"])


if __name__ == "__main__":
    unittest.main()
