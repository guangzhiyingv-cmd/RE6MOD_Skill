"""Curl the source finger bones inward, bake the pose, and remove the bones.

The curled shape is baked into the mesh and the finger weights are merged into
the hand bone, so the prepared model keeps slightly closed hands without
carrying any finger bones into the target skeleton.
"""

from __future__ import annotations

import importlib
import math

import bpy
from mathutils import Vector

import source_rig_utils


FINGER_CHAINS = ("thumb", "index", "middle", "ring", "pinky")
FINGER_SEGMENTS = ("01", "02", "03")
SIDES = ("L", "R")
AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}
CURL_EPSILON = 1.0e-5


def finger_semantic_keys() -> list[str]:
    """Every finger bone key of the standard preset vocabulary."""
    return [
        f"{chain}_{segment}_{side}"
        for side in SIDES
        for chain in FINGER_CHAINS
        for segment in FINGER_SEGMENTS
    ]


def _toolkit_module(name: str):
    source_rig_utils.enable_modding_toolkit()
    return importlib.import_module(f"Modding-Toolkit.core.{name}")


def _side_chains(resolved: dict, side: str) -> dict:
    return {
        chain: [
            resolved[f"{chain}_{segment}_{side}"]
            for segment in FINGER_SEGMENTS
        ]
        for chain in FINGER_CHAINS
    }


def _set_rotation(pose_bone, axis: str, degrees: float) -> None:
    pose_bone.rotation_mode = "XYZ"
    values = [0.0, 0.0, 0.0]
    values[AXIS_INDEX[axis]] = math.radians(degrees)
    pose_bone.rotation_euler = values


def _palm_normal(
    armature,
    side: str,
    chains: dict,
    hand_name: str,
) -> Vector:
    """Palm-side normal from the hand axis and thumb-to-pinky axis."""
    hand = armature.pose.bones[hand_name]
    distal = (hand.tail - hand.head).normalized()
    thumb_head = armature.pose.bones[chains["thumb"][0]].head
    pinky_head = armature.pose.bones[chains["pinky"][0]].head
    radial = thumb_head - pinky_head
    radial = radial - distal * radial.dot(distal)
    if radial.length <= CURL_EPSILON:
        raise RuntimeError(
            f"Could not resolve the palm plane for hand {hand_name!r}."
        )
    radial.normalize()
    normal = (
        distal.cross(radial)
        if side == "L"
        else radial.cross(distal)
    )
    if normal.length <= CURL_EPSILON:
        raise RuntimeError(
            f"Could not resolve the palm normal for hand {hand_name!r}."
        )
    return normal.normalized()


def _curl_metrics(
    armature,
    bones: list[str],
    chains: dict,
    axis: str,
    angle: float,
    palm_normal: Vector,
) -> dict:
    """Signed fingertip movement toward the palm for both rotation signs."""
    rest_tips = {
        chain: armature.pose.bones[names[-1]].tail.copy()
        for chain, names in chains.items()
    }
    metrics = {}
    for sign in (1, -1):
        _pose_all(armature, bones, axis, angle * sign)
        bpy.context.view_layer.update()
        metrics[str(sign)] = {
            chain: (
                armature.pose.bones[names[-1]].tail - rest_tips[chain]
            ).dot(palm_normal)
            for chain, names in chains.items()
        }
    return metrics


def _pose_all(armature, bones: list[str], axis: str, degrees: float) -> None:
    for name in bones:
        _set_rotation(armature.pose.bones[name], axis, degrees)
    bpy.context.view_layer.update()


