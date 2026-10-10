"""Stage 9 for RE6: export the .mod and finalize the Mod folder.

RE6 does not use the Modding Toolkit batch exporters, so this stage drives the
add-on's own `re6_mod.export_re6_mod` operator, copies the finished `.mrl` and
the reused `.ccl` next to it, writes `modinfo.ini`, and then re-parses every
binary resource to prove the export matches what the earlier stages planned.
Immediately before export, meshes containing the head vertex group
``RE6Bone_004`` are assigned ``IASkinTB1wt`` and all other meshes are assigned
``IASkinTB4wt`` through the RE6 Model Editor operator.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
import shutil
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import re6_target_skeleton
import re6_material_textures


MOD_COLLECTION_TYPE = "RE6_MOD_COLLECTION"
MODEL_DIR = "nativePC_mod/data/chara/pl/pl0600/model"
EXPORT_VERTEX_FORMAT = "IASkinTB4wt"
HEAD_EXPORT_VERTEX_FORMAT = "IASkinTB1wt"
HEAD_VERTEX_GROUP = "RE6Bone_004"
HEAD_COVERAGE_THRESHOLD = 1.0 / 3.0
HEAD_WEIGHT_SHARE_THRESHOLD = 1.0 / 3.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--mod-root", type=Path)
    return parser.parse_args(argv[separator:])


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def mod_root(manifest: dict, profile: dict, override) -> Path:
    if override:
        return Path(override).resolve()
    display = (
        profile.get("target_character_display")
        or profile["target_character"]
    )
    return (
        Path(manifest["source"]["path"]).resolve().parent
        / f"{manifest['mod']['name']}_{display}"
    )


def modinfo_text(manifest: dict, profile: dict, export_config: dict) -> str:
    info = export_config.get("modinfo") or {}
    name = info.get("name") or manifest["mod"]["name"]
    lines = [
        f"name={name}",
        f"version={info.get('version', '1.0')}",
        f"description={info.get('description', 'Require RE6 SideLoader.')}",
        f"author={info.get('author', 'GZYV')}",
    ]
    # The screenshot is the user's own; only the file name is fixed, and it
    # follows the character this mod adds.
    lines.append(f"screenshot={info.get('screenshot') or (name + '.jpg')}")
    lines.append(f"category={info.get('category', '!Characters > Ada')}")
    bundle = info.get("bundle") or f"RE6 {name}"
    lines.append(f"nameAsBundle={bundle}")
    return "\n".join(lines) + "\n"


def reference_screenshot(source: Path) -> str | None:
    """Screenshot file name declared by the reference mod's modinfo.ini."""
    modinfo = source / "modinfo.ini"
    if not modinfo.is_file():
        return None
    for line in modinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() == "screenshot":
            return value.strip()
    return None


