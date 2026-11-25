"""Compose the generated office/key poses into the 2D application's sprite atlas.

Run with NumPy, SciPy and Pillow. Pillow is needed only to author the atlas.
"""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import map_coordinates
from scipy.spatial import Delaunay

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets/source/voice-avatar"
SIZE = 418
COLUMNS = 8
POSE_STEPS = 8

# Landmarks for the original poses and the second set of photographed in-betweens.
POSES = (
    (
        (0, 0, 512, 512), (256, 98), (181, 149), .68,
        ((253, 37), (205, 76), (302, 75), (256, 98), (216, 126), (293, 124),
         (258, 131), (257, 159), (225, 178), (288, 175), (155, 201), (366, 203),
         (119, 329), (408, 328), (229, 388), (322, 390), (258, 289), (151, 455), (390, 454)),
    ),
    (
        (512, 0, 1024, 512), (770, 96), (184, 145), .68,
        ((766, 31), (716, 71), (816, 72), (770, 96), (727, 123), (809, 122),
         (771, 129), (770, 156), (739, 176), (800, 176), (678, 172), (864, 176),
         (634, 318), (913, 317), (663, 449), (891, 451), (772, 274), (678, 461), (865, 461)),
    ),
    (
        (1024, 0, 1536, 512), (1282, 93), (189, 134), .69,
        ((1277, 24), (1228, 67), (1330, 66), (1282, 93), (1240, 123), (1320, 121),
         (1285, 128), (1285, 161), (1249, 169), (1319, 165), (1183, 141), (1379, 142),
         (1137, 303), (1433, 303), (1164, 445), (1401, 447), (1285, 245),
         (1194, 459), (1360, 459)),
    ),
    (
        (0, 512, 512, 1024), (257, 598), (191, 119), .72,
        ((252, 536), (211, 571), (303, 570), (257, 598), (221, 621), (294, 623),
         (260, 630), (260, 654), (231, 676), (287, 669), (151, 654), (356, 654),
         (114, 799), (404, 798), (143, 904), (369, 907), (257, 759), (167, 971), (357, 972)),
    ),
    (
        (512, 512, 1024, 1024), (768, 580), (194, 103), .75,
        ((763, 522), (721, 553), (813, 553), (768, 580), (733, 606), (803, 604),
         (770, 607), (772, 634), (743, 659), (802, 656), (681, 669), (863, 668),
         (634, 826), (919, 824), (645, 947), (891, 951), (774, 755), (684, 981), (865, 981)),
    ),
    (
        (1024, 512, 1536, 1024), (1278, 575), (195, 86), .76,
        ((1278, 522), (1235, 550), (1322, 550), (1278, 575), (1246, 601), (1313, 599),
         (1281, 601), (1281, 626), (1251, 654), (1308, 649), (1199, 666), (1364, 668),
         (1156, 837), (1414, 833), (1177, 960), (1391, 958), (1283, 764),
         (1196, 989), (1366, 989)),
    ),
)

BETWEEN_POSES = (
    (
        (0, 0, 512, 512), (286, 115), (183, 147), .67,
        ((283, 45), (235, 81), (333, 80), (286, 115), (249, 142), (323, 140),
         (289, 152), (290, 180), (257, 188), (319, 185), (170, 204), (375, 206),
         (107, 345), (420, 349), (235, 349), (360, 383), (280, 294), (156, 461), (375, 459)),
    ),
    (
        (512, 0, 1024, 512), (774, 128), (186, 139), .68,
        ((768, 53), (719, 91), (821, 91), (774, 128), (734, 149), (811, 147),
         (778, 165), (780, 192), (745, 204), (809, 198), (670, 199), (874, 199),
         (591, 317), (942, 326), (635, 417), (880, 424), (771, 285), (698, 460), (852, 460)),
    ),
    (
        (1024, 0, 1536, 512), (1265, 112), (190, 127), .71,
        ((1262, 36), (1211, 76), (1320, 80), (1265, 112), (1227, 144), (1307, 142),
         (1270, 149), (1272, 183), (1235, 197), (1308, 191), (1150, 168), (1397, 168),
         (1100, 296), (1455, 296), (1131, 453), (1401, 449), (1266, 248), (1194, 465), (1370, 463)),
    ),
    (
        (0, 512, 512, 1024), (256, 590), (192, 112), .73,
        ((249, 520), (204, 553), (302, 554), (256, 590), (220, 620), (294, 616),
         (258, 627), (261, 650), (223, 670), (291, 667), (141, 669), (378, 650),
         (90, 784), (435, 769), (132, 910), (391, 885), (254, 751), (156, 987), (351, 987)),
    ),
    (
        (512, 512, 1024, 1024), (756, 571), (194, 96), .75,
        ((753, 510), (707, 542), (799, 543), (756, 571), (720, 597), (789, 596),
         (759, 606), (762, 630), (729, 651), (790, 648), (657, 670), (857, 663),
         (605, 809), (930, 796), (641, 949), (900, 937), (774, 753), (684, 983), (866, 983)),
    ),
    (
        (1024, 512, 1536, 1024), (1268, 569), (195, 91), .76,
        ((1264, 509), (1222, 541), (1310, 539), (1268, 569), (1235, 599), (1304, 594),
         (1269, 602), (1271, 629), (1244, 652), (1302, 644), (1179, 674), (1360, 670),
         (1139, 824), (1422, 821), (1156, 955), (1397, 951), (1280, 756), (1204, 989), (1366, 989)),
    ),
)


