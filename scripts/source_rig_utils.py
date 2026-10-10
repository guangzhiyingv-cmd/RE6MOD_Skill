"""Source rig preset detection and generic A/T pose handling."""

from __future__ import annotations

import importlib
import math
import re
from pathlib import Path

import bpy
from mathutils import Vector


PRESET_MATCH_THRESHOLD = 0.95
PRESET_TIE_TOLERANCE = 1e-9
REE_POSE_PRESETS: set[str] = set()


def enable_modding_toolkit() -> None:
    errors = []
    for module in ("Modding-Toolkit", "Modding-Toolkit-dev"):
        # Re-enabling an enabled add-on re-registers its classes, which
        # invalidates property pointers the caller may still hold.
        if module in bpy.context.preferences.addons:
            return
        result = bpy.ops.preferences.addon_enable(module=module)
        if "FINISHED" in result:
            return
        errors.append(f"{module}: {result}")
    raise RuntimeError("Modding Toolkit is not available: " + "; ".join(errors))


def bone_mapper_module() -> object:
    enable_modding_toolkit()
    return importlib.import_module("Modding-Toolkit.core.bone_mapper")


def score_preset(
    armature: bpy.types.Object,
    preset_name: str,
) -> dict:
    module = bone_mapper_module()
    ui_config = importlib.import_module("Modding-Toolkit.core.ui_config")
    mapper = module.BoneMapManager()
    if not mapper.load_preset(preset_name, is_import_x=True):
        return {
            "preset": preset_name,
            "matched": 0,
            "total": 0,
            "coverage": 0.0,
            "game_code": None,
        }
    matched = 0
    checked = 0
    for standard_key in module.STANDARD_BONE_NAMES:
        if standard_key in ui_config.OPTIONAL_BONES:
            continue
        checked += 1
        bone_name, _matches = mapper.get_matches_for_standard(
            armature,
            standard_key,
        )
        if bone_name:
            matched += 1
    return {
        "preset": preset_name,
        "matched": matched,
        "total": checked,
        "coverage": matched / checked if checked else 0.0,
        "game_code": mapper.preset_info.get("game_code"),
        "name": mapper.preset_info.get("name"),
    }


def candidate_preset_scores(
    armature: bpy.types.Object,
) -> list[dict]:
    module = bone_mapper_module()
    mapper = module.BoneMapManager()
    marker = Path(
        mapper.get_preset_path("MMD.json", is_import_x=True)
    )
    preset_dir = marker.parent
    if not preset_dir.is_dir():
        return []
    scores = [
        score_preset(armature, path.name)
        for path in sorted(preset_dir.glob("*.json"))
    ]
    return sorted(
        scores,
        key=lambda item: (-item["coverage"], item["preset"]),
    )


def detect_source_rig(
    armature: bpy.types.Object,
    requested_preset: str | None = None,
) -> dict:
    module = bone_mapper_module()
    scores = candidate_preset_scores(armature)
    if requested_preset and str(requested_preset).lower() != "auto":
        selected = next(
            (
                item
                for item in scores
                if item["preset"].lower()
                == Path(requested_preset).name.lower()
            ),
            None,
        )
        if selected is None:
            selected = score_preset(
                armature,
                Path(requested_preset).name,
            )
        return {
            "requested": requested_preset,
            "preset": selected["preset"],
            "game_code": selected["game_code"],
            "coverage": selected["coverage"],
            "matched": selected["matched"],
            "total": selected["total"],
            "scores": scores,
            "source": "requested",
            "selection_status": "explicit",
            "ambiguous": False,
            "tied_presets": [],
        }

    detected = module.auto_detect_preset(armature, is_import_x=True)
    selected = next(
        (
            item
            for item in scores
            if detected is not None
            and item["preset"].lower() == Path(detected).name.lower()
        ),
        None,
    )
    tied_presets = []
    if scores:
        best_coverage = float(scores[0]["coverage"])
        if best_coverage >= PRESET_MATCH_THRESHOLD:
            tied_presets = [
                item
                for item in scores
                if abs(
                    float(item["coverage"]) - best_coverage
                )
                <= PRESET_TIE_TOLERANCE
            ]
    ambiguous = len(tied_presets) > 1
    selected_preset = None
    if detected is not None and not ambiguous:
        selected_preset = Path(detected).name
    return {
        "requested": None,
        "preset": selected_preset,
        "toolkit_preset": Path(detected).name if detected else None,
        "game_code": (
            selected["game_code"] if selected and not ambiguous else None
        ),
        "coverage": (
            selected["coverage"] if selected and not ambiguous else 0.0
        ),
        "matched": selected["matched"] if selected and not ambiguous else 0,
        "total": selected["total"] if selected and not ambiguous else 0,
        "scores": scores,
        "source": "auto",
        "selection_status": (
            "ambiguous"
            if ambiguous
            else "detected"
            if selected_preset
            else "unmatched"
        ),
        "ambiguous": ambiguous,
        "tied_presets": tied_presets,
    }


