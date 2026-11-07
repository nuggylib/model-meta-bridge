import functools
import math
import os
import struct
from array import array

import bmesh
import bpy


def mesh_objects():
    """Sorted by name, so a cursor names the same mesh on every page."""
    return sorted(
        (o for o in bpy.context.scene.objects if o.type == "MESH"),
        key=lambda o: o.name,
    )


def summary(obj):
    return {
        "name": obj.name,
        "vertex_groups": [group.name for group in obj.vertex_groups],
        "material_slots": [slot.name for slot in obj.material_slots],
    }


def weight_report(target):
    name, group_name, weight = target["mesh"], target["vertex_group"], target["weight"]
    report = {"mesh": name, "vertex_group": group_name}
    obj = _mesh_object(name)
    if obj is None:
        return {**report, "error": "no mesh"}
    group = obj.vertex_groups.get(group_name)
    if group is None:
        return {**report, "error": "no vertex group"}
    index = group.index
    # Blender stores weights as float32: a painted 0.9 is 0.899999976, so compare in float32.
    (stored,) = struct.unpack("f", struct.pack("f", weight))
    assigned = at_weight = 0
    for vertex in obj.data.vertices:
        for element in vertex.groups:
            if element.group == index:
                assigned += 1
                at_weight += element.weight == stored
                break
    return {
        **report,
        "vertices": len(obj.data.vertices),
        "assigned": assigned,
        "at_weight": at_weight,
    }


def texture_report(target):
    name, maps = target["mesh"], target["maps"]
    obj = _mesh_object(name)
    if obj is None:
        return {"mesh": name, "error": "no mesh"}
    files = _saved_image_files(obj)
    return {
        "mesh": name,
        "maps": {
            texture_map: any(f.endswith(f"{name}_{texture_map}.png") for f in files)
            for texture_map in maps
        },
    }


def uv_report(target):
    name = target["mesh"]
    obj = _mesh_object(name)
    if obj is None:
        return {"mesh": name, "error": "no mesh"}
    if obj.mode == "EDIT":  # Blender empties the mesh's UV arrays until edit mode ends.
        return {"mesh": name, "error": "in edit mode"}
    polygons = obj.data.polygons
    starts = array("i", [0]) * len(polygons)
    totals = array("i", [0]) * len(polygons)
    polygons.foreach_get("loop_start", starts)
    polygons.foreach_get("loop_total", totals)
    uv_maps = [(layer.name, *_uv_coords(layer)) for layer in obj.data.uv_layers]
    return {
        "mesh": name,
        "faces": len(polygons),
        "has_default_uv_map": any(_is_default_layout(us, vs) for _, us, vs in uv_maps),
        "uv_maps": [
            {"name": map_name, "zero_area": _zero_area_faces(us, vs, starts, totals)}
            for map_name, us, vs in uv_maps
        ],
    }


def modifier_report(target):
    name = target["mesh"]
    obj = _mesh_object(name)
    if obj is None:
        return {"mesh": name, "error": "no mesh"}
    return {"mesh": name, "modifiers": [modifier.type for modifier in obj.modifiers]}


def _uv_coords(layer):
    coords = array("f", [0]) * (2 * len(layer.uv))
    layer.uv.foreach_get("vector", coords)
    return coords[0::2], coords[1::2]


def _zero_area_faces(us, vs, starts, totals):
    zero_area = 0
    for start, total in zip(starts, totals, strict=True):
        u, v = us[start : start + total], vs[start : start + total]
        doubled_area = sum(u[i - 1] * v[i] - u[i] * v[i - 1] for i in range(total))
        zero_area += doubled_area == 0
    return zero_area


def _is_default_layout(us, vs):
    coords = set(zip(us, vs, strict=True))
    return any(len(layout & coords) * 2 > len(layout) for layout in _default_layouts())


@functools.cache
def _default_layouts():
    """Each Add-menu primitive's generated UVs at its default settings."""
    builders = (
        lambda bm: bmesh.ops.create_cube(bm, size=2, calc_uvs=True),
        lambda bm: bmesh.ops.create_grid(
            bm, x_segments=1, y_segments=1, size=1, calc_uvs=True
        ),
        lambda bm: bmesh.ops.create_grid(
            bm, x_segments=10, y_segments=10, size=1, calc_uvs=True
        ),
        lambda bm: bmesh.ops.create_uvsphere(
            bm, u_segments=32, v_segments=16, radius=1, calc_uvs=True
        ),
        lambda bm: bmesh.ops.create_icosphere(
            bm, subdivisions=2, radius=1, calc_uvs=True
        ),
        lambda bm: bmesh.ops.create_cone(
            bm, cap_ends=True, segments=32, radius1=1, radius2=1, depth=2, calc_uvs=True
        ),
        lambda bm: bmesh.ops.create_cone(
            bm, cap_ends=True, segments=32, radius1=1, radius2=0, depth=2, calc_uvs=True
        ),
    )
    return [*(_bmesh_layout(build) for build in builders), _torus_layout(48, 12)]


def _bmesh_layout(build):
    # Free-standing: never linked to bpy.data, so the scene is untouched.
    bm = bmesh.new()
    try:
        layer = bm.loops.layers.uv.new()
        build(bm)
        return frozenset(
            tuple(loop[layer].uv) for face in bm.faces for loop in face.loops
        )
    finally:
        bm.free()


def _torus_layout(major_segments, minor_segments):
    # The torus is a Python operator with no bmesh builder; this mirrors add_uvs() in
    # Blender's scripts/startup/bl_operators/add_mesh_torus.py.
    def steps(count):
        step = 1.0 / count
        wrap = 1.0 - step / 2.0
        prev = 0.5 + math.fmod(0.5, step)
        values = []
        for _ in range(count):
            following = prev + step
            values += (prev, following)
            prev = following - 1.0 if following > wrap else following
        return array("f", values)  # Rounded to float32, as the mesh stores them.

    return frozenset(
        (u, v) for u in steps(major_segments) for v in steps(minor_segments)
    )


def _mesh_object(name):
    obj = bpy.context.scene.objects.get(name)
    return obj if obj is not None and obj.type == "MESH" else None


def _saved_image_files(obj):
    files = set()
    for slot in obj.material_slots:
        material = slot.material
        if material is None or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            image = node.image if node.type == "TEX_IMAGE" else None
            if image is None or image.source != "FILE":
                continue
            path = bpy.path.abspath(image.filepath, library=image.library)
            if os.path.isfile(path):
                files.add(os.path.basename(path))
    return files
