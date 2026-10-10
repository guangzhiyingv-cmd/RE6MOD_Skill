#!/usr/bin/env python3
"""Check a target profile against the local RE Engine modding environment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


SOURCE_ADAPTERS = {
    "pmx": "mmd",
    "pmd": "mmd",
    "fbx": "fbx",
    "glb": "gltf",
    "gltf": "gltf",
    "vrm": "vrm",
    "blend": "blender",
    "obj": "obj",
}


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", type=Path, default=skill_root)
    parser.add_argument(
        "--profile",
        type=Path,
        default=skill_root / "profiles" / "re6-pl0600.json",
    )
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument(
        "--source-adapter",
        choices=("auto",) + tuple(sorted(set(SOURCE_ADAPTERS.values()))),
        default="auto",
    )
    return parser.parse_args()


def load_profile(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_source_adapter(
    requested_adapter: str,
    profile: dict,
    project_path: Path | None,
) -> str:
    if requested_adapter != "auto":
        return requested_adapter
    if project_path:
        project = json.loads(
            project_path.resolve().read_text(encoding="utf-8")
        )
        source = project.get("source", {})
        adapter = source.get("adapter")
        if adapter:
            return str(adapter)
        source_format = str(source.get("format") or "").lower()
        if source_format in SOURCE_ADAPTERS:
            return SOURCE_ADAPTERS[source_format]
    source_format = str(profile.get("source_format") or "").lower()
    return SOURCE_ADAPTERS.get(source_format, "mmd")


def blender_version(blender_path: Path) -> dict[str, object]:
    if not blender_path.is_file():
        return {"ok": False, "output": "Blender executable not found"}
    try:
        result = subprocess.run(
            [str(blender_path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "output": str(error)}
    output = (result.stdout or result.stderr).strip()
    first_line = output.splitlines()[0] if output else ""
    version_text = first_line.removeprefix("Blender ").strip()
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "version": version_text,
        "output": output,
    }


INTERNAL_ADDONS = {
    "mmd_tools": "assets/RE6/Tools/mmd_tools",
    "Modding-Toolkit": "assets/RE6/Tools/Modding-Toolkit",
    "re6_model_editor": "assets/RE6/Tools/re6_model_editor-0.3.0",
}


def locate_addon(skill_root: Path, name: str) -> str | None:
    relative = INTERNAL_ADDONS.get(name)
    if relative is None:
        return None
    candidate = skill_root / relative
    return str(candidate) if candidate.is_dir() else None


def main() -> int:
    args = parse_args()
    skill_root = args.skill_root.resolve()
    profile_path = args.profile.resolve()
    profile = load_profile(profile_path)
    project_path = args.project.resolve() if args.project else None
    source_adapter = resolve_source_adapter(
        args.source_adapter,
        profile,
        project_path,
    )

    blender_version_required = str(profile["blender"]["version"])
    configured_blender = (
        args.blender
        or os.environ.get("BLENDER_EXECUTABLE")
        or profile["blender"].get("executable")
    )
    if configured_blender:
        blender_path = Path(configured_blender).resolve()
    else:
        discovered = shutil.which("blender")
        blender_path = (
            Path(discovered).resolve()
            if discovered
            else Path("blender").resolve()
        )
    version = blender_version(blender_path)
    version_matches = str(version.get("version", "")).startswith(
        blender_version_required + "."
    )

    required_addon_names = [
        name
        for name in profile.get("addons", [])
        if not (name == "mmd_tools" and source_adapter != "mmd")
    ]
    addons = {
        name: locate_addon(skill_root, name)
        for name in required_addon_names
    }
    skipped_addons = {
        name: f"not required for source adapter {source_adapter!r}"
        for name in profile.get("addons", [])
        if name not in required_addon_names
    }
    assets = {
        relative: (
            (skill_root / relative).is_file()
            or (skill_root / relative).is_dir()
        )
        for relative in profile.get("required_assets", [])
    }
    profile_paths = {
        relative: (
            (skill_root / relative).is_file()
            or (skill_root / relative).is_dir()
        )
        for relative in profile.get("reference_assets", {}).values()
    }

    report = {
        "profile": str(profile_path),
        "profile_id": profile.get("profile_id"),
        "project": str(project_path) if project_path else None,
        "source_adapter": source_adapter,
        "skill_root": str(skill_root),
        "blender": str(blender_path),
        "blender_version_required": blender_version_required,
        "blender_version": version,
        "internal_addon_paths": {
            name: str((skill_root / relative).resolve())
            for name, relative in INTERNAL_ADDONS.items()
        },
        "addons": addons,
        "skipped_addons": skipped_addons,
        "required_assets": assets,
        "profile_paths": profile_paths,
        "ok": (
            version["ok"]
            and version_matches
            and all(addons.values())
            and all(assets.values())
            and all(profile_paths.values())
        ),
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
