"""Import source model files through a format adapter."""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Matrix

import internal_deps


FORMAT_ADAPTERS = {
    "pmx": "mmd",
    "pmd": "mmd",
    "fbx": "fbx",
    "glb": "gltf",
    "gltf": "gltf",
    "vrm": "vrm",
    "blend": "blender",
    "obj": "obj",
}


def adapter_for_source(
    source: Path,
    requested_adapter: str | None = None,
    source_format: str | None = None,
) -> str:
    if requested_adapter:
        return requested_adapter
    if source_format and source_format in FORMAT_ADAPTERS:
        return FORMAT_ADAPTERS[source_format]
    suffix = source.suffix.lower().lstrip(".")
    try:
        return FORMAT_ADAPTERS[suffix]
    except KeyError as error:
        raise RuntimeError(
            f"Unsupported source format: {source.suffix}"
        ) from error


def ensure_mmd_tools() -> None:
    if hasattr(bpy.types, "MMD_TOOLS_OT_import_model"):
        return
    internal_deps.ensure_mmd_tools()
    if not hasattr(bpy.types, "MMD_TOOLS_OT_import_model"):
        raise RuntimeError("The internal MMD Tools add-on did not register.")


def imported_objects(before: set[str]) -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.context.scene.objects
        if obj.name not in before
    ]


def scale_imported_objects(
    objects: list[bpy.types.Object],
    scale: float,
) -> None:
    if abs(scale - 1.0) <= 1e-12:
        return
    object_names = {obj.name for obj in objects}
    roots = [
        obj
        for obj in objects
        if obj.parent is None or obj.parent.name not in object_names
    ]
    for root in roots:
        root.matrix_world = Matrix.Scale(scale, 4) @ root.matrix_world
    # Children read their parent through the dependency graph, so their
    # matrix_world stays stale until the view layer is evaluated.
    bpy.context.view_layer.update()


def import_mmd(source: Path, scale: float) -> list[bpy.types.Object]:
    ensure_mmd_tools()
    before = set(bpy.context.scene.objects.keys())
    result = bpy.ops.mmd_tools.import_model(
        filepath=str(source),
        scale=scale,
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
    return imported_objects(before)


def import_fbx(source: Path, scale: float) -> list[bpy.types.Object]:
    before = set(bpy.context.scene.objects.keys())
    result = bpy.ops.import_scene.fbx(filepath=str(source))
    if "CANCELLED" in result:
        raise RuntimeError(f"FBX import failed: {result}")
    objects = imported_objects(before)
    scale_imported_objects(objects, scale)
    return objects


def import_gltf(source: Path, scale: float) -> list[bpy.types.Object]:
    before = set(bpy.context.scene.objects.keys())
    result = bpy.ops.import_scene.gltf(filepath=str(source))
    if "CANCELLED" in result:
        raise RuntimeError(f"GLTF import failed: {result}")
    objects = imported_objects(before)
    scale_imported_objects(objects, scale)
    return objects


def import_vrm(source: Path, scale: float) -> list[bpy.types.Object]:
    if not hasattr(bpy.ops.import_scene, "vrm"):
        raise RuntimeError(
            "VRM importer is not available. Install and enable a VRM add-on."
        )
    before = set(bpy.context.scene.objects.keys())
    result = bpy.ops.import_scene.vrm(filepath=str(source))
    if "CANCELLED" in result:
        raise RuntimeError(f"VRM import failed: {result}")
    objects = imported_objects(before)
    scale_imported_objects(objects, scale)
    return objects


def import_obj(source: Path, scale: float) -> list[bpy.types.Object]:
    before = set(bpy.context.scene.objects.keys())
    result = bpy.ops.wm.obj_import(filepath=str(source))
    if "CANCELLED" in result:
        raise RuntimeError(f"OBJ import failed: {result}")
    objects = imported_objects(before)
    scale_imported_objects(objects, scale)
    return objects


def import_blend(source: Path, scale: float) -> list[bpy.types.Object]:
    before = set(bpy.context.scene.objects.keys())
    with bpy.data.libraries.load(str(source), link=False) as (
        data_from,
        data_to,
    ):
        data_to.objects = list(data_from.objects)
    objects = [obj for obj in data_to.objects if obj is not None]
    for obj in objects:
        if obj.users_collection:
            continue
        bpy.context.scene.collection.objects.link(obj)
    scale_imported_objects(objects, scale)
    return imported_objects(before)


def import_source(
    source: Path,
    adapter: str,
    scale: float = 1.0,
) -> list[bpy.types.Object]:
    if adapter == "mmd":
        return import_mmd(source, scale)
    if adapter == "fbx":
        return import_fbx(source, scale)
    if adapter == "gltf":
        return import_gltf(source, scale)
    if adapter == "vrm":
        return import_vrm(source, scale)
    if adapter == "obj":
        return import_obj(source, scale)
    if adapter == "blender":
        return import_blend(source, scale)
    raise RuntimeError(f"Unsupported source adapter: {adapter}")


def find_imported_armature(
    objects: list[bpy.types.Object],
) -> bpy.types.Object:
    armatures = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(
            f"Expected one imported armature, found {len(armatures)}."
        )
    return armatures[0]


def find_imported_mesh(
    objects: list[bpy.types.Object],
) -> bpy.types.Object:
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("No imported mesh was found.")

    # Physics rigid-body helper meshes (mmd_tools) and other unskinned
    # helpers must never be merged into the character. When any skinned
    # mesh exists, restrict discovery to those.
    skinned = [
        obj
        for obj in meshes
        if any(
            modifier.type == "ARMATURE" and modifier.object is not None
            for modifier in obj.modifiers
        )
    ]
    candidates = skinned or meshes
    if len(candidates) == 1:
        return candidates[0]

    armatures = {
        modifier.object
        for obj in candidates
        for modifier in obj.modifiers
        if modifier.type == "ARMATURE" and modifier.object is not None
    }
    if len(armatures) > 1:
        raise RuntimeError(
            "Imported meshes use multiple armatures; merge or select one "
            "skinned character before running the shared split stage."
        )
    shape_key_sets = {
        tuple(
            key.name for key in obj.data.shape_keys.key_blocks
        )
        if obj.data.shape_keys
        else ()
        for obj in candidates
    }
    if len(shape_key_sets) > 1:
        raise RuntimeError(
            "Imported meshes have incompatible shape-key sets and cannot be "
            "joined safely. Provide one prepared skinned mesh."
        )

    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in candidates:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = candidates[0]
    result = bpy.ops.object.join()
    if "CANCELLED" in result:
        raise RuntimeError(f"Could not join imported meshes: {result}")
    return bpy.context.active_object