def pose_layer(sheet, definition):
    box, anchor, position, scale, marks = definition
    crop = sheet.crop(box)
    sprite = crop.resize(tuple(round(size * scale) for size in crop.size), Image.Resampling.LANCZOS)
    origin = np.asarray(position) - (np.asarray(anchor) - box[:2]) * scale
    layer = Image.new("RGBA", (SIZE, SIZE))
    layer.paste(sprite, tuple(np.rint(origin).astype(int)))
    # Fixed canvas corners keep unused areas of the interpolated mesh transparent.
    bounds = ((0, 0), (SIZE - 1, 0), (0, SIZE - 1), (SIZE - 1, SIZE - 1))
    points = (np.asarray(marks) - anchor) * scale + position
    return np.asarray(layer, dtype=np.float32), np.vstack((bounds, points))


def morph(left, right, grid, target, mix):
    first, a = left
    last, b = right
    mesh = Delaunay(target)
    triangles = mesh.find_simplex(grid)
    transform = mesh.transform[triangles]
    fraction = np.einsum("nij,nj->ni", transform[:, :2], grid - transform[:, 2])
    barycenter = np.column_stack((fraction, 1 - fraction.sum(axis=1)))

    def warp(layer, points):
        xy = np.einsum("ni,nij->nj", barycenter, points[mesh.simplices[triangles]])
        # Premultiplied RGB keeps the translucent silhouette edges clean.
        premultiplied = layer.copy()
        premultiplied[:, :, :3] *= layer[:, :, 3:] / 255
        return np.column_stack([
            map_coordinates(premultiplied[:, :, c], xy[:, ::-1].T, order=1, mode="nearest")
            for c in range(4)
        ]).reshape(SIZE, SIZE, 4)

    blend = mix * mix * (3 - 2 * mix)
    result = (1 - blend) * warp(first, a) + blend * warp(last, b)
    alpha = result[:, :, 3:]
    result[:, :, :3] *= 255 / np.maximum(alpha, 1)
    opacity = np.clip((alpha / 255 - .24) / .70, 0, 1)
    alpha[:] = 255 * opacity * opacity * (3 - 2 * opacity)
    return Image.fromarray(np.rint(result).clip(0, 255).astype(np.uint8))


def face_axes(corners):
    left, right = np.asarray(corners)
    width = np.linalg.norm(right - left)
    along = (right - left) / width
    down = np.array([-along[1], along[0]])
    return (left + right) / 2, along, down, width


def transfer_expression(standing, face, source_corners, target_corners, width_ratio):
    """Match the mouth line, including roll, before feathering the cheek and jaw."""
    origin, across, down, width = face_axes(target_corners)
    source_origin, source_across, source_down, source_width = face_axes(source_corners)
    row, column = np.indices((SIZE, SIZE), dtype=np.float32)
    x = (column - origin[0]) * across[0] + (row - origin[1]) * across[1]
    y = (column - origin[0]) * down[0] + (row - origin[1]) * down[1]
    # Compress the lips and opening near the mouth line while keeping the surrounding skin aligned.
    compressed = np.interp(y, [-12, -5, -1.6, 0, 1.6, 5, 12],
                           [-12, -5, -3, 0, 3, 5, 12])
    along = source_width / (width * width_ratio)
    depth = source_width / width
    source_x = source_origin[0] + x * along * source_across[0] + compressed * depth * source_down[0]
    source_y = source_origin[1] + x * along * source_across[1] + compressed * depth * source_down[1]
    texture = np.asarray(face, dtype=np.float32)
    patch = np.stack(
        [map_coordinates(texture[:, :, channel], (source_y, source_x), order=1, mode="nearest")
         for channel in range(3)],
        axis=2,
    )
    base = np.asarray(standing.convert("RGB"), dtype=np.float32)
    skin = (np.abs(x) < width * .35) & (y > -8.5) & (y < -5)
    light = np.median(base[skin] - patch[skin], axis=0).clip(-22, 22)
    distance = np.hypot(x / (width * .65), (y - .5) / 8.5)
    alpha = np.clip((1 - distance) / .28, 0, 1)[:, :, None]
    result = base + alpha * ((patch + light) - base)
    return Image.fromarray(np.rint(result).clip(0, 255).astype(np.uint8))


