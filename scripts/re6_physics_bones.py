"""Prepare RE6 physics bones: cleanup, id assignment, and renaming.

RE6 stores bones as a function id plus a 256 entry remap table. The base
skeleton keeps the ids of the retail model; physics bones must move into the
two id ranges the game leaves free so chains and effects can address them.
This module applies the shared Stage 8A cleanup rules and then renames the
survivors to ``RE6Bone_<id>`` using the physics ranges 213-249 and 135-189.
"""

from __future__ import annotations

import bpy


PHYSICS_ID_RANGES = ((213, 249), (135, 189))
MAX_PHYSICS_BONES = sum(high - low + 1 for low, high in PHYSICS_ID_RANGES)


def physics_id_pool() -> list[int]:
    """Physics bone ids in assignment order (213..249, then 135..189)."""
    pool = []
    for low, high in PHYSICS_ID_RANGES:
        pool.extend(range(low, high + 1))
    return pool


def positive_weight_bones(mesh_objects) -> set[str]:
    """Names of vertex groups with at least one positive weight."""
    weighted = set()
    for obj in mesh_objects:
        names = {group.index: group.name for group in obj.vertex_groups}
        for vertex in obj.data.vertices:
            for assignment in vertex.groups:
                if assignment.weight > 0.0:
                    name = names.get(assignment.group)
                    if name:
                        weighted.add(name)
    return weighted


def classify_bones(armature, base_bones: set[str]) -> dict:
    """Split armature bones into base bones and physics bones."""
    base = []
    physics = []
    for bone in armature.data.bones:
        (base if bone.name in base_bones else physics).append(bone.name)
    return {
        "base_bones": sorted(base),
        "physics_bones": sorted(physics),
        "base_count": len(base),
        "physics_count": len(physics),
    }


def _reparent_children_to_nearest_survivor(armature, doomed: set[str]) -> dict:
    """Reparent children of doomed bones to the nearest surviving ancestor."""
    applied = {}
    if not doomed:
        return applied
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit_bones = armature.data.edit_bones

        def survivor_of(bone):
            parent = bone.parent
            while parent is not None and parent.name in doomed:
                parent = parent.parent
            return parent

        for name in sorted(doomed):
            bone = edit_bones.get(name)
            if bone is None:
                continue
            target = survivor_of(bone)
            for child in list(bone.children):
                head = child.head.copy()
                tail = child.tail.copy()
                roll = child.roll
                child.parent = target
                child.use_connect = False
                child.head = head
                child.tail = tail
                child.roll = roll
                applied[child.name] = (
                    target.name if target is not None else None
                )
        for name in sorted(doomed):
            bone = edit_bones.get(name)
            if bone is not None:
                edit_bones.remove(bone)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if active is not None:
            bpy.context.view_layer.objects.active = active
    return applied


def cleanup_physics_bones(armature, mesh_objects, base_bones: set[str]) -> dict:
    """Apply the shared Stage 8A cleanup rules to the physics bones.

    Deletes physics bones without positive weight, keeping an unweighted bone
    only when its parent is weighted and has no other children. Never deletes
    a base bone, a weighted physics bone, or that preserved exception.
    """
    weighted = positive_weight_bones(mesh_objects)
    physics = classify_bones(armature, base_bones)["physics_bones"]
    preserved = []
    doomed = set()
    for name in physics:
        if name in weighted:
            continue
        bone = armature.data.bones.get(name)
        if bone is None:
            continue
        parent = bone.parent
        if (
            parent is not None
            and parent.name in weighted
            and len(parent.children) == 1
        ):
            preserved.append({"bone": name, "weighted_parent": parent.name})
            continue
        doomed.add(name)
    reparented = _reparent_children_to_nearest_survivor(armature, doomed)
    return {
        "deleted_bones": sorted(doomed),
        "deleted_count": len(doomed),
        "preserved_unweighted": preserved,
        "reparented_children": reparented,
        "weighted_physics_bones": sorted(weighted & set(physics)),
    }


