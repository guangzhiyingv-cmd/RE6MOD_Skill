"""Create RE mesh collections, graft physics bones, and rebind source meshes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import re6_physics_bones
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


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(
    obj: bpy.types.Object,
    collection: bpy.types.Collection,
) -> None:
    for source_collection in list(obj.users_collection):
        source_collection.objects.unlink(obj)
    collection.objects.link(obj)


def create_mesh_collection(collection_name: str) -> bpy.types.Collection:
    expected_name = f"{collection_name}.mesh"
    existing = bpy.data.collections.get(expected_name)
    if existing is not None:
        return existing
    result = bpy.ops.re_mesh.create_mesh_collection(
        collectionName=collection_name,
        lodCount=1,
    )
    if "CANCELLED" in result:
        raise RuntimeError(f"Could not create mesh collection: {expected_name}")
    collection = bpy.data.collections.get(expected_name)
    if collection is None:
        raise RuntimeError(f"Mesh collection was not created: {expected_name}")
    return collection


def bone_names(armature: bpy.types.Object) -> set[str]:
    return {bone.name for bone in armature.data.bones}


def root_tree(armature: bpy.types.Object, root_name: str) -> set[str]:
    root = armature.data.bones.get(root_name)
    if root is None:
        return set()
    return {root_name} | {
        bone.name for bone in root.children_recursive
    }


def vertex_group_usage(
    mesh_object: bpy.types.Object,
    valid_bones: set[str],
    tree: set[str],
) -> dict:
    group_names = {
        group.index: group.name
        for group in mesh_object.vertex_groups
    }
    weighted_counts = Counter()
    zero_weight_vertices = 0

    for vertex in mesh_object.data.vertices:
        positive = [
            assignment
            for assignment in vertex.groups
            if assignment.weight > 0.0
        ]
        if not positive:
            zero_weight_vertices += 1
            continue
        for assignment in positive:
            name = group_names.get(assignment.group)
            if name:
                weighted_counts[name] += 1

    weighted_groups = sorted(weighted_counts)
    helper_groups = sorted(
        name for name in weighted_counts if name.startswith("UV_")
    )
    missing_bones = sorted(
        name
        for name in weighted_groups
        if name not in valid_bones and not name.startswith("UV_")
    )
    return {
        "mesh": mesh_object.name,
        "vertices": len(mesh_object.data.vertices),
        "zero_weight_vertices": zero_weight_vertices,
        "weighted_groups": len(weighted_groups),
        "missing_bones": missing_bones,
        "helper_groups": helper_groups,
        "detached_root_bones": [
            name
            for name in weighted_groups
            if name in valid_bones and name not in tree
        ],
    }


def rebind_mesh(
    mesh_object: bpy.types.Object,
    target_armature: bpy.types.Object,
) -> None:
    world_matrix = mesh_object.matrix_world.copy()
    modifiers = [
        modifier
        for modifier in mesh_object.modifiers
        if modifier.type == "ARMATURE"
    ]
    if modifiers:
        for modifier in modifiers:
            modifier.object = target_armature
    else:
        modifier = mesh_object.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = target_armature
    mesh_object.parent = target_armature
    mesh_object.parent_type = "OBJECT"
    mesh_object.matrix_parent_inverse = target_armature.matrix_world.inverted()
    mesh_object.matrix_world = world_matrix


def remove_mmd_metadata_groups(
    mesh_objects: list[bpy.types.Object],
) -> dict[str, list[str]]:
    removed = {}
    for mesh_object in mesh_objects:
        removed_names = []
        for group_name in ("mmd_edge_scale", "mmd_vertex_order"):
            group = mesh_object.vertex_groups.get(group_name)
            if group is not None:
                mesh_object.vertex_groups.remove(group)
                removed_names.append(group_name)
        if removed_names:
            removed[mesh_object.name] = removed_names
    return removed


def find_source_armature() -> bpy.types.Object:
    source_collection = bpy.data.collections.get("_SOURCE_HIDDEN")
    if source_collection is not None:
        armatures = [
            obj for obj in source_collection.objects if obj.type == "ARMATURE"
        ]
        if len(armatures) == 1:
            return armatures[0]
    raise RuntimeError("Could not find the source armature.")


def find_target_armature() -> bpy.types.Object:
    armatures = [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "ARMATURE"
        and obj.users_collection
        and all(
            collection.name not in {"_SOURCE_HIDDEN"}
            for collection in obj.users_collection
        )
    ]
    if len(armatures) != 1:
        raise RuntimeError(
            f"Expected one visible target armature, found {len(armatures)}."
        )
    return armatures[0]


def reparent_detached_root_bones(
    armature: bpy.types.Object,
    mapping: dict[str, str],
) -> dict[str, dict]:
    if not mapping:
        return {}
    set_active(armature)
    bpy.ops.object.mode_set(mode="EDIT")
    applied = {}
    edit_bones = armature.data.edit_bones
    for child_name, parent_name in mapping.items():
        child = edit_bones.get(child_name)
        parent = edit_bones.get(parent_name)
        if child is None or parent is None:
            bpy.ops.object.mode_set(mode="OBJECT")
            raise RuntimeError(
                f"Detached-bone mapping cannot resolve {child_name!r} -> "
                f"{parent_name!r}."
            )
        if child.parent is not None:
            continue
        head = child.head.copy()
        tail = child.tail.copy()
        roll = child.roll
        child.parent = parent
        child.use_connect = False
        child.head = head
        child.tail = tail
        child.roll = roll
        applied[child_name] = {
            "parent": parent_name,
            "head": list(head),
            "tail": list(tail),
            "roll": roll,
        }
    bpy.ops.object.mode_set(mode="OBJECT")
    return applied


DEFAULT_PART_OBJECT_NAMES = {
    "body": "00_Body",
    "face": "10_Face",
    "hair": "20_Hair",
}


def is_re6_profile(profile: dict) -> bool:
    return (profile.get("target_skeleton") or {}).get("source") == "re6_mod"


def split_object_names(profile: dict, part_names) -> dict:
    """Split-stage mesh name for every prepared part."""
    declared = profile.get("part_output_names") or {}
    return {
        part_name: declared.get(
            part_name,
            DEFAULT_PART_OBJECT_NAMES.get(part_name, f"{part_name}_mesh"),
        )
        for part_name in part_names
    }


def create_re6_mod_collection(profile: dict, skill_root: Path):
    """Import the RE6 template and keep only its mod collection.

    The collection carries the header, remap and group data the exporter
    needs, so the mod inherits them instead of rebuilding them by hand.
    Returns the collection and the template's own base bone names.
    """
    model_path = (skill_root / profile["target_skeleton"]["model"]).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f"RE6 template model not found: {model_path}")
    re6_target_skeleton.enable_re6_addon()
    before_objects = set(bpy.data.objects.keys())
    before_collections = set(bpy.data.collections.keys())
    result = bpy.ops.re6_mod.import_re6_mod(
        filepath=str(model_path),
        importArmatureOnly=False,
        importShadow=True,
        loadMrlData=False,
        loadMaterials=False,
        loadPhysics=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not import the RE6 template: {model_path}")

    imported_objects = [
        bpy.data.objects[name]
        for name in bpy.data.objects.keys()
        if name not in before_objects
    ]
    mod_collections = [
        collection
        for collection in bpy.data.collections
        if collection.get("~TYPE") == "RE6_MOD_COLLECTION"
        and collection.name not in before_collections
    ]
    if len(mod_collections) != 1:
        raise RuntimeError(
            "Expected one RE6 mod collection in the template, found "
            f"{len(mod_collections)}."
        )
    mod_collection = mod_collections[0]
    base_bones = {
        bone.name
        for obj in imported_objects
        if obj.type == "ARMATURE"
        for bone in obj.data.bones
    }

    # The template only supplies collection properties: drop its meshes,
    # armature and helper collections.
    for obj in imported_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    keep = {mod_collection}
    changed = True
    while changed:
        changed = False
        for collection in bpy.data.collections:
            if collection in keep:
                continue
            if any(child in keep for child in collection.children):
                keep.add(collection)
                changed = True
    for name in set(bpy.data.collections.keys()) - before_collections:
        collection = bpy.data.collections.get(name)
        if collection is not None and collection not in keep:
            bpy.data.collections.remove(collection)
    return mod_collection, base_bones


def re6_skeleton_check(armature, base_bones: set[str]) -> dict:
    """Final RE6 skeleton check; replaces the former dedicated Stage 4."""
    pattern = re.compile(r"^RE6Bone_(\d{3})$")
    present = {bone.name for bone in armature.data.bones}
    seen_ids = {}
    invalid_names = []
    duplicate_ids = []
    for bone in armature.data.bones:
        match = pattern.match(bone.name)
        if match is None:
            invalid_names.append(bone.name)
            continue
        bone_id = int(match.group(1))
        if bone_id in seen_ids:
            duplicate_ids.append(bone.name)
        seen_ids[bone_id] = bone.name
    over_range = sorted(
        name for bone_id, name in seen_ids.items() if bone_id > 254
    )
    missing_base = sorted(base_bones - present)
    return {
        "skel": armature.name,
        "bone_count": len(armature.data.bones),
        "invalid_names": sorted(invalid_names),
        "invalid_name_count": len(invalid_names),
        "duplicate_ids": duplicate_ids,
        "over_range_ids": over_range,
        "missing_base_bones": missing_base,
        "ok": not (
            invalid_names
            or duplicate_ids
            or over_range
            or missing_base
        ),
    }


def load_chain_simplification_plan(
    project_root: Path,
    base_bones: set[str],
) -> dict:
    """Project simplification plan, else a default covering all physics bones."""
    path = project_root / "reports" / "ai_chain_simplification_plan.json"
    if path.is_file():
        plan = load_json(path)
        plan.setdefault("source", str(path))
        return plan
    return re6_physics_bones.default_simplification_plan(base_bones)


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
        else project_root / "stages" / "stage_03_rig.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_03_rig.json"
    )

    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    enable_addon(("Modding-Toolkit", "Modding-Toolkit-dev"))
    skill_root = profile_path.parents[1]
    re6_profile = is_re6_profile(profile)
    if not re6_profile:
        enable_addon(("RE-Mesh-Editor-main",))

    source_armature = find_source_armature()
    aligned_target = find_target_armature()

    no_merge = bool(
        manifest.get("settings", {}).get("no_mesh_merge", False)
    )
    prepared_meshes = profile["prepared_meshes"]
    prepared_collections = profile["prepared_collections"]
    prepared_armatures = profile["prepared_armatures"]
    part_names = list(manifest["parts"])
    split_names = split_object_names(profile, part_names)
    mesh_objects = {}
    for part_name in part_names:
        object_name = split_names[part_name]
        source_collection_name = (
            manifest.get("parts", {})
            .get(part_name, {})
            .get("collection")
        )
        source_collection = (
            bpy.data.collections.get(source_collection_name)
            if source_collection_name
            else None
        )
        if (
            source_collection is not None
            and source_collection_name != "SOURCE_PARTS"
        ):
            objects = sorted(
                (
                    obj
                    for obj in source_collection.all_objects
                    if obj.type == "MESH"
                ),
                key=lambda item: item.name,
            )
        else:
            objects = []
            candidate = bpy.data.objects.get(object_name)
            if candidate is not None and candidate.type == "MESH":
                objects = [candidate]
        if not objects:
            raise RuntimeError(f"Split mesh not found for part: {part_name}")
        if no_merge:
            mesh_objects[part_name] = objects
        else:
            single = objects[0]
            single.name = prepared_meshes[part_name]
            single.data.name = f"{prepared_meshes[part_name]}_mesh"
            mesh_objects[part_name] = [single]

    all_meshes = [
        obj
        for objects in mesh_objects.values()
        for obj in objects
    ]

    re6_state = None
    if re6_profile:
        mod_collection, base_bones = create_re6_mod_collection(
            profile, skill_root
        )
        collections = {
            part_name: mod_collection for part_name in part_names
        }
        re6_state = {"base_bones": base_bones}
    else:
        collections = {
            part_name: create_mesh_collection(
                prepared_collections[part_name].removesuffix(".mesh")
            )
            for part_name in part_names
        }

    reference_collection = ensure_collection("_REFERENCE")
    hidden_source = ensure_collection("_SOURCE_HIDDEN")
    reference_name = f"_REF_{aligned_target.name}"
    aligned_target.name = reference_name
    aligned_target.data.name = reference_name
    move_to_collection(aligned_target, reference_collection)
    reference_collection.hide_viewport = False
    reference_collection.hide_render = False
    aligned_target.hide_set(False)
    aligned_target.hide_viewport = False
    aligned_target.hide_render = False

    body_armature = aligned_target.copy()
    body_armature.data = aligned_target.data.copy()
    body_armature.name = prepared_armatures["body"]
    body_armature.data.name = prepared_armatures["body"]
    move_to_collection(body_armature, collections["body"])

    for part_name, objects in mesh_objects.items():
        for obj in objects:
            move_to_collection(obj, collections[part_name])

    source_parent = source_armature.parent
    move_to_collection(source_armature, hidden_source)
    if source_parent is not None:
        move_to_collection(source_parent, hidden_source)
    source_backup = bpy.data.objects.get("_SOURCE_mesh")
    if source_backup is not None:
        move_to_collection(source_backup, hidden_source)

    settings = bpy.context.scene.mhw_suite_settings
    source_preset = source_rig_utils.effective_source_preset(manifest)
    settings.import_preset_enum = source_preset
    settings.target_preset_enum = profile.get(
        "alignment_preset",
        "re6.json",
    )
    settings.align_mode_override = "POS_ONLY"
    # Read the configured value back once: helpers later in the stage
    # re-register add-ons, which can invalidate this settings pointer.
    source_preset_value = settings.import_preset_enum

    before_bones = bone_names(body_armature)
    set_active(body_armature)
    for collection in source_armature.users_collection:
        collection.hide_viewport = False
    source_armature.hide_set(False)
    source_armature.hide_viewport = False
    source_armature.select_set(True)
    result = bpy.ops.modder.smart_graft()
    if "CANCELLED" in result:
        raise RuntimeError("Graft Physics Bones was cancelled.")
    after_bones = bone_names(body_armature)
    grafted_bones = sorted(after_bones - before_bones)

    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    for obj in all_meshes:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects["body"][0]
    groups_before = {
        obj.name: {group.name for group in obj.vertex_groups}
        for obj in all_meshes
    }
    result = bpy.ops.modder.direct_convert()
    if "CANCELLED" in result:
        raise RuntimeError("Vertex group conversion was cancelled.")
    groups_after = {
        obj.name: {group.name for group in obj.vertex_groups}
        for obj in all_meshes
    }

    removed_metadata = (
        remove_mmd_metadata_groups(all_meshes)
        if manifest.get("source", {}).get("adapter") == "mmd"
        else {}
    )
    for obj in all_meshes:
        rebind_mesh(obj, body_armature)

    detached_reparenting = reparent_detached_root_bones(
        body_armature,
        manifest.get(
            "detached_physics_parents",
            profile.get("detached_physics_parents", {}),
        ),
    )

    # RE6 physics bones must move into the physics id ranges before the mod is
    # exported, so the chain preprocessing runs here rather than in Stage 8.
    re6_physics_report = None
    physics_bone_map = None
    if re6_state is not None:
        base_bones = re6_state["base_bones"]
        plan = load_chain_simplification_plan(project_root, base_bones)
        chain_config = profile.get("physics_chains") or {}
        re6_physics_report = {
            "cleanup": re6_physics_bones.cleanup_physics_bones(
                body_armature,
                all_meshes,
                base_bones,
            ),
            "unchained": re6_physics_bones.merge_unchained_physics_bones(
                body_armature,
                all_meshes,
                base_bones,
                chain_config,
                plan,
            ),
            "simplification": re6_physics_bones.simplify_physics_chains(
                body_armature,
                base_bones,
                plan,
                chain_config,
            ),
            "ids": re6_physics_bones.assign_physics_ids(
                body_armature,
                base_bones,
                all_meshes,
            ),
        }
        renamed = re6_physics_report["ids"]["renamed"]
        kept_by_category = re6_physics_report["unchained"]["kept"][
            "kept_by_category"
        ]
        physics_bone_map = {
            "stage": "stage_03_rig",
            "renamed": renamed,
            "ids": re6_physics_report["ids"]["assigned"],
            "by_category": {
                category: sorted(
                    renamed[name]
                    for name in bones
                    if name in renamed
                )
                for category, bones in kept_by_category.items()
            },
        }
        re6_physics_report["bone_map"] = physics_bone_map
        skeleton_check = re6_skeleton_check(body_armature, base_bones)
        re6_physics_report["skeleton_check"] = skeleton_check
        if skeleton_check["invalid_names"]:
            raise RuntimeError(
                "RE6 skeleton has bones that are not named RE6Bone_<id>: "
                + ", ".join(skeleton_check["invalid_names"][:10])
            )
        # The working body skeleton is the mod's `skel`: keep it visible for
        # manual review and export.
        body_armature.hide_set(False)
        body_armature.hide_viewport = False
        body_armature.hide_render = False

    source_armature.hide_set(True)
    source_armature.hide_render = True
    if source_backup is not None:
        source_backup.hide_set(True)
        source_backup.hide_render = True
    hidden_source.hide_viewport = True
    hidden_source.hide_render = True
    reference_collection.hide_viewport = False
    reference_collection.hide_render = False

    valid_bones = bone_names(body_armature)
    roots = [
        bone.name
        for bone in body_armature.data.bones
        if bone.parent is None
    ]
    if "root" in valid_bones:
        primary_root = "root"
    elif len(roots) == 1:
        primary_root = roots[0]
    else:
        raise RuntimeError(f"Cannot select primary root bone. Roots: {roots}")
    tree = root_tree(body_armature, primary_root)
    checks = [
        vertex_group_usage(obj, valid_bones, tree)
        for obj in all_meshes
    ]
    failures = [
        {
            "mesh": check["mesh"],
            "zero_weight_vertices": check["zero_weight_vertices"],
            "missing_bones": check["missing_bones"],
            "detached_root_bones": check["detached_root_bones"],
        }
        for check in checks
        if check["zero_weight_vertices"]
        or check["missing_bones"]
        or check["detached_root_bones"]
    ]
    if failures:
        detached_parents = {}
        for check in checks:
            for bone_name in check["detached_root_bones"]:
                bone = body_armature.data.bones.get(bone_name)
                detached_parents[bone_name] = (
                    bone.parent.name if bone is not None and bone.parent else None
                )
        failure_report = {
            "stage": "rig",
            "status": "failed",
            "project": str(project_path),
            "input": str(input_path),
            "armature_roots": roots,
            "detached_bone_parents": detached_parents,
            "checks": checks,
            "failures": failures,
        }
        write_json(report_path, failure_report)
        raise RuntimeError(
            "Stage 3 validation failed. See report: " f"{report_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "rig",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "collections": {
            name: collection.name
            for name, collection in collections.items()
        },
        "target_armature": body_armature.name,
        "source_armature": source_armature.name,
        "source_preset": source_preset_value,
        "grafted_bone_count": len(grafted_bones),
        "grafted_bones": grafted_bones,
        "re6_physics": re6_physics_report,
        "detached_reparenting": detached_reparenting,
        "renamed_vertex_groups": {
            name: {
                "added": sorted(groups_after[name] - groups_before[name]),
                "removed": sorted(groups_before[name] - groups_after[name]),
            }
            for name in groups_after
        },
        "removed_metadata_groups": removed_metadata,
        "primary_root": primary_root,
        "checks": checks,
    }
    write_json(report_path, report)

    if physics_bone_map is not None:
        write_json(
            project_root / "reports" / "re6_physics_bone_map.json",
            physics_bone_map,
        )

    for part_name in part_names:
        manifest["parts"][part_name]["status"] = "rigged"
        manifest["parts"][part_name]["collection"] = collections[
            part_name
        ].name
        manifest["parts"][part_name]["validation"] = "stage_03_rig"
    if physics_bone_map is not None:
        manifest["physics_bone_map"] = {
            "stage": "stage_03_rig",
            "path": str(
                project_root / "reports" / "re6_physics_bone_map.json"
            ),
            "bone_count": len(physics_bone_map["renamed"]),
            "categories": sorted(physics_bone_map["by_category"]),
        }
    manifest["stage_history"].append(
        {
            "stage": "rig",
            "blend": str(output_path),
            "report": str(report_path),
            "target_armature": body_armature.name,
            "grafted_bone_count": len(grafted_bones),
        }
    )
    write_json(project_path, manifest)
    print(f"RIG_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
