"""Stage 9 for RE6: export the .mod and finalize the Mod folder.

RE6 does not use the Modding Toolkit batch exporters, so this stage drives the
add-on's own `re6_mod.export_re6_mod` operator, copies the finished `.mrl` and
the reused `.ccl` next to it, writes `modinfo.ini`, and then re-parses every
binary resource to prove the export matches what the earlier stages planned.
Immediately before export, normal meshes are assigned ``IASkinTB4wt`` and
face meshes are assigned ``IASkinTB1wt`` through the RE6 Model Editor operator.
"""

from __future__ import annotations

import argparse
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
FACE_EXPORT_VERTEX_FORMAT = "IASkinTB1wt"


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


def configured_face_material_names(manifest: dict) -> set[str]:
    """Return source and output material names that belong to the face group."""
    settings = manifest.get("settings") or {}
    export_config = settings.get("export") or {}
    configured = export_config.get("face_material_names")
    if configured is None:
        configured = (
            settings.get("cylindrical_face_normals") or {}
        ).get("mask_material_names") or []
    if isinstance(configured, str):
        configured = [configured]

    names = {
        str(value).strip()
        for value in configured
        if str(value).strip()
    }
    material_names = (settings.get("mrl") or {}).get("material_names") or {}
    for source_name, output_name in material_names.items():
        if str(source_name) in names and str(output_name).strip():
            names.add(str(output_name).strip())
    if not names:
        raise RuntimeError(
            "Face vertex format selection requires "
            "settings.export.face_material_names or "
            "settings.cylindrical_face_normals.mask_material_names."
        )
    return names


def material_aliases(material: bpy.types.Material) -> set[str]:
    """Material names before and after the Stage 6 ASCII rename."""
    aliases = {material.name}
    source_name = material.get("re6_source_name")
    if source_name:
        aliases.add(str(source_name))
    return aliases


def mesh_material_hashes(
    obj: bpy.types.Object,
    hashes_module,
) -> list[str]:
    """Sorted RE6 material hashes used by one mesh."""
    result = set()
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            continue
        stored = str(material.get("re6_hash") or "").strip()
        if stored:
            try:
                result.add(f"{int(stored, 16):08x}")
                continue
            except ValueError:
                pass
        result.add(f"{hashes_module.jamcrc32(material.name):08x}")
    return sorted(result)


def partition_export_meshes(
    meshes: list[bpy.types.Object],
    face_material_names: set[str],
) -> tuple[list[bpy.types.Object], list[bpy.types.Object]]:
    """Split meshes into the normal and face vertex-format groups."""
    regular = []
    face = []
    for obj in meshes:
        slots = [
            slot.material
            for slot in obj.material_slots
            if slot.material is not None
        ]
        if not slots:
            raise RuntimeError(
                f"Exported mesh {obj.name!r} has no materials."
            )
        slot_is_face = [
            bool(material_aliases(material) & face_material_names)
            for material in slots
        ]
        if any(slot_is_face) and not all(slot_is_face):
            raise RuntimeError(
                f"Exported mesh {obj.name!r} mixes face and non-face "
                "materials; split it before export."
            )
        (face if all(slot_is_face) else regular).append(obj)
    if not face:
        raise RuntimeError(
            "No face mesh matched the configured face material names: "
            + ", ".join(sorted(face_material_names))
        )
    return regular, face


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
    manifest: dict,
    hashes_module,
) -> dict:
    """Set normal meshes to IASkinTB4wt and face meshes to IASkinTB1wt."""
    vertex_core = importlib.import_module(f"{addon_module}.core.vertex")
    vertex_formats = importlib.import_module(
        f"{addon_module}.blender.vertex_formats"
    )
    default_hash, default_value = resolve_vertex_format(
        vertex_core,
        EXPORT_VERTEX_FORMAT,
    )
    face_hash, face_value = resolve_vertex_format(
        vertex_core,
        FACE_EXPORT_VERTEX_FORMAT,
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

    face_names = configured_face_material_names(manifest)
    regular, face = partition_export_meshes(meshes, face_names)
    if not regular:
        raise RuntimeError(
            "The RE6 mod collection contains no non-face meshes."
        )

    assignments = []
    for kind, group, target_hash, target_value, weights in (
        ("default", regular, default_hash, default_value, "4"),
        ("face", face, face_hash, face_value, "1"),
    ):
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
                    else FACE_EXPORT_VERTEX_FORMAT
                ),
                "hash": target_value,
                "weights": int(weights),
                "mesh_count": len(group),
                "meshes": result["meshes"],
                "material_hashes": sorted(
                    {
                        value
                        for obj in group
                        for value in mesh_material_hashes(
                            obj,
                            hashes_module,
                        )
                    }
                ),
                "operator_result": result["operator_result"],
            }
        )

    default_assignment = assignments[0]
    face_assignment = assignments[1]
    return {
        "name": EXPORT_VERTEX_FORMAT,
        "hash": default_value,
        "mesh_count": len(meshes),
        "meshes": sorted(obj.name for obj in meshes),
        "operator_result": default_assignment["operator_result"],
        "face": {
            "name": FACE_EXPORT_VERTEX_FORMAT,
            "hash": face_value,
            "weights": face_assignment["weights"],
            "mesh_count": face_assignment["mesh_count"],
            "meshes": face_assignment["meshes"],
            "material_hashes": face_assignment["material_hashes"],
            "operator_result": face_assignment["operator_result"],
        },
        "face_material_names": sorted(face_names),
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
    hashes = importlib.import_module(f"{addon_module}.core.hashes")
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
        manifest,
        hashes,
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
                f"{mod.materials[mesh.material]:08x}"
                if mesh.material < len(mod.materials)
                else None
            ),
            "hash": f"{mesh.blocktype:08x}",
            "name": vertex_core.official_name(mesh.blocktype),
        }
        for index, mesh in enumerate(mod.meshes)
    ]
    expected_by_material = {}
    for assignment in vertex_format_report["assignments"]:
        for material_hash in assignment["material_hashes"]:
            previous = expected_by_material.get(material_hash)
            if previous is not None and previous != assignment["hash"]:
                raise RuntimeError(
                    "A material is assigned more than one vertex format: "
                    f"{material_hash} -> {previous}, {assignment['hash']}"
                )
            expected_by_material[material_hash] = assignment["hash"]
    for entry in mod_mesh_formats:
        entry["expected_hash"] = expected_by_material.get(entry["material"])
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
        if entry["expected_hash"] is None
        or entry["hash"] != entry["expected_hash"]
    ]
    if unexpected_vertex_formats:
        failures.append(
            {
                "reason": "the exported .mod contains an unexpected vertex format",
                "default_format": vertex_format_report["hash"],
                "face_format": vertex_format_report["face"]["hash"],
                "unexpected": unexpected_vertex_formats,
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
    manifest["settings"]["export_face_vertex_format"] = (
        FACE_EXPORT_VERTEX_FORMAT
    )
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
            "face_vertex_format": FACE_EXPORT_VERTEX_FORMAT,
            "face_vertex_format_meshes": vertex_format_report["face"][
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