def seed_from_reference(
    skill_root: Path,
    root: Path,
    export_config: dict,
    texture_dir: Path,
) -> dict:
    """Copy the reference mod's tree so its auxiliary files are inherited.

    Everything except the model resources we replace and the textures we ship
    ourselves is taken over from the reference mod, which keeps the extra
    slots, the event face models and the rest of the folder layout intact.
    """
    relative = export_config.get("reference_mod_root")
    if not relative:
        raise RuntimeError("settings.export.reference_mod_root is not set.")
    source = (skill_root / relative).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Reference mod not found: {source}")
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, root, dirs_exist_ok=True)

    # The reference screenshot must not carry over; the user supplies their
    # own under the character-based name written into modinfo.ini.
    screenshot = reference_screenshot(source)
    screenshot_removed = []
    if screenshot:
        candidate = root / screenshot
        if candidate.is_file():
            candidate.unlink()
            screenshot_removed.append(screenshot)

    texture_root = texture_dir.resolve()
    removed = []
    for path in sorted(root.rglob("*.tex")):
        if texture_root in path.parents:
            continue
        removed.append(str(path.relative_to(root)))
        path.unlink()
    for directory in sorted(root.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return {
        "reference_mod": str(source),
        "removed_screenshot": screenshot_removed,
        "removed_textures": removed,
        "removed_texture_count": len(removed),
    }


def resolve_vertex_format(vertex_core, format_name: str) -> tuple[int, str]:
    """Resolve one game layout by its official RE6 name."""
    target_hashes = [
        value
        for value in vertex_core.FORMATS
        if vertex_core.official_name(value) == format_name
    ]
    if len(target_hashes) != 1:
        raise RuntimeError(
            "Could not resolve one RE6 vertex format named "
            f"{format_name!r}: {target_hashes}"
        )
    target_hash = target_hashes[0]
    return target_hash, f"{target_hash:08x}"


def partition_export_meshes(
    meshes: list[bpy.types.Object],
) -> tuple[list[bpy.types.Object], list[bpy.types.Object], list[dict]]:
    """Split meshes by their positive RE6Bone_004 weight proportions."""
    regular = []
    head = []
    decisions = []
    for obj in meshes:
        group = obj.vertex_groups.get(HEAD_VERTEX_GROUP)
        total_vertices = len(obj.data.vertices)
        head_vertices = 0
        head_weight_sum = 0.0
        all_weight_sum = 0.0
        head_full_weight_vertices = 0
        for vertex in obj.data.vertices:
            head_weight = 0.0
            for item in vertex.groups:
                weight = float(item.weight)
                if weight <= 0.0:
                    continue
                all_weight_sum += weight
                if group is not None and item.group == group.index:
                    head_weight = weight
            if head_weight > 0.0:
                head_vertices += 1
                head_weight_sum += head_weight
                if head_weight >= 0.999999:
                    head_full_weight_vertices += 1

        head_vertex_ratio = (
            head_vertices / total_vertices if total_vertices else 0.0
        )
        head_weight_share = (
            head_weight_sum / all_weight_sum if all_weight_sum else 0.0
        )
        use_head_format = (
            head_vertex_ratio >= HEAD_COVERAGE_THRESHOLD
            and head_weight_share >= HEAD_WEIGHT_SHARE_THRESHOLD
        )
        (head if use_head_format else regular).append(obj)
        decisions.append(
            {
                "mesh": obj.name,
                "vertices": total_vertices,
                "head_vertices": head_vertices,
                "head_vertex_ratio": head_vertex_ratio,
                "head_weight_sum": head_weight_sum,
                "all_weight_sum": all_weight_sum,
                "head_weight_share": head_weight_share,
                "head_full_weight_vertices": head_full_weight_vertices,
                "format": (
                    HEAD_EXPORT_VERTEX_FORMAT
                    if use_head_format
                    else EXPORT_VERTEX_FORMAT
                ),
            }
        )
    return regular, head, decisions


def select_meshes(meshes: list[bpy.types.Object]) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    for obj in meshes:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]


def set_vertex_format_group(
    meshes: list[bpy.types.Object],
    target_hash: int,
    weights: str,
    vertex_formats,
) -> dict:
    """Apply one format with the RE6 Model Editor operator and read it back."""
    target_value = f"{target_hash:08x}"
    select_meshes(meshes)
    result = bpy.ops.re6_mod.set_vertex_format(
        useAuto=False,
        skinned=True,
        weights=weights,
        uvCount=1,
        useColor=False,
        useTangent=True,
        extraData=False,
        vertexFormat=target_value,
    )
    if "FINISHED" not in result:
        raise RuntimeError(
            f"RE6 set_vertex_format failed for {target_value}: {result}"
        )

    stored = {
        obj.name: vertex_formats.stored_format(obj.data)
        for obj in meshes
    }
    mismatches = [
        name
        for name, value in stored.items()
        if value != target_hash
    ]
    if mismatches:
        raise RuntimeError(
            f"RE6 vertex format {target_value} validation failed for: "
            + ", ".join(sorted(mismatches))
        )
    return {
        "operator_result": sorted(result),
        "meshes": sorted(stored),
    }


