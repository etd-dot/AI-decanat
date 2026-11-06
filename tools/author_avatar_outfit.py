"""Fit a project-owned business outfit to the exported MetaHuman (Blender Python).

The base shirt/shorts topology and skin-weight references derive from Epic Core
Data. New sleeves, trouser legs, lapels, collar and shoes are authored here.
"""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector, bvhtree, kdtree

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".runtime/unreal/authoring"
OUTPUT = ROOT / "assets/source/avatar"
MATERIALS = [
    ("Shirt", (0.72, 0.73, 0.70), 0.85),
    ("Jacket", (0.038, 0.045, 0.055), 0.86),
    ("Lapel", (0.047, 0.055, 0.065), 0.82),
    ("Trousers", (0.032, 0.039, 0.048), 0.88),
    ("Leather", (0.018, 0.014, 0.012), 0.42),
    ("Sole", (0.012, 0.012, 0.012), 0.8),
    ("Buttons", (0.016, 0.019, 0.023), 0.38),
]


def make_mesh(name, vertices, faces, material):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(bpy.data.materials[MATERIALS[material][0]])
    obj["office_material"] = material
    return obj


def split(source, name, contains, material):
    selected = [poly for poly in source.data.polygons if contains in source.data.materials[poly.material_index].name]
    indices = sorted({n for poly in selected for n in poly.vertices})
    remap = {old: new for new, old in enumerate(indices)}
    obj = make_mesh(name, [source.matrix_world @ source.data.vertices[n].co for n in indices], [[remap[n] for n in poly.vertices] for poly in selected], material)
    for group in source.vertex_groups:
        obj.vertex_groups.new(name=group.name)
    for old, new in remap.items():
        for group in source.data.vertices[old].groups:
            obj.vertex_groups[group.group].add([new], group.weight, "REPLACE")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.00005)
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def boundary_loops(bm):
    edges = {edge for edge in bm.edges if edge.is_boundary}
    loops = []
    while edges:
        edge = next(iter(edges))
        start, current = edge.verts
        chain = [start, current]
        edges.remove(edge)
        while current != start:
            candidates = [e for e in current.link_edges if e in edges]
            if not candidates:
                break
            edge = candidates[0]
            edges.remove(edge)
            current = edge.other_vert(current)
            if current != start:
                chain.append(current)
        if current == start and len(chain) >= 5:
            loops.append(chain)
    return loops


def extend_loop(bm, loop, centres, radii):
    centre = sum((v.co for v in loop), Vector()) / len(loop)
    direction = (centres[0] - centre).normalized()
    u = direction.cross(Vector((0, 1, 0))).normalized()
    if u.length < 0.5:
        u = direction.cross(Vector((1, 0, 0))).normalized()
    v = direction.cross(u).normalized()
    angles = [math.atan2((point.co - centre).dot(v), (point.co - centre).dot(u)) for point in loop]
    previous = loop
    last_direction = direction
    for n, (target, radius) in enumerate(zip(centres, radii, strict=True)):
        next_direction = (centres[min(n + 1, len(centres) - 1)] - centres[max(0, n - 1)]).normalized() if len(centres) > 1 else direction
        if next_direction.length < 0.5:
            next_direction = last_direction
        rotation = last_direction.rotation_difference(next_direction)
        u, v = rotation @ u, rotation @ v
        ring = [bm.verts.new(target + u * (math.cos(angle) * radius[0]) + v * (math.sin(angle) * radius[1])) for angle in angles]
        for i in range(len(ring)):
            j = (i + 1) % len(ring)
            bm.faces.new((previous[i], previous[j], ring[j], ring[i]))
        previous = ring
        last_direction = next_direction


