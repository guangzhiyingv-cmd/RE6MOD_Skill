"""Stage 6 for RE6: give every prepared material a hash and write the .mrl.

RE6 keeps the material list in the .mod (hashes only) and the shader, texture
and parameter data in a separate .mrl library keyed by the same hashes. This
stage names the prepared Blender materials, records their hash on the
materials, renames the mesh objects to the exporter's scheme, and writes the
.mrl from the game's vanilla character material template.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import re6_target_skeleton
import re6_material_textures
import source_rig_utils


MAT_PROPERTY = "re6_hash"
MOD_COLLECTION_TYPE = "RE6_MOD_COLLECTION"


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--mrl", type=Path)
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


def core_module(skill_root: Path, profile: dict) -> object:
    """Import the add-on's pure Python core from the shipped add-on copy."""
    addon = (
        skill_root / profile["reference_assets"]["model_editor_addon"]
    ).resolve()
    if not addon.is_dir():
        raise FileNotFoundError(f"RE6 add-on copy not found: {addon}")
    if str(addon) not in sys.path:
        sys.path.insert(0, str(addon))
    return importlib.import_module("core.mrl")


def hashes_module(skill_root: Path, profile: dict) -> object:
    addon = (
        skill_root / profile["reference_assets"]["model_editor_addon"]
    ).resolve()
    if str(addon) not in sys.path:
        sys.path.insert(0, str(addon))
    return importlib.import_module("core.hashes")


def mod_collection() -> bpy.types.Collection:
    collections = [
        collection
        for collection in bpy.data.collections
        if collection.get("~TYPE") == MOD_COLLECTION_TYPE
    ]
    if len(collections) != 1:
        raise RuntimeError(
            "Expected one RE6 mod collection, found "
            f"{len(collections)}."
        )
    return collections[0]


def mod_meshes(collection: bpy.types.Collection) -> list:
    return [
        obj
        for obj in collection.all_objects
        if obj.type == "MESH" and obj.data.polygons
    ]


def ascii_material_name(name: str, rename: dict) -> str:
    """English, unique material name used for the hash."""
    if name in rename:
        return str(rename[name])
    if name.isascii():
        return name
    return ""


def stage6_materials(collection, rename: dict) -> tuple[list, dict]:
    """One entry per material used by the mod meshes, in slot order."""
    ordered = []
    seen = set()
    for obj in mod_meshes(collection):
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in seen:
                continue
            seen.add(material.name)
            ordered.append(material)
    return ordered, rename