def default_simplification_plan(base_bones: set[str]) -> dict:
    """Fallback plan: every physics bone is a simplification candidate."""
    return {
        "source": "auto_default",
        "protected_bones": sorted(base_bones),
        "exclusions": [],
        "max_physics_bones": MAX_PHYSICS_BONES,
    }


def chain_category(name: str, categories: dict) -> str | None:
    """Semantic category of a physics bone, from the profile's token table."""
    for category, tokens in categories.items():
        if any(token and token in name for token in tokens):
            return category
    return None


def select_kept_physics_bones(
    armature,
    base_bones: set[str],
    chain_config: dict,
    plan: dict | None = None,
) -> dict:
    """Split physics bones into kept chains and unchained bones.

    Every semantic category is kept by default so the chain budget is used as
    far as it goes. Only helper bones and the categories the caller decides to
    drop are merged into their parents.
    """
    categories = chain_config.get("categories") or {}
    priority = list(chain_config.get("priority") or ())
    excluded = list(chain_config.get("excluded") or ())
    plan = plan or {}
    excluded.extend(plan.get("exclusions") or [])
    drop_categories = set(plan.get("drop_categories") or ())
    keep_categories = set(plan.get("keep_categories") or ())
    physics = classify_bones(armature, base_bones)["physics_bones"]
    kept = []
    dropped = []
    by_category = {}
    for name in physics:
        if any(token in name for token in excluded):
            dropped.append(name)
            continue
        category = chain_category(name, categories)
        if category in drop_categories:
            dropped.append(name)
            continue
        if category is not None and (
            not keep_categories or category in keep_categories
        ):
            kept.append(name)
            by_category.setdefault(category, []).append(name)
        else:
            dropped.append(name)
    return {
        "priority": priority,
        "kept_bones": kept,
        "kept_by_category": by_category,
        "kept_category_counts": {
            key: len(value) for key, value in sorted(by_category.items())
        },
        "unchained_bones": dropped,
        "unchained_count": len(dropped),
    }


def merge_unchained_physics_bones(
    armature,
    mesh_objects,
    base_bones: set[str],
    chain_config: dict,
    plan: dict | None = None,
) -> dict:
    """Merge physics bones outside the kept categories into their parent."""
    selection = select_kept_physics_bones(
        armature, base_bones, chain_config, plan
    )
    pairs = []
    orphans = []
    for name in selection["unchained_bones"]:
        bone = armature.data.bones.get(name)
        if bone is None:
            continue
        parent = bone.parent
        if parent is None:
            orphans.append(name)
            continue
        pairs.append((parent.name, name))
    merged = {}
    if pairs:
        weight_utils = _toolkit_module("weight_utils")
        before = {bone.name for bone in armature.data.bones}
        weight_utils.merge_weights_and_delete_bones(armature, pairs)
        after = {bone.name for bone in armature.data.bones}
        merged = {
            child: parent for parent, child in pairs if child in before - after
        }
    return {
        "kept": selection,
        "merge_pair_count": len(pairs),
        "merged": merged,
        "merged_count": len(merged),
        "orphan_bones": sorted(orphans),
        "remaining_physics_bones": classify_bones(
            armature, base_bones
        )["physics_count"],
    }


def _toolkit_module(name: str):
    import importlib

    import source_rig_utils

    source_rig_utils.enable_modding_toolkit()
    return importlib.import_module(f"Modding-Toolkit.core.{name}")


def _simplify_pass(armature, names) -> dict:
    """Select the given bones and run Modding Toolkit's chain simplification."""
    wanted = set(names)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = armature
    before = {bone.name for bone in armature.data.bones}
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        for bone in armature.data.edit_bones:
            selected = bone.name in wanted
            bone.select = selected
            bone.select_head = selected
            bone.select_tail = selected
        result = bpy.ops.mhw.general_tools(action="SIMPLIFY_CHAIN")
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if active is not None:
            bpy.context.view_layer.objects.active = active
    after = {bone.name for bone in armature.data.bones}
    return {
        "result": sorted(result),
        "selected_count": len(wanted),
        "deleted_bones": sorted(before - after),
        "deleted_count": len(before - after),
    }


