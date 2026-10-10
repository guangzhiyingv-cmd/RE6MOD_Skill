"""Stage 7 for RE6: encode base color and normal textures as .tex files.

The .mrl written in Stage 6 points at ``<texture_dir>/<material>_BM``, so this
stage produces those files plus either ``<material>_NM`` or a shared
``Null_NM`` fallback. RE6 stores base color as BC1 (DXT1) and normal maps as
DXT5nm BC3. The game rejects textures above 2048 on either axis, so every
image is scaled down before it is encoded.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import re6_target_skeleton
import re6_material_textures


MOD_COLLECTION_TYPE = "RE6_MOD_COLLECTION"
DEFAULT_MAX_SIZE = 2048


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
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


def addon_root(skill_root: Path, profile: dict) -> Path:
    addon = (
        skill_root / profile["reference_assets"]["model_editor_addon"]
    ).resolve()
    if not addon.is_dir():
        raise FileNotFoundError(f"RE6 add-on copy not found: {addon}")
    if str(addon) not in sys.path:
        sys.path.insert(0, str(addon))
    return addon


def mod_collection() -> bpy.types.Collection:
    collections = [
        collection
        for collection in bpy.data.collections
        if collection.get("~TYPE") == MOD_COLLECTION_TYPE
    ]
    if len(collections) != 1:
        raise RuntimeError(
            f"Expected one RE6 mod collection, found {len(collections)}."
        )
    return collections[0]


def base_color_image(material: bpy.types.Material):
    """Image of the node that feeds Base Color, else the first image node."""
    if not material.use_nodes:
        return None
    tree = material.node_tree
    output = next(
        (
            node
            for node in tree.nodes
            if node.type == "BSDF_PRINCIPLED"
        ),
        None,
    )
    if output is not None:
        socket = output.inputs.get("Base Color")
        if socket is not None and socket.is_linked:
            node = socket.links[0].from_node
            if node.type == "TEX_IMAGE" and node.image is not None:
                return node.image
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
    return None


def fit_size(width: int, height: int, limit: int) -> tuple[int, int]:
    """Largest size within ``limit`` on both axes, keeping the aspect ratio."""
    longest = max(width, height)
    if longest <= limit:
        return width, height
    scale = limit / float(longest)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def reduce_image_brightness(image, percent: float) -> None:
    """Multiply RGB by (1 - percent / 100), leaving alpha unchanged."""
    if percent <= 0.0:
        return
    factor = max(0.0, 1.0 - percent / 100.0)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape(-1, 4)
    rgba[:, :3] *= factor
    image.pixels.foreach_set(pixels)
    image.update()


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
    texture_config = settings.get("texture") or {}

    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_07_textures.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_07_textures.json"
    )
    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    compression = str(texture_config.get("compression", "BC1")).upper()
    if compression != "BC1":
        raise RuntimeError(
            "RE6 base color textures must be encoded as BC1: "
            f"settings.texture.compression is {compression!r}"
        )
    max_size = int(texture_config.get("max_size", DEFAULT_MAX_SIZE))
    if max_size > DEFAULT_MAX_SIZE:
        raise RuntimeError(
            "RE6 textures may not exceed "
            f"{DEFAULT_MAX_SIZE}x{DEFAULT_MAX_SIZE}: settings.texture."
            f"max_size is {max_size}"
        )
    normal_max_size = int(
        texture_config.get("normal_max_size", max_size)
    )
    if normal_max_size > DEFAULT_MAX_SIZE:
        raise RuntimeError(
            "RE6 normal textures may not exceed "
            f"{DEFAULT_MAX_SIZE}x{DEFAULT_MAX_SIZE}: settings.texture."
            f"normal_max_size is {normal_max_size}"
        )
    brightness_reduction = float(
        texture_config.get("base_brightness_reduction_percent", 40.0)
    )
    if not 0.0 <= brightness_reduction <= 100.0:
        raise RuntimeError(
            "settings.texture.base_brightness_reduction_percent must be "
            "between 0 and 100."
        )
    null_normal_relative = texture_config.get("null_normal_dds")
    if not null_normal_relative:
        raise RuntimeError(
            "settings.texture.null_normal_dds is not configured."
        )
    null_normal_path = (skill_root / str(null_normal_relative)).resolve()
    if not null_normal_path.is_file():
        raise FileNotFoundError(
            f"Null normal DDS not found: {null_normal_path}"
        )
    null_normal_texture = str(
        mrl_config.get("null_normal_texture") or "Null_NM"
    ).replace("/", "\\")
    null_normal_name = null_normal_texture.rsplit("\\", 1)[-1]

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    # The add-on's Blender layer uses package relative imports, so import it
    # through the module name it is installed as instead of a bare path.
    addon_module = re6_target_skeleton.enable_re6_addon()
    tex_core = importlib.import_module(f"{addon_module}.core.tex")
    tex_tools = importlib.import_module(f"{addon_module}.blender.tex_tools")
    collection = mod_collection()

    texture_dir = str(
        mrl_config.get("texture_dir") or "data/chara/pl/pl0600/gzyv"
    ).replace("\\", "/").strip("/")
    mod_root = (
        args.mod_root.resolve()
        if args.mod_root
        else (
            Path(manifest["source"]["path"]).resolve().parent
            / (
                f"{manifest['mod']['name']}_"
                f"{profile.get('target_character_display') or profile['target_character']}"
            )
        )
    )
    out_dir = mod_root / "nativePC_mod" / texture_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    materials = []
    seen = set()
    for obj in collection.all_objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in seen:
                continue
            seen.add(material.name)
            materials.append(material)

    records = []
    base_texture_count = 0
    normal_texture_count = 0
    fallback_normal_users = []
    preprocessed_base_images = set()
    failures = []
    for material in materials:
        image = base_color_image(material)
        if image is None:
            failures.append(
                {
                    "material": material.name,
                    "reason": "no base color image is bound",
                }
            )
            continue
        source_size = list(image.size)
        target_size = fit_size(image.size[0], image.size[1], max_size)
        if target_size != tuple(image.size):
            image.scale(target_size[0], target_size[1])
        if image.name not in preprocessed_base_images:
            reduce_image_brightness(image, brightness_reduction)
            preprocessed_base_images.add(image.name)
        # encode the scaled pixels as BC1 and keep the default format byte for
        # a base color texture of that layout
        name = f"{material.name}_BM"
        tex_bytes = tex_tools.image_to_tex_bytes(
            image,
            "albedo",
            layout=tex_core.BC1,
            fmt=tex_core.default_fmt(name, tex_core.BC1),
        )
        target = out_dir / f"{name}.tex"
        target.write_bytes(tex_bytes)
        parsed = tex_core.parse(tex_bytes)
        records.append(
            {
                "kind": "base",
                "material": material.name,
                "image": image.filepath or image.name,
                "source_size": source_size,
                "size": [parsed.width, parsed.height],
                "mips": parsed.mips,
                "fmt": parsed.fmt,
                "layout": parsed.layout,
                "bytes": len(tex_bytes),
                "tex": str(target),
                "brightness_reduction_percent": brightness_reduction,
            }
        )
        base_texture_count += 1

        normal_image = re6_material_textures.find_normal_image(material)
        if normal_image is None:
            fallback_normal_users.append(material.name)
            continue
        normal_source_size = list(normal_image.size)
        normal_target_size = fit_size(
            normal_image.size[0],
            normal_image.size[1],
            normal_max_size,
        )
        if normal_target_size != tuple(normal_image.size):
            normal_image.scale(
                normal_target_size[0],
                normal_target_size[1],
            )
        normal_name = f"{material.name}_NM"
        normal_bytes = tex_tools.image_to_tex_bytes(
            normal_image,
            "normal",
            layout=tex_core.BC3,
            fmt=tex_core.default_fmt(normal_name, tex_core.BC3),
        )
        normal_target = out_dir / f"{normal_name}.tex"
        normal_target.write_bytes(normal_bytes)
        normal_parsed = tex_core.parse(normal_bytes)
        records.append(
            {
                "kind": "normal",
                "material": material.name,
                "image": normal_image.filepath or normal_image.name,
                "source_size": normal_source_size,
                "size": [normal_parsed.width, normal_parsed.height],
                "mips": normal_parsed.mips,
                "fmt": normal_parsed.fmt,
                "layout": normal_parsed.layout,
                "bytes": len(normal_bytes),
                "tex": str(normal_target),
            }
        )
        normal_texture_count += 1

    fallback_normal_count = 0
    if fallback_normal_users:
        null_bytes = tex_tools.image_file_to_tex(
            str(null_normal_path),
            compression="AUTO",
            normal_maps="KEEP",
        )
        null_target = out_dir / f"{null_normal_name}.tex"
        null_target.write_bytes(null_bytes)
        null_parsed = tex_core.parse(null_bytes)
        records.append(
            {
                "kind": "normal_fallback",
                "material": None,
                "image": str(null_normal_path),
                "source_size": [
                    null_parsed.width,
                    null_parsed.height,
                ],
                "size": [null_parsed.width, null_parsed.height],
                "mips": null_parsed.mips,
                "fmt": null_parsed.fmt,
                "layout": null_parsed.layout,
                "bytes": len(null_bytes),
                "tex": str(null_target),
                "used_by": fallback_normal_users,
            }
        )
        fallback_normal_count = 1

    if failures:
        report = {
            "stage": "textures",
            "status": "failed",
            "project": str(project_path),
            "input": str(input_path),
            "failures": failures,
            "textures": records,
        }
        write_json(report_path, report)
        raise RuntimeError(
            "Stage 7 texture conversion failed; see " f"{report_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "textures",
        "status": "passed",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "mod_root": str(mod_root),
        "texture_dir": str(out_dir),
        "compression": compression,
        "max_size": max_size,
        "base_brightness_reduction_percent": brightness_reduction,
        "texture_count": len(records),
        "base_texture_count": base_texture_count,
        "normal_texture_count": normal_texture_count,
        "fallback_normal_count": fallback_normal_count,
        "fallback_normal_materials": fallback_normal_users,
        "textures": records,
    }
    write_json(report_path, report)

    manifest["settings"]["texture_report"] = str(report_path)
    for part_name in list(manifest.get("parts", {})):
        manifest["parts"][part_name]["status"] = "textures_ready"
        manifest["parts"][part_name]["validation"] = "stage_07_textures"
    manifest["stage_history"].append(
        {
            "stage": "textures",
            "blend": str(output_path),
            "report": str(report_path),
            "texture_count": len(records),
            "compression": compression,
        }
    )
    write_json(project_path, manifest)
    print(f"TEXTURE_STAGE={output_path}")
    print(f"TEXTURE_DIR={out_dir}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
