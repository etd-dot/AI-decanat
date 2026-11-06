"""Detach the project mapping's obsolete editor-only source animation reference."""

if __name__ == "__main__":
    import unreal

    path = "/Game/Decanat/Animations/Face/PA_DecanatArkit"
    mapping = unreal.load_asset(path)
    if not mapping or not mapping.get_editor_property("skeleton"):
        raise RuntimeError("The project mapping has no actual face skeleton")
    mapping.set_editor_property("source_animation", None)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mapping, only_if_is_dirty=False):
        raise RuntimeError("Cannot save the detached project mapping")
    face = unreal.load_asset("/Game/Decanat/Characters/ABP_DecanatFace")
    if not unreal.DecanatMetaHumanTools.configure_face_source(face):
        raise RuntimeError("Cannot explicitly bind the face graph to the body component")
    if not unreal.EditorAssetLibrary.save_loaded_asset(face, only_if_is_dirty=False):
        raise RuntimeError("Cannot save the face source binding")