def simplify_physics_chains(
    armature,
    base_bones: set[str],
    plan: dict,
    chain_config: dict | None = None,
    target: int = MAX_PHYSICS_BONES,
) -> dict:
    """Reduce long physics chains until the physics id pool fits.

    Each pass selects the planned, unprotected physics bones and lets Modding
    Toolkit merge every other bone of each chain into its pair. The loop stops
    when the count fits, when a pass deletes nothing, or when no candidate is
    left, so a failure to converge is reported instead of looping forever.
    """
    protected = set(plan.get("protected_bones") or ()) | set(base_bones)
    exclusions = list(plan.get("exclusions") or [])
    chain_config = chain_config or {}
    categories = chain_config.get("categories") or {}
    priority = list(chain_config.get("priority") or ())
    passes = []
    dropped_categories = []
    stalled = False
    while True:
        physics = classify_bones(armature, base_bones)["physics_bones"]
        if len(physics) <= target:
            break
        candidates = [
            name
            for name in physics
            if name not in protected
            and not any(token in name for token in exclusions)
        ]
        if len(candidates) >= 2:
            outcome = _simplify_pass(armature, candidates)
            passes.append(outcome)
            if outcome["deleted_count"]:
                continue
        # Simplification cannot reduce further. Give up the lowest priority
        # category that still has bones, merging it into the parents, so the
        # remaining chains keep the budget the caller cares about most.
        counts = {}
        for name in physics:
            category = chain_category(name, categories)
            if category is not None:
                counts[category] = counts.get(category, 0) + 1
        # Sacrifice the non-priority categories first (largest first, to free
        # the most slots), then the priority ones from the lowest up.
        non_priority = sorted(
            (name for name in counts if name not in priority),
            key=lambda name: (-counts[name], name),
        )
        drop_order = non_priority + [
            name for name in reversed(priority) if name in counts
        ]
        if not drop_order:
            stalled = True
            break
        dropped_categories.append(drop_order[0])
        outcome = merge_unchained_physics_bones(
            armature,
            [],
            base_bones,
            chain_config,
            {"drop_categories": dropped_categories},
        )
        if not outcome["merged_count"]:
            stalled = True
            break
    remaining = classify_bones(armature, base_bones)["physics_count"]
    return {
        "plan_source": plan.get("source"),
        "target": target,
        "passes": passes,
        "pass_count": len(passes),
        "dropped_categories": dropped_categories,
        "remaining_physics_bones": remaining,
        "within_pool": remaining <= target,
        "stalled": stalled,
    }


def assign_physics_ids(
    armature,
    base_bones: set[str],
    mesh_objects,
    reserved_ids: set[int] | None = None,
) -> dict:
    """Rename surviving physics bones and their vertex groups to RE6Bone_<id>."""
    reserved = set(reserved_ids or ())
    physics = classify_bones(armature, base_bones)["physics_bones"]
    pool = [value for value in physics_id_pool() if value not in reserved]
    if len(physics) > MAX_PHYSICS_BONES:
        raise RuntimeError(
            "Physics bone count exceeds the RE6 physics id pool: "
            f"{len(physics)} > {MAX_PHYSICS_BONES}. Simplify the numbered "
            "physics chains before assigning ids."
        )
    assigned = {}
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit_bones = armature.data.edit_bones
        for name, bone_id in zip(physics, pool):
            bone = edit_bones.get(name)
            if bone is None:
                continue
            assigned[name] = bone_id
            bone.name = f"RE6Bone_{bone_id:03d}"
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if active is not None:
            bpy.context.view_layer.objects.active = active
    # Renaming a bone does not rename the matching vertex group, so the skin
    # weights would lose their bone. Keep the two in sync.
    for obj in mesh_objects:
        for old_name, bone_id in assigned.items():
            group = obj.vertex_groups.get(old_name)
            if group is not None:
                group.name = f"RE6Bone_{bone_id:03d}"
    return {
        "assigned": assigned,
        "renamed": {
            old: f"RE6Bone_{bone_id:03d}"
            for old, bone_id in assigned.items()
        },
        "assigned_count": len(assigned),
        "pool_size": MAX_PHYSICS_BONES,
        "ranges": [list(item) for item in PHYSICS_ID_RANGES],
    }
