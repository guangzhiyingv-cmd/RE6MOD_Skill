"""Merge prepared-mesh materials, separate when needed, and clean groups."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    import sys

    argv = sys.argv
    separator = argv.index("--") + 1 if "--" in argv else len(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--merge-textures", action="store_true")
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


def collection_meshes(
    collection_names: list[str],
) -> list[bpy.types.Object]:
    result = {}
    for collection_name in collection_names:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise RuntimeError(f"Prepared collection not found: {collection_name}")
        for obj in collection.all_objects:
            if obj.type == "MESH":
                result[obj.name] = obj
    return [result[name] for name in sorted(result)]


def select_all(objects: list[bpy.types.Object]) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]


def object_hidden(obj: bpy.types.Object) -> bool:
    try:
        return obj.hide_get()
    except RuntimeError:
        return obj.hide_viewport


def set_object_hidden(obj: bpy.types.Object, hidden: bool) -> None:
    obj.hide_viewport = hidden
    try:
        obj.hide_set(hidden)
    except RuntimeError:
        pass


def visibility_state() -> dict[str, tuple[bool, bool]]:
    return {
        obj.name: (object_hidden(obj), obj.hide_viewport)
        for obj in bpy.data.objects
    }


def restore_visibility(state: dict[str, tuple[bool, bool]]) -> None:
    for name, (hidden, hide_viewport) in state.items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        set_object_hidden(obj, hidden)
        obj.hide_viewport = hide_viewport


def show_only(objects: list[bpy.types.Object]) -> None:
    keep = {obj.name for obj in objects}
    for obj in bpy.data.objects:
        if obj.name in keep:
            set_object_hidden(obj, False)
        else:
            set_object_hidden(obj, True)
    bpy.context.view_layer.update()


def material_names(obj: bpy.types.Object) -> list[str]:
    return [
        slot.material.name
        for slot in obj.material_slots
        if slot.material is not None
    ]


def linked_image_node(
    socket: bpy.types.NodeSocket,
    seen: set[tuple[str, str]] | None = None,
) -> bpy.types.Node | None:
    seen = seen or set()
    for link in socket.links:
        source = link.from_node
        key = (source.id_data.name, source.name)
        if key in seen:
            continue
        seen.add(key)
        if source.type == "TEX_IMAGE" and source.image is not None:
            return source

        for source_socket in source.inputs:
            if not source_socket.links:
                continue
            image_node = linked_image_node(
                source_socket,
                seen,
            )
            if image_node is not None:
                return image_node
    return None


def base_image_for_material(
    material: bpy.types.Material,
) -> tuple[str, str] | None:
    if not material.use_nodes or material.node_tree is None:
        return None

    base_node = (
        material.node_tree.nodes.get("mmd_base_tex")
        or next(
            (
                node
                for node in material.node_tree.nodes
                if node.type == "TEX_IMAGE"
                and node.label == "Mmd Base Tex"
            ),
            None,
        )
    )
    if base_node is None:
        principled = next(
            (
                node
                for node in material.node_tree.nodes
                if node.type == "BSDF_PRINCIPLED"
            ),
            None,
        )
        if principled is not None:
            base_input = principled.inputs.get("Base Color")
            if base_input is not None:
                base_node = linked_image_node(
                    base_input,
                )
    if base_node is None or base_node.type != "TEX_IMAGE":
        return None
    if base_node.image is None:
        return None

    filepath = bpy.path.abspath(base_node.image.filepath)
    if not filepath:
        return None
    resolved = str(Path(filepath).resolve())
    return base_node.image.name, resolved


def material_base_groups(
    meshes: list[bpy.types.Object],
) -> tuple[dict[str, dict], list[dict]]:
    groups = {}
    unresolved = []
    for obj in meshes:
        for slot_index, slot in enumerate(obj.material_slots):
            material = slot.material
            if material is None:
                unresolved.append(
                    {
                        "mesh": obj.name,
                        "slot": slot_index,
                        "material": None,
                        "reason": "empty material slot",
                    }
                )
                continue
            base_image = base_image_for_material(material)
            if base_image is None:
                unresolved.append(
                    {
                        "mesh": obj.name,
                        "slot": slot_index,
                        "material": material.name,
                        "reason": "Base Color image could not be resolved",
                    }
                )
                continue
            image_name, image_path = base_image
            key = (
                obj.name,
                os.path.normcase(image_path),
            )
            group = groups.setdefault(
                key,
                {
                    "mesh": obj.name,
                    "image_name": image_name,
                    "image_path": image_path,
                    "materials": set(),
                    "face_counts": Counter(),
                },
            )
            group["materials"].add(material.name)
            group["face_counts"][material.name] += sum(
                1
                for polygon in obj.data.polygons
                if polygon.material_index == slot_index
            )
    return groups, unresolved


def choose_canonical_materials(
    groups: dict[str, dict],
) -> dict[str, bpy.types.Material]:
    canonical = {}
    for key, group in sorted(groups.items()):
        names = sorted(group["materials"])
        selected_name = max(
            names,
            key=lambda name: (
                group["face_counts"][name],
                -names.index(name),
            ),
        )
        material = bpy.data.materials.get(selected_name)
        if material is None:
            raise RuntimeError(
                f"Canonical material disappeared before merge: {selected_name}"
            )
        canonical[key] = material
    return canonical


def merge_materials_by_base_image(
    meshes: list[bpy.types.Object],
) -> tuple[dict, list[dict]]:
    groups, unresolved = material_base_groups(meshes)
    canonical = choose_canonical_materials(groups)
    merges = []
    mesh_reports = []

    for obj in meshes:
        original_materials = [
            slot.material
            for slot in obj.material_slots
        ]
        polygon_indices = [
            polygon.material_index
            for polygon in obj.data.polygons
        ]
        new_materials = []
        new_index_by_slot = {}
        old_index_to_new = {}
        for old_index, material in enumerate(original_materials):
            if material is None:
                raise RuntimeError(
                    f"Empty material slot on {obj.name}: {old_index}"
                )
            base_image = base_image_for_material(material)
            if base_image is None:
                target_material = material
                base_path = None
            else:
                _, base_path = base_image
                target_material = canonical[
                    (
                        obj.name,
                        os.path.normcase(base_path),
                    )
                ]

            target_index = new_index_by_slot.get(target_material.name)
            if target_index is None:
                target_index = len(new_materials)
                new_index_by_slot[target_material.name] = target_index
                new_materials.append(target_material)
            old_index_to_new[old_index] = target_index
            if target_material.name != material.name:
                merges.append(
                    {
                        "mesh": obj.name,
                        "base_image": base_path,
                        "source_material": material.name,
                        "canonical_material": target_material.name,
                    }
                )

        if len(new_materials) == len(original_materials):
            continue
        remapped_polygon_indices = [
            old_index_to_new[index]
            for index in polygon_indices
        ]
        obj.data.materials.clear()
        for material in new_materials:
            obj.data.materials.append(material)
        for polygon, material_index in zip(
            obj.data.polygons,
            remapped_polygon_indices,
        ):
            polygon.material_index = material_index
        mesh_reports.append(
            {
                "mesh": obj.name,
                "slots_before": len(original_materials),
                "slots_after": len(new_materials),
                "materials_before": [
                    material.name
                    for material in original_materials
                    if material is not None
                ],
                "materials_after": [
                    material.name
                    for material in new_materials
                ],
            }
        )

    group_reports = []
    for key, group in sorted(groups.items()):
        canonical_material = canonical[key]
        materials = sorted(group["materials"])
        group_reports.append(
            {
                "image_name": group["image_name"],
                "image_path": group["image_path"],
                "mesh": group["mesh"],
                "materials": materials,
                "canonical_material": canonical_material.name,
                "merged": len(materials) > 1,
            }
        )

    result = {
        "groups": group_reports,
        "merges": merges,
        "mesh_updates": mesh_reports,
        "unresolved": unresolved,
    }
    return result, unresolved


def texture_image_for_material(
    material: bpy.types.Material,
) -> bpy.types.Image | None:
    if not material.use_nodes or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            image = node.image
            if image.size[0] > 0 and image.size[1] > 0:
                return image
    return None


def material_uv_extents(
    meshes: list[bpy.types.Object],
) -> dict[str, tuple[float, float]]:
    extents: dict[str, tuple[float, float]] = {}
    for obj in meshes:
        max_x, max_y = 1.0, 1.0
        for uv_layer in obj.data.uv_layers:
            for loop in uv_layer.data:
                max_x = max(max_x, abs(loop.uv[0]))
                max_y = max(max_y, abs(loop.uv[1]))
        max_x = min(max_x, 25.0)
        max_y = min(max_y, 25.0)
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            current = extents.get(slot.material.name, (1.0, 1.0))
            extents[slot.material.name] = (
                max(current[0], max_x),
                max(current[1], max_y),
            )
    return extents


def material_tile_size(
    cap: int,
    extent: tuple[float, float],
) -> tuple[int, int]:
    return (
        math.ceil(cap * max(1.0, extent[0])),
        math.ceil(cap * max(1.0, extent[1])),
    )


def cap_material_texture_sizes(
    scene: bpy.types.Scene,
    meshes: list[bpy.types.Object],
    max_side: int,
) -> dict:
    """Cap every prepared material tile so the atlas pack fits ``max_side``.

    Material Combiner sizes each atlas tile from the material's source image
    and its UV extent, so a project whose source textures are all 4096 px
    cannot fit an 8192 atlas without per-material caps. The largest uniform
    cap that fits is chosen first, then individual materials are upgraded
    greedily (largest source image first) while the budget allows.
    """
    materials: list[bpy.types.Material] = []
    seen: set[str] = set()
    for obj in meshes:
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in seen:
                continue
            seen.add(material.name)
            materials.append(material)

    extents = material_uv_extents(meshes)
    textured = []
    untextured = []
    pixels = {}
    for material in materials:
        image = texture_image_for_material(material)
        if image is None:
            untextured.append(material)
            continue
        textured.append(material)
        pixels[material.name] = image.size[0] * image.size[1]

    diffuse = int(scene.smc_diffuse_size)
    budget = max_side * max_side

    def total(caps: dict[str, int]) -> int:
        value = len(untextured) * diffuse * diffuse
        for material in textured:
            width, height = material_tile_size(
                caps[material.name],
                extents.get(material.name, (1.0, 1.0)),
            )
            value += width * height
        return value

    chosen = 32
    for candidate in (4096, 2048, 1024, 512, 256, 128, 64, 32):
        if total(
            {material.name: candidate for material in textured}
        ) <= budget:
            chosen = candidate
            break
    caps = {material.name: chosen for material in textured}

    for material in sorted(
        textured,
        key=lambda item: (-pixels.get(item.name, 0), item.name),
    ):
        while caps[material.name] < 4096:
            trial = dict(caps)
            trial[material.name] = trial[material.name] * 2
            if total(trial) > budget:
                break
            caps[material.name] = trial[material.name]

    for material in textured:
        material.smc_size = True
        material.smc_size_width = caps[material.name]
        material.smc_size_height = caps[material.name]

    return {
        "uniform_cap": chosen,
        "caps": {name: value for name, value in sorted(caps.items())},
        "textured_materials": sorted(pixels),
        "untextured_materials": sorted(
            material.name for material in untextured
        ),
        "estimated_pixels": total(caps),
        "atlas_pixels": budget,
    }


def merge_all_textures_with_matcombiner(
    project_root: Path,
    meshes: list[bpy.types.Object],
) -> dict:
    atlas_dir = project_root / "textures" / "stage_05c_merged"
    atlas_dir.mkdir(parents=True, exist_ok=True)
    existing_all = bpy.data.materials.get("all")
    if existing_all is not None:
        raise RuntimeError(
            "A material named 'all' already exists before texture merging."
        )

    before = {
        obj.name: {
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
            "uv_layers": len(obj.data.uv_layers),
            "material_slots": material_names(obj),
        }
        for obj in meshes
    }
    state = visibility_state()
    try:
        show_only(meshes)
        scene = bpy.context.scene
        scene.smc_size = "CUST"
        scene.smc_size_width = 8192
        scene.smc_size_height = 8192
        scene.smc_crop = True
        scene.smc_pixel_art = False
        scene.smc_diffuse_size = 32
        scene.smc_gaps = 0
        scene.smc_packer_type = "MAX_RECTS"
        scene.smc_include_extra_textures = False

        refresh_result = bpy.ops.smc.refresh_ob_data()
        if "FINISHED" not in refresh_result:
            raise RuntimeError(
                f"Material Combiner list refresh failed: {refresh_result}"
            )

        prepared_names = {obj.name for obj in meshes}
        invalid_entries = []
        for item in scene.smc_ob_data:
            if item.ob is not None and item.ob.name not in prepared_names:
                invalid_entries.append(item.ob.name)
            if item.mat is not None:
                item.used = True
                item.layer = 1
        if invalid_entries:
            raise RuntimeError(
                "Material Combiner found unexpected objects: "
                f"{sorted(set(invalid_entries))}"
            )

        size_caps = cap_material_texture_sizes(
            scene,
            meshes,
            max(scene.smc_size_width, scene.smc_size_height),
        )

        combine_result = bpy.ops.smc.combiner(
            "EXEC_DEFAULT",
            directory=str(atlas_dir) + os.sep,
        )
        if "FINISHED" not in combine_result:
            raise RuntimeError(
                f"Material Combiner failed: {combine_result}"
            )
    finally:
        restore_visibility(state)

    generated_materials = {
        slot.material
        for obj in meshes
        for slot in obj.material_slots
        if slot.material is not None
        and slot.material.name.startswith("material_atlas_")
    }
    if len(generated_materials) != 1:
        raise RuntimeError(
            "Texture merge must produce exactly one generated material; "
            f"found {len(generated_materials)}."
        )

    material = next(iter(generated_materials))
    material.name = "all"
    for obj in meshes:
        names = material_names(obj)
        if names != ["all"]:
            raise RuntimeError(
                f"Texture merge left unexpected materials on {obj.name}: {names}"
            )
        if obj.material_slots[0].material != material:
            raise RuntimeError(
                f"Texture merge assigned a different material on {obj.name}."
            )

    image_nodes = [
        node
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    ]
    unique_images = {node.image for node in image_nodes}
    if len(unique_images) != 1:
        raise RuntimeError(
            "Texture merge must leave exactly one image; "
            f"found {len(unique_images)}."
        )
    image = next(iter(unique_images))
    width, height = image.size
    original_size = [width, height]
    max_side = max(width, height)
    target_side = min(
        8192,
        1 << (max(1, max_side) - 1).bit_length(),
    )
    resized_to_square = width != target_side or height != target_side
    if resized_to_square:
        image.scale(target_side, target_side)
        image.save()
        width, height = image.size
    if (
        width != height
        or width > 8192
        or width <= 0
        or width & (width - 1)
    ):
        raise RuntimeError(
            "Merged atlas must be a square with a power-of-two side no "
            f"greater than 8192: {width}x{height}"
        )

    after = {
        obj.name: {
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
            "uv_layers": len(obj.data.uv_layers),
            "material_slots": material_names(obj),
        }
        for obj in meshes
    }
    for name, source in before.items():
        target = after[name]
        for key in ("vertices", "polygons", "uv_layers"):
            if source[key] != target[key]:
                raise RuntimeError(
                    f"Texture merge changed {key} on {name}: "
                    f"{source[key]} -> {target[key]}"
                )

    return {
        "enabled": True,
        "atlas_directory": str(atlas_dir),
        "atlas_path": bpy.path.abspath(image.filepath),
        "image_name": image.name,
        "original_image_size": original_size,
        "image_size": [width, height],
        "target_power_of_two_side": target_side,
        "resized_to_power_of_two_square": resized_to_square,
        "material": material.name,
        "max_size": [8192, 8192],
        "size_caps": size_caps,
        "extra_texture_atlases": False,
        "before": before,
        "after": after,
    }


def positive_group_counts(obj: bpy.types.Object) -> dict[str, int]:
    group_names = {
        group.index: group.name
        for group in obj.vertex_groups
    }
    counts = Counter()
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            if assignment.weight <= 0.0:
                continue
            name = group_names.get(assignment.group)
            if name:
                counts[name] += 1
    return dict(counts)


def zero_weight_vertex_count(obj: bpy.types.Object) -> int:
    return sum(
        1
        for vertex in obj.data.vertices
        if not any(
            assignment.weight > 0.0
            for assignment in vertex.groups
        )
    )


def clean_empty_or_orphan_groups(objects: list) -> None:
    """Remove unweighted groups and groups that do not match the armature."""
    for obj in objects:
        armature = obj.find_armature()
        bone_names = (
            set(armature.data.bones.keys()) if armature else set()
        )
        used = {
            assignment.group
            for vertex in obj.data.vertices
            for assignment in vertex.groups
            if assignment.weight > 0.0001
        }
        removable = [
            group
            for group in obj.vertex_groups
            if group.index not in used
            or (armature is not None and group.name not in bone_names)
        ]
        for group in removable:
            obj.vertex_groups.remove(group)


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    profile = load_json(profile_path)
    skill_root = profile_path.parents[1]
    project_root = Path(manifest["paths"]["project_root"])
    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_05c_mesh_cleanup.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_05c_mesh_cleanup.json"
    )

    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    enable_addon(("Modding-Toolkit", "Modding-Toolkit-dev"))

    # Prefer the collections the previous stage actually used; the profile's
    # static names can be shadowed by a suffixed leftover import.
    collection_names = []
    for part_name in manifest.get("parts", {}):
        name = (
            manifest["parts"][part_name].get("collection")
            or profile["prepared_collections"].get(part_name)
        )
        if name and name not in collection_names:
            collection_names.append(name)
    if not collection_names:
        collection_names = list(profile["prepared_collections"].values())
    prepared_meshes = collection_meshes(collection_names)
    if not prepared_meshes:
        raise RuntimeError("No prepared meshes were found.")

    before = {
        obj.name: {
            "vertices": len(obj.data.vertices),
            "material_slots": material_names(obj),
            "vertex_groups": len(obj.vertex_groups),
            "positive_groups": len(positive_group_counts(obj)),
        }
        for obj in prepared_meshes
    }

    merge_textures = (
        args.merge_textures
        or bool(
            manifest.get("settings", {}).get(
                "stage_5c_merge_textures",
                False,
            )
        )
    )
    # RE6 merges material slots that share one
    # Base Color image is optional and defaults to on.
    merge_by_base_image = bool(
        manifest.get("settings", {}).get(
            "stage_5c_merge_by_base_image",
            True,
        )
    )
    material_merge = None
    texture_merge = None
    result = None
    if merge_textures:
        enable_addon(("material-combiner-addon-master",))
        texture_merge = merge_all_textures_with_matcombiner(
            project_root,
            prepared_meshes,
        )
    else:
        if merge_by_base_image:
            # Outline shells carry the generated Outline material, which has
            # no Base Color image and must not fail the material audit.
            audit_meshes = [
                obj
                for obj in prepared_meshes
                if not obj.name.endswith("_Outline")
            ]
            material_merge, unresolved_base_images = (
                merge_materials_by_base_image(audit_meshes)
            )
        else:
            material_merge, unresolved_base_images = {}, []
        if unresolved_base_images:
            failure_report = {
                "stage": "mesh_cleanup",
                "status": "failed",
                "project": str(project_path),
                "input": str(input_path),
                "material_merge": material_merge,
                "failures": [
                    {
                        "reason": "Base Color texture resolution failed",
                        "unresolved": unresolved_base_images,
                    }
                ],
            }
            write_json(report_path, failure_report)
            raise RuntimeError(
                "Stage 5C stopped before material separation because one or "
                "more Base Color bindings could not be resolved: "
                f"{report_path}"
            )

        select_all(prepared_meshes)
        result = bpy.ops.mhw.separate_by_materials(
            rename=True,
            prune_keys=True,
            prune_groups=True,
            clean_suffix=True,
        )
        if "CANCELLED" in result:
            raise RuntimeError(f"Separate by Materials failed: {result}")

    fragments = collection_meshes(collection_names)
    select_all(fragments)
    before_cleanup = {
        obj.name: {
            "vertices": len(obj.data.vertices),
            "materials": material_names(obj),
            "groups_before_cleanup": len(obj.vertex_groups),
        }
        for obj in fragments
    }
    clean_empty_or_orphan_groups(fragments)

    fragment_reports = []
    weighted_group_names_by_mesh = {}
    failures = []
    for obj in fragments:
        materials = material_names(obj)
        counts = positive_group_counts(obj)
        armature = obj.find_armature()
        bone_names = (
            {bone.name for bone in armature.data.bones}
            if armature is not None
            else set()
        )
        missing = sorted(
            name
            for name in counts
            if name not in bone_names
        )
        report = {
            "mesh": obj.name,
            "vertices": len(obj.data.vertices),
            "materials": materials,
            "positive_groups": len(counts),
            "zero_weight_vertices": zero_weight_vertex_count(obj),
            "missing_bones": missing,
            "armature": armature.name if armature else None,
            "groups_before_cleanup": before_cleanup.get(
                obj.name, {}
            ).get("groups_before_cleanup"),
            "groups_after_cleanup": len(obj.vertex_groups),
        }
        fragment_reports.append(report)
        weighted_group_names_by_mesh[obj.name] = sorted(counts)
        if len(materials) != 1:
            failures.append(
                {
                    "mesh": obj.name,
                    "reason": "expected exactly one material",
                    "materials": materials,
                }
            )
        if report["zero_weight_vertices"]:
            failures.append(
                {
                    "mesh": obj.name,
                    "reason": "zero-weight vertices remain",
                    "count": report["zero_weight_vertices"],
                }
            )
        if missing:
            failures.append(
                {
                    "mesh": obj.name,
                    "reason": "weighted groups are missing from armature",
                    "missing_bones": missing,
                }
            )

    required_groups = profile.get("required_expression_groups", [])
    present_groups = {
        name
        for names in weighted_group_names_by_mesh.values()
        for name in names
    }
    missing_required_groups = sorted(
        name for name in required_groups if name not in present_groups
    )
    if missing_required_groups:
        failures.append(
            {
                "reason": "required expression groups were lost",
                "missing_required_groups": missing_required_groups,
            }
        )

    if failures:
        failure_report = {
            "stage": "mesh_cleanup",
            "status": "failed",
            "project": str(project_path),
            "input": str(input_path),
            "before": before,
            "material_merge": material_merge,
            "texture_merge": texture_merge,
            "fragments": fragment_reports,
            "missing_required_groups": missing_required_groups,
            "failures": failures,
        }
        write_json(report_path, failure_report)
        raise RuntimeError(f"Stage 5C validation failed: {report_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)
    report = {
        "stage": "mesh_cleanup",
        "status": "passed",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "cleanup_script": str(cleanup_script),
        "operator_result": list(result) if result else None,
        "before": before,
        "material_merge": material_merge,
        "texture_merge": texture_merge,
        "fragment_count": len(fragments),
        "fragments": fragment_reports,
        "weighted_group_names_by_mesh": weighted_group_names_by_mesh,
        "required_expression_groups": required_groups,
        "missing_required_groups": missing_required_groups,
    }
    write_json(report_path, report)

    for part_name in list(manifest.get("parts", {})):
        manifest["parts"][part_name]["status"] = (
            "textures_merged"
            if texture_merge
            else "meshes_separated"
        )
        manifest["parts"][part_name]["validation"] = "stage_05c_mesh_cleanup"
    manifest["stage_history"].append(
        {
            "stage": "mesh_cleanup",
            "blend": str(output_path),
            "report": str(report_path),
            "fragment_count": len(fragments),
            "required_groups": required_groups,
            "merge_textures": bool(texture_merge),
        }
    )
    write_json(project_path, manifest)
    print(f"MESH_CLEANUP_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
