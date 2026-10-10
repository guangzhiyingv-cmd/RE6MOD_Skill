"""Apply toon cylindrical face normals with cross-mesh seam blending."""

from __future__ import annotations

import argparse
import heapq
import importlib
import json
from pathlib import Path

import bpy
import numpy as np


PART_NAMES = ("body", "face", "hair")
OUTLINE_SUFFIX = "_Outline"


def parse_args() -> argparse.Namespace:
    import sys

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


def enable_addon(modules: tuple[str, ...]) -> str:
    errors = []
    for module in modules:
        result = bpy.ops.preferences.addon_enable(module=module)
        if "FINISHED" in result:
            return module
        errors.append(f"{module}: {result}")
    raise RuntimeError("Could not enable addon. Tried: " + "; ".join(errors))


def collection_meshes(collection_name: str) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise RuntimeError(f"Prepared collection not found: {collection_name}")
    return [
        obj
        for obj in sorted(collection.all_objects, key=lambda item: item.name)
        if (
            obj.type == "MESH"
            and not obj.name.endswith(OUTLINE_SUFFIX)
            and obj.data.polygons
        )
    ]


def resolved_collection_name(
    manifest: dict,
    profile: dict,
    part_name: str,
) -> str:
    # The manifest records the collection the previous stage actually used, so
    # it wins over the profile's static name (a leftover import can otherwise
    # shadow it with a suffixed duplicate).
    part = manifest.get("parts", {}).get(part_name, {})
    for key in ("collection", "mesh_collection"):
        value = part.get(key)
        if value:
            return str(value)

    profile_collections = profile.get("prepared_collections", {})
    if part_name in profile_collections:
        return str(profile_collections[part_name])

    raise RuntimeError(
        f"Prepared collection is missing for part: {part_name}"
    )


def resolved_prepared_mesh_name(
    manifest: dict,
    profile: dict,
    part_name: str,
) -> str | None:
    prepared_meshes = profile.get("prepared_meshes", {})
    if part_name in prepared_meshes:
        return str(prepared_meshes[part_name])

    part = manifest.get("parts", {}).get(part_name, {})
    for key in ("mesh", "mesh_name", "object"):
        value = part.get(key)
        if value:
            return str(value)
    return None


def object_mesh_context(obj: bpy.types.Object, normal_utils) -> dict:
    co, lv, ls, lt = normal_utils.mesh_arrays(obj.data)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    linear = matrix[:3, :3]
    origin = matrix[:3, 3]
    return {
        "obj": obj,
        "co": co,
        "world_co": co @ linear.T + origin,
        "lv": lv,
        "ls": ls,
        "lt": lt,
        "matrix": matrix,
        "linear": linear,
        "origin": origin,
        "mirrored": bool(np.linalg.det(linear) < 0.0),
    }


def boundary_vertex_mask(lv, ls, lt) -> np.ndarray:
    edge_counts: dict[tuple[int, int], int] = {}
    for start, total in zip(ls, lt):
        for offset in range(int(total)):
            a = int(lv[int(start) + offset])
            b = int(lv[int(start) + (offset + 1) % int(total)])
            key = (a, b) if a < b else (b, a)
            edge_counts[key] = edge_counts.get(key, 0) + 1

    boundary = np.zeros(len(lv), dtype=bool)
    for (a, b), count in edge_counts.items():
        if count == 1:
            boundary[a] = True
            boundary[b] = True
    return boundary


def material_vertex_flags(
    obj: bpy.types.Object,
    material_names: list[str],
) -> np.ndarray:
    """Mask of vertices touched by a polygon of the named materials.

    Targets such as RE6 keep the face inside the body mesh. Joined meshes keep
    duplicated vertices along material borders, so the seam has to be derived
    from positions rather than from shared edges.
    """
    material_indices = {
        index
        for index, slot in enumerate(obj.material_slots)
        if slot.material is not None and slot.material.name in material_names
    }
    if not material_indices:
        raise RuntimeError(
            f"None of the configured face mask materials exist on {obj.name}: "
            + ", ".join(material_names)
        )
    flags = np.zeros(len(obj.data.vertices), dtype=bool)
    for polygon in obj.data.polygons:
        if polygon.material_index not in material_indices:
            continue
        for vertex_index in polygon.vertices:
            flags[vertex_index] = True
    return flags