def effective_source_preset(manifest: dict) -> str:
    source_rig = manifest.get("source_rig", {})
    if not source_rig:
        return "MMD.json"
    for key in ("effective_preset", "preset", "detected_preset"):
        value = source_rig.get(key)
        if value:
            return Path(str(value)).name
    raise RuntimeError(
        "No effective source rig preset is available. Run source rig "
        "normalization or set source_rig.effective_preset."
    )


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _side_from_name(name: str) -> str | None:
    lowered = name.lower()
    if "left" in lowered:
        return "left"
    if "right" in lowered:
        return "right"
    if re.search(r"(^|[._\-\s])l($|[._\-\s0-9])", lowered):
        return "left"
    if re.search(r"(^|[._\-\s])r($|[._\-\s0-9])", lowered):
        return "right"
    return None


def _heuristic_upper_arm_candidates(
    armature: bpy.types.Object,
) -> dict[str, list[dict]]:
    excluded = (
        "forearm",
        "fore_arm",
        "arm_lower",
        "lowerarm",
        "lower_arm",
        "elbow",
        "hand",
        "wrist",
        "twist",
        "helper",
        "shoulder",
        "clavicle",
        "ひじ",
        "手首",
        "捩",
        "肩",
    )
    allowed = (
        "upperarm",
        "upper_arm",
        "upper arm",
        "arm_upper",
        "arm",
        "上腕",
        "上臂",
        "大臂",
        "腕",
    )
    result = {"left": [], "right": []}
    for bone in armature.data.bones:
        lowered = bone.name.lower()
        if any(token in lowered for token in excluded):
            continue
        if not any(token in lowered for token in allowed):
            continue
        side = _side_from_name(bone.name)
        if side is None:
            continue
        children = [child.name.lower() for child in bone.children]
        parent_name = bone.parent.name.lower() if bone.parent else ""
        score = 1.0
        if any(
            token in _normalized_name(bone.name)
            for token in ("upperarm", "armupper", "上腕", "上臂")
        ):
            score += 2.0
        if any(
            any(token in child for token in ("forearm", "elbow", "ひじ"))
            for child in children
        ):
            score += 1.0
        if any(
            token in parent_name
            for token in ("shoulder", "clavicle", "肩")
        ):
            score += 0.5
        result[side].append(
            {
                "name": bone.name,
                "score": score,
                "children": [child.name for child in bone.children],
                "parent": bone.parent.name if bone.parent else None,
            }
        )
    for side in result:
        result[side].sort(key=lambda item: (-item["score"], item["name"]))
    return result


def find_upper_arm_bones(
    armature: bpy.types.Object,
    preset: str | None,
) -> dict:
    if preset:
        module = bone_mapper_module()
        mapper = module.BoneMapManager()
        if mapper.load_preset(preset, is_import_x=True):
            names = {}
            matches = {}
            for side, standard_key in (
                ("left", "upperarm_L"),
                ("right", "upperarm_R"),
            ):
                bone_name, matched = mapper.get_matches_for_standard(
                    armature,
                    standard_key,
                )
                if bone_name is None:
                    break
                names[side] = bone_name
                matches[side] = matched
            else:
                return {
                    "method": "preset",
                    "preset": preset,
                    "names": names,
                    "matches": matches,
                }

    candidates = _heuristic_upper_arm_candidates(armature)
    selected = {}
    for side in ("left", "right"):
        values = candidates[side]
        if not values:
            raise RuntimeError(
                f"Could not identify the {side} upper-arm bone. "
                f"Candidates: {values}"
            )
        if len(values) > 1 and abs(
            values[0]["score"] - values[1]["score"]
        ) <= 1e-9:
            raise RuntimeError(
                f"Ambiguous {side} upper-arm bone candidates: {values}"
            )
        selected[side] = values[0]["name"]
    return {
        "method": "heuristic",
        "preset": preset,
        "names": selected,
        "matches": {},
        "candidates": candidates,
    }


