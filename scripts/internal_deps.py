"""Load the Blender add-ons vendored inside the skill."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
ADDON_PATHS = {
    "mmd_tools": "assets/RE6/Tools/mmd_tools",
    "Modding-Toolkit": "assets/RE6/Tools/Modding-Toolkit",
    "re6_model_editor": "assets/RE6/Tools/re6_model_editor-0.3.0",
}
_REGISTERED: set[str] = set()


def addon_path(module_name: str) -> Path:
    relative = ADDON_PATHS[module_name]
    path = (SKILL_ROOT / relative).resolve()
    if not path.is_dir():
        raise FileNotFoundError(
            f"Internal add-on {module_name} is missing: {path}"
        )
    return path


def _unload_module_tree(module_name: str) -> None:
    module = sys.modules.get(module_name)
    if module is not None and hasattr(module, "unregister"):
        try:
            module.unregister()
        except Exception:  # noqa: BLE001 - another module may already own it
            pass
    for key in sorted(
        (
            key
            for key in sys.modules
            if key == module_name or key.startswith(module_name + ".")
        ),
        reverse=True,
    ):
        sys.modules.pop(key, None)


def ensure_addon(module_name: str):
    """Return a registered add-on module loaded from the skill directory."""
    path = addon_path(module_name)
    init_file = path / "__init__.py"
    if not init_file.is_file():
        raise FileNotFoundError(
            f"Internal add-on entry point is missing: {init_file}"
        )

    current = sys.modules.get(module_name)
    current_file = (
        Path(current.__file__).resolve()
        if current is not None and getattr(current, "__file__", None)
        else None
    )
    if current is not None and current_file != init_file:
        _unload_module_tree(module_name)
        current = None

    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    if current is None:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_file,
            submodule_search_locations=[str(path)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Could not create a module spec for {module_name}: {path}"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        module = current

    if module_name not in _REGISTERED:
        register = getattr(module, "register", None)
        if register is None:
            raise RuntimeError(
                f"Internal add-on {module_name} has no register()."
            )
        register()
        _REGISTERED.add(module_name)
    return module


def ensure_mmd_tools():
    return ensure_addon("mmd_tools")


def ensure_modding_toolkit():
    return ensure_addon("Modding-Toolkit")


def ensure_re6_model_editor():
    module = ensure_addon("re6_model_editor")
    prefs = importlib.import_module("re6_model_editor.blender.prefs")
    preferences = prefs.get()
    if preferences is not None and hasattr(preferences, "showConsole"):
        preferences.showConsole = False
    return module
