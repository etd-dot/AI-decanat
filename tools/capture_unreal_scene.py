"""Capture the actual Office map from its fixed camera in the full Unreal Editor."""

import time
from pathlib import Path


def capture(unreal, *, output_name="office.png", setup=None):
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.load_level("/Game/Decanat/Office"):
        raise RuntimeError("Cannot open the real Office map")
    if setup is not None:
        setup()
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    camera = next((actor for actor in actors if isinstance(actor, unreal.CameraActor)), None)
    if camera is None:
        raise RuntimeError("Office has no fixed camera")
    output = Path(unreal.Paths.project_dir()).resolve().parents[1] / ".runtime/unreal" / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    task = unreal.AutomationLibrary.take_high_res_screenshot(
        1280, 720, str(output), camera=camera, delay=2.0,
    )
    if not task or not task.is_valid_task():
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        raise RuntimeError("Unreal could not start the screenshot task")
    started = time.monotonic()
    state = {"handle": None}

    def tick(_delta):
        if task.is_task_done() or time.monotonic() - started > 180:
            unreal.unregister_slate_post_tick_callback(state["handle"])
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            if not output.exists():
                unreal.log_error("Office render was not saved; inspect the editor log")
            else:
                unreal.log(f"Actual Office render saved: {output}")

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    import unreal

    capture(unreal)