def load_template_material(
    skill_root: Path,
    profile: dict,
    mrl_config: dict,
    mrl_core,
) -> dict:
    """Pick the vanilla material every new material is cloned from."""
    relative = mrl_config.get("template_mrl") or profile.get(
        "reference_assets", {}
    ).get("mrl_template")
    if not relative:
        raise RuntimeError("settings.mrl.template_mrl is not configured.")
    path = (skill_root / relative).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"MRL template not found: {path}")
    template = mrl_core.parse(path.read_bytes())

    wanted = str(mrl_config.get("template_material") or "").strip()
    chosen = None
    if wanted:
        chosen = template.find(int(wanted, 16))
    if chosen is None and template.materials:
        chosen = template.materials[0]
    if chosen is None:
        raise RuntimeError(
            "Could not pick an MRL template material from " f"{path}."
        )

    slots = [
        command.b
        for command in chosen.commands
        if command.type == mrl_core.CMD_TEXTURE
    ]
    configured = str(mrl_config.get("albedo_slot") or "").strip()
    if configured:
        albedo = int(configured, 16)
        if albedo not in slots:
            raise RuntimeError(
                f"albedo_slot {configured} is not a slot of the template "
                f"material: {[f'{slot:08x}' for slot in slots]}"
            )
    elif len(slots) == 1:
        albedo = slots[0]
    else:
        raise RuntimeError(
            "The template material has several texture slots; set "
            "settings.mrl.albedo_slot. Slots: "
            + ", ".join(f"{slot:08x}" for slot in slots)
        )
    configured_normal = str(mrl_config.get("normal_slot") or "").strip()
    normal = None
    if configured_normal:
        normal = int(configured_normal, 16)
        if normal not in slots:
            raise RuntimeError(
                f"normal_slot {configured_normal} is not a slot of the "
                f"template material: {[f'{slot:08x}' for slot in slots]}"
            )
    elif 0x2266034A in slots:
        normal = 0x2266034A

    preserved_slots = [
        str(value).strip()
        for value in (mrl_config.get("preserved_texture_slots") or [])
    ]
    for value in preserved_slots:
        if int(value, 16) not in slots:
            raise RuntimeError(
                f"preserved_texture_slot {value} is not a slot of the "
                "template material: "
                f"{[f'{slot:08x}' for slot in slots]}"
            )

    return {
        "path": path,
        "file": template,
        "material": chosen,
        "albedo_slot": albedo,
        "normal_slot": normal,
        "preserved_slots": preserved_slots,
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
    mrl_config = settings.get("mrl") or {}

    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_06_mrl.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_06_mrl.json"
    )
    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    mrl_core = core_module(skill_root, profile)
    hashes = hashes_module(skill_root, profile)
    template_info = load_template_material(
        skill_root,
        profile,
        mrl_config,
        mrl_core,
    )
    template = template_info["file"]
    template_material = template_info["material"]
    albedo_slot = template_info["albedo_slot"]
    normal_slot = template_info["normal_slot"]
    preserved_slots = template_info["preserved_slots"]
    null_normal_texture = str(
        mrl_config.get("null_normal_texture") or ""
    ).replace("/", "\\").strip("\\")
    if normal_slot is not None and not null_normal_texture:
        raise RuntimeError(
            "settings.mrl.null_normal_texture is required when normal_slot "
            "is configured."
        )
    preserved_bindings = (
        re6_material_textures.preserved_template_bindings(
            mrl_core,
            template_info["path"],
            f"{template_material.name:08x}",
            preserved_slots,
        )
        if preserved_slots
        else {}
    )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    enable_addon(("Modding-Toolkit", "Modding-Toolkit-dev"))
    re6_target_skeleton.enable_re6_addon()

    collection = mod_collection()
    meshes = mod_meshes(collection)
    if not meshes:
        raise RuntimeError("The RE6 mod collection has no meshes.")

    materials, _ = stage6_materials(collection, {})
    rename_map = mrl_config.get("material_names") or {}
    texture_dir = str(
        mrl_config.get("texture_dir") or "data/chara/pl/pl0600/gzyv"
    ).replace("/", "\\")
    texture_prefix = str(mrl_config.get("texture_prefix") or "")

    records = []
    entries = []
    # Like the reference mod, the library contains only the materials this
    # mod changes. Vanilla materials remain resolved by the game.
    for material in materials:
        english = ascii_material_name(material.name, rename_map)
        if not english:
            raise RuntimeError(
                "Material needs an English name in settings.mrl."
                f"material_names: {material.name!r}"
            )
        name_hash = hashes.jamcrc32(english)
        material[MAT_PROPERTY] = f"{name_hash:08x}"
        material["re6_source_name"] = material.name
        # rename_meshes builds the object name from the material name, and the
        # exporter hashes that suffix, so the material itself must be ASCII.
        material.name = english
        base = f"{texture_dir}\\{texture_prefix}{english}"
        binds = {albedo_slot: f"{base}_BM"}
        for slot, path in preserved_bindings.items():
            binds[slot] = path
        normal_image = re6_material_textures.find_normal_image(material)
        normal_texture = None
        normal_source = None
        if normal_slot is not None:
            if normal_image is not None:
                normal_texture = (
                    f"{texture_dir}\\{texture_prefix}{english}_NM"
                )
                normal_source = (
                    normal_image.filepath or normal_image.name
                )
            else:
                normal_texture = null_normal_texture
            binds[normal_slot] = normal_texture
        core = copy.deepcopy(template_material)
        core.name = name_hash
        entries.append((core, binds))
        records.append(
            {
                "material": material.name,
                "english": english,
                "hash": f"{name_hash:08x}",
                "base_texture": binds[albedo_slot],
                "normal_texture": normal_texture,
                "normal_source": normal_source,
                "normal_is_null": bool(
                    normal_slot is not None
                    and normal_texture == null_normal_texture
                ),
                "albedo_slot": f"{albedo_slot:08x}",
                "normal_slot": (
                    f"{normal_slot:08x}"
                    if normal_slot is not None
                    else None
                ),
                "preserved_textures": {
                    f"{slot:08x}": path
                    for slot, path in preserved_bindings.items()
                },
                "slots": [
                    f"{command.b:08x}"
                    for command in core.commands
                    if command.type == mrl_core.CMD_TEXTURE
                ],
            }
        )

    # The exporter reads the hash from the object name suffix unless the mesh
    # material carries the hash property, so keep both paths in agreement.
    source_rig_utils.enable_modding_toolkit()
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    rename_result = bpy.ops.re6_mod.rename_meshes()
    if "CANCELLED" in rename_result:
        raise RuntimeError(f"rename_meshes failed: {rename_result}")

    mrl = mrl_core.assemble(
        entries,
        unk=template.unk,
        version=template.version,
    )
    mrl_path = (
        args.mrl.resolve()
        if args.mrl
        else project_root / "stages" / "pl0600.mrl"
    )
    mrl_path.parent.mkdir(parents=True, exist_ok=True)
    mrl_path.write_bytes(mrl_core.serialize(mrl))

    # Import the finished library back into the .blend so the user can review
    # the entries and their linked materials before export.
    import_result = bpy.ops.re6_mrl.import_re6_mrl(filepath=str(mrl_path))
    if "CANCELLED" in import_result:
        raise RuntimeError(
            f"Could not import the finished .mrl: {mrl_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "mrl",
        "status": "passed",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "mrl": str(mrl_path),
        "mod_collection": collection.name,
        "texture_dir": texture_dir,
        "template": {
            "path": str(template_info["path"]),
            "version": f"{template.version:08x}",
            "unk": f"{template.unk:08x}",
            "material": f"{template_material.name:08x}",
            "albedo_slot": f"{albedo_slot:08x}",
            "normal_slot": (
                f"{normal_slot:08x}"
                if normal_slot is not None
                else None
            ),
            "null_normal_texture": null_normal_texture or None,
            "preserved_texture_slots": preserved_slots,
            "preserved_textures": {
                f"{slot:08x}": path
                for slot, path in preserved_bindings.items()
            },
        },
        "material_count": len(records),
        "texture_count": len(mrl.textures),
        "materials": records,
        "objects": [obj.name for obj in meshes],
    }
    write_json(report_path, report)

    manifest["settings"]["mrl_report"] = str(report_path)
    manifest["settings"]["mrl_path"] = str(mrl_path)
    for part_name in list(manifest.get("parts", {})):
        manifest["parts"][part_name]["status"] = "mrl_ready"
        manifest["parts"][part_name]["validation"] = "stage_06_mrl"
    manifest["stage_history"].append(
        {
            "stage": "mrl",
            "blend": str(output_path),
            "report": str(report_path),
            "mrl": str(mrl_path),
            "material_count": len(records),
        }
    )
    write_json(project_path, manifest)
    print(f"MRL_STAGE={output_path}")
    print(f"MRL_FILE={mrl_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