def find_preset_bones(
    armature: bpy.types.Object,
    preset: str | None,
    standard_keys,
) -> dict | None:
    """Resolve standard (semantic) bone keys through a Modding Toolkit preset.

    Returns None when no preset is available or when any requested key cannot
    be resolved, so callers can fall back to another measurement or defer.
    """
    if not preset:
        return None
    module = bone_mapper_module()
    mapper = module.BoneMapManager()
    if not mapper.load_preset(preset, is_import_x=True):
        return None
    resolved = {}
    for standard_key in standard_keys:
        bone_name, _matches = mapper.get_matches_for_standard(
            armature,
            standard_key,
        )
        if bone_name is None:
            return None
        resolved[standard_key] = bone_name
    return resolved


def bone_world_height(
    armature: bpy.types.Object,
    bone_name: str,
) -> float:
    """World-space height of a bone head."""
    bone = armature.data.bones.get(bone_name)
    if bone is None:
        raise RuntimeError(
            f"Bone {bone_name!r} is missing from {armature.name!r}"
        )
    return (armature.matrix_world @ bone.head_local).z


def upper_arm_pose(
    armature: bpy.types.Object,
    preset: str | None,
) -> dict:
    resolution = find_upper_arm_bones(armature, preset)
    angles = {}
    bone_names = {}
    for side, bone_name in resolution["names"].items():
        bone = armature.data.bones[bone_name]
        vector = (
            armature.matrix_world.to_3x3()
            @ (bone.tail_local - bone.head_local)
        )
        horizontal = math.hypot(vector.x, vector.y)
        angles[side] = math.degrees(
            math.atan2(abs(vector.z), horizontal)
        )
        bone_names[side] = {
            "bone": bone_name,
            "matches": resolution.get("matches", {}).get(side, []),
        }
    maximum_angle = max(angles.values())
    return {
        "pose": "a_pose" if maximum_angle >= 15.0 else "t_pose",
        "angles_below_horizontal_degrees": angles,
        "bone_names": bone_names,
        "a_pose_threshold_degrees": 15.0,
        "resolution": resolution["method"],
    }


def set_active(obj: bpy.types.Object) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def background_apply_and_rebind(
    arm_obj: bpy.types.Object,
) -> int:
    import numpy as np

    bpy.ops.object.mode_set(mode="OBJECT")
    mesh_children = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and obj.find_armature() == arm_obj
    ]
    deform_matrices = {
        pose_bone.name: (
            pose_bone.matrix
            @ arm_obj.data.bones[pose_bone.name].matrix_local.inverted()
        )
        for pose_bone in arm_obj.pose.bones
        if arm_obj.data.bones[pose_bone.name].use_deform
    }
    identity = np.identity(4, dtype=np.float64)
    for mesh_obj in mesh_children:
        mesh = mesh_obj.data
        vertex_count = len(mesh.vertices)
        weights = np.zeros((vertex_count, 4, 4), dtype=np.float64)
        weights[:] = identity
        group_matrices = {
            group.index: deform_matrices.get(group.name)
            for group in mesh_obj.vertex_groups
        }
        for vertex in mesh.vertices:
            total = np.zeros((4, 4), dtype=np.float64)
            total_weight = 0.0
            for membership in vertex.groups:
                if membership.weight <= 0.0:
                    continue
                matrix = group_matrices.get(membership.group)
                if matrix is None:
                    continue
                total += membership.weight * np.array(matrix)
                total_weight += membership.weight
            if total_weight > 0.0:
                weights[vertex.index] = total / total_weight
        mesh_to_arm = (
            arm_obj.matrix_world.inverted()
            @ mesh_obj.matrix_world
        )
        arm_to_mesh = mesh_to_arm.inverted()
        mesh_space = (
            np.array(arm_to_mesh)
            @ weights
            @ np.array(mesh_to_arm)
        )
        targets = (
            list(mesh.shape_keys.key_blocks)
            if mesh.shape_keys
            else [mesh]
        )
        for target in targets:
            coordinates = np.empty(
                vertex_count * 3,
                dtype=np.float32,
            )
            if target is mesh:
                mesh.vertices.foreach_get("co", coordinates)
            else:
                target.data.foreach_get("co", coordinates)
            coordinates = coordinates.reshape(vertex_count, 3)
            homogeneous = np.concatenate(
                (
                    coordinates.astype(np.float64),
                    np.ones((vertex_count, 1), dtype=np.float64),
                ),
                axis=1,
            )
            transformed = np.einsum(
                "nij,nj->ni",
                mesh_space,
                homogeneous,
            )[:, :3].astype(np.float32)
            if target is mesh:
                mesh.vertices.foreach_set(
                    "co",
                    transformed.reshape(-1),
                )
            else:
                target.data.foreach_set(
                    "co",
                    transformed.reshape(-1),
                )
        mesh.update()
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.select_all(action="SELECT")
    applied = bpy.ops.pose.armature_apply(selected=True)
    if "FINISHED" not in applied:
        raise RuntimeError(
            f"Could not apply the converted armature pose: {applied}"
        )
    bpy.ops.pose.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    return len(mesh_children)