def build(version="office_v4"):
    bpy.ops.wm.open_mainfile(filepath=str(WORK / "reference.blend"))
    source = next(obj for obj in bpy.data.objects if obj.type == "MESH" and "Outfits" in obj.name)
    body = next(obj for obj in bpy.data.objects if obj.type == "MESH" and "BodyMesh" in obj.name)
    reference = [source, body]
    bones = json.loads((WORK / "skeleton.json").read_text())["bones"]
    bone_index = {bone["name"]: n for n, bone in enumerate(bones)}
    matrices = []
    for bone in bones:
        x, y, z, w = bone["rotation"]
        matrix = Matrix.LocRotScale(Vector(bone["position"]), Quaternion((w, x, y, z)), Vector(bone["scale"]))
        matrices.append(matrix if bone["parent"] < 0 else matrices[bone["parent"]] @ matrix)

    def joint(name):
        p = matrices[bone_index[name]].translation
        return Vector((p.x / 100, -p.y / 100, p.z / 100))

    for name, colour, roughness in MATERIALS:
        material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        material.diffuse_color = (*colour, 1)
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*colour, 1)
        shader.inputs["Roughness"].default_value = roughness
    shirt = split(source, "Office_Shirt", "Shirt", 0)
    trousers = split(source, "Office_Trousers", "Short", 3)
    jacket = split(source, "Office_Jacket", "Shirt", 1)
    authored = [shirt, trousers, jacket]
    body_points = [body.matrix_world @ vertex.co for vertex in body.data.vertices]

    bm = bmesh.new()
    bm.from_mesh(trousers.data)
    bmesh.ops.bisect_plane(bm, geom=[*bm.verts, *bm.edges, *bm.faces], plane_co=(0, 0, 0.80), plane_no=(0, 0, 1), clear_inner=True, dist=0.00001)
    pants_loops = []
    leg_loops = {}
    for loop in boundary_loops(bm):
        centre = sum((v.co for v in loop), Vector()) / len(loop)
        if centre.z > 0.82:
            continue
        suffix = "l" if centre.x > 0 else "r"
        radius = sum((vertex.co - centre).length for vertex in loop) / len(loop)
        if suffix not in leg_loops or radius > leg_loops[suffix][0]:
            leg_loops[suffix] = (radius, loop, centre)
    for suffix, (_radius, loop, _centre) in leg_loops.items():
        knee, ankle = joint(f"calf_{suffix}"), joint(f"foot_{suffix}")
        centres, radii = [], []
        for z, rx, ry in ((0.74, .098, .105), (.66, .092, .094), (.57, .083, .085), (.49, .074, .078), (.40, .069, .074), (.31, .062, .068), (.22, .055, .061), (.14, .050, .056), (.095, .049, .055)):
            factor = max(0, min(1, (z - ankle.z) / (knee.z - ankle.z)))
            position = ankle.lerp(knee, factor)
            position.z = z
            sample_z = max(z, .19)
            section = [p for p in body_points if abs(p.z - sample_z) < .018 and p.x * (1 if suffix == "l" else -1) > .02]
            if section:
                low = [min(p[i] for p in section) for i in range(2)]
                high = [max(p[i] for p in section) for i in range(2)]
                position.x, position.y = (low[0]+high[0])/2, (low[1]+high[1])/2
                rx, ry = (high[0]-low[0])/2+.015, (high[1]-low[1])/2+.018
            centres.append(position)
            radii.append((rx, ry))
        extend_loop(bm, loop, centres, radii)
        pants_loops.append(len(loop))
    if len(pants_loops) < 2:
        raise RuntimeError(f"Cannot identify both trouser hems: {pants_loops}")
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(trousers.data)
    bm.free()

    bm = bmesh.new()
    bm.from_mesh(jacket.data)
    bm.normal_update()
    for vertex in bm.verts:
        vertex.co += vertex.normal * 0.013
    for suffix in ("l", "r"):
        shoulder, elbow = joint(f"upperarm_{suffix}"), joint(f"lowerarm_{suffix}")
        direction = (elbow - shoulder).normalized()
        sign = 1 if suffix == "l" else -1
        arm_faces = [face for face in bm.faces if face.calc_center_median().x * sign > .23]
        arm_edges = {edge for face in arm_faces for edge in face.edges}
        arm_vertices = {vertex for face in arm_faces for vertex in face.verts}
        bmesh.ops.bisect_plane(bm, geom=[*arm_vertices, *arm_edges, *arm_faces], plane_co=shoulder + direction * .15, plane_no=direction, clear_outer=True, dist=.00001)
    sleeves = []
    for loop in boundary_loops(bm):
        centre = sum((v.co for v in loop), Vector()) / len(loop)
        if abs(centre.x) < .25:
            continue
        suffix = "l" if centre.x > 0 else "r"
        elbow, wrist = joint(f"lowerarm_{suffix}"), joint(f"hand_{suffix}")
        centres = [centre.lerp(elbow, .5), elbow, elbow.lerp(wrist, .3), elbow.lerp(wrist, .65), elbow.lerp(wrist, .94)]
        extend_loop(bm, loop, centres, [(.083, .083), (.081, .079), (.074, .072), (.063, .062), (.049, .048)])
        sleeves.append(len(loop))
    if len(sleeves) < 2:
        raise RuntimeError(f"Cannot identify jacket cuffs: {sleeves}")
    # Split the front panel along exact lapel lines instead of deleting whole
    # triangles, which leaves a ragged opening at this mesh's tessellation.
    planes = [((.017,0,0),(1,0,0)),((-.017,0,0),(1,0,0)),((.101,0,0),(1,0,0)),((-.101,0,0),(1,0,0)),((0,0,1.25),(0,0,1)),((0,0,1.61),(0,0,1)),((.017,0,1.25),(1,0,-.084/.36)),((-.017,0,1.25),(-1,0,-.084/.36))]
    for point, normal in planes:
        faces = [face for face in bm.faces if face.calc_center_median().y < -.015 and abs(face.calc_center_median().x) < .22]
        edges = {edge for face in faces for edge in face.edges}
        vertices_ = {vertex for face in faces for vertex in face.verts}
        bmesh.ops.bisect_plane(bm, geom=[*vertices_,*edges,*faces],plane_co=point,plane_no=normal,dist=.000001)
    jacket.data.materials.append(bpy.data.materials["Shirt"])
    for face in bm.faces:
        centre = face.calc_center_median()
        gap = .017 + max(0, min(1, (centre.z - 1.25) / .36)) * .084
        if centre.y < -.015 and abs(centre.x) < gap:
            face.material_index = 1
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(jacket.data)
    bm.free()

    shirt_points = [vertex.co for vertex in shirt.data.vertices]
    surface_bm = bmesh.new()
    surface_bm.from_mesh(shirt.data)
    surface = bvhtree.BVHTree.FromBMesh(surface_bm)

    def front(z, x=0):
        hit, _normal, _index, _distance = surface.ray_cast(Vector((x,-1,z)),Vector((0,1,0)))
        if hit is not None and hit.y < -.015:
            return hit.y
        points = [p.y for p in shirt_points if abs(p.z - z) < .035 and abs(p.x-x) < .03 and p.y < -.015]
        return min(points) if points else -.13

    for sign in (-1, 1):
        lapel = [(sign*x, front(z,sign*x)-.020, z) for x,z in ((.085,1.61),(.145,1.49),(.111,1.455),(.142,1.425),(.025,1.245),(.050,1.435))]
        authored.append(make_mesh(f"Office_Lapel_{sign}", lapel, [(0,1,2,3,4,5)], 2))
        collar = [(sign*x, front(min(z,1.60),sign*x)-.006, z) for x,z in ((.015,1.625),(.075,1.625),(.098,1.535),(.040,1.570))]
        authored.append(make_mesh(f"Office_Collar_{sign}", collar, [(0,1,2,3)], 0))
    for z in (1.20,1.10):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, location=(.023,front(z)-.025,z))
        button = bpy.context.object
        button.name = "Office_Button"
        button.scale = (.006,.003,.006)
        button.data.materials.append(bpy.data.materials["Buttons"])
        button["office_material"] = 6
        authored.append(button)

    for suffix in ("l","r"):
        ankle, toe = joint(f"foot_{suffix}"), joint(f"ball_{suffix}")
        forward = toe-ankle
        forward.z = 0
        forward.normalize()
        side = forward.cross(Vector((0,0,1))).normalized()
        foot_points = [p for p in body_points if p.z < .13 and p.x * (1 if suffix == "l" else -1) > .02]
        along = [p.dot(forward) for p in foot_points]
        across = [p.dot(side) for p in foot_points]
        base = forward*((max(along)+min(along))/2)+side*((max(across)+min(across))/2)
        half_length, half_width = (max(along)-min(along))/2+.012, (max(across)-min(across))/2+.010
        centres = [base,base,base,base.lerp(ankle,.65),ankle]
        heights = [-.002,.027,.067,.110,.145]
        widths = [half_width,half_width,half_width,half_width*.85,.048]
        lengths = [half_length,half_length,half_length*.96,half_length*.65,.067]
        vertices=[]
        for centre,z,width,length in zip(centres,heights,widths,lengths,strict=True):
            for n in range(32):
                angle=n*math.tau/32
                sine, cosine = math.sin(angle), math.cos(angle)
                p=centre+side*(math.copysign(abs(sine)**.5,sine)*width)+forward*(math.copysign(abs(cosine)**.5,cosine)*length)
                p.z=z
                vertices.append(p)
        faces=[]
        for ring in range(len(heights)-1):
            for n in range(32):
                faces.append((ring*32+n,ring*32+(n+1)%32,(ring+1)*32+(n+1)%32,(ring+1)*32+n))
        shoe=make_mesh(f"Office_Shoe_{suffix}",vertices,faces,4)
        authored.append(shoe)
        sole=make_mesh(f"Office_Sole_{suffix}",vertices[:64],faces[:32]+[tuple(reversed(range(32)))],5)
        authored.append(sole)

    samples=[]
    # Object scale changes (notably buttons) are lazy until the depsgraph updates.
    bpy.context.view_layer.update()
    for obj in authored:
        if obj.name.startswith("Office_Button") and max(obj.dimensions) > .025:
            raise RuntimeError("Button scale was not evaluated; refusing an oversized wardrobe export")
    for source_mesh in reference:
        for vertex in source_mesh.data.vertices:
            weights=[(source_mesh.vertex_groups[group.group].name,group.weight) for group in vertex.groups if source_mesh.vertex_groups[group.group].name in bone_index and group.weight>.0001]
            if weights:
                samples.append((source_mesh.matrix_world@vertex.co,weights))
    tree=kdtree.KDTree(len(samples))
    for n,(position,_weights) in enumerate(samples):
        tree.insert(position,n)
    tree.balance()
    vertices,weights,triangles=[],[],[]
    for obj in authored:
        offset=len(vertices)
        for vertex in obj.data.vertices:
            point=obj.matrix_world@vertex.co
            total={bone_index[obj.vertex_groups[group.group].name]:group.weight for group in vertex.groups if obj.vertex_groups[group.group].name in bone_index}
            if not total:
                for _co,n,distance in tree.find_n(point,4):
                    for bone,weight in samples[n][1]:
                        total[bone_index[bone]]=total.get(bone_index[bone],0)+weight/max(.001,distance)**2
            selected=sorted(total.items(),key=lambda pair:pair[1],reverse=True)[:8]
            normalizer=sum(weight for _bone,weight in selected)
            normalized=[(bone,weight/normalizer) for bone,weight in selected]
            weights.append(normalized)
            vertices.append([round(point.x*100,5),round(-point.y*100,5),round(point.z*100,5)])
            for bone,weight in normalized:
                group=obj.vertex_groups.get(bones[bone]["name"]) or obj.vertex_groups.new(name=bones[bone]["name"])
                group.add([vertex.index],weight,"REPLACE")
        obj.data.calc_loop_triangles()
        material=int(obj["office_material"])
        for triangle in obj.data.loop_triangles:
            a,b,c=triangle.vertices
            triangle_material = 0 if obj == jacket and triangle.material_index == 1 else material
            triangles.append([offset+a,offset+c,offset+b,triangle_material])
        for polygon in obj.data.polygons:
            polygon.use_smooth=True
    result={"asset":f"/Game/Decanat/Wardrobe/{version}/SKM_OfficeOutfit","materials":[{"name":name,"colour":colour,"roughness":roughness} for name,colour,roughness in MATERIALS],"vertices_cm":vertices,"weights":weights,"triangles":triangles,"derived_from":"Epic MetaHuman Creator Core Data default garment and reference weights","review_required":True}
    (WORK/"office-outfit.json").write_text(json.dumps(result,separators=(",",":")),encoding="utf-8")
    for obj in list(bpy.data.objects):
        if obj not in authored:
            bpy.data.objects.remove(obj,do_unlink=True)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT/f"office-outfit-{version}.blend"))
    print(json.dumps({"vertices":len(vertices),"triangles":len(triangles),"trouser_hem_loops":pants_loops,"jacket_cuff_loops":sleeves}))


if __name__ == "__main__":
    build(sys.argv[sys.argv.index("--")+1] if "--" in sys.argv else "office_v4")
