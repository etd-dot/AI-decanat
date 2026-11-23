"""Update only this project's generated motion bindings in the existing Office."""


def bind(unreal):
    keys = {"SitIdleAnimation":"SitIdle","StandIdleAnimation":"StandIdle","StandAnimation":"Stand","SitAnimation":"Sit"}
    assets = {key: unreal.load_asset(f"/Game/Decanat/Animations/office_v5/AS_{name}") for key,name in keys.items()}
    if not all(assets.values()):
        raise RuntimeError("The new real motion clips are unavailable")
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Decanat/Office"):
        raise RuntimeError("Cannot load Office")
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    avatars = [actor for actor in actors if actor.get_class().get_path_name() == "/Script/Decanat.DecanatAvatar"]
    if len(avatars)!=1:
        raise RuntimeError("Expected exactly one application avatar")
    avatar = avatars[0]
    for existing in avatar.get_editor_property("animation_assets").values():
        if not existing.get_path_name().startswith("/Game/Decanat/Animations/office_v"):
            raise RuntimeError("Preserve manually assigned external animation assets")
    avatar.set_editor_property("animation_assets", assets)
    if not level.save_current_level():
        raise RuntimeError("Cannot save motion references")


if __name__ == "__main__":
    import unreal

    bind(unreal)
