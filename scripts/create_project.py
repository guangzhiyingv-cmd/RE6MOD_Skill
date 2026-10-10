#!/usr/bin/env python3
"""Create a source-specific project manifest for a character replacement mod."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


PART_NAMES = ("body", "face", "hair")

SOURCE_ADAPTERS = {
    "pmx": "mmd",
    "pmd": "mmd",
    "fbx": "fbx",
    "glb": "gltf",
    "gltf": "gltf",
    "vrm": "vrm",
    "blend": "blender",
    "obj": "obj",
}

SOURCE_FORMATS = tuple(SOURCE_ADAPTERS)


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        type=Path,
        default=skill_root / "profiles" / "re6-pl0600.json",
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--work-root",
        type=Path,
        required=True,
        help=(
            "Project work root. It must live in the mod's own top-level "
            "folder, for example E:\\mod\\re6\\Lossi\\work for a source "
            "model under E:\\mod\\re6\\Lossi."
        ),
    )
    parser.add_argument("--mod-name", required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--scale-hint", type=float)
    parser.add_argument(
        "--source-format",
        choices=("auto",) + SOURCE_FORMATS,
        default="auto",
    )
    parser.add_argument(
        "--source-preset",
        default="auto",
        help=(
            "Modding Toolkit import preset filename, or 'auto' to detect "
            "after import."
        ),
    )
    parser.add_argument("--merge-textures", action="store_true")
    parser.add_argument(
        "--no-merge-by-base-image",
        dest="merge_by_base_image",
        action="store_false",
        default=True,
        help=(
            "Keep Stage 5C from merging material slots that share one Base "
            "Color image."
        ),
    )
    parser.add_argument(
        "--outline",
        "--make-outline",
        dest="make_outline",
        action="store_true",
    )
    parser.add_argument(
        "--cylindrical-face-normals",
        dest="make_cylindrical_face_normals",
        action="store_true",
        default=True,
        help="Enable the default cylindrical face-normal preprocessing.",
    )
    parser.add_argument(
        "--no-cylindrical-face-normals",
        dest="make_cylindrical_face_normals",
        action="store_false",
        help="Disable cylindrical face-normal preprocessing for this project.",
    )
    parser.add_argument(
        "--execution-mode",
        choices=("normal", "low-token"),
        default="low-token",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "character-replacement-mod"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def load_profile(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_texture_roots(source: Path) -> list[str]:
    roots = []
    for name in (
        "tex",
        "toon",
        "texture",
        "textures",
        "maps",
        "materials",
    ):
        candidate = source.parent / name
        if candidate.is_dir():
            roots.append(str(candidate.resolve()))
    return roots


def main() -> int:
    args = parse_args()
    profile_path = args.profile.resolve()
    profile = load_profile(profile_path)
    # Targets with a single prepared part (for example RE6) declare their own
    # part list in the profile instead of using the body/face/hair default.
    part_names = tuple(profile.get("part_names") or PART_NAMES)
    source = args.source.resolve()
    work_root = args.work_root.resolve()

    if not source.is_file():
        raise FileNotFoundError(f"Source model not found: {source}")
    suffix_format = source.suffix.lower().lstrip(".")
    source_format = (
        suffix_format
        if args.source_format == "auto"
        else args.source_format
    )
    adapter = SOURCE_ADAPTERS.get(source_format)
    if adapter is None:
        raise ValueError(
            f"Unsupported source format {source_format!r}. Supported formats: "
            + ", ".join(SOURCE_FORMATS)
        )

    project_id = args.project_id or slugify(args.mod_name)
    project_root = work_root / project_id
    project_file = project_root / "project.json"
    if project_file.exists():
        raise FileExistsError(
            f"Project manifest already exists: {project_file}"
        )

    for relative in ("reports", "stages", "manual"):
        (project_root / relative).mkdir(parents=True, exist_ok=True)

    parts = {
        part_name: {
            "status": "pending",
            "material_assignments": {},
            "collection": None,
            "validation": None,
        }
        for part_name in part_names
    }

    manifest = {
        "schema_version": 2,
        "project_id": project_id,
        "source": {
            "path": str(source),
            "format": source_format,
            "adapter": adapter,
            "size": source.stat().st_size,
            "sha256": sha256(source),
            "texture_roots": source_texture_roots(source),
        },
        "source_rig": {
            "requested_preset": (
                None
                if str(args.source_preset).lower() == "auto"
                else args.source_preset
            ),
            "detected_preset": None,
            "effective_preset": None,
            "mapping_file": None,
            "detection": None,
        },
        "target_profile": {
            "profile_id": profile["profile_id"],
            "path": str(profile_path),
            "game": profile["game"],
            "target_character": profile["target_character"],
            "target_characters": profile.get("target_characters", []),
            "target_slot": profile.get("target_slot"),
            "parts": list(part_names),
            "face_skeleton": profile["reference_assets"].get("face_skeleton"),
        },
        "mod": {
            "name": args.mod_name,
            "manual_export": True,
        },
        "paths": {
            "work_root": str(work_root),
            "project_root": str(project_root),
            "reports": str(project_root / "reports"),
            "stages": str(project_root / "stages"),
            "manual": str(project_root / "manual"),
        },
        "settings": {
            "scale_hint": args.scale_hint,
            "scale_source": "user" if args.scale_hint is not None else None,
            "stage_5c_merge_textures": args.merge_textures,
            "stage_5c_merge_by_base_image": args.merge_by_base_image,
            "make_outline": args.make_outline,
            "make_cylindrical_face_normals": (
                args.make_cylindrical_face_normals
            ),
            "cylindrical_face_normals": {
                **{
                    "axis": [0.0, 0.0, 1.0],
                    "origin": "object_origin",
                    "mask_vertex_group": None,
                    "mask_material_names": [],
                    "seam_neighbor_parts": ["body"],
                    "seam_match_distance": 0.001,
                    "seam_blend_distance": 0.02,
                    "outer_boundary_perimeter_ratio": 0.75,
                    "allow_nearby_vertex_fallback": False,
                    "seam_normal_mode": "bilateral",
                    "strength": 1.0,
                    "reencode_shape_keys": True,
                },
                **(profile.get("cylindrical_face_normals") or {}),
            },
            "mrl": {
                **{
                    "texture_dir": "data/chara/pl/pl0600/gzyv",
                    "texture_prefix": "",
                    "material_names": {},
                    "template_mrl": "assets/RE6/pl0600_0.mrl",
                    "template_material": "68b3d093",
                    "albedo_slot": "cd06f347",
                    "normal_slot": "2266034a",
                    "preserved_texture_slots": [
                        "57c1c3d5",
                        "0ed1b35a",
                    ],
                    "null_normal_texture": (
                        "data/chara/pl/pl0600/gzyv/Null_NM"
                    ),
                },
                **(profile.get("mrl") or {}),
            },
            "texture": {
                **{
                    "compression": "BC1",
                    "max_size": 2048,
                    "normal_max_size": 2048,
                    "base_brightness_reduction_percent": 40,
                    "null_normal_dds": "assets/RE6/Null_NM.dds",
                },
                **(profile.get("texture") or {}),
            },
            "ctc": {
                **{
                    "node": {
                        "angle_limit_start_degrees": 15.0,
                        "angle_limit_end_degrees": 60.0,
                        "collision_radius": 2.5,
                        "collision_shape": 1,
                    },
                    "chain": {
                        "damping": 0.02,
                        "limit_force": 100.0,
                        "col_group": 1,
                    },
                },
                **(profile.get("ctc") or {}),
            },
            "export": {
                **{
                    "reference_mod_root": None,
                    "ccl_source": None,
                    "modinfo": {
                        "author": "GZYV",
                        "version": "1.0",
                        "description": "Require RE6 SideLoader.",
                        "category": "!Characters > Ada",
                    },
                },
                **(profile.get("export") or {}),
            },
            "execution_mode": args.execution_mode,
            "skip_reimport_validation": args.execution_mode == "low-token",
        },
        "parts": parts,
        "bone_mapping": {},
        "expression_mapping": {},
        "chain_candidates": {part_name: [] for part_name in part_names},
        "manual_review": [],
        "stage_history": [],
    }
    project_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.execution_mode == "low-token":
        run_state_file = project_root / "reports" / "run_state.json"
        run_state_file.write_text(
            json.dumps(
                {
                    "execution_mode": "low-token",
                    "current_stage": "stage_0",
                    "status": "pending",
                    "input_blend": None,
                    "output_blend": None,
                    "report": None,
                    "log": None,
                    "next_stage": "stage_0",
                    "next_action": "run_stage_0",
                    "blockers": [],
                    "updated_at": None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(str(project_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
