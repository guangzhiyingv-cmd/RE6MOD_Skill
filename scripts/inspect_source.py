"""Inspect any supported source model without modifying it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import source_import
import source_rig_utils


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--adapter")
    parser.add_argument("--source-preset", default="auto")
    return parser.parse_args(argv[separator:])


def load_legacy_inspector() -> object:
    path = SCRIPT_DIR / "inspect_mmd_source.py"
    spec = importlib.util.spec_from_file_location(
        "legacy_source_inspector",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load inspector helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def infer_adapter(source: Path, requested: str | None) -> str:
    if requested:
        return requested
    suffix = source.suffix.lower().lstrip(".")
    try:
        return {
            "pmx": "mmd",
            "pmd": "mmd",
            "fbx": "fbx",
            "glb": "gltf",
            "gltf": "gltf",
            "vrm": "vrm",
            "blend": "blender",
            "obj": "obj",
        }[suffix]
    except KeyError as error:
        raise ValueError(f"Unsupported source format: {source.suffix}") from error


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    report_path = args.report.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source model not found: {source}")

    project = None
    if args.project:
        project = json.loads(
            args.project.resolve().read_text(encoding="utf-8")
        )
    adapter = (
        (project or {}).get("source", {}).get("adapter")
        or args.adapter
    )
    adapter = infer_adapter(source, adapter)
    helpers = load_legacy_inspector()
    helpers.clear_scene()
    imported = source_import.import_source(
        source,
        adapter,
        scale=args.scale,
    )
    meshes = [
        helpers.inspect_mesh(obj)
        for obj in imported
        if obj.type == "MESH"
    ]
    armatures = [
        helpers.inspect_armature(obj)
        for obj in imported
        if obj.type == "ARMATURE"
    ]
    source_rig = None
    source_pose = None
    armature_objects = [
        obj for obj in imported if obj.type == "ARMATURE"
    ]
    if len(armature_objects) == 1:
        requested = (
            (project or {})
            .get("source_rig", {})
            .get("requested_preset")
        )
        if str(args.source_preset).lower() != "auto":
            requested = args.source_preset
        source_rig = source_rig_utils.detect_source_rig(
            armature_objects[0],
            requested_preset=requested,
        )
        try:
            source_pose = source_rig_utils.upper_arm_pose(
                armature_objects[0],
                source_rig.get("preset"),
            )
        except RuntimeError as error:
            source_pose = {
                "pose": "unknown",
                "status": "deferred",
                "reason": "upper-arm chain could not be resolved",
                "error": str(error),
            }

    images = sorted(
        {
            image.filepath
            for image in bpy.data.images
            if image.filepath
        }
    )
    report = {
        "source": str(source),
        "project": str(args.project.resolve()) if args.project else None,
        "source_format": source.suffix.lower().lstrip("."),
        "adapter": adapter,
        "scale": args.scale,
        "mesh_count": len(meshes),
        "armature_count": len(armatures),
        "image_count": len(images),
        "source_rig": source_rig,
        "source_pose": source_pose,
        "images": images,
        "meshes": meshes,
        "armatures": armatures,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"SOURCE_REPORT={report_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
