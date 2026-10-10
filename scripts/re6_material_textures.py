"""Find role-specific texture images on prepared RE6 materials."""

from __future__ import annotations

import os
from pathlib import Path


NORMAL_SUFFIXES = ("_NM", "_N", "_NORMAL", "_NRM")


def _image_from_socket(socket, seen: set[int]):
    if socket is None or not socket.is_linked:
        return None
    node = socket.links[0].from_node
    marker = id(node)
    if marker in seen:
        return None
    seen.add(marker)
    if node.type == "TEX_IMAGE" and node.image is not None:
        return node.image
    for input_socket in node.inputs:
        image = _image_from_socket(input_socket, seen)
        if image is not None:
            return image
    return None


def find_normal_image(material):
    """Return the image connected to Normal, or a node named like a normal map."""
    if material is None or not material.use_nodes:
        return None
    tree = material.node_tree
    for node in tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Normal")
        image = _image_from_socket(socket, set())
        if image is not None:
            return image
    for node in tree.nodes:
        if node.type != "NORMAL_MAP":
            continue
        image = _image_from_socket(node.inputs.get("Color"), set())
        if image is not None:
            return image
    for node in tree.nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        stem = os.path.splitext(os.path.basename(node.image.filepath or node.image.name))[0]
        if stem.upper().endswith(NORMAL_SUFFIXES):
            return node.image
    return None


def preserved_template_bindings(
    mrl_core,
    template_path: Path,
    material_hash: str,
    slots: list[str],
) -> dict[int, str]:
    """Texture bindings inherited unchanged from one vanilla MRL material."""
    template = mrl_core.parse(template_path.read_bytes())
    material = template.find(int(material_hash, 16))
    if material is None:
        raise RuntimeError(
            f"Template material {material_hash} is missing from "
            f"{template_path}."
        )
    available = dict(mrl_core.bindings(template, material))
    result = {}
    for value in slots:
        slot = int(value, 16)
        path = available.get(slot)
        if not path:
            raise RuntimeError(
                f"Template slot {value} has no texture in "
                f"{template_path}."
            )
        result[slot] = path
    return result