def render_atlas(destination: Path, preview: Path | None):
    plate = Image.open(ART / "office-v2.png").convert("RGBA")
    foreground_mask = Image.new("L", plate.size)
    ImageDraw.Draw(foreground_mask).polygon(
        [(0, 1048), (688, 1048), (768, 816), (776, 805), (787, 798),
         (1173, 790), (1184, 796), (1120, 1048), (1253, 1048),
         (1253, 1253), (0, 1253)],
        fill=255,
    )
    foreground = plate.copy()
    foreground.putalpha(foreground_mask)
    base = plate.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    foreground = foreground.resize((SIZE, SIZE), Image.Resampling.LANCZOS)

    def composite(character):
        return Image.alpha_composite(Image.alpha_composite(base, character), foreground)

    poses = Image.open(ART / "poses-v2.png").convert("RGBA")
    inbetweens = Image.open(ART / "poses-between.png").convert("RGBA")
    actors = []
    for key, intermediate in zip(POSES, BETWEEN_POSES, strict=True):
        actors.extend((pose_layer(poses, key), pose_layer(inbetweens, intermediate)))
    # The last intermediate and the standing key settle into the same final movement.
    actors[-2], actors[-1] = actors[-1], actors[-2]
    landmarks = np.array([points for _, points in actors])
    for _ in range(2):
        landmarks[1:-1] = .22 * landmarks[:-2] + .56 * landmarks[1:-1] + .22 * landmarks[2:]
    trajectory = PchipInterpolator(np.arange(len(actors)), landmarks, axis=0)
    grid = np.indices((SIZE, SIZE))[::-1].reshape(2, -1).T
    frames = [composite(Image.fromarray(actors[0][0].astype(np.uint8)))]
    for key, pair in enumerate(zip(actors, actors[1:], strict=False)):
        for step in range(1 if key == 0 else 0, POSE_STEPS):
            mix = step / POSE_STEPS
            frames.append(composite(morph(*pair, grid, trajectory(key + mix), mix)))
    frames.append(composite(Image.fromarray(actors[-1][0].astype(np.uint8))))
    standing = frames[-1]

    faces = Image.open(ART / "mouths-v3.png").convert("RGB")
    _, anchor, location, scale, _ = POSES[-1]
    # Raw landmarks on the standing sprite. Viewer-right is higher.
    neutral_corners = (np.array([(1268, 604), (1299, 601)]) - anchor) * scale + location
    for corners, width_ratio in (
        (((315, 477), (520, 453)), .84),
        (((1043, 481), (1240, 459)), .83),
        (((1782, 471), (1946, 452)), .72),
    ):
        frames.append(transfer_expression(standing, faces, corners, neutral_corners, width_ratio))
    count = (len(actors) - 1) * POSE_STEPS + 1
    expression_frames = [image.convert("RGB") for image in frames[count - 1:]]
    for left, right in combinations(expression_frames, 2):
        frames.extend(Image.blend(left, right, alpha) for alpha in (.25, .5, .75))

    atlas = Image.new("RGB", (COLUMNS * SIZE, ((len(frames) + COLUMNS - 1) // COLUMNS) * SIZE))
    for index, frame in enumerate(frames):
        atlas.paste(frame, ((index % COLUMNS) * SIZE, (index // COLUMNS) * SIZE))
    atlas.save(destination, optimize=True)
    if preview:
        sequence = ([frames[0]] * 22 + frames[1:count] + frames[count - 1:count + 3] * 10
                    + list(reversed(frames[:count - 1])) + [frames[0]] * 16)
        sequence[0].save(preview, save_all=True, append_images=sequence[1:], duration=20, loop=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    render_atlas(args.destination, args.preview)
