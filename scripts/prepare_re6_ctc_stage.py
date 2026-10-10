"""Stage 8 for RE6: build physics chains through the RE6 Model Editor.

The chains reference bones by function id, which is why Stage 3 renamed every
physics bone to ``RE6Bone_<id>``. This stage rebuilds the non-branching bone
paths inside each semantic category, creates them as plugin CTC objects, and
exports the collection with the plugin exporter. Collisions (.ccl) are reused
from the reference mod later.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import re6_target_skeleton


BONE_PREFIX = "RE6Bone_"
MOD_COLLECTION_TYPE = "RE6_MOD_COLLECTION"
CTC_COLLECTION_TYPE = "RE6_CTC_COLLECTION"


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ctc", type=Path)
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


def armature_of(collection: bpy.types.Collection):
    armatures = [
        obj for obj in collection.all_objects if obj.type == "ARMATURE"
    ]
    if len(armatures) != 1:
        raise RuntimeError(
            f"Expected one armature in {collection.name}, found "
            f"{len(armatures)}."
        )
    return armatures[0]


def bone_id(name: str) -> int | None:
    if not name.startswith(BONE_PREFIX):
        return None
    tail = name[len(BONE_PREFIX):]
    return int(tail) if tail.isdigit() else None


def build_paths(armature, category_bones: set[str]) -> tuple[list, list]:
    """Ordered non-branching parent to child paths inside one category."""
    children: dict[str, list[str]] = {}
    roots = []
    for name in sorted(category_bones):
        bone = armature.data.bones.get(name)
        if bone is None:
            continue
        parent = bone.parent
        if parent is not None and parent.name in category_bones:
            children.setdefault(parent.name, []).append(name)
        else:
            roots.append(name)
    paths = []
    branch_stops = []
    for root in roots:
        path = [root]
        current = root
        while True:
            kids = sorted(children.get(current, []))
            if not kids:
                break
            if len(kids) > 1:
                branch_stops.append(
                    {"bone": current, "children": kids}
                )
                break
            current = kids[0]
            path.append(current)
        if len(path) >= 2:
            paths.append(path)
    return paths, branch_stops


def set_active_armature(armature) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature.hide_set(False)
    armature.hide_viewport = False
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature


def remove_existing_ctc_objects() -> None:
    """Remove old CTC plugin data so Stage 8 can rebuild it from scratch."""
    ctc_object_types = {
        "RE6_CTC_HEADER",
        "RE6_CTC_CHAIN",
        "RE6_CTC_NODE",
        "RE6_CTC_NODE_FRAME",
        "RE6_CTC_NODE_FRAME_HELPER",
        "RE6_CTC_HELPER",
    }
    for obj in list(bpy.data.objects):
        if obj.get("~TYPE") in ctc_object_types:
            bpy.data.objects.remove(obj, do_unlink=True)

    def remove_tree(collection) -> None:
        for child in list(collection.children):
            remove_tree(child)
        bpy.data.collections.remove(collection)

    for collection in list(bpy.data.collections):
        if collection.get("~TYPE") == CTC_COLLECTION_TYPE:
            remove_tree(collection)


def create_ctc_collection(name: str):
    result = bpy.ops.re6_ctc.create_ctc_collection(collectionName=name)
    if "CANCELLED" in result:
        raise RuntimeError(f"Could not create CTC collection: {result}")
    collection = bpy.context.scene.re6_ctc_toolpanel.ctcCollection
    if collection is None:
        raise RuntimeError("The plugin did not expose the new CTC collection.")
    return collection


def create_chain_with_plugin(
    armature,
    collection,
    header,
    entry_col,
    chain_index: int,
    path: list[str],
    chain_config: dict,
    node_config: dict,
    ctc_core,
    ctc_functions,
    ctc_operators,
) -> tuple[object, list[float]]:
    angle_start = float(
        node_config.get("angle_limit_start_degrees", 15.0)
    )
    angle_end = float(
        node_config.get("angle_limit_end_degrees", 60.0)
    )
    collision_radius = float(node_config.get("collision_radius", 2.5))
    collision_shape = int(node_config.get("collision_shape", 1))
    chain = ctc_core.Chain(
        colGroup=int(chain_config.get("col_group", 1)),
        damping=float(chain_config.get("damping", 0.1)),
        limitForce=float(chain_config.get("limit_force", 100.0)),
    )
    nodes = []
    degrees = []
    for index, bone_name in enumerate(path):
        ratio = (
            index / float(len(path) - 1) if len(path) > 1 else 0.0
        )
        value = angle_start + (angle_end - angle_start) * ratio
        degrees.append(round(value, 4))
        fid = bone_id(bone_name)
        if fid is None:
            raise RuntimeError(f"Bone is not RE6Bone_<id>: {bone_name}")
        nodes.append(
            ctc_core.Node(
                boneFunctionId=fid,
                collisionShape=collision_shape,
                boneColRadius=collision_radius,
                angleLimitRadius=math.radians(value),
            )
        )
    frames = [
        (
            ctc_operators.frame_rows_between(
                armature,
                armature.data.bones[bone_name],
                (
                    armature.data.bones[path[index + 1]]
                    if index + 1 < len(path)
                    else None
                ),
            ),
            None,
            0,
        )
        for index, bone_name in enumerate(path)
    ]
    chain_name = (
        f"CTC_CHAIN_{chain_index:02d} - {path[0]} > {path[-1]}"
    )
    chain_obj = ctc_functions.make_chain(
        bpy.context.scene.re6_ctc_toolpanel,
        header,
        entry_col,
        armature,
        chain_name,
        chain,
        nodes,
        path,
        frames,
    )
    return chain_obj, degrees


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    profile = load_json(profile_path)
    project_root = Path(manifest["paths"]["project_root"])
    settings = manifest.get("settings", {})
    ctc_config = settings.get("ctc") or {}

    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_08_ctc.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_08_ctc.json"
    )
    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    map_path = Path(
        settings.get("re6_physics_bone_map_path")
        or project_root / "reports" / "re6_physics_bone_map.json"
    ).resolve()
    if not map_path.is_file():
        raise FileNotFoundError(
            f"Physics bone map not found: {map_path}. Run Stage 3 first."
        )
    bone_map = load_json(map_path)
    by_category = bone_map.get("by_category") or {}
    if not by_category:
        raise RuntimeError(
            f"No physics chain categories in {map_path}."
        )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    addon_module = re6_target_skeleton.enable_re6_addon()
    ctc_core = importlib.import_module(f"{addon_module}.core.ctc")
    ctc_functions = importlib.import_module(
        f"{addon_module}.blender.ctc_functions"
    )
    ctc_operators = importlib.import_module(
        f"{addon_module}.blender.ctc_operators"
    )
    collection = next(
        (
            item
            for item in bpy.data.collections
            if item.get("~TYPE") == MOD_COLLECTION_TYPE
        ),
        None,
    )
    if collection is None:
        raise RuntimeError("The RE6 mod collection was not found.")
    armature = armature_of(collection)

    node_config = ctc_config.get("node") or {}
    chain_config = ctc_config.get("chain") or {}
    angle_start = float(node_config.get("angle_limit_start_degrees", 15.0))
    angle_end = float(node_config.get("angle_limit_end_degrees", 60.0))
    collision_radius = float(node_config.get("collision_radius", 2.5))
    collision_shape = int(node_config.get("collision_shape", 1))
    remove_existing_ctc_objects()
    chain_source = "created_with_plugin_operators"
    chains_report = []
    skipped = []
    set_active_armature(armature)
    collection = create_ctc_collection("pl0600")
    header = ctc_functions.find_header(collection)
    if header is None:
        header = ctc_functions.create_header(
            collection,
            "CTC_HEADER pl0600.ctc",
        )
    entry_col = ctc_functions.entries_collection(
        "Chain Entries",
        collection,
        make_new=True,
    )
    chain_index = 0
    for category, names in sorted(by_category.items()):
        category_bones = {
            name for name in names if name.startswith(BONE_PREFIX)
        }
        paths, branch_stops = build_paths(armature, category_bones)
        for stop in branch_stops:
            skipped.append({"category": category, **stop})
        for path in paths:
            ids = [bone_id(name) for name in path]
            if any(value is None for value in ids):
                skipped.append(
                    {
                        "category": category,
                        "path": path,
                        "reason": "bone name is not RE6Bone_<id>",
                    }
                )
                continue
            chain_obj, degrees = create_chain_with_plugin(
                armature,
                collection,
                header,
                entry_col,
                chain_index,
                path,
                chain_config,
                node_config,
                ctc_core,
                ctc_functions,
                ctc_operators,
            )
            chain_index += 1
            chains_report.append(
                {
                    "category": category,
                    "object": chain_obj.name,
                    "nodes": len(ids),
                    "ids": ids,
                    "bones": path,
                    "angle_limits_degrees": degrees,
                }
            )
    ctc_functions.align_chains(collection)
    ctc_functions.set_chain_bone_color(armature, collection)

    if not chains_report:
        raise RuntimeError("No physics chain is available in the CTC collection.")

    ctc_path = (
        args.ctc.resolve()
        if args.ctc
        else mod_root(manifest, profile, args.mod_root)
        / "nativePC_mod/data/chara/pl/pl0600/model/pl0600.ctc"
    )
    ctc_path.parent.mkdir(parents=True, exist_ok=True)
    panel = bpy.context.scene.re6_ctc_toolpanel
    panel.exportCTCCollection = collection
    export_result = bpy.ops.re6_ctc.export_re6_ctc(
        filepath=str(ctc_path),
        exportCCL=False,
    )
    if "CANCELLED" in export_result or not ctc_path.is_file():
        raise RuntimeError(
            f"Plugin .ctc export failed: {export_result} -> {ctc_path}"
        )
    ctc = ctc_core.parse(ctc_path.read_bytes())
    # Keep a canonical copy inside the project: Stage 9 rebuilds the Mod root
    # from the reference mod and replaces the chains with this file.
    stage_copy = project_root / "stages" / "pl0600.ctc"
    stage_copy.write_bytes(ctc_path.read_bytes())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    category_counts = {}
    for entry in chains_report:
        category_counts[entry["category"]] = (
            category_counts.get(entry["category"], 0) + 1
        )
    report = {
        "stage": "chains",
        "status": "passed",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "ctc": str(ctc_path),
        "ctc_collection": collection.name,
        "chain_source": chain_source,
        "bone_map": str(map_path),
        "chain_count": len(ctc.chains),
        "node_count": ctc.node_count,
        "chains_per_category": category_counts,
        "chains": chains_report,
        "skipped": skipped,
        "export": {
            "path": str(ctc_path),
            "result": sorted(export_result),
        },
        "node_settings": {
            "angle_limit_start_degrees": angle_start,
            "angle_limit_end_degrees": angle_end,
            "collision_radius": collision_radius,
            "collision_shape": collision_shape,
        },
    }
    write_json(report_path, report)

    manifest["settings"]["ctc_report"] = str(report_path)
    manifest["settings"]["ctc_path"] = str(stage_copy)
    for part_name in list(manifest.get("parts", {})):
        manifest["parts"][part_name]["status"] = "chains_ready"
        manifest["parts"][part_name]["validation"] = "stage_08_ctc"
    manifest["stage_history"].append(
        {
            "stage": "chains",
            "blend": str(output_path),
            "report": str(report_path),
            "ctc": str(ctc_path),
            "chain_count": len(ctc.chains),
        }
    )
    write_json(project_path, manifest)
    print(f"CTC_STAGE={output_path}")
    print(f"CTC_FILE={ctc_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