def apply_mmd_t_pose(armature: bpy.types.Object) -> dict:
    pose_ops = importlib.import_module("Modding-Toolkit.core.pose_ops")
    pose_ops._apply_and_rebind = background_apply_and_rebind
    set_active(armature)
    result = bpy.ops.modder.mmd_a_to_tpose()
    if "FINISHED" not in result:
        raise RuntimeError(f"MMD A to T-Pose conversion failed: {result}")
    return {
        "applied": True,
        "method": "mmd_a_to_tpose",
        "operator_result": list(result),
    }


def apply_generic_t_pose(
    armature: bpy.types.Object,
    preset: str,
) -> dict:
    resolution = find_upper_arm_bones(armature, preset)
    set_active(armature)
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.select_all(action="DESELECT")
    changed = []
    for side, sign in (("left", 1.0), ("right", -1.0)):
        bone_name = resolution["names"][side]
        pose_bone = armature.pose.bones[bone_name]
        bone = armature.data.bones[bone_name]
        bone_vector = (
            armature.matrix_world.to_3x3()
            @ (bone.tail_local - bone.head_local)
        ).normalized()
        target_direction = Vector((sign, 0.0, 0.0))
        rotation = bone_vector.rotation_difference(target_direction)
        current_matrix = pose_bone.matrix.copy()
        new_matrix = rotation.to_matrix().to_4x4() @ current_matrix
        new_matrix[0][3] = current_matrix[0][3]
        new_matrix[1][3] = current_matrix[1][3]
        new_matrix[2][3] = current_matrix[2][3]
        pose_bone.matrix = new_matrix
        bpy.context.view_layer.update()
        changed.append(bone_name)
    mesh_count = background_apply_and_rebind(armature)
    return {
        "applied": True,
        "method": "generic_a_to_tpose",
        "preset": preset,
        "bones": changed,
        "mesh_count": mesh_count,
    }


def apply_pose_conversion(
    armature: bpy.types.Object,
    preset: str | None,
    detection: dict,
) -> dict:
    if detection["pose"] != "a_pose":
        return {
            "applied": False,
            "method": None,
            "reason": "source is already T-pose",
        }
    if not preset:
        return {
            "applied": False,
            "deferred": True,
            "method": "normalize_to_mmd_then_convert",
            "reason": "source preset is unknown",
        }
    preset_name = Path(preset).name.lower()
    if preset_name == "mmd.json":
        return apply_mmd_t_pose(armature)

    if preset_name in REE_POSE_PRESETS and hasattr(
        bpy.ops.modder,
        "ree_to_tpose",
    ):
        set_active(armature)
        result = bpy.ops.modder.ree_to_tpose()
        if "FINISHED" in result:
            return {
                "applied": True,
                "method": "ree_to_tpose",
                "preset": preset,
                "operator_result": list(result),
            }
        fallback = apply_generic_t_pose(armature, preset)
        fallback["fallback_from"] = "ree_to_tpose"
        fallback["ree_operator_result"] = list(result)
        return fallback

    return apply_generic_t_pose(armature, preset)
