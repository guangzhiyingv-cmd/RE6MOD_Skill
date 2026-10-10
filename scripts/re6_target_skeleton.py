"""RE6 target skeleton helpers: semantic preset lookup and template measurement.

RE6 bone names are ordinal (``RE6Bone_###``), so a bone only becomes
meaningful through the semantic preset shipped with the skill. This module
resolves those semantics, imports the reference model's armature, and measures
the reference height used to calibrate a source model.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy

import internal_deps


def enable_re6_addon() -> str:
    """Load the skill's internal RE6 Model Editor and return its module name."""
    return internal_deps.ensure_re6_model_editor().__name__


def load_bone_semantics(path: Path) -> dict:
    """Read a Modding Toolkit style preset into {semantic: [bone names]}."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        raise RuntimeError(f"Bone semantics file has no mappings: {path}")
    semantics = {}
    for key, entry in mappings.items():
        names = list(entry.get("main") or [])
        if names:
            semantics[key] = names
    if not semantics:
        raise RuntimeError(f"Bone semantics file is empty: {path}")
    return semantics


def resolve_semantic_bones(semantics: dict, keys) -> dict:
    """Resolve semantic keys to target bone names."""
    resolved = {}
    for key in keys:
        names = semantics.get(key)
        if not names:
            raise RuntimeError(f"Bone semantics do not define {key!r}")
        resolved[key] = names[0]
    return resolved


def import_reference_armature(model_path: Path):
    """Import one reference model as an armature only."""
    enable_re6_addon()
    before_objects = set(bpy.data.objects.keys())
    before_collections = set(bpy.data.collections.keys())
    result = bpy.ops.re6_mod.import_re6_mod(
        filepath=str(model_path),
        importArmatureOnly=True,
        importShadow=False,
        loadMrlData=False,
        loadMaterials=False,
        loadPhysics=False,
        # Do not create the template's collection pair here: Stage 3 imports
        # the same template to inherit those properties, and a leftover
        # collection would force Blender to suffix the real one with ".001".
        addNestedCollections=False,
        createCollections=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError(
            f"Could not import the RE6 reference model: {model_path}"
        )
    imported = [
        bpy.data.objects[name]
        for name in bpy.data.objects.keys()
        if name not in before_objects
    ]
    armatures = [obj for obj in imported if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(
            "Expected one armature in the RE6 reference model, "
            f"found {len(armatures)}."
        )
    return armatures[0], imported, before_collections


def remove_imported(
    imported: list,
    before_collections: set[str],
) -> None:
    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)
    for name in set(bpy.data.collections.keys()) - before_collections:
        collection = bpy.data.collections.get(name)
        if collection is not None and not collection.objects:
            bpy.data.collections.remove(collection)


def bone_world_point(
    armature: bpy.types.Object,
    bone_name: str,
    point: str = "head",
) -> list[float]:
    bone = armature.data.bones.get(bone_name)
    if bone is None:
        raise RuntimeError(
            f"Bone {bone_name!r} is missing from {armature.name!r}"
        )
    local = bone.head_local if point == "head" else bone.tail_local
    world = armature.matrix_world @ local
    return [world.x, world.y, world.z]


def measure_reference(
    profile_path: Path,
    alignment: dict,
) -> dict:
    """Measure the alignment height of the profile's reference model."""
    skill_root = Path(profile_path).resolve().parents[1]
    model_path = (skill_root / alignment["template_model"]).resolve()
    semantics_path = (skill_root / alignment["bone_semantics"]).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(
            f"RE6 reference model not found: {model_path}"
        )
    if not semantics_path.is_file():
        raise FileNotFoundError(
            f"RE6 bone semantics not found: {semantics_path}"
        )

    semantics = load_bone_semantics(semantics_path)
    reference_keys = list(alignment["reference_bones"])
    resolved = resolve_semantic_bones(semantics, reference_keys)

    armature, imported, before_collections = import_reference_armature(
        model_path
    )
    try:
        points = {
            key: bone_world_point(armature, bone_name)
            for key, bone_name in resolved.items()
        }
        height = sum(point[2] for point in points.values()) / len(points)
        report = {
            "mode": "shoulder",
            "template_model": str(model_path),
            "bone_semantics": str(semantics_path),
            "armature": armature.name,
            "bone_count": len(armature.data.bones),
            "reference_bones": resolved,
            "bone_points": points,
            "height": height,
            "point": alignment.get("point", "head"),
        }
    finally:
        remove_imported(imported, before_collections)
    return report
