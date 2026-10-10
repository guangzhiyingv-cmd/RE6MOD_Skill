"""Normalize an unknown source rig to MMD names without touching physics bones."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import source_rig_utils


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path)
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


def is_ascii_name(name: str) -> bool:
    return bool(name) and all(32 <= ord(character) < 127 for character in name)


def find_source_armature() -> bpy.types.Object:
    hidden = bpy.data.collections.get("_SOURCE_HIDDEN")
    if hidden is not None:
        armatures = [obj for obj in hidden.objects if obj.type == "ARMATURE"]
        if len(armatures) == 1:
            return armatures[0]
    armatures = [
        obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"
    ]
    if len(armatures) != 1:
        raise RuntimeError(
            f"Expected one source armature, found {len(armatures)}."
        )
    return armatures[0]


def rename_vertex_groups(
    armature: bpy.types.Object,
    mapping: dict[str, str],
) -> dict[str, list[str]]:
    changed = {}
    for mesh in bpy.data.objects:
        if mesh.type != "MESH":
            continue
        if mesh.find_armature() != armature and mesh.parent != armature:
            continue
        renamed = []
        for old_name, new_name in mapping.items():
            group = mesh.vertex_groups.get(old_name)
            if group is None:
                continue
            collision = mesh.vertex_groups.get(new_name)
            if collision is not None and collision != group:
                raise RuntimeError(
                    f"Vertex group collision on {mesh.name}: "
                    f"{old_name!r} -> {new_name!r}"
                )
            group.name = new_name
            renamed.append(old_name)
        if renamed:
            changed[mesh.name] = renamed
    return changed


def validate_mapping(
    armature: bpy.types.Object,
    mapping: dict[str, str],
    physics_bones: set[str],
) -> dict:
    existing = {bone.name for bone in armature.data.bones}
    missing_sources = sorted(name for name in mapping if name not in existing)
    missing_physics = sorted(name for name in physics_bones if name not in existing)
    physics_renamed = sorted(name for name in mapping if name in physics_bones)
    invalid_targets = sorted(
        name for name in mapping.values() if not is_ascii_name(name)
    )
    targets = list(mapping.values())
    duplicate_targets = sorted(
        name for name in set(targets) if targets.count(name) > 1
    )
    surviving = existing - set(mapping)
    collisions = sorted(
        target
        for target in set(targets)
        if target in surviving or target in physics_bones
    )
    if (
        missing_sources
        or missing_physics
        or physics_renamed
        or invalid_targets
        or duplicate_targets
        or collisions
    ):
        raise RuntimeError(
            "Source rig mapping validation failed: "
            f"missing_sources={missing_sources}, "
            f"missing_physics={missing_physics}, "
            f"physics_renamed={physics_renamed}, "
            f"invalid_targets={invalid_targets}, "
            f"duplicate_targets={duplicate_targets}, "
            f"collisions={collisions}"
        )
    return {
        "mapping_count": len(mapping),
        "physics_count": len(physics_bones),
        "missing_sources": [],
        "missing_physics": [],
        "physics_renamed": [],
        "invalid_targets": [],
        "duplicate_targets": [],
        "collisions": [],
    }


def apply_main_bone_mapping(
    armature: bpy.types.Object,
    mapping: dict[str, str],
) -> None:
    if not mapping:
        return
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    armature.hide_set(False)
    armature.hide_viewport = False
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.mode_set(mode="EDIT")
    for old_name, new_name in mapping.items():
        armature.data.edit_bones[old_name].name = new_name
    bpy.ops.object.mode_set(mode="OBJECT")


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    project_root = Path(manifest["paths"]["project_root"])
    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_01b_normalized_rig.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_01b_normalized_rig.json"
    )
    mapping_path = (
        args.mapping.resolve()
        if args.mapping
        else Path(
            manifest.get("source_rig", {}).get("mapping_file")
            or (
                project_root
                / "reports"
                / "ai_source_to_mmd_bone_mapping.json"
            )
        ).resolve()
    )

    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    source_rig_utils.enable_modding_toolkit()
    armature = find_source_armature()
    source_collection = bpy.data.collections.get("_SOURCE_HIDDEN")
    source_collection_hide_viewport = (
        source_collection.hide_viewport
        if source_collection is not None
        else None
    )
    if source_collection is not None:
        # Pose operators cannot select hidden bones in background mode.
        source_collection.hide_viewport = False
        bpy.context.view_layer.update()
    current_preset = manifest.get("source_rig", {}).get(
        "effective_preset"
    )

    if current_preset:
        source_rig_utils.set_active(armature)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)
        report = {
            "stage": "source_rig_normalization",
            "status": "passed",
            "mode": "pass_through",
            "project": str(project_path),
            "input": str(input_path),
            "output": str(output_path),
            "effective_preset": current_preset,
        }
        write_json(report_path, report)
        print(f"SOURCE_RIG_STAGE={output_path}")
        bpy.ops.wm.quit_blender()
        return 0

    if not mapping_path.is_file():
        raise FileNotFoundError(
            "Source rig has no recognized preset. Provide an AI mapping with "
            "main_bone_mapping and physics_bones: " + str(mapping_path)
        )

    payload = load_json(mapping_path)
    mapping = payload.get("main_bone_mapping")
    if not isinstance(mapping, dict):
        raise RuntimeError("main_bone_mapping must be a JSON object.")
    physics_bones = set(payload.get("physics_bones") or [])
    validation = validate_mapping(armature, mapping, physics_bones)

    apply_main_bone_mapping(armature, mapping)
    renamed_groups = rename_vertex_groups(armature, mapping)
    mmd_detection = source_rig_utils.detect_source_rig(armature)
    if (
        mmd_detection.get("preset") != "MMD.json"
        or float(mmd_detection.get("coverage") or 0.0) < 0.95
    ):
        raise RuntimeError(
            "Normalized source rig does not match MMD.json at 95% coverage: "
            + json.dumps(mmd_detection, ensure_ascii=False)
        )

    current_detection = source_rig_utils.upper_arm_pose(
        armature,
        "MMD.json",
    )
    pose_conversion = source_rig_utils.apply_pose_conversion(
        armature,
        "MMD.json",
        current_detection,
    )
    pose_after = source_rig_utils.upper_arm_pose(
        armature,
        "MMD.json",
    )

    if source_collection is not None:
        source_collection.hide_viewport = bool(source_collection_hide_viewport)
        armature.hide_set(bool(source_collection_hide_viewport))
        armature.hide_viewport = bool(source_collection_hide_viewport)
        bpy.context.view_layer.update()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)
    report = {
        "stage": "source_rig_normalization",
        "status": "passed",
        "mode": "ai_main_bones_to_mmd",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "mapping": str(mapping_path),
        "validation": validation,
        "renamed_vertex_groups": renamed_groups,
        "mmd_detection": mmd_detection,
        "pose_conversion": pose_conversion,
        "pose_after": pose_after,
    }
    write_json(report_path, report)

    manifest["source_rig"]["effective_preset"] = "MMD.json"
    manifest["source_rig"]["mapping_file"] = str(mapping_path)
    manifest["source_rig"]["physics_bones"] = sorted(physics_bones)
    manifest["source_rig"]["normalization_report"] = str(report_path)
    manifest.setdefault("settings", {})[
        "requires_source_rig_normalization"
    ] = False
    source_pose = manifest.get("settings", {}).get("source_pose") or {}
    manifest.setdefault("settings", {})["source_pose"] = {
        **source_pose,
        "after": pose_after,
        "conversion": pose_conversion,
    }
    manifest["stage_history"].append(
        {
            "stage": "source_rig_normalization",
            "blend": str(output_path),
            "report": str(report_path),
            "effective_preset": "MMD.json",
            "main_bone_count": len(mapping),
            "physics_bone_count": len(physics_bones),
        }
    )
    manifest["active_stage"] = str(output_path)
    manifest["active_stage_validation"] = str(report_path)
    write_json(project_path, manifest)
    print(f"SOURCE_RIG_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