def apply_export_vertex_format(
    collection: bpy.types.Collection,
    addon_module: str,
) -> dict:
    """Set head-group meshes to 1wt and all other meshes to 4wt."""
    vertex_core = importlib.import_module(f"{addon_module}.core.vertex")
    vertex_formats = importlib.import_module(
        f"{addon_module}.blender.vertex_formats"
    )
    default_hash, default_value = resolve_vertex_format(
        vertex_core,
        EXPORT_VERTEX_FORMAT,
    )
    head_hash, head_value = resolve_vertex_format(
        vertex_core,
        HEAD_EXPORT_VERTEX_FORMAT,
    )

    meshes = [
        obj for obj in collection.all_objects if obj.type == "MESH"
    ]
    if not meshes:
        raise RuntimeError(
            f"The RE6 mod collection {collection.name!r} has no meshes."
        )
    if not any(
        obj.type == "ARMATURE" for obj in collection.all_objects
    ):
        raise RuntimeError(
            f"The RE6 mod collection {collection.name!r} has no armature."
        )

    regular, head, head_selection = partition_export_meshes(meshes)

    assignments = []
    for kind, group, target_hash, target_value, weights in (
        ("default", regular, default_hash, default_value, "4"),
        ("head", head, head_hash, head_value, "1"),
    ):
        if not group:
            continue
        result = set_vertex_format_group(
            group,
            target_hash,
            weights,
            vertex_formats,
        )
        assignments.append(
            {
                "kind": kind,
                "name": (
                    EXPORT_VERTEX_FORMAT
                    if kind == "default"
                    else HEAD_EXPORT_VERTEX_FORMAT
                ),
                "hash": target_value,
                "weights": int(weights),
                "mesh_count": len(group),
                "meshes": result["meshes"],
                "operator_result": result["operator_result"],
            }
        )

    default_assignment = next(
        (item for item in assignments if item["kind"] == "default"),
        None,
    )
    head_assignment = next(
        (item for item in assignments if item["kind"] == "head"),
        None,
    )
    return {
        "name": EXPORT_VERTEX_FORMAT,
        "hash": default_value,
        "mesh_count": len(meshes),
        "meshes": sorted(obj.name for obj in meshes),
        "operator_result": (
            default_assignment["operator_result"]
            if default_assignment is not None
            else []
        ),
        "head": {
            "name": HEAD_EXPORT_VERTEX_FORMAT,
            "hash": head_value,
            "weights": 1,
            "mesh_count": (
                head_assignment["mesh_count"]
                if head_assignment is not None
                else 0
            ),
            "meshes": (
                head_assignment["meshes"]
                if head_assignment is not None
                else []
            ),
            "operator_result": (
                head_assignment["operator_result"]
                if head_assignment is not None
                else []
            ),
        },
        "head_vertex_group": HEAD_VERTEX_GROUP,
        "head_selection_thresholds": {
            "head_vertex_ratio": HEAD_COVERAGE_THRESHOLD,
            "head_weight_share": HEAD_WEIGHT_SHARE_THRESHOLD,
        },
        "head_selection": head_selection,
        "assignments": assignments,
    }


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    profile = load_json(profile_path)
    skill_root = profile_path.parents[1]
    project_root = Path(manifest["paths"]["project_root"])
    settings = manifest.get("settings", {})
    export_config = settings.get("export") or {}
    mrl_config = settings.get("mrl") or {}

    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_09_export.json"
    )
    root = mod_root(manifest, profile, args.mod_root)
    model_dir = root / MODEL_DIR
    model_dir.mkdir(parents=True, exist_ok=True)
    texture_dir = root / "nativePC_mod" / str(
        mrl_config.get("texture_dir") or "data/chara/pl/pl0600/gzyv"
    ).replace("\\", "/").strip("/")

    # Build the mod on top of the reference mod: its auxiliary files come
    # along, our model resources and textures replace the ones it shipped.
    seed_report = seed_from_reference(
        skill_root,
        root,
        export_config,
        texture_dir,
    )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    addon_module = re6_target_skeleton.enable_re6_addon()
    mod_core = importlib.import_module(f"{addon_module}.core.mod211")
    mrl_core = importlib.import_module(f"{addon_module}.core.mrl")
    ctc_core = importlib.import_module(f"{addon_module}.core.ctc")
    vertex_core = importlib.import_module(f"{addon_module}.core.vertex")

    collections = [
        item
        for item in bpy.data.collections
        if item.get("~TYPE") == MOD_COLLECTION_TYPE
    ]
    if len(collections) != 1:
        raise RuntimeError(
            f"Expected one RE6 mod collection, found {len(collections)}."
        )
    collection = collections[0]
    importlib.import_module(f"{addon_module}.blender.operators")
    vertex_format_report = apply_export_vertex_format(
        collection,
        addon_module,
    )
    panel = bpy.context.scene.re6_mod_toolpanel
    panel.exportModCollection = collection

    mod_path = model_dir / "pl0600.mod"
    if mod_path.exists():
        mod_path.unlink()
    export_result = bpy.ops.re6_mod.export_re6_mod(
        filepath=str(mod_path),
        selectedOnly=False,
        visibleOnly=False,
        exportAllLODs=True,
        useBlenderMaterialName=False,
        shadowMode="KEEP",
    )
    if "FINISHED" not in export_result or not mod_path.is_file():
        raise RuntimeError(
            f"RE6 mod export failed: {export_result} -> {mod_path}"
        )

    # The .mrl written by Stage 6 and the reference .ccl belong next to the
    # exported model.
    mrl_source = Path(
        settings.get("mrl_path") or project_root / "stages" / "pl0600.mrl"
    ).resolve()
    if not mrl_source.is_file():
        raise FileNotFoundError(f"Finished .mrl not found: {mrl_source}")
    # The reference ships the library as pl0600_0/_1/_2.mrl, so replace those
    # names with our library and drop any pl0600.mrl the reference lacked.
    mrl_filenames = list(
        export_config.get("mrl_filenames")
        or ["pl0600_0.mrl", "pl0600_1.mrl", "pl0600_2.mrl"]
    )
    mrl_payload = mrl_source.read_bytes()
    mrl_path = model_dir / mrl_filenames[0]
    mrl_paths = []
    for name in mrl_filenames:
        target = model_dir / name
        target.write_bytes(mrl_payload)
        mrl_paths.append(str(target))
    legacy_mrl = model_dir / "pl0600.mrl"
    if legacy_mrl.is_file():
        legacy_mrl.unlink()

    ctc_source = Path(
        settings.get("ctc_path") or project_root / "stages" / "pl0600.ctc"
    ).resolve()
    if not ctc_source.is_file():
        raise FileNotFoundError(f"Finished .ctc not found: {ctc_source}")
    ctc_path = model_dir / "pl0600.ctc"
    ctc_path.write_bytes(ctc_source.read_bytes())

    ccl_relative = export_config.get("ccl_source") or profile.get(
        "reference_assets", {}
    ).get("collision_template")
    if not ccl_relative:
        raise RuntimeError("settings.export.ccl_source is not configured.")
    ccl_source = (skill_root / ccl_relative).resolve()
    if not ccl_source.is_file():
        raise FileNotFoundError(f"Reused .ccl not found: {ccl_source}")
    ccl_path = model_dir / "pl0600.ccl"
    ccl_path.write_bytes(ccl_source.read_bytes())

    modinfo_path = root / "modinfo.ini"
    modinfo_path.write_text(
        modinfo_text(manifest, profile, export_config),
        encoding="utf-8",
    )

    # ---- verification -------------------------------------------------
    mod = mod_core.Mod211.parse(mod_path.read_bytes())
    mrl = mrl_core.parse(mrl_path.read_bytes())
    ctc = ctc_core.parse(ctc_path.read_bytes())
    mod_hashes = [f"{value:08x}" for value in mod.materials]
    mrl_hashes = [f"{material.name:08x}" for material in mrl.materials]
    mod_mesh_formats = [
        {
            "index": index,
            "material": (
                f"{mod.materials[(mesh.unk6 << 4) | (mesh.flags >> 12)]:08x}"
                if (mesh.unk6 << 4) | (mesh.flags >> 12)
                < len(mod.materials)
                else None
            ),
            "hash": f"{mesh.blocktype:08x}",
            "name": vertex_core.official_name(mesh.blocktype),
        }
        for index, mesh in enumerate(mod.meshes)
    ]
    expected_format_counts = {
        assignment["hash"]: assignment["mesh_count"]
        for assignment in vertex_format_report["assignments"]
    }
    actual_format_counts = Counter(
        entry["hash"] for entry in mod_mesh_formats
    )
    bone_ids = sorted({row[0] for row in mod.bone_table()})
    texture_files = sorted(texture_dir.rglob("*.tex"))
    textures = sorted(path.name for path in texture_files)
    native_root = root / "nativePC_mod"
    mrl_texture_paths = {
        value.replace("\\", "/").strip("/")
        for value in mrl.textures
    }
    actual_textures = {
        path.relative_to(native_root).with_suffix("").as_posix()
        for path in texture_files
    }
    template_relative = mrl_config.get("template_mrl")
    template_material = mrl_config.get("template_material")
    preserved_slots = list(
        mrl_config.get("preserved_texture_slots") or []
    )
    external_allowed = set()
    if preserved_slots:
        if not template_relative or not template_material:
            raise RuntimeError(
                "Both template_mrl and template_material are required for "
                "preserved_texture_slots."
            )
        template_path = (skill_root / str(template_relative)).resolve()
        external_allowed = {
            value.replace("\\", "/").strip("/")
            for value in re6_material_textures.preserved_template_bindings(
                mrl_core,
                template_path,
                str(template_material),
                preserved_slots,
            ).values()
        }
    shipped_expected = mrl_texture_paths - external_allowed
    missing_textures = sorted(shipped_expected - actual_textures)
    extra_textures = sorted(actual_textures - shipped_expected)
    unregistered_external = sorted(
        mrl_texture_paths - shipped_expected - external_allowed
    )

    failures = []
    # The reference mod's convention: the .mrl ships only the materials this
    # mod changes, while the .mod may also carry target-owned hashes. Require
    # only that every .mrl entry is listed by the exported .mod.
    missing_in_mod = [h for h in mrl_hashes if h not in mod_hashes]
    if missing_in_mod:
        failures.append(
            {
                "reason": "the .mrl ships materials the .mod does not list",
                "missing_in_mod": missing_in_mod,
                "mod": mod_hashes,
                "mrl": mrl_hashes,
            }
        )
    if not mod.meshes:
        failures.append({"reason": "the exported .mod has no meshes"})
    unexpected_vertex_formats = [
        entry
        for entry in mod_mesh_formats
        if entry["hash"] not in expected_format_counts
    ]
    if unexpected_vertex_formats:
        failures.append(
            {
                "reason": "the exported .mod contains an unexpected vertex format",
                "default_format": vertex_format_report["hash"],
                "head_format": vertex_format_report["head"]["hash"],
                "unexpected": unexpected_vertex_formats,
            }
        )
    mismatched_format_counts = {
        target_hash: {
            "expected": expected_count,
            "actual": actual_format_counts.get(target_hash, 0),
        }
        for target_hash, expected_count in expected_format_counts.items()
        if actual_format_counts.get(target_hash, 0) != expected_count
    }
    if mismatched_format_counts:
        failures.append(
            {
                "reason": "the exported .mod vertex-format counts changed",
                "expected": expected_format_counts,
                "actual": dict(actual_format_counts),
                "mismatches": mismatched_format_counts,
            }
        )
    if missing_textures:
        failures.append(
            {
                "reason": "the .mrl references missing texture files",
                "missing_textures": missing_textures,
            }
        )
    if extra_textures:
        failures.append(
            {
                "reason": "texture files are not referenced by the .mrl",
                "extra_textures": extra_textures,
            }
        )
    if unregistered_external:
        failures.append(
            {
                "reason": "the .mrl references unregistered external textures",
                "unregistered_external": unregistered_external,
            }
        )

    report = {
        "stage": "export",
        "status": "failed" if failures else "passed",
        "project": str(project_path),
        "input": str(input_path),
        "mod_root": str(root),
        "model_dir": str(model_dir),
        "export_result": sorted(export_result),
        "seed": seed_report,
        "vertex_format": vertex_format_report,
        "files": {
            "mod": str(mod_path),
            "mrl": mrl_paths,
            "ctc": str(ctc_path),
            "ccl": str(ccl_path),
            "modinfo": str(modinfo_path),
        },
        "mod": {
            "bones": mod.boneCount,
            "meshes": len(mod.meshes),
            "materials": mod_hashes,
            "bone_ids": bone_ids,
            "vertex_formats": mod_mesh_formats,
            "vertex_format_counts": dict(actual_format_counts),
        },
        "mrl": {
            "materials": mrl_hashes,
            "textures": len(mrl.textures),
            "texture_paths": sorted(mrl_texture_paths),
            "external_texture_paths": sorted(
                external_allowed & mrl_texture_paths
            ),
        },
        "extra_mod_materials": [
            h for h in mod_hashes if h not in mrl_hashes
        ],
        "ctc": {
            "chains": len(ctc.chains),
            "nodes": ctc.node_count,
        },
        "textures": textures,
        "texture_files": {
            "expected_shipped": len(shipped_expected),
            "expected_external": len(external_allowed & mrl_texture_paths),
            "actual": len(actual_textures),
            "missing": missing_textures,
            "extra": extra_textures,
            "unregistered_external": unregistered_external,
        },
        "failures": failures,
    }
    write_json(report_path, report)
    if failures:
        raise RuntimeError(
            f"Stage 9 export validation failed; see {report_path}"
        )

    manifest["settings"]["export_report"] = str(report_path)
    manifest["settings"]["export_vertex_format"] = EXPORT_VERTEX_FORMAT
    manifest["settings"]["export_head_vertex_format"] = (
        HEAD_EXPORT_VERTEX_FORMAT
    )
    manifest["settings"]["export_head_vertex_group"] = HEAD_VERTEX_GROUP
    for part_name in list(manifest.get("parts", {})):
        manifest["parts"][part_name]["status"] = "exported"
        manifest["parts"][part_name]["validation"] = "stage_09_export"
    manifest["stage_history"].append(
        {
            "stage": "export",
            "report": str(report_path),
            "mod": str(mod_path),
            "bones": mod.boneCount,
            "meshes": len(mod.meshes),
            "vertex_format": EXPORT_VERTEX_FORMAT,
            "vertex_format_meshes": vertex_format_report["mesh_count"],
            "head_vertex_format": HEAD_EXPORT_VERTEX_FORMAT,
            "head_vertex_group": HEAD_VERTEX_GROUP,
            "head_vertex_format_meshes": vertex_format_report["head"][
                "mesh_count"
            ],
        }
    )
    write_json(project_path, manifest)
    print(f"EXPORT_REPORT={report_path}")
    print(f"EXPORT_MOD={mod_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