def _resolve_side_sign(
    armature,
    side: str,
    bones: list[str],
    chains: dict,
    hand_name: str,
    axis: str,
    angle: float,
    requested: str,
) -> dict:
    palm_normal = _palm_normal(
        armature,
        side,
        chains,
        hand_name,
    )
    metrics = _curl_metrics(
        armature,
        bones,
        chains,
        axis,
        angle,
        palm_normal,
    )
    positive_min = min(metrics["1"].values())
    negative_min = min(metrics["-1"].values())
    if max(positive_min, negative_min) <= CURL_EPSILON:
        raise RuntimeError(
            f"No inward finger rotation direction was found for "
            f"{hand_name!r}: {metrics}"
        )
    measured_sign = 1 if positive_min >= negative_min else -1
    chosen = metrics[str(measured_sign)]
    outward = {
        chain: value
        for chain, value in chosen.items()
        if value <= CURL_EPSILON
    }
    if outward:
        raise RuntimeError(
            f"Not every finger curls toward the palm of {hand_name!r}: "
            f"{outward}"
        )

    requested_value = str(requested).lower()
    if requested_value in ("1", "+1"):
        if measured_sign != 1:
            raise RuntimeError(
                f"finger_cleanup requested +1 for {hand_name!r}, but "
                "palm-normal validation requires -1."
            )
    elif requested_value == "-1":
        if measured_sign != -1:
            raise RuntimeError(
                f"finger_cleanup requested -1 for {hand_name!r}, but "
                "palm-normal validation requires +1."
            )
    elif requested_value in ("mirrored", "mmd", "auto"):
        pass
    else:
        raise RuntimeError(
            "finger_cleanup.sign must be auto, mirrored, 1 or -1: "
            f"{requested!r}"
        )

    report = {
        "sign": measured_sign,
        "requested": str(requested),
        "palm_normal": list(palm_normal),
        "curl_metrics": metrics,
        "validation": {
            "minimum_toward_palm": min(chosen.values()),
            "maximum_toward_palm": max(chosen.values()),
        },
    }
    if requested_value in ("mirrored", "mmd"):
        mirrored_hint = -1 if side == "L" else 1
        report["mirrored_hint"] = mirrored_hint
        report["hint_corrected"] = measured_sign != mirrored_hint
    return report


def apply_finger_cleanup(
    mesh_obj,
    armature,
    preset: str | None,
    config: dict,
) -> dict:
    """Rotate, bake, and remove the finger bones described by *config*."""
    if not config or not config.get("enabled", True):
        return {"enabled": False, "reason": "disabled by the target profile"}

    keys = finger_semantic_keys()
    resolved = source_rig_utils.find_preset_bones(armature, preset, keys)
    hands = source_rig_utils.find_preset_bones(
        armature,
        preset,
        [f"hand_{side}" for side in SIDES],
    )
    if resolved is None or hands is None:
        return {
            "enabled": False,
            "deferred": True,
            "reason": (
                "source finger or hand bones could not be resolved through "
                "the source preset"
            ),
            "source_preset": preset,
        }

    keep = set(config.get("keep_bones") or ["thumb_01_L", "thumb_01_R"])
    keep_bones = sorted(key for key in keep if key in resolved)
    rotatable_by_side = {
        side: sorted(
            resolved[key]
            for key in resolved
            if key.endswith(f"_{side}") and key not in keep
        )
        for side in SIDES
    }
    rotatable = sorted(
        name for names in rotatable_by_side.values() for name in names
    )
    all_finger_bones = sorted(resolved.values())

    axis = str(config.get("axis", "X")).upper()
    if axis not in AXIS_INDEX:
        raise RuntimeError(f"finger_cleanup.axis must be X, Y or Z: {axis!r}")
    angle = float(config.get("angle_degrees", 20.0))

    chains_by_side = {side: _side_chains(resolved, side) for side in SIDES}
    hand_bones = {side: hands[f"hand_{side}"] for side in SIDES}
    requested_sign = config.get("sign", "auto")

    bpy.context.view_layer.objects.active = armature
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.mode_set(mode="POSE")
    try:
        rotations = {
            side: _resolve_side_sign(
                armature,
                side,
                rotatable_by_side[side],
                chains_by_side[side],
                hand_bones[side],
                axis,
                angle,
                requested_sign,
            )
            for side in SIDES
        }
        for side in SIDES:
            _pose_all(
                armature,
                rotatable_by_side[side],
                axis,
                angle * rotations[side]["sign"],
            )
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    pose_bake = _toolkit_module("pose_bake")
    baked_meshes = pose_bake.bake_pose_to_rest(armature)

    weight_utils = _toolkit_module("weight_utils")
    bone_pairs = [
        (hand_bones[side], bone_name)
        for side in SIDES
        for chain in chains_by_side[side].values()
        for bone_name in chain
    ]
    weight_utils.merge_weights_and_delete_bones(armature, bone_pairs)

    if mesh_obj is not None:
        bpy.context.view_layer.objects.active = mesh_obj
    return {
        "enabled": True,
        "angle_degrees": angle,
        "axis": axis,
        "rotation": rotations,
        "rotated_bone_count": len(rotatable),
        "kept_bone_names": keep_bones,
        "deleted_bone_count": len(all_finger_bones),
        "deleted_bones": all_finger_bones,
        "merged_to": hand_bones,
        "baked_meshes": baked_meshes,
    }
