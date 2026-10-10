"""Import, calibrate, and split a source model into body, face, and hair."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import re6_target_skeleton
import internal_deps
import source_import
import source_rig_utils


DEFAULT_PART_OBJECT_NAMES = {
    "body": "00_Body",
    "face": "10_Face",
    "hair": "20_Hair",
}
DEFAULT_DELETED_MATERIALS = ("目影", "发影")


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv[separator:])


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def enable_addon(module: str) -> None:
    internal_deps.ensure_modding_toolkit()


def set_active(obj: bpy.types.Object) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(
    obj: bpy.types.Object,
    collection: bpy.types.Collection,
) -> None:
    for source_collection in list(obj.users_collection):
        source_collection.objects.unlink(obj)
    collection.objects.link(obj)


def mesh_material_names(obj: bpy.types.Object) -> list[str]:
    return [
        slot.material.name
        for slot in obj.material_slots
        if slot.material is not None
    ]


def delete_plus_material_meshes(
    objects: list[bpy.types.Object],
) -> tuple[list[bpy.types.Object], list[dict]]:
    remaining = []
    deleted = []
    for obj in objects:
        material_names = mesh_material_names(obj)
        plus_materials = [
            name
            for name in material_names
            if name.endswith("+")
        ]
        if not plus_materials:
            remaining.append(obj)
            continue

        assigned_materials = set(plus_materials)
        other_materials = sorted(set(material_names) - assigned_materials)
        if other_materials:
            raise RuntimeError(
                f"Object {obj.name!r} mixes '+' materials with other "
                f"materials: {other_materials}"
            )

        deleted.append(
            {
                "object": obj.name,
                "materials": plus_materials,
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons),
            }
        )
        bpy.data.objects.remove(obj, do_unlink=True)
        for material_name in plus_materials:
            material = bpy.data.materials.get(material_name)
            if material is not None and material.users == 0:
                bpy.data.materials.remove(material)
    return remaining, deleted


def delete_configured_material_meshes(
    objects: list[bpy.types.Object],
    deleted_material_names: list[str],
) -> tuple[list[bpy.types.Object], list[dict]]:
    """Delete the material-separated meshes of project-deleted materials.

    ``settings.deleted_materials`` records MMD helper, shadow, or effect
    materials that the project explicitly resolves as not exported. An object
    is deleted only when every material it uses is listed; an object that
    mixes listed and exported materials is an error because its part
    ownership would be ambiguous.
    """
    if not deleted_material_names:
        return objects, []
    configured = set(deleted_material_names)
    remaining = []
    deleted = []
    for obj in objects:
        material_names = mesh_material_names(obj)
        matched = sorted(set(material_names) & configured)
        if not matched:
            remaining.append(obj)
            continue

        other_materials = sorted(set(material_names) - configured)
        if other_materials:
            raise RuntimeError(
                f"Object {obj.name!r} mixes materials deleted by the project "
                f"{matched} with exported materials {other_materials}."
            )

        deleted.append(
            {
                "object": obj.name,
                "materials": matched,
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons),
                "reason": "preprocess_deleted_materials",
            }
        )
        bpy.data.objects.remove(obj, do_unlink=True)
        for material_name in matched:
            material = bpy.data.materials.get(material_name)
            if material is not None and material.users == 0:
                bpy.data.materials.remove(material)
    return remaining, deleted


def summarize_mesh(obj: bpy.types.Object) -> dict:
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    minimum = [
        min(corner[axis] for corner in corners)
        for axis in range(3)
    ]
    maximum = [
        max(corner[axis] for corner in corners)
        for axis in range(3)
    ]
    return {
        "name": obj.name,
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "materials": mesh_material_names(obj),
        "shape_keys": (
            [key.name for key in obj.data.shape_keys.key_blocks]
            if obj.data.shape_keys
            else []
        ),
        "bounds_world": {
            "min": minimum,
            "max": maximum,
            "center": [
                (minimum[axis] + maximum[axis]) * 0.5
                for axis in range(3)
            ],
        },
    }


def mesh_bounds(obj: bpy.types.Object) -> dict:
    corners = [
        obj.matrix_world @ Vector(corner)
        for corner in obj.bound_box
    ]
    minimum = [
        min(corner[axis] for corner in corners)
        for axis in range(3)
    ]
    maximum = [
        max(corner[axis] for corner in corners)
        for axis in range(3)
    ]
    return {
        "min": minimum,
        "max": maximum,
        "height": maximum[2] - minimum[2],
    }


def object_calibration_points(
    obj: bpy.types.Object,
    exclude_materials: set[str],
) -> list[Vector]:
    """World-space points used by the height calibration.

    ``settings.calibration_height.exclude_materials`` lists source materials
    that float away from the body, for example a halo or a large back
    ornament. They must not stretch the measured character height, otherwise
    the calibrated scale shrinks the actual body.
    """
    if not exclude_materials:
        return [
            obj.matrix_world @ Vector(corner)
            for corner in obj.bound_box
        ]
    slot_names = {
        index: (slot.material.name if slot.material else "")
        for index, slot in enumerate(obj.material_slots)
    }
    included: set[int] = set()
    for polygon in obj.data.polygons:
        if slot_names.get(polygon.material_index, "") in exclude_materials:
            continue
        included.update(polygon.vertices)
    if not included:
        raise RuntimeError(
            f"Calibration exclusion removed every vertex of {obj.name!r}."
        )
    return [
        obj.matrix_world @ obj.data.vertices[index].co
        for index in sorted(included)
    ]


def union_mesh_bounds(
    objects: list[bpy.types.Object],
    exclude_materials: set[str] | None = None,
) -> dict:
    excluded = exclude_materials or set()
    points = [
        point
        for obj in objects
        for point in object_calibration_points(obj, excluded)
    ]
    if not points:
        raise RuntimeError("Cannot measure bounds of an empty object list.")
    minimum = [
        min(point[axis] for point in points)
        for axis in range(3)
    ]
    maximum = [
        max(point[axis] for point in points)
        for axis in range(3)
    ]
    return {
        "min": minimum,
        "max": maximum,
        "height": maximum[2] - minimum[2],
    }


def separate_by_material(
    obj: bpy.types.Object,
) -> list[bpy.types.Object]:
    set_active(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="MATERIAL")
    bpy.ops.object.mode_set(mode="OBJECT")
    return [
        candidate
        for candidate in bpy.context.selected_objects
        if candidate.type == "MESH"
    ]


def target_skeleton_height(profile_path: Path) -> dict:
    profile = load_json(profile_path)
    skill_root = profile_path.parents[1]
    skeleton_path = (
        skill_root / profile["reference_assets"]["body_skeleton"]
    ).resolve()
    if not skeleton_path.is_file():
        raise FileNotFoundError(
            f"Target skeleton not found: {skeleton_path}"
        )

    enable_addon("RE-Mesh-Editor-main")
    before_objects = set(bpy.data.objects.keys())
    before_collections = set(bpy.data.collections.keys())
    if ".fbxskel." in skeleton_path.name:
        result = bpy.ops.re_fbxskel.importfile(filepath=str(skeleton_path))
    else:
        result = bpy.ops.import_scene.fbx(filepath=str(skeleton_path))
    if "FINISHED" not in result:
        raise RuntimeError(
            f"Target skeleton import failed: {skeleton_path}"
        )

    imported_objects = [
        bpy.data.objects[name]
        for name in bpy.data.objects.keys()
        if name not in before_objects
    ]
    armatures = [
        obj
        for obj in imported_objects
        if obj.type == "ARMATURE"
    ]
    if len(armatures) != 1:
        raise RuntimeError(
            f"Expected one imported target armature, found {len(armatures)}."
        )

    armature = armatures[0]
    points = []
    for bone in armature.data.bones:
        points.extend(
            (
                armature.matrix_world @ bone.head_local,
                armature.matrix_world @ bone.tail_local,
            )
        )
    minimum = [
        min(point[axis] for point in points)
        for axis in range(3)
    ]
    maximum = [
        max(point[axis] for point in points)
        for axis in range(3)
    ]
    report = {
        "path": str(skeleton_path),
        "armature": armature.name,
        "bone_count": len(armature.data.bones),
        "height": maximum[2] - minimum[2],
        "bounds": {
            "min": minimum,
            "max": maximum,
        },
    }

    for obj in imported_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection_name in set(bpy.data.collections.keys()) - before_collections:
        collection = bpy.data.collections.get(collection_name)
        if collection is not None and not collection.objects:
            bpy.data.collections.remove(collection)
    return report


def build_material_map(manifest: dict) -> dict[str, str]:
    assignments = {}
    for part_name, part in manifest["parts"].items():
        for material_name, assignment in part["material_assignments"].items():
            assigned_part = assignment.get("part")
            if assigned_part != part_name:
                raise RuntimeError(
                    f"Material {material_name!r} is stored under {part_name!r} "
                    f"but assigned to {assigned_part!r}."
                )
            if material_name in assignments:
                raise RuntimeError(
                    f"Material has multiple assignments: {material_name!r}"
                )
            assignments[material_name] = part_name
    return assignments


def classify_material(name: str | None, assignments: dict[str, str]) -> str:
    if not name or name not in assignments:
        raise RuntimeError(f"Material has no explicit assignment: {name!r}")
    return assignments[name]


def classify_object(
    obj: bpy.types.Object,
    assignments: dict[str, str],
) -> str:
    classifications = {
        classify_material(name, assignments)
        for name in mesh_material_names(obj)
    }
    if len(classifications) != 1:
        raise RuntimeError(
            f"Object {obj.name!r} has mixed part classifications: "
            f"{sorted(classifications)}"
        )
    return classifications.pop()


def profile_alignment(profile_path: Path) -> dict | None:
    """Alignment configuration declared by the target profile, if any."""
    alignment = load_json(profile_path).get("alignment")
    return alignment if isinstance(alignment, dict) else None


def part_object_names(manifest: dict, profile_path: Path) -> dict:
    """Output mesh name for every prepared part declared by the profile."""
    declared = load_json(profile_path).get("part_output_names") or {}
    names = {}
    for part_name in manifest["parts"]:
        names[part_name] = declared.get(
            part_name,
            DEFAULT_PART_OBJECT_NAMES.get(part_name, f"{part_name}_mesh"),
        )
    return names


def source_alignment_reference(
    armature: bpy.types.Object,
    preset: str | None,
    alignment: dict,
) -> dict | None:
    """Measure the same semantic bones on the source armature.

    Returns None when the source preset cannot resolve them, for example on an
    unknown rig that still has to be normalized in Stage 1B.
    """
    keys = list(alignment["reference_bones"])
    resolved = source_rig_utils.find_preset_bones(armature, preset, keys)
    if resolved is None:
        return None
    heights = {
        key: source_rig_utils.bone_world_height(armature, bone_name)
        for key, bone_name in resolved.items()
    }
    return {
        "method": "preset",
        "preset": preset,
        "bones": resolved,
        "heights": heights,
        "height": sum(heights.values()) / len(heights),
    }


def build_calibration(
    profile_path: Path,
    armature: bpy.types.Object,
    source_preset: str | None,
    source_bounds: dict,
    scale: float,
    calibration_exclude_materials: set,
) -> dict:
    """Compare the calibrated source against the profile's alignment target.

    Profiles that declare a shoulder alignment are measured bone-to-bone
    against the reference model. Other profiles keep the total-height check
    against the reference skeleton file.
    """
    tolerance = 0.10
    calibration = {
        "source_scale": scale,
        "source_bounds": source_bounds,
        "height_exclude_materials": sorted(calibration_exclude_materials),
    }
    alignment = profile_alignment(profile_path)
    if alignment and alignment.get("mode") == "shoulder":
        tolerance = float(alignment.get("tolerance", tolerance))
        reference = re6_target_skeleton.measure_reference(
            profile_path,
            alignment,
        )
        source_reference = source_alignment_reference(
            armature,
            source_preset,
            alignment,
        )
        calibration["tolerance"] = tolerance
        if source_reference is None:
            calibration.update(
                {
                    "mode": "shoulder",
                    "measure": "shoulder_height",
                    "reference": reference,
                    "source_reference": None,
                    "source_to_target_height_ratio": None,
                    "height_delta_ratio": None,
                    "within_ten_percent": True,
                    "deferred": True,
                    "deferred_reason": (
                        "source shoulder bones could not be resolved "
                        "through the source preset"
                    ),
                }
            )
            return calibration
        ratio = source_reference["height"] / reference["height"]
        calibration.update(
            {
                "mode": "shoulder",
                "measure": "shoulder_height",
                "reference": reference,
                "source_reference": source_reference,
                "source_to_target_height_ratio": ratio,
                "height_delta_ratio": abs(ratio - 1.0),
                "within_ten_percent": abs(ratio - 1.0) <= tolerance,
            }
        )
        return calibration

    target_skeleton = target_skeleton_height(profile_path)
    ratio = source_bounds["height"] / target_skeleton["height"]
    calibration.update(
        {
            "mode": "bounds",
            "measure": "total_height",
            "target_skeleton": target_skeleton,
            "tolerance": tolerance,
            "source_to_target_height_ratio": ratio,
            "height_delta_ratio": abs(ratio - 1.0),
            "within_ten_percent": abs(ratio - 1.0) <= tolerance,
        }
    )
    return calibration


def join_objects(objects, name):
    """Join the given mesh objects into one and return the result."""
    if not objects:
        raise RuntimeError(f"No meshes were provided for {name}.")
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) == 1:
        joined = objects[0]
        joined.name = name
        joined.data.name = f"{name}_mesh"
        bpy.ops.object.material_slot_remove_unused()
        return joined
    result = bpy.ops.object.join()
    if "CANCELLED" in result:
        raise RuntimeError(f"Could not join meshes into {name}: {result}")
    joined = bpy.context.active_object
    joined.name = name
    joined.data.name = f"{name}_mesh"
    bpy.ops.object.material_slot_remove_unused()
    return joined


def stage1_face_normal_config(manifest: dict) -> dict | None:
    """Face normal config when the target runs it inside Stage 1."""
    settings = manifest.get("settings", {})
    if not bool(settings.get("make_cylindrical_face_normals", True)):
        return None
    config = settings.get("cylindrical_face_normals") or {}
    stage = str(config.get("stage", "5c_pre")).lower()
    if stage not in ("stage_1", "stage1", "split"):
        return None
    return config


def apply_face_normals_in_split(separated, config):
    """Run the RE6 face-normal pass on a temporary face group.

    Targets such as RE6 keep one body part in the output, so the face is only
    grouped out for this operation: the split meshes are grouped by material
    into face and body, the face group is joined and processed exactly like
    the RE6 face part, then merged back into the body.
    """
    mask = [str(value) for value in config.get("mask_material_names") or []]
    if not mask:
        return (
            {"status": "skipped", "reason": "mask_material_names is empty"},
            separated,
        )
    wanted = set(mask)
    face_objects = []
    body_objects = []
    for obj in separated:
        if set(mesh_material_names(obj)) & wanted:
            face_objects.append(obj)
        else:
            body_objects.append(obj)
    if not face_objects or not body_objects:
        return (
            {
                "status": "skipped",
                "reason": "the face or body group is empty",
                "face_objects": len(face_objects),
                "body_objects": len(body_objects),
            },
            separated,
        )

    face_normals = importlib.import_module(
        "prepare_cylindrical_face_normals_stage"
    )
    normal_utils = importlib.import_module(
        "Modding-Toolkit.core.normal_utils"
    )
    face_mesh = join_objects(face_objects, "_FACE_TEMP")
    body_mesh = join_objects(body_objects, "_BODY_TEMP")
    result = face_normals.process_face_object(
        face_mesh,
        [body_mesh],
        config,
        normal_utils,
    )

    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    body_mesh.select_set(True)
    face_mesh.select_set(True)
    bpy.context.view_layer.objects.active = body_mesh
    join_result = bpy.ops.object.join()
    if "CANCELLED" in join_result:
        raise RuntimeError(
            f"Could not merge the face group back: {join_result}"
        )
    merged = bpy.context.active_object
    return (
        {
            "status": "applied",
            "face_objects": len(face_objects),
            "body_objects": len(body_objects),
            "result": result,
        },
        [merged],
    )


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    manifest = load_json(project_path)
    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    project_root = Path(manifest["paths"]["project_root"])
    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_01_split.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_01_split.json"
    )

    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    part_names = list(manifest["parts"])
    output_names = part_object_names(manifest, profile_path)
    face_normal_config = stage1_face_normal_config(manifest)
    face_normal_report = None

    source = Path(manifest["source"]["path"]).resolve()
    scale = manifest["settings"].get("scale_hint")
    if scale is None:
        raise RuntimeError(
            "settings.scale_hint is required before splitting the source."
        )

    material_map = build_material_map(manifest)
    configured_deleted_material_names = list(
        manifest.get("settings", {}).get("deleted_materials") or []
    )
    if len(set(configured_deleted_material_names)) != len(
        configured_deleted_material_names
    ):
        raise RuntimeError(
            "settings.deleted_materials contains duplicate names: "
            f"{configured_deleted_material_names}"
        )
    deleted_material_names = list(configured_deleted_material_names)
    for material_name in DEFAULT_DELETED_MATERIALS:
        if material_name not in deleted_material_names:
            deleted_material_names.append(material_name)
    deleted_material_set = set(deleted_material_names)
    calibration_exclude_materials = set(
        manifest.get("settings", {})
        .get("calibration_height", {})
        .get("exclude_materials")
        or []
    )

    bpy.ops.wm.read_factory_settings(use_empty=True)
    source_rig_utils.enable_modding_toolkit()
    adapter = source_import.adapter_for_source(
        source,
        requested_adapter=manifest["source"].get("adapter"),
        source_format=manifest["source"].get("format"),
    )
    imported = source_import.import_source(
        source,
        adapter,
        scale=float(scale),
    )
    no_merge = bool(
        manifest.get("settings", {}).get("no_mesh_merge", False)
    )
    if no_merge:
        source_meshes = [
            obj for obj in imported if obj.type == "MESH"
        ]
        imported_armatures = [
            obj for obj in imported if obj.type == "ARMATURE"
        ]
        if not source_meshes:
            raise RuntimeError("No imported source mesh was found.")
        if len(imported_armatures) != 1:
            raise RuntimeError(
                "no_mesh_merge requires exactly one imported armature, "
                f"found {len(imported_armatures)}."
            )
        source_armature = imported_armatures[0]
        source_bounds = union_mesh_bounds(
            source_meshes,
            calibration_exclude_materials,
        )
        source_mesh_name = ", ".join(
            sorted(obj.name for obj in source_meshes)
        )
    else:
        source_mesh = source_import.find_imported_mesh(imported)
        source_armature = source_import.find_imported_armature(imported)
        source_meshes = [source_mesh]
        source_mesh_name = source_mesh.name
        source_bounds = union_mesh_bounds(
            [source_mesh],
            calibration_exclude_materials,
        )
    requested_preset = manifest.get("source_rig", {}).get(
        "requested_preset"
    )
    source_rig_detection = source_rig_utils.detect_source_rig(
        source_armature,
        requested_preset=requested_preset,
    )
    source_preset = source_rig_detection.get("preset")
    try:
        pose_before = source_rig_utils.upper_arm_pose(
            source_armature,
            source_preset,
        )
    except RuntimeError as error:
        if source_preset is not None:
            raise
        pose_before = {
            "pose": "unknown",
            "status": "deferred",
            "reason": "source preset is unknown",
            "error": str(error),
        }
        pose_conversion = {
            "applied": False,
            "deferred": True,
            "method": "normalize_to_mmd_then_detect",
            "reason": "source preset is unknown",
        }
        pose_after = pose_before
    else:
        pose_conversion = source_rig_utils.apply_pose_conversion(
            source_armature,
            source_preset,
            pose_before,
        )
        pose_after = source_rig_utils.upper_arm_pose(
            source_armature,
            source_preset,
        )
    calibration = build_calibration(
        profile_path=profile_path,
        armature=source_armature,
        source_preset=source_preset,
        source_bounds=source_bounds,
        scale=float(scale),
        calibration_exclude_materials=calibration_exclude_materials,
    )
    if not calibration["within_ten_percent"]:
        raise RuntimeError(
            f"Calibrated {calibration['measure']} differs from the target "
            f"by {calibration['height_delta_ratio']:.3f}; review scale_hint."
        )
    pose_detection = {
        "before": pose_before,
        "after": pose_after,
        "conversion": pose_conversion,
    }
    normalization_required = source_preset is None
    imported_materials = set()
    for mesh in source_meshes:
        imported_materials.update(mesh_material_names(mesh))
    missing_configured_materials = sorted(
        name
        for name in configured_deleted_material_names
        if name not in imported_materials
    )
    if missing_configured_materials:
        raise RuntimeError(
            "settings.deleted_materials names materials that are not present "
            f"in the source: {missing_configured_materials}"
        )
    exported_materials = imported_materials - deleted_material_set
    unknown_materials = sorted(exported_materials - set(material_map))
    unused_assignments = sorted(
        set(material_map) - exported_materials - deleted_material_set
    )
    if unknown_materials:
        raise RuntimeError(
            f"Materials have no explicit assignment: {unknown_materials}"
        )
    if unused_assignments:
        raise RuntimeError(
            f"Assignments reference missing materials: {unused_assignments}"
        )

    source_collection = ensure_collection("_SOURCE_HIDDEN")
    parts_collection = ensure_collection("SOURCE_PARTS")

    part_summaries = []
    deleted_configured_materials: list[dict] = []
    if no_merge:
        for mesh in source_meshes:
            backup = mesh.copy()
            backup.data = mesh.data.copy()
            backup.name = f"_SOURCE_{mesh.name}"
            backup.data.name = f"_SOURCE_{mesh.name}_data"
            bpy.context.scene.collection.objects.link(backup)
            move_to_collection(backup, source_collection)
            backup.hide_render = True
            backup.hide_set(True)

        part_objects = {name: [] for name in part_names}
        assignments = []
        deleted_plus_materials = []
        for mesh in source_meshes:
            source_material_names = mesh_material_names(mesh)
            classifications = {
                classify_material(name, material_map)
                for name in source_material_names
                if name not in deleted_material_set
            }
            has_deleted_material = any(
                name in deleted_material_set
                for name in source_material_names
            )
            if len(classifications) == 1 and not has_deleted_material:
                candidates = [mesh]
            else:
                candidates = separate_by_material(mesh)
                candidates, removed = delete_plus_material_meshes(
                    candidates
                )
                deleted_plus_materials.extend(removed)
            candidates, removed_configured = (
                delete_configured_material_meshes(
                    candidates,
                    deleted_material_names,
                )
            )
            deleted_configured_materials.extend(removed_configured)
            for obj in candidates:
                material_names = mesh_material_names(obj)
                part_name = classify_object(obj, material_map)
                part_objects[part_name].append(obj)
                assignments.append(
                    {
                        "object": obj.name,
                        "source_object": mesh.name,
                        "part": part_name,
                        "materials": material_names,
                        "vertices": len(obj.data.vertices),
                        "polygons": len(obj.data.polygons),
                    }
                )

        part_collections = {}
        for part_name in part_names:
            objects = part_objects[part_name]
            if not objects:
                raise RuntimeError(
                    f"No mesh objects were assigned to {part_name}."
                )
            collection = ensure_collection(
                f"SOURCE_PARTS_{part_name.upper()}"
            )
            for obj in sorted(objects, key=lambda item: item.name):
                move_to_collection(obj, collection)
            part_collections[part_name] = collection.name
            part_summaries.append(
                {
                    "part": part_name,
                    "collection": collection.name,
                    "objects": [
                        summarize_mesh(obj)
                        for obj in sorted(
                            objects, key=lambda item: item.name
                        )
                    ],
                }
            )
    else:
        source_mesh = source_meshes[0]
        backup = source_mesh.copy()
        backup.data = source_mesh.data.copy()
        backup.name = "_SOURCE_mesh"
        backup.data.name = "_SOURCE_mesh_data"
        bpy.context.scene.collection.objects.link(backup)
        move_to_collection(backup, source_collection)
        backup.hide_render = True
        backup.hide_set(True)

        separated = separate_by_material(source_mesh)
        if not separated:
            raise RuntimeError("Material separation produced no mesh objects.")
        separated, deleted_plus_materials = delete_plus_material_meshes(
            separated
        )
        separated, deleted_configured_materials = (
            delete_configured_material_meshes(
                separated,
                deleted_material_names,
            )
        )
        if not separated:
            raise RuntimeError(
                "All material-separated meshes were deleted as '+' materials."
            )

        if face_normal_config is not None:
            face_normal_report, separated = apply_face_normals_in_split(
                separated,
                face_normal_config,
            )

        part_objects = {name: [] for name in part_names}
        assignments = []
        for obj in separated:
            material_names = mesh_material_names(obj)
            part_name = classify_object(obj, material_map)
            part_objects[part_name].append(obj)
            assignments.append(
                {
                    "object": obj.name,
                    "part": part_name,
                    "materials": material_names,
                    "vertices": len(obj.data.vertices),
                    "polygons": len(obj.data.polygons),
                }
            )

        joined_parts = []
        for part_name, object_name in output_names.items():
            objects = part_objects[part_name]
            if not objects:
                raise RuntimeError(
                    f"No material objects were assigned to {part_name}."
                )
            for selected in bpy.context.selected_objects:
                selected.select_set(False)
            for obj in objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = objects[0]
            bpy.ops.object.join()
            joined = bpy.context.active_object
            joined.name = object_name
            joined.data.name = f"{object_name}_mesh"
            bpy.ops.object.material_slot_remove_unused()
            move_to_collection(joined, parts_collection)
            joined_parts.append(joined)
        part_collections = {
            part_name: parts_collection.name
            for part_name in part_names
        }
        part_summaries = [
            {
                "part": part_name,
                "collection": parts_collection.name,
                "objects": [summarize_mesh(obj)],
            }
            for part_name, obj in zip(
                part_names,
                joined_parts,
            )
        ]

    move_to_collection(source_armature, source_collection)
    source_armature.hide_render = True
    source_armature.hide_set(True)
    source_collection.hide_render = True
    source_collection.hide_viewport = True

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "split",
        "project": str(project_path),
        "source": str(source),
        "output": str(output_path),
        "scale": scale,
        "source_armature": source_armature.name,
        "source_mesh": source_mesh_name,
        "source_format": manifest["source"].get("format"),
        "source_adapter": manifest["source"].get("adapter"),
        "source_rig": source_rig_detection,
        "requires_source_rig_normalization": normalization_required,
        "source_material_count": len(imported_materials),
        "calibration": calibration,
        "pose_detection": pose_detection,
        "deleted_plus_material_count": len(deleted_plus_materials),
        "deleted_plus_materials": deleted_plus_materials,
        "default_deleted_material_names": list(DEFAULT_DELETED_MATERIALS),
        "configured_deleted_material_names": (
            configured_deleted_material_names
        ),
        "deleted_material_config": deleted_material_names,
        "deleted_configured_material_count": len(
            deleted_configured_materials
        ),
        "deleted_configured_materials": deleted_configured_materials,
        "exported_material_count": len(exported_materials),
        "no_mesh_merge": no_merge,
        "face_normals": face_normal_report,
        "part_collections": part_collections,
        "parts": part_summaries,
        "assignments": assignments,
    }
    write_json(report_path, report)

    for part_name in part_names:
        manifest["parts"][part_name]["status"] = "split"
        manifest["parts"][part_name]["collection"] = part_collections[
            part_name
        ]
    manifest["settings"]["scale_validation"] = calibration
    manifest["settings"]["source_pose"] = pose_detection
    manifest["settings"]["face_normals"] = face_normal_report
    manifest.setdefault("source_rig", {})
    manifest["source_rig"]["adapter"] = manifest["source"].get("adapter")
    manifest["source_rig"]["detected_preset"] = source_preset
    manifest["source_rig"]["effective_preset"] = source_preset
    manifest["source_rig"]["detection"] = source_rig_detection
    manifest["source_rig"]["mapping_file"] = (
        str(
            project_root
            / "reports"
            / "ai_source_to_mmd_bone_mapping.json"
        )
        if normalization_required
        else None
    )
    manifest["settings"]["requires_source_rig_normalization"] = (
        normalization_required
    )
    manifest["stage_history"].append(
        {
            "stage": "split",
            "blend": str(output_path),
            "report": str(report_path),
            "scale": scale,
            "pose_conversion_applied": pose_conversion["applied"],
            "height_delta_ratio": calibration["height_delta_ratio"],
        }
    )
    write_json(project_path, manifest)
    print(f"SPLIT_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