def vertex_group_weights(
    obj: bpy.types.Object,
    group_name: str | None,
) -> np.ndarray:
    weights = np.ones(len(obj.data.vertices), dtype=np.float64)
    if not group_name:
        return weights

    group = obj.vertex_groups.get(group_name)
    if group is None:
        raise RuntimeError(
            f"Face normal mask vertex group is missing on {obj.name}: "
            f"{group_name}"
        )

    weights.fill(0.0)
    for vertex in obj.data.vertices:
        value = sum(
            assignment.weight
            for assignment in vertex.groups
            if assignment.group == group.index
        )
        weights[vertex.index] = float(np.clip(value, 0.0, 1.0))
    return weights


def material_vertex_weights(
    obj: bpy.types.Object,
    material_names: list[str],
) -> np.ndarray:
    if not material_names:
        return np.ones(len(obj.data.vertices), dtype=np.float64)

    material_indices = set()
    for index, slot in enumerate(obj.material_slots):
        material = slot.material
        if material is not None and material.name in material_names:
            material_indices.add(index)
    if not material_indices:
        raise RuntimeError(
            f"None of the configured face mask materials exist on {obj.name}: "
            + ", ".join(material_names)
        )

    weights = np.zeros(len(obj.data.vertices), dtype=np.float64)
    for polygon in obj.data.polygons:
        if polygon.material_index not in material_indices:
            continue
        for vertex_index in polygon.vertices:
            weights[vertex_index] = 1.0
    return weights


def axis_vector(value) -> np.ndarray:
    if isinstance(value, str):
        key = value.strip().lower().replace("global_", "")
        if key in {"x", "global x"}:
            return np.array((1.0, 0.0, 0.0))
        if key in {"y", "global y"}:
            return np.array((0.0, 1.0, 0.0))
        if key in {"z", "global z"}:
            return np.array((0.0, 0.0, 1.0))
        raise RuntimeError(f"Unsupported cylindrical normal axis: {value!r}")
    vector = np.asarray(value, dtype=np.float64).reshape(3)
    length = np.linalg.norm(vector)
    if length < 1e-12:
        raise RuntimeError("Cylindrical normal axis cannot be zero.")
    return vector / length


def cylinder_center_world(
    face: dict,
    origin_mode: str,
) -> np.ndarray:
    mode = origin_mode.strip().lower()
    if mode in {"object", "object_origin", "origin"}:
        return face["origin"].copy()
    if mode in {"bbox", "bounding_box", "bounding_box_center"}:
        return (
            face["world_co"].min(axis=0)
            + face["world_co"].max(axis=0)
        ) * 0.5
    if mode == "cursor":
        return np.array(bpy.context.scene.cursor.location, dtype=np.float64)
    raise RuntimeError(
        "Unsupported cylindrical normal origin: "
        f"{origin_mode!r}"
    )


def world_to_local_normals(
    world_normals: np.ndarray,
    context: dict,
    normal_utils,
) -> np.ndarray:
    local = normal_utils.normalize(world_normals @ context["linear"])
    if context["mirrored"]:
        local = -local
    return local


