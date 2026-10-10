"""Align the target RE Engine skeleton to the imported MMD skeleton."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import finger_cleanup
import re6_target_skeleton
import source_rig_utils


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
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


def enable_addon(modules: tuple[str, ...]) -> None:
    errors = []
    for module in modules:
        result = bpy.ops.preferences.addon_enable(module=module)
        if "FINISHED" in result:
            return
        errors.append(f"{module}: {result}")
    raise RuntimeError("Could not enable addon. Tried: " + "; ".join(errors))


def set_active(obj: bpy.types.Object) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def bone_positions(armature: bpy.types.Object) -> dict[str, tuple[float, float, float]]:
    return {
        bone.name: tuple(bone.head_local)
        for bone in armature.data.bones
    }


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


def find_bound_mesh(armature: bpy.types.Object):
    """First mesh skinned to the source armature, for the cleanup helpers."""
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if any(
            modifier.type == "ARMATURE"
            and modifier.object == armature
            for modifier in obj.modifiers
        ):
            return obj
    return None


def profile_finger_cleanup(profile: dict) -> dict | None:
    """Finger cleanup configuration declared by the target profile."""
    config = profile.get("finger_cleanup")
    return config if isinstance(config, dict) else None


def target_semantic_coverage(
    profile: dict,
    skill_root: Path,
    armature: bpy.types.Object,
) -> dict | None:
    """Informational: which profile bone semantics exist on the target.

    Only profiles that declare an alignment semantics file get this, so other
    games keep their report unchanged. It never blocks the stage.
    """
    alignment = profile.get("alignment")
    if not isinstance(alignment, dict):
        return None
    semantics_relative = alignment.get("bone_semantics")
    if not semantics_relative:
        return None
    semantics = re6_target_skeleton.load_bone_semantics(
        (skill_root / semantics_relative).resolve()
    )
    present = {bone.name for bone in armature.data.bones}
    resolved = {}
    missing = {}
    for key, candidates in semantics.items():
        found = [name for name in candidates if name in present]
        if found:
            resolved[key] = found
        else:
            missing[key] = candidates
    return {
        "semantics_total": len(semantics),
        "semantics_resolved": len(resolved),
        "semantics_missing": sorted(missing),
        "resolved": resolved,
    }


def import_target_skeleton(profile: dict, skill_root: Path):
    """Import the profile's target armature and report where it came from."""
    target = profile.get("target_skeleton") or {}
    if target.get("source") == "re6_mod":
        model_path = (skill_root / target["model"]).resolve()
        if not model_path.is_file():
            raise FileNotFoundError(
                f"RE6 target model not found: {model_path}"
            )
        armature, _imported, _before = (
            re6_target_skeleton.import_reference_armature(model_path)
        )
        return armature, model_path, "re6_mod"

    path = (
        skill_root / profile["reference_assets"]["body_skeleton"]
    ).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Target skeleton not found: {path}")
    before_objects = set(bpy.data.objects.keys())
    if ".fbxskel." in path.name:
        enable_addon(("RE-Mesh-Editor-main",))
        result = bpy.ops.re_fbxskel.importfile(filepath=str(path))
    else:
        result = bpy.ops.import_scene.fbx(filepath=str(path))
    if "CANCELLED" in result:
        raise RuntimeError(f"Target skeleton import was cancelled: {path}")

    new_objects = [
        bpy.data.objects[name]
        for name in bpy.data.objects.keys()
        if name not in before_objects
    ]
    armatures = [obj for obj in new_objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(
            f"Expected one imported target armature, found {len(armatures)}."
        )
    return armatures[0], path, "skeleton_file"


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    project_root = Path(manifest["paths"]["project_root"])
    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_02_aligned.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_02_aligned.json"
    )

    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    profile = load_json(profile_path)
    skill_root = profile_path.parents[1]

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    enable_addon(("Modding-Toolkit", "Modding-Toolkit-dev"))

    source_armature = find_source_armature()
    target_armature, target_skeleton_path, target_source = (
        import_target_skeleton(profile, skill_root)
    )
    for collection in source_armature.users_collection:
        collection.hide_viewport = False
        collection.hide_render = False

    settings = bpy.context.scene.mhw_suite_settings
    source_preset = source_rig_utils.effective_source_preset(manifest)
    target_preset = profile.get(
        "alignment_preset",
        "re6.json",
    )
    settings.import_preset_enum = source_preset
    settings.target_preset_enum = target_preset
    settings.align_mode_override = "POS_ONLY"
    # Read the configured values back once. Helpers later in the stage
    # re-register add-ons, which can invalidate this settings pointer.
    preset_state = {
        "source": settings.import_preset_enum,
        "target": settings.target_preset_enum,
        "align_mode": settings.align_mode_override,
    }

    before_positions = bone_positions(target_armature)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    source_armature.hide_set(False)
    source_armature.hide_viewport = False
    source_armature.select_set(True)
    target_armature.select_set(True)
    bpy.context.view_layer.objects.active = target_armature

    result = bpy.ops.modder.universal_snap()
    if "CANCELLED" in result:
        raise RuntimeError("Armature position alignment was cancelled.")

    after_positions = bone_positions(target_armature)
    changed = sorted(
        name
        for name, before in before_positions.items()
        if name in after_positions
        and (
            (after_positions[name][0] - before[0]) ** 2
            + (after_positions[name][1] - before[1]) ** 2
            + (after_positions[name][2] - before[2]) ** 2
        ) > 1e-12
    )

    coverage = target_semantic_coverage(profile, skill_root, target_armature)

    # Runs after Stage 1B normalization, so a normalized source resolves its
    # finger and hand bones through the MMD preset recorded in the manifest.
    finger_report = finger_cleanup.apply_finger_cleanup(
        find_bound_mesh(source_armature),
        source_armature,
        source_preset,
        profile_finger_cleanup(profile),
    )

    source_collection = bpy.data.collections.get("_SOURCE_HIDDEN")
    if source_collection is not None:
        source_armature.hide_set(True)
        source_armature.hide_render = True
        source_collection.hide_viewport = True
        source_collection.hide_render = True

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "aligned",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "target_skeleton": str(target_skeleton_path),
        "target_skeleton_source": target_source,
        "source_armature": source_armature.name,
        "target_armature": target_armature.name,
        "target_bone_count": len(before_positions),
        "source_preset": preset_state["source"],
        "target_preset": preset_state["target"],
        "source_rig": manifest.get("source_rig", {}),
        "align_mode": preset_state["align_mode"],
        "target_semantic_coverage": coverage,
        "changed_bones": changed,
        "changed_count": len(changed),
        "finger_cleanup": finger_report,
    }
    write_json(report_path, report)

    manifest["settings"]["finger_cleanup"] = finger_report
    manifest["stage_history"].append(
        {
            "stage": "aligned",
            "blend": str(output_path),
            "report": str(report_path),
            "target_armature": target_armature.name,
        }
    )
    write_json(project_path, manifest)
    print(f"ALIGN_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
