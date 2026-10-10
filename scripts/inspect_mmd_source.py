"""Inspect a PMX source model without modifying it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import internal_deps


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(argv[separator:])


def ensure_mmd_tools() -> None:
    if hasattr(bpy.types, "MMD_TOOLS_OT_import_model"):
        return
    internal_deps.ensure_mmd_tools()
    if not hasattr(bpy.types, "MMD_TOOLS_OT_import_model"):
        raise RuntimeError("The internal MMD Tools add-on did not register.")


def clear_scene() -> None:
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)


def bounds_for_vertices(obj: bpy.types.Object, indices: set[int]) -> dict | None:
    if not indices:
        return None
    coordinates = [
        obj.matrix_world @ obj.data.vertices[index].co
        for index in indices
    ]
    minimum = [
        min(coordinate[axis] for coordinate in coordinates)
        for axis in range(3)
    ]
    maximum = [
        max(coordinate[axis] for coordinate in coordinates)
        for axis in range(3)
    ]
    return {
        "min": minimum,
        "max": maximum,
        "center": [
            (minimum[axis] + maximum[axis]) * 0.5
            for axis in range(3)
        ],
    }


def dominant_weight_bones(
    obj: bpy.types.Object,
    indices: set[int],
    limit: int = 20,
) -> list[dict]:
    totals: dict[str, float] = {}
    for index in indices:
        vertex = obj.data.vertices[index]
        for membership in vertex.groups:
            group_name = obj.vertex_groups[membership.group].name
            totals[group_name] = totals.get(group_name, 0.0) + membership.weight
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"name": name, "total_weight": round(weight, 6)}
        for name, weight in ranked[:limit]
    ]


def inspect_mesh(obj: bpy.types.Object) -> dict:
    mesh = obj.data
    vertices_by_material: dict[int, set[int]] = {
        index: set()
        for index in range(len(mesh.materials))
    }
    for polygon in mesh.polygons:
        vertices_by_material.setdefault(polygon.material_index, set()).update(
            polygon.vertices
        )

    materials = []
    for index in range(len(mesh.materials)):
        material = mesh.materials[index]
        indices = vertices_by_material.get(index, set())
        materials.append(
            {
                "slot": index,
                "name": material.name if material else None,
                "used": bool(indices),
                "vertex_count": len(indices),
                "bounds_world": bounds_for_vertices(obj, indices),
                "dominant_weight_bones": dominant_weight_bones(obj, indices),
            }
        )

    shape_keys = []
    if mesh.shape_keys:
        shape_keys = [
            key_block.name
            for key_block in mesh.shape_keys.key_blocks
        ]

    return {
        "name": obj.name,
        "data_name": mesh.name,
        "vertex_count": len(mesh.vertices),
        "polygon_count": len(mesh.polygons),
        "material_count": len(mesh.materials),
        "uv_layers": [layer.name for layer in mesh.uv_layers],
        "vertex_group_count": len(obj.vertex_groups),
        "shape_keys": shape_keys,
        "materials": materials,
    }


def inspect_armature(obj: bpy.types.Object) -> dict:
    return {
        "name": obj.name,
        "data_name": obj.data.name,
        "bone_count": len(obj.data.bones),
        "bones": [
            {
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "head": list(bone.head_local),
                "tail": list(bone.tail_local),
            }
            for bone in obj.data.bones
        ],
    }


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    report_path = args.report.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source model not found: {source}")

    clear_scene()
    ensure_mmd_tools()
    result = bpy.ops.mmd_tools.import_model(
        filepath=str(source),
        scale=args.scale,
        types={"MESH", "ARMATURE", "PHYSICS", "DISPLAY", "MORPHS"},
        clean_model=True,
        remove_doubles=False,
        fix_bone_order=True,
        fix_ik_links=False,
        apply_bone_fixed_axis=False,
        rename_bones=True,
        use_underscore=False,
        use_mipmap=True,
        save_log=False,
        log_level="ERROR",
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"MMD import failed: {result}")

    meshes = [
        inspect_mesh(obj)
        for obj in bpy.context.scene.objects
        if obj.type == "MESH"
    ]
    armatures = [
        inspect_armature(obj)
        for obj in bpy.context.scene.objects
        if obj.type == "ARMATURE"
    ]
    images = sorted(
        {
            image.filepath
            for image in bpy.data.images
            if image.filepath
        }
    )
    report = {
        "source": str(source),
        "scale": args.scale,
        "mesh_count": len(meshes),
        "armature_count": len(armatures),
        "image_count": len(images),
        "images": images,
        "meshes": meshes,
        "armatures": armatures,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"MMD_SOURCE_REPORT={report_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