def smoothstep(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def geodesic_from_seams(
    face: dict,
    cluster_by_vertex: np.ndarray,
    seam_clusters: set[int],
) -> tuple[np.ndarray, np.ndarray, int]:
    cluster_count = int(cluster_by_vertex.max()) + 1
    adjacency: list[list[tuple[int, float]]] = [
        [] for _ in range(cluster_count)
    ]
    for edge in face["obj"].data.edges:
        a_vertex, b_vertex = edge.vertices
        a = int(cluster_by_vertex[a_vertex])
        b = int(cluster_by_vertex[b_vertex])
        if a == b:
            continue
        length = float(
            np.linalg.norm(
                face["world_co"][a_vertex]
                - face["world_co"][b_vertex]
            )
        )
        adjacency[a].append((b, length))
        adjacency[b].append((a, length))

    distance = np.full(cluster_count, np.inf, dtype=np.float64)
    nearest = np.full(cluster_count, -1, dtype=np.int64)
    queue = []
    for cluster in sorted(seam_clusters):
        distance[cluster] = 0.0
        nearest[cluster] = cluster
        heapq.heappush(queue, (0.0, cluster, cluster))

    while queue:
        current_distance, cluster, seed = heapq.heappop(queue)
        if current_distance > distance[cluster] + 1e-15:
            continue
        if nearest[cluster] != seed:
            continue
        for neighbor, length in adjacency[cluster]:
            candidate = current_distance + length
            if candidate + 1e-15 >= distance[neighbor]:
                continue
            distance[neighbor] = candidate
            nearest[neighbor] = seed
            heapq.heappush(queue, (candidate, neighbor, seed))

    return distance, nearest, cluster_count


def reencode_shape_keys(
    obj: bpy.types.Object,
    normal_utils,
    enabled: bool,
) -> dict | None:
    if not enabled:
        return None
    if obj.data.shape_keys is None or len(obj.data.shape_keys.key_blocks) < 2:
        return None

    reencode = getattr(normal_utils, "reencode_for_shape", None)
    if reencode is None:
        raise RuntimeError(
            "The installed Modding Toolkit does not provide "
            "normal_utils.reencode_for_shape; enable Modding-Toolkit "
            "stable before this stage."
        )

    shapekey_utils = importlib.import_module(
        "Modding-Toolkit.core.shapekey_utils"
    )
    deformed = shapekey_utils.shape_key_mix(obj)
    if deformed is None:
        raise RuntimeError(
            f"Shape-key geometry could not be resolved for {obj.name}."
        )
    count, fresh, residual = reencode(obj.data, deformed)
    return {
        "mesh": obj.name,
        "corners": int(count),
        "captured_intent": bool(fresh),
        "residual_mean_degrees": float(residual.mean()),
        "residual_max_degrees": float(residual.max()),
    }


def set_custom_normals(
    obj: bpy.types.Object,
    normals: np.ndarray,
    normal_utils,
) -> None:
    clear_intent = getattr(normal_utils, "clear_intent", None)
    if clear_intent is not None:
        clear_intent(obj.data)
    obj.data.normals_split_custom_set(normals.tolist())
    obj.data.update()


def process_face_object(
    face_obj: bpy.types.Object,
    neighbors: list[bpy.types.Object],
    config: dict,
    normal_utils,
) -> dict:
    face = object_mesh_context(face_obj, normal_utils)
    neighbor_contexts = [
        object_mesh_context(obj, normal_utils)
        for obj in neighbors
    ]
    contexts = [face] + neighbor_contexts

    co_parts = []
    lv_parts = []
    ls_parts = []
    lt_parts = []
    flip_parts = []
    vertex_offset = 0
    loop_offset = 0
    for context in contexts:
        co_parts.append(context["world_co"])
        lv_parts.append(context["lv"].astype(np.int64) + vertex_offset)
        ls_parts.append(context["ls"].astype(np.int64) + loop_offset)
        lt_parts.append(context["lt"].astype(np.int64))
        flip_parts.append(
            np.full(
                len(context["ls"]),
                -1.0 if context["mirrored"] else 1.0,
            )
        )
        context["vertex_offset"] = vertex_offset
        context["loop_offset"] = loop_offset
        vertex_offset += len(context["co"])
        loop_offset += len(context["lv"])

    combined_co = np.concatenate(co_parts)
    combined_lv = np.concatenate(lv_parts)
    combined_ls = np.concatenate(ls_parts)
    combined_lt = np.concatenate(lt_parts)
    combined_flip = np.concatenate(flip_parts)

    match_distance = float(config.get("seam_match_distance", 0.001))
    if match_distance <= 0.0:
        raise RuntimeError("seam_match_distance must be greater than zero.")
    cluster, cluster_count = normal_utils.position_clusters(
        combined_co,
        match_distance,
    )

    face_vertex_cluster = cluster[
        face["vertex_offset"] + np.arange(len(face["co"]))
    ]
    if neighbor_contexts:
        neighbor_vertex_cluster = np.concatenate(
            [
                cluster[
                    context["vertex_offset"]
                    + np.arange(len(context["co"]))
                ]
                for context in neighbor_contexts
            ]
        )
        has_neighbor = np.zeros(cluster_count, dtype=bool)
        has_neighbor[neighbor_vertex_cluster] = True
    else:
        # Single-mesh targets have no separate neighbour: the material-region
        # border is the seam, so every cluster counts as shared.
        has_neighbor = np.ones(cluster_count, dtype=bool)

    mask_materials = [
        str(value) for value in config.get("mask_material_names", [])
    ]
    region_seam = bool(mask_materials) and bool(
        config.get("material_region_seam", False)
    )
    if region_seam:
        # Single-mesh targets: the seam is a position cluster that carries
        # both masked (face) and unmasked (body) geometry.
        region = material_vertex_flags(face_obj, mask_materials)
        inside_clusters = np.zeros(cluster_count, dtype=bool)
        outside_clusters = np.zeros(cluster_count, dtype=bool)
        inside_clusters[face_vertex_cluster[region]] = True
        outside_clusters[face_vertex_cluster[~region]] = True
        seam_clusters = np.flatnonzero(inside_clusters & outside_clusters)
        seam_mask = np.zeros(cluster_count, dtype=bool)
        seam_mask[seam_clusters] = True
        boundary_vertices = seam_mask[face_vertex_cluster]
    else:
        boundary_vertices = boundary_vertex_mask(
            face["lv"],
            face["ls"],
            face["lt"],
        )
        boundary_clusters = np.unique(
            face_vertex_cluster[np.flatnonzero(boundary_vertices)]
        )
        seam_clusters = boundary_clusters[has_neighbor[boundary_clusters]]

    fallback_used = False
    if not len(seam_clusters):
        if not bool(config.get("allow_nearby_vertex_fallback", False)):
            raise RuntimeError(
                f"No face boundary vertices on {face_obj.name} match a "
                "neighbor mesh inside seam_match_distance. Increase the "
                "distance or enable allow_nearby_vertex_fallback after review."
            )
        nearby = np.unique(face_vertex_cluster[has_neighbor[face_vertex_cluster]])
        seam_clusters = nearby
        fallback_used = True

    if not len(seam_clusters):
        raise RuntimeError(
            f"No cross-mesh seam vertices were found for {face_obj.name}."
        )

    shared_world, merged_positions, degenerate_positions = (
        normal_utils.integral_normals(
            combined_co,
            combined_lv,
            combined_ls,
            combined_lt,
            distance=match_distance,
            angle_limit=180.0,
            face_flip=combined_flip,
        )
    )
    shared_accumulator = np.zeros((cluster_count, 3), dtype=np.float64)
    np.add.at(
        shared_accumulator,
        cluster[combined_lv],
        shared_world,
    )
    shared_cluster_normal = normal_utils.normalize(shared_accumulator)
    seam_cluster_list = [int(value) for value in seam_clusters]
    bad_shared = np.linalg.norm(
        shared_cluster_normal[np.asarray(seam_cluster_list, dtype=np.int64)],
        axis=1,
    ) < 1e-9
    if np.any(bad_shared):
        raise RuntimeError(
            f"Shared seam normals cancel on {face_obj.name} at "
            f"{int(bad_shared.sum())} cluster(s). Check for opposite "
            "surfaces at the seam or lower seam_match_distance."
        )

    distance, nearest_seed, _ = geodesic_from_seams(
        face,
        face_vertex_cluster,
        set(seam_cluster_list),
    )
    reachable = np.isfinite(distance[face_vertex_cluster])

    blend_distance = float(config.get("seam_blend_distance", 0.02))
    if blend_distance < 0.0:
        raise RuntimeError("seam_blend_distance cannot be negative.")
    if blend_distance == 0.0:
        seam_falloff = np.where(
            distance[face_vertex_cluster] <= 0.0,
            0.0,
            1.0,
        )
    else:
        seam_falloff = smoothstep(
            distance[face_vertex_cluster] / blend_distance
        )
    seam_falloff[~reachable] = 0.0

    seed_cluster = nearest_seed[face_vertex_cluster]
    seed_local_vertex = np.zeros((len(face["co"]), 3), dtype=np.float64)
    seed_local_vertex[reachable] = world_to_local_normals(
        shared_cluster_normal[seed_cluster[reachable]],
        face,
        normal_utils,
    )

    axis_world = axis_vector(config.get("axis", [0.0, 0.0, 1.0]))
    center_world = cylinder_center_world(
        face,
        str(config.get("origin", "object_origin")),
    )
    radial = face["world_co"] - center_world
    radial -= np.outer(radial @ axis_world, axis_world)
    radius = np.linalg.norm(radial, axis=1)
    extent = float(
        np.linalg.norm(
            face["world_co"].max(axis=0)
            - face["world_co"].min(axis=0)
        )
    )
    on_axis = radius < max(1e-12, extent * 1e-6)
    cylinder_world = radial / np.maximum(radius[:, None], 1e-12)
    cylinder_local = world_to_local_normals(
        cylinder_world,
        face,
        normal_utils,
    )
    cylinder_local[on_axis] = seed_local_vertex[on_axis]

    base = normal_utils.corner_normals(face_obj.data)
    falloff_loop = seam_falloff[face["lv"]]
    seed_loop = seed_local_vertex[face["lv"]]
    cylinder_loop = cylinder_local[face["lv"]]
    direction = normal_utils.slerp(
        seed_loop,
        cylinder_loop,
        falloff_loop,
    )

    mask_group = config.get("mask_vertex_group")
    group_mask = vertex_group_weights(
        face_obj,
        str(mask_group) if mask_group else None,
    )
    material_mask = material_vertex_weights(
        face_obj,
        [
            str(value)
            for value in config.get("mask_material_names", [])
        ],
    )
    mask_vertex = group_mask * material_mask
    reachable_vertex = reachable.astype(np.float64)
    blend_weight = (
        mask_vertex[face["lv"]]
        * reachable_vertex[face["lv"]]
        * float(config.get("strength", 1.0))
    )
    blend_weight = np.clip(blend_weight, 0.0, 1.0)

    final = normal_utils.slerp(base, direction, blend_weight)
    invalid = ~np.isfinite(final).all(axis=1)
    final[invalid] = base[invalid]
    set_custom_normals(face_obj, final, normal_utils)
    face_shape_report = reencode_shape_keys(
        face_obj,
        normal_utils,
        bool(config.get("reencode_shape_keys", True)),
    )

    neighbor_reports = []
    seam_normal_mode = str(
        config.get("seam_normal_mode", "bilateral")
    ).strip().lower()
    if seam_normal_mode not in {"bilateral", "face_only"}:
        raise RuntimeError(
            "seam_normal_mode must be 'bilateral' or 'face_only'."
        )
    if seam_normal_mode == "bilateral":
        seam_cluster_array = np.asarray(seam_cluster_list, dtype=np.int64)
        for context in neighbor_contexts:
            obj = context["obj"]
            vertex_cluster = cluster[
                context["vertex_offset"] + np.arange(len(context["co"]))
            ]
            loop_cluster = vertex_cluster[context["lv"]]
            seam_loop = np.isin(loop_cluster, seam_cluster_array)
            if not seam_loop.any():
                continue
            normals = normal_utils.corner_normals(obj.data)
            normals[seam_loop] = world_to_local_normals(
                shared_cluster_normal[loop_cluster[seam_loop]],
                context,
                normal_utils,
            )
            set_custom_normals(obj, normals, normal_utils)
            shape_report = reencode_shape_keys(
                obj,
                normal_utils,
                bool(config.get("reencode_shape_keys", True)),
            )
            neighbor_reports.append(
                {
                    "mesh": obj.name,
                    "seam_corners": int(seam_loop.sum()),
                    "shape_keys": shape_report,
                }
            )

    transition_vertices = int(
        (
            reachable
            & (seam_falloff > 0.02)
            & (seam_falloff < 0.98)
        ).sum()
    )
    return {
        "face_mesh": face_obj.name,
        "vertices": len(face["co"]),
        "boundary_vertices": int(boundary_vertices.sum()),
        "seam_clusters": len(seam_cluster_list),
        "seam_vertices_on_face": int(
            np.isin(face_vertex_cluster, seam_cluster_array).sum()
        ),
        "reachable_vertices": int(reachable.sum()),
        "transition_vertices": transition_vertices,
        "merged_positions": int(merged_positions),
        "degenerate_positions": int(degenerate_positions),
        "fallback_used": fallback_used,
        "face_shape_keys": face_shape_report,
        "neighbors": neighbor_reports,
    }


def main() -> int:
    args = parse_args()
    project_path = args.project.resolve()
    input_path = args.input.resolve()
    manifest = load_json(project_path)
    settings = manifest.get("settings", {})
    if not bool(settings.get("make_cylindrical_face_normals", True)):
        raise RuntimeError(
            "Stage 5C-Pre was requested, but "
            "settings.make_cylindrical_face_normals is not true."
        )

    config = settings.get("cylindrical_face_normals") or {}
    profile_path = Path(manifest["target_profile"]["path"]).resolve()
    profile = load_json(profile_path)
    project_root = Path(manifest["paths"]["project_root"])
    output_path = (
        args.output.resolve()
        if args.output
        else project_root / "stages" / "stage_05c_pre_face_normals.blend"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else project_root / "reports" / "stage_05c_pre_face_normals.json"
    )
    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite successful stage: {output_path}"
        )

    bpy.ops.wm.open_mainfile(filepath=str(input_path), load_ui=False)
    enabled_addon = enable_addon(
        ("Modding-Toolkit", "Modding-Toolkit-dev")
    )
    normal_utils = importlib.import_module(
        "Modding-Toolkit.core.normal_utils"
    )

    # Targets such as RE6 keep the face inside the body mesh; the profile and
    # manifest select which part holds it.
    face_part = str(config.get("face_part") or "face")
    region_seam_requested = bool(
        config.get("mask_material_names")
    ) and bool(config.get("material_region_seam", False))
    face_collection = resolved_collection_name(
        manifest,
        profile,
        face_part,
    )
    face_objects = collection_meshes(face_collection)
    face_mesh_name = str(
        config.get("face_mesh")
        or resolved_prepared_mesh_name(manifest, profile, face_part)
        or ""
    )
    if face_mesh_name:
        face_objects = [
            obj for obj in face_objects if obj.name == face_mesh_name
        ]
    if len(face_objects) != 1:
        raise RuntimeError(
            "Stage 5C-Pre requires exactly one main face mesh before material "
            f"separation; found {len(face_objects)} in {face_collection} "
            f"after resolving {face_mesh_name or 'all collection meshes'!r}."
        )
    face_obj = face_objects[0]

    neighbor_parts = [
        str(value)
        for value in config.get("seam_neighbor_parts", ["body"])
    ]
    if not neighbor_parts and not region_seam_requested:
        raise RuntimeError("seam_neighbor_parts cannot be empty.")
    neighbors = []
    configured_neighbors = [
        str(value)
        for value in config.get("seam_neighbor_meshes", [])
    ]
    if configured_neighbors:
        missing = [
            name
            for name in configured_neighbors
            if bpy.data.objects.get(name) is None
        ]
        if missing:
            raise RuntimeError(
                "Configured seam neighbor meshes are missing: "
                + ", ".join(missing)
            )
        neighbors = [
            bpy.data.objects[name]
            for name in configured_neighbors
            if bpy.data.objects[name].type == "MESH"
        ]
    known_parts = set(manifest.get("parts", {})) or set(PART_NAMES)
    for part_name in neighbor_parts:
        if part_name not in known_parts and part_name not in PART_NAMES:
            raise RuntimeError(
                f"Unsupported seam neighbor part: {part_name!r}"
            )
        collection_name = resolved_collection_name(
            manifest,
            profile,
            part_name,
        )
        part_meshes = collection_meshes(collection_name)
        prepared_name = resolved_prepared_mesh_name(
            manifest,
            profile,
            part_name,
        )
        if prepared_name:
            named = [
                obj for obj in part_meshes if obj.name == prepared_name
            ]
            if named:
                part_meshes = named
        neighbors.extend(part_meshes)
    neighbors = list({obj.name: obj for obj in neighbors}.values())
    if not neighbors and not region_seam_requested:
        raise RuntimeError("No seam neighbor meshes were found.")

    result = process_face_object(
        face_obj,
        neighbors,
        config,
        normal_utils,
    )
    if result["seam_vertices_on_face"] == 0:
        raise RuntimeError("The face has no vertices on the detected seam.")
    if result["transition_vertices"] == 0 and float(
        config.get("seam_blend_distance", 0.02)
    ) > 0.0:
        raise RuntimeError(
            "No transition vertices were produced. Increase "
            "seam_blend_distance or check the face topology."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)

    report = {
        "stage": "cylindrical_face_normals",
        "status": "passed",
        "project": str(project_path),
        "input": str(input_path),
        "output": str(output_path),
        "addon": enabled_addon,
        "config": config,
        "result": result,
    }
    write_json(report_path, report)

    manifest.setdefault("settings", {})[
        "cylindrical_face_normals_report"
    ] = str(report_path)
    manifest.setdefault("parts", {}).setdefault(face_part, {})[
        "validation"
    ] = "stage_05c_pre_face_normals"
    manifest.setdefault("stage_history", []).append(
        {
            "stage": "cylindrical_face_normals",
            "blend": str(output_path),
            "report": str(report_path),
            "face_mesh": face_obj.name,
            "seam_clusters": result["seam_clusters"],
            "transition_vertices": result["transition_vertices"],
        }
    )
    write_json(project_path, manifest)
    print(f"CYLINDRICAL_FACE_NORMALS_STAGE={output_path}")
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
