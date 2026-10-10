"""Create optional black outline shells before MDF preparation."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import internal_deps


OUTLINE_MATERIAL_NAME = "Outline"
OUTLINE_SUFFIX = "_Outline"
PART_NAMES = ("body", "face", "hair")


def parse_args() -> argparse.Namespace:
    import sys

    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--thickness", type=float, default=0.001)
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


def select_only(objects: list[bpy.types.Object]) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]


def prepared_collection_name(
    manifest: dict,
    profile: dict,
    part_name: str,
) -> str:
    # The manifest records the collection the previous stage actually used, so
    # it wins over the profile's static name.
    part = manifest.get("parts", {}).get(part_name, {})
    for key in ("collection", "mesh_collection"):
        value = part.get(key)
        if value:
            return str(value)

    profile_collections = profile.get("prepared_collections", {})
    if part_name in profile_collections:
        return str(profile_collections[part_name])

    raise RuntimeError(
        f"Prepared collection is missing for part: {part_name}"
    )


def collection_meshes(collection_name: str) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise RuntimeError(f"Prepared collection not found: {collection_name}")
    return [
        obj
        for obj in sorted(collection.all_objects, key=lambda item: item.name)
        if obj.type == "MESH" and not obj.name.endswith(OUTLINE_SUFFIX)
    ]


def expression_group_names(profile: dict) -> set[str]:
    return {
        str(name)
        for name in profile.get("required_expression_groups", [])
        if str(name)
    }


def resolve_head_bone(manifest: dict, profile: dict) -> str:
    expression_mapping = manifest.get("expression_mapping") or {}
    eye_weights = expression_mapping.get("eye_weights") or {}
    head_bone = eye_weights.get("head_bone")
    if head_bone:
        return str(head_bone)

    profile_head = profile.get("face_alignment_bone")
    if profile_head:
        return str(profile_head)

    required = {
        str(name)
        for name in profile.get("bones", {}).get("required_face_controls", [])
    }
    if "Head" in required:
        return "Head"
    if "Head_0" in required:
        return "Head_0"
    return "Head"


def configure_outline_material() -> dict:
    material = bpy.data.materials.get(OUTLINE_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(name=OUTLINE_MATERIAL_NAME)

    material.use_nodes = True
    material.diffuse_color = (0.0, 0.0, 0.0, 1.0)
    material.use_backface_culling = True
    if material.node_tree is not None:
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = (
                0.0,
                0.0,
                0.0,
                1.0,
            )
            if "Metallic" in principled.inputs:
                principled.inputs["Metallic"].default_value = 0.0
    return {
        "name": material.name,
        "base_color": [0.0, 0.0, 0.0, 1.0],
        "use_backface_culling": bool(material.use_backface_culling),
    }


def apply_positive_normal_offset(
    obj: bpy.types.Object,
    thickness: float,
) -> None:
    modifier = obj.modifiers.new(
        name="MTK_OutlineOffset",
        type="DISPLACE",
    )
    modifier.direction = "NORMAL"
    modifier.strength = thickness
    modifier.mid_level = 0.0

    other_modifiers = [
        item
        for item in obj.modifiers
        if item.name != modifier.name and item.show_viewport
    ]
    for item in other_modifiers:
        item.show_viewport = False

    try:
        key_blocks = (
            obj.data.shape_keys.key_blocks
            if obj.data.shape_keys
            else []
        )
        if len(key_blocks) >= 2:
            shapekey_utils = importlib.import_module(
                "Modding-Toolkit.core.shapekey_utils"
            )
            ok, message_key, values = shapekey_utils.check(obj)
            if not ok:
                raise RuntimeError(
                    f"Outline displacement cannot preserve shape keys on "
                    f"{obj.name}: {message_key} {values}"
                )
            shapekey_utils.apply_modifiers_keep_shape_keys(obj)
        else:
            if key_blocks:
                with bpy.context.temp_override(
                    object=obj,
                    active_object=obj,
                    selected_editable_objects=[obj],
                ):
                    bpy.ops.object.shape_key_remove(all=True)
            select_only([obj])
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_editable_objects=[obj],
            ):
                result = bpy.ops.object.modifier_apply(
                    modifier=modifier.name
                )
            if "FINISHED" not in result:
                raise RuntimeError(
                    f"Could not apply outline displacement on {obj.name}: "
                    f"{result}"
                )
    finally:
        for item in other_modifiers:
            current = obj.modifiers.get(item.name)
            if current is not None:
                current.show_viewport = True


def reverse_mesh_normals(obj: bpy.types.Object) -> None:
    select_only([obj])
    result = bpy.ops.object.mode_set(mode="EDIT")
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not enter Edit Mode for {obj.name}")
    bpy.ops.mesh.select_all(action="SELECT")
    result = bpy.ops.mesh.flip_normals()
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not reverse normals for {obj.name}")
    bpy.ops.object.mode_set(mode="OBJECT")


def normalize_face_outline(
    obj: bpy.types.Object,
    head_bone: str,
    expression_groups: set[str],
    eye_bone_names: set[str],
) -> dict:
    removed_groups = []
    for group in list(obj.vertex_groups):
        if group.name in expression_groups and group.name != head_bone:
            removed_groups.append(group.name)
            obj.vertex_groups.remove(group)

    eye_outline = (
        obj.name.lower().startswith("eyes")
        or any(
            group.name in eye_bone_names
            for group in obj.vertex_groups
        )
    )
    if eye_outline:
        for group in list(obj.vertex_groups):
            if group.name not in eye_bone_names:
                obj.vertex_groups.remove(group)
        group_names = {
            group.index: group.name
            for group in obj.vertex_groups
        }
        total_weights = []
        zero_weight_vertices = 0
        for vertex in obj.data.vertices:
            total_weight = sum(
                assignment.weight
                for assignment in vertex.groups
                if group_names.get(assignment.group) in eye_bone_names
                and assignment.weight > 0.0
            )
            total_weights.append(total_weight)
            if total_weight <= 0.0:
                zero_weight_vertices += 1
        return {
            "head_bone": head_bone,
            "removed_expression_groups": sorted(removed_groups),
            "eye_only": True,
            "eye_bones": sorted(eye_bone_names),
            "zero_weight_vertices": zero_weight_vertices,
            "total_weight_min": min(total_weights, default=0.0),
            "total_weight_max": max(total_weights, default=0.0),
            "total_weight_average": (
                sum(total_weights) / len(total_weights)
                if total_weights
                else 0.0
            ),
        }

    head_group = obj.vertex_groups.get(head_bone)
    if head_group is None:
        head_group = obj.vertex_groups.new(name=head_bone)

    locked_groups = []
    for group in obj.vertex_groups:
        group.lock_weight = group.name != head_bone
        if group.lock_weight:
            locked_groups.append(group.name)

    head_group.add(
        [vertex.index for vertex in obj.data.vertices],
        1.0,
        "REPLACE",
    )
    select_only([obj])
    result = bpy.ops.object.vertex_group_normalize_all(
        group_select_mode="ALL",
        lock_active=False,
    )
    fallback_used = "FINISHED" not in result
    if fallback_used:
        for group in obj.vertex_groups:
            group.lock_weight = False
        result = bpy.ops.object.vertex_group_normalize_all(
            group_select_mode="ALL",
            lock_active=False,
        )
        for group in obj.vertex_groups:
            group.lock_weight = group.name != head_bone

    if "FINISHED" not in result:
        raise RuntimeError(
            f"Face outline weight normalization failed on {obj.name}: "
            f"{result}"
        )

    head_weights = []
    for vertex in obj.data.vertices:
        try:
            head_weights.append(head_group.weight(vertex.index))
        except RuntimeError:
            head_weights.append(0.0)

    return {
        "head_bone": head_bone,
        "removed_expression_groups": sorted(removed_groups),
        "eye_only": False,
        "zero_weight_vertices": 0,
        "locked_group_count": len(locked_groups),
        "normalization_result": list(result),
        "normalization_fallback_used": fallback_used,
        "head_weight_min": min(head_weights, default=0.0),
        "head_weight_max": max(head_weights, default=0.0),
        "head_weight_average": (
            sum(head_weights) / len(head_weights)
            if head_weights
            else 0.0
        ),
    }


def create_part_outlines(
    part_name: str,
    collection_name: str,
    thickness: float,
) -> tuple[list[bpy.types.Object], dict]:
    sources = collection_meshes(collection_name)
    if not sources:
        raise RuntimeError(
            f"No prepared source meshes found for {part_name}: "
            f"{collection_name}"
        )

    existing_outlines = [
        obj.name
        for obj in bpy.data.collections[collection_name].all_objects
        if obj.type == "MESH" and obj.name.endswith(OUTLINE_SUFFIX)
    ]
    if existing_outlines:
        raise RuntimeError(
            f"Outline objects already exist for {part_name}: "
            f"{sorted(existing_outlines)}"
        )

    material = configure_outline_material()
    created = []
    for source in sources:
        outline = source.copy()
        outline.data = source.data.copy()
        outline.name = source.name + OUTLINE_SUFFIX
        outline.data.name = source.data.name + OUTLINE_SUFFIX
        for collection in source.users_collection:
            collection.objects.link(outline)
        if not outline.users_collection:
            bpy.context.scene.collection.objects.link(outline)
        outline.select_set(False)
        outline.data.materials.clear()
        outline.data.materials.append(
            bpy.data.materials[OUTLINE_MATERIAL_NAME]
        )
        apply_positive_normal_offset(outline, thickness)
        reverse_mesh_normals(outline)
        created.append(outline)

    missing_suffix = [
        obj.name
        for obj in created
        if not obj.name.endswith(OUTLINE_SUFFIX)
    ]
    if missing_suffix:
        raise RuntimeError(
            f"Outline objects are missing the {OUTLINE_SUFFIX} suffix: "
            f"{missing_suffix}"
        )

    for obj in created:
        materials = [
            slot.material.name
            for slot in obj.material_slots
            if slot.material is not None
        ]
        if materials != [OUTLINE_MATERIAL_NAME]:
            raise RuntimeError(
                f"Unexpected material slots on {obj.name}: {materials}"
            )

    return created, {
        "collection": collection_name,
        "source_count": len(sources),
        "outline_count": len(created),
        "thickness": thickness,
        "material": material["name"],
    }


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    profile = load_json(profile_path)
    project_root = Path(manifest["paths"]["project_root"])
    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_05d_outlines.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_05d_outlines.json"
    )

    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )
    if not bool(manifest.get("settings", {}).get("make_outline", False)):
        raise RuntimeError(
            "Stage 5D was requested, but settings.make_outline is not true."
        )
    if args.thickness <= 0.0:
        raise ValueError("Outline thickness must be greater than zero.")

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    enable_addon("Modding-Toolkit")

    # Parts come from the manifest so single-body targets such as RE6 work.
    part_names = list(manifest.get("parts", {})) or list(PART_NAMES)
    has_face = "face" in part_names
    expression_groups = expression_group_names(profile)
    head_bone = resolve_head_bone(manifest, profile) if has_face else None
    eye_bone_names = set()
    if has_face:
        eye_weights = (manifest.get("expression_mapping") or {}).get(
            "eye_weights"
        ) or {}
        eye_bone_names = {
            str(name)
            for name in (
                eye_weights.get("left_bone"),
                eye_weights.get("right_bone"),
            )
            if name
        }
        if not eye_bone_names:
            raise RuntimeError(
                "Face outline eye weights require "
                "expression_mapping.eye_weights left_bone and right_bone."
            )
    outline_reports = {}
    outline_objects = {}

    for part_name in part_names:
        collection_name = prepared_collection_name(
            manifest,
            profile,
            part_name,
        )
        created, part_report = create_part_outlines(
            part_name,
            collection_name,
            args.thickness,
        )
        outline_objects[part_name] = created
        outline_reports[part_name] = part_report

    material_report = configure_outline_material()
    face_weight_reports = []
    for obj in outline_objects.get("face", []):
        report = normalize_face_outline(
            obj,
            head_bone,
            expression_groups,
            eye_bone_names,
        )
        report["mesh"] = obj.name
        face_weight_reports.append(report)
        if report["zero_weight_vertices"] > 0:
            raise RuntimeError(
                f"Face outline has zero-weight vertices: {obj.name}"
            )
        if not report.get("eye_only") and report["head_weight_min"] <= 0.0:
            raise RuntimeError(
                f"Face outline has zero-weight vertices after normalization: "
                f"{obj.name}"
            )

    for part_name, objects in outline_objects.items():
        if len(objects) != outline_reports[part_name]["outline_count"]:
            raise RuntimeError(
                f"Outline count changed during setup for {part_name}."
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "outlines",
        "status": "passed",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "thickness": args.thickness,
        "material": material_report,
        "parts": outline_reports,
        "face_weights": face_weight_reports,
        "outline_objects": {
            part_name: [obj.name for obj in objects]
            for part_name, objects in outline_objects.items()
        },
    }
    write_json(report_path, report)

    for part_name in part_names:
        manifest.setdefault("parts", {}).setdefault(part_name, {})[
            "status"
        ] = "outlines_ready"
        manifest["parts"][part_name]["validation"] = "stage_05d_outlines"
    manifest["stage_history"].append(
        {
            "stage": "outlines",
            "blend": str(output_path),
            "report": str(report_path),
            "thickness": args.thickness,
            "outline_counts": {
                part_name: part_report["outline_count"]
                for part_name, part_report in outline_reports.items()
            },
            "material": OUTLINE_MATERIAL_NAME,
        }
    )
    write_json(project_path, manifest)
    print(f"OUTLINE_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
