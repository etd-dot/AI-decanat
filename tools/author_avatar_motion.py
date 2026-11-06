"""Author office motions in the actual MetaHuman reference skeleton (Blender Python).

All distances are centimetres in Unreal mesh space; no FBX animation round-trip.
The exported clips require visual/contact review in Unreal before acceptance.
"""

import json
import math
import sys
from pathlib import Path

from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".runtime/unreal/authoring"
FORWARD = Vector((0, 1, 0))
UP = Vector((0, 0, 1))
SIDE = Vector((1, 0, 0))


def smooth(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def author(version="office_v5"):
    bones = json.loads((WORK / "skeleton.json").read_text(encoding="utf-8"))["bones"]
    index = {bone["name"]: n for n, bone in enumerate(bones)}
    local_ref = []
    for bone in bones:
        x, y, z, w = bone["rotation"]
        local_ref.append(Matrix.LocRotScale(Vector(bone["position"]), Quaternion((w, x, y, z)), Vector(bone["scale"])))

    def globals_for(locals_):
        result = []
        for n, bone in enumerate(bones):
            parent = bone["parent"]
            result.append(locals_[n].copy() if parent < 0 else result[parent] @ locals_[n])
        return result

    ref = globals_for(local_ref)
    active = ["pelvis", *[f"spine_0{n}" for n in range(1, 6)], "neck_01", "head"]
    for suffix in ("l", "r"):
        active.extend(f"{part}_{suffix}" for part in ("thigh", "calf", "foot", "upperarm", "lowerarm", "hand"))
    for name in active:
        if name not in index:
            raise RuntimeError(f"Required anatomical bone missing: {name}")

    def pose(sitting, lean_extra=0.0, breath=0.0):
        local = [matrix.copy() for matrix in local_ref]

        def world():
            return globals_for(local)

        def set_world(name, matrix):
            n = index[name]
            parent = bones[n]["parent"]
            local[n] = matrix if parent < 0 else world()[parent].inverted() @ matrix

        def rotate_world(name, axis, degrees):
            matrix = world()[index[name]]
            position = matrix.translation.copy()
            matrix = Quaternion(axis, math.radians(degrees)).to_matrix().to_4x4() @ matrix
            matrix.translation = position
            set_world(name, matrix)

        def aim(name, child, target):
            matrices = world()
            matrix = matrices[index[name]]
            position = matrix.translation.copy()
            current = matrices[index[child]].translation - position
            desired = target - position
            rotation = current.rotation_difference(desired).to_matrix().to_4x4()
            matrix = rotation @ matrix
            matrix.translation = position
            set_world(name, matrix)

        def solve_chain(upper, lower, end, target, bend):
            matrices = world()
            start = matrices[index[upper]].translation.copy()
            middle = ref[index[lower]].translation
            length_a = (middle - ref[index[upper]].translation).length
            length_b = (ref[index[end]].translation - middle).length
            delta = target - start
            distance = min(delta.length, length_a + length_b - 0.005)
            direction = delta.normalized()
            along = (length_a * length_a - length_b * length_b + distance * distance) / (2 * distance)
            height = math.sqrt(max(0, length_a * length_a - along * along))
            pole = (bend - direction * bend.dot(direction)).normalized()
            knee = start + direction * along + pole * height
            aim(upper, lower, knee)
            aim(lower, end, target)

        pelvis = ref[index["pelvis"]].copy()
        pelvis.translation += -FORWARD * (35 * sitting) + UP * ((53 - pelvis.translation.z) * sitting)
        set_world("pelvis", pelvis)
        lean = 22 * sitting + lean_extra + breath * 0.4
        for n in range(1, 6):
            rotate_world(f"spine_0{n}", SIDE, -lean / 5)
        rotate_world("neck_01", SIDE, lean * 0.65)
        rotate_world("head", SIDE, lean * 0.35)
        rotate_world("neck_01", SIDE, -8 * sitting)
        rotate_world("head", SIDE, -10 * sitting)
        rotate_world("neck_01", UP, -16 * (1 - sitting))
        rotate_world("head", UP, -16 * (1 - sitting))
        contacts = {}
        for suffix, sign in (("l", 1), ("r", -1)):
            foot = f"foot_{suffix}"
            ankle = ref[index[foot]].translation.copy()
            solve_chain(f"thigh_{suffix}", f"calf_{suffix}", foot, ankle, FORWARD)
            matrix = world()[index[foot]]
            rotation = ref[index[foot]].to_quaternion()
            set_world(foot, Matrix.LocRotScale(matrix.translation, rotation, Vector((1, 1, 1))))
            contacts[foot] = (world()[index[foot]].translation - ankle).length

            upper, lower, hand = (f"{part}_{suffix}" for part in ("upperarm", "lowerarm", "hand"))
            shoulder = world()[index[upper]].translation
            standing_hand = ref[index["pelvis"]].translation + SIDE * (sign * 23) + FORWARD * 3 - UP * 1
            seated_hand = SIDE * (sign * 14) + FORWARD * 30 + UP * 81
            target = standing_hand.lerp(seated_hand, sitting)
            # Keep a lateral pole component: interpolating only forward/down can
            # align the pole with the arm and flip the elbow across its target.
            pole = FORWARD * .75 - UP * (.25 + .75 * sitting) + SIDE * (sign * .35)
            solve_chain(upper, lower, hand, target, pole.normalized())
            reference_arm = ref[index[hand]].translation - ref[index[upper]].translation
            rotation_delta = reference_arm.rotation_difference(target - shoulder)
            desired_rotation = rotation_delta @ ref[index[hand]].to_quaternion()
            current = world()[index[hand]]
            set_world(hand, Matrix.LocRotScale(current.translation, desired_rotation, Vector((1, 1, 1))))
        return local, contacts

    output = WORK / version
    output.mkdir(parents=True, exist_ok=True)
    diagnostics = {"version": version, "fps": 30, "chair_seat_cm": [10, 0, 47], "avatar_root_cm": [45, 0, 0], "clips": {}}
    for name, frames in (("SitIdle", 121), ("StandIdle", 121), ("Stand", 73), ("Sit", 73)):
        tracks = {name_: {"bone": name_, "positions": [], "rotations": []} for name_ in active}
        contact_error = 0
        for frame in range(frames):
            progress = frame / (frames - 1)
            if name in {"Stand", "Sit"}:
                phase = progress if name == "Stand" else 1 - progress
                sitting = 1 - smooth((phase - 0.16) / 0.84)
                extra = 9 * math.sin(math.pi * phase)
                breath = 0
            else:
                sitting = float(name == "SitIdle")
                extra = 0
                breath = math.sin(progress * math.tau)
            transforms, contacts = pose(sitting, extra, breath)
            contact_error = max(contact_error, *contacts.values())
            for bone_name, track in tracks.items():
                transform = transforms[index[bone_name]]
                position = transform.translation
                rotation = transform.to_quaternion().normalized()
                if track["rotations"]:
                    previous = track["rotations"][-1]
                    if sum(a*b for a,b in zip((rotation.x,rotation.y,rotation.z,rotation.w),previous,strict=True)) < 0:
                        rotation.negate()
                track["positions"].append([round(value, 6) for value in position])
                track["rotations"].append([round(value, 8) for value in (rotation.x, rotation.y, rotation.z, rotation.w)])
        clip = {"asset": f"/Game/Decanat/Animations/{version}/AS_{name}", "frames": frames, "fps": 30, "tracks": list(tracks.values())}
        (output / f"{name}.json").write_text(json.dumps(clip, separators=(",", ":")), encoding="utf-8")
        diagnostics["clips"][name] = {"frames": frames, "duration_seconds": (frames - 1) / 30, "max_ankle_position_error_cm": contact_error}
    (output / "motion-diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics))


if __name__ == "__main__":
    author(sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "office_v5")
