"""Curl the source finger bones inward, bake the pose, and remove the bones.

The curled shape is baked into the mesh and the finger weights are merged into
the hand bone, so the prepared model keeps slightly closed hands without
carrying any finger bones into the target skeleton.
"""

from __future__ import annotations

import importlib
import math

import bpy

import source_rig_utils


FINGER_CHAINS = ("thumb", "index", "middle", "ring", "pinky")
FINGER_SEGMENTS = ("01", "02", "03")
SIDES = ("L", "R")
AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


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


def _side_metric(armature, chains: dict, wrist_name: str) -> float:
    """Total distance from one hand's curled fingertips to its wrist.

    Curling a finger inward brings the tip closer to the wrist, so the smaller
    metric identifies the inward direction without depending on a rig
    convention. It is measured per hand because mirrored left and right
    fingers cancel out when both hands are summed.
    """
    bpy.context.view_layer.update()
    wrist = armature.pose.bones[wrist_name].head
    return sum(
        (armature.pose.bones[names[-1]].tail - wrist).length
        for names in chains.values()
    )


def _pose_all(armature, bones: list[str], axis: str, degrees: float) -> None:
    for name in bones:
        _set_rotation(armature.pose.bones[name], axis, degrees)
    bpy.context.view_layer.update()


def _resolve_side_sign(
    armature,
    side: str,
    bones: list[str],
    chains: dict,
    wrist_name: str,
    axis: str,
    angle: float,
    requested: str,
) -> dict:
    if requested in ("1", "+1"):
        return {"sign": 1, "requested": requested, "metric": None}
    if requested in ("-1",):
        return {"sign": -1, "requested": requested, "metric": None}
    if str(requested).lower() in ("mirrored", "mmd"):
        # Mirrored MMD rigs curl inward with opposite local-X signs, so the
        # left and right hands need mirrored values.
        return {
            "sign": -1 if side == "L" else 1,
            "requested": str(requested),
            "metric": None,
        }
    if str(requested).lower() != "auto":
        raise RuntimeError(
            "finger_cleanup.sign must be auto, mirrored, 1 or -1: "
            f"{requested!r}"
        )
    _pose_all(armature, bones, axis, angle)
    positive = _side_metric(armature, chains, wrist_name)
    _pose_all(armature, bones, axis, -angle)
    negative = _side_metric(armature, chains, wrist_name)
    sign = 1 if positive <= negative else -1
    return {
        "sign": sign,
        "requested": "auto",
        "metric": {"positive": positive, "negative": negative},
    }


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
