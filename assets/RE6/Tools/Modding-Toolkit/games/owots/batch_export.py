import bpy
import json
import os
import shutil

from ...core.i18n import T
from ...core.re_mesh_compat import call_re_mesh_op, re_mesh_op_available
from ...core.bone_utils import align_armatures_by_name
from ...core import console_export
from ...core import export_prep


def _get_export_schemes_dir():
    addon_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = os.path.join(addon_dir, "assets", "export_schemes", "owots")
    os.makedirs(d, exist_ok=True)
    return d


def _get_native_skeletons_dir():
    addon_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(addon_dir, "assets", "native_skeletons", "owots")


_scheme_cache = []

def get_schemes_callback(self, context):
    global _scheme_cache
    _scheme_cache = []
    d = _get_export_schemes_dir()
    if os.path.exists(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.json'):
                name = f.replace('.json', '')
                _scheme_cache.append((f, name, ""))
    if not _scheme_cache:
        _scheme_cache.append(('NONE', "No scheme", ""))
    return _scheme_cache


def _load_scheme(filename):
    filepath = os.path.join(_get_export_schemes_dir(), filename)
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def _make_key(character_id, entry_id, suffix):
    key = f"owotsex_{character_id}_{entry_id}_{suffix}"
    return key.replace(" ", "_").replace("(", "").replace(")", "")


def _get_binding(scene, character_id, entry_id, suffix):
    return scene.get(_make_key(character_id, entry_id, suffix), "")


def _set_binding(scene, character_id, entry_id, suffix, value):
    scene[_make_key(character_id, entry_id, suffix)] = value


def _make_en_key(character_id, entry_id, suffix):
    key = f"owotsen_{character_id}_{entry_id}_{suffix}"
    return key.replace(" ", "_").replace("(", "").replace(")", "")


def _get_enabled(scene, character_id, entry_id, suffix):
    return scene.get(_make_en_key(character_id, entry_id, suffix), True)


def _set_enabled(scene, character_id, entry_id, suffix, value):
    scene[_make_en_key(character_id, entry_id, suffix)] = value


# Simplified mode keys
def _get_simplified_group_key(character_id, group_name, suffix):
    key = f"owotssg_{character_id}_{group_name}_{suffix}"
    return key.replace(" ", "_").replace("(", "").replace(")", "").replace("-", "_")


def _get_simplified_group_binding(scene, character_id, group_name, suffix):
    return scene.get(_get_simplified_group_key(character_id, group_name, suffix), "")


def _set_simplified_group_binding(scene, character_id, group_name, suffix, value):
    scene[_get_simplified_group_key(character_id, group_name, suffix)] = value


def _get_simplified_empty_key(character_id, suffix):
    return f"owotsse_{character_id}_{suffix}"


def _get_simplified_empty_binding(scene, character_id, suffix):
    return scene.get(_get_simplified_empty_key(character_id, suffix), "")


def _set_simplified_empty_binding(scene, character_id, suffix, value):
    scene[_get_simplified_empty_key(character_id, suffix)] = value


def get_binding_character_id(scene, scheme):
    """Character whose bindings this export should use."""
    if scene.get("owots_use_default_bindings", False):
        inherited = scheme.get("inherit_bindings_from", "")
        if inherited:
            return inherited
    return scheme["character_id"]


def binding_entry_id(entry_id, scheme_character_id, binding_character_id):
    """Map a scheme entry id onto the character whose bindings are read.

    Entry ids are prefixed with their scheme's character id (``ch001_50_00``),
    so when an outfit inherits another outfit's bindings
    (``inherit_bindings_from`` + ``owots_use_default_bindings``) the raw id
    would address a key that was never written -- the inherited bindings would
    silently resolve to nothing. Rewriting the prefix (``ch001_50_00`` ->
    ``ch001_00_00``) lets the default outfit's per-entry bindings be found.

    A no-op unless an inherited character id is actually in effect.
    """
    if not binding_character_id or binding_character_id == scheme_character_id:
        return entry_id
    if scheme_character_id and entry_id.startswith(scheme_character_id):
        return binding_character_id + entry_id[len(scheme_character_id):]
    return entry_id


def resolve_mesh_mdf2(scene, character_id, group, entry, use_simplified,
                      scheme_character_id=None):
    """``(mesh_col_name, mdf2_col_name)`` this entry currently resolves to
    under the given mode -- "" for a slot with nothing bound (disabled, or
    simplified mode has nothing picked yet). The single place that decides
    "what collection backs this entry's mesh/mdf2 right now", read by both
    the real export loop (``_run_export``) and the pre-export check, so a
    future change to the mode-resolution rules can't make them disagree.

    ``character_id`` is the character whose bindings are read (the inherited
    one when reusing the default outfit); ``scheme_character_id`` is the
    currently selected scheme's own character, needed to remap the entry id.

    sfur/chain2/clsp and the "empty"-scheme blank-copy path are left to the
    export loop: none of them are part of what pre_export_check inspects, and
    a blank copy has no Blender data behind it for either caller to read.
    """
    if use_simplified:
        group_name = group["name"]
        simp = entry.get("simplified", "user")
        if simp == "user":
            return (_get_simplified_group_binding(scene, character_id, group_name, "mesh"),
                    _get_simplified_group_binding(scene, character_id, group_name, "mdf2"))
        elif simp == "empty":
            return (_get_simplified_empty_binding(scene, character_id, "mesh"),
                    _get_simplified_empty_binding(scene, character_id, "mdf2"))
        return "", ""  # "skip", or any future unknown value

    entry_id = binding_entry_id(entry["id"], scheme_character_id, character_id)
    mesh_col = (_get_binding(scene, character_id, entry_id, "mesh")
                if _get_enabled(scene, character_id, entry_id, "mesh") else "")
    mdf2_col = (_get_binding(scene, character_id, entry_id, "mdf2")
                if _get_enabled(scene, character_id, entry_id, "mdf2") else "")
    return mesh_col, mdf2_col


MESH_SETTINGS = {
    "exportAllLODs": True,
    "autoSolveRepeatedUVs": True,
    "preserveSharpEdges": True,
    "rotate90": True,
    "useBlenderMaterialName": False,
    "preserveBoneMatrices": False,
    "exportBoundingBoxes": False,
}


def _do_export_mesh(filepath, collection_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    call_re_mesh_op('exportfile', filepath=filepath, targetCollection=collection_name, **MESH_SETTINGS)

def _do_export_mdf2(filepath, collection_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    panel = getattr(bpy.context.scene, "re_mdf_toolpanel", None)
    previous_game = getattr(panel, "activeGame", None) if panel is not None else None
    try:
        if panel is not None:
            panel.activeGame = "OWOTS"
        bpy.ops.re_mdf.exportfile(filepath=filepath, targetCollection=collection_name)
    finally:
        if panel is not None and previous_game is not None:
            panel.activeGame = previous_game

def _do_export_sfur(filepath, collection_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    bpy.ops.re_sfur.exportfile(filepath=filepath, targetCollection=collection_name)

def _do_export_fbxskel(filepath, armature_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    bpy.ops.re_fbxskel.exportfile(filepath=filepath, targetArmature=armature_name)

def _do_export_chain2(filepath, collection_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    bpy.ops.re_chain2.exportfile(filepath=filepath, targetCollection=collection_name)

def _do_export_clsp(filepath, collection_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    bpy.ops.re_clsp.exportfile(filepath=filepath, targetCollection=collection_name)

def _get_blank_path(filetype, filename=None):
    """Return the path to a built-in blank file.

    Some OWOTS attachment slots need a slot-specific empty mesh instead of the
    generic fallback. ``filename`` lets a scheme select that exact file.
    """
    addon_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    name = filename if filename else f"blank.{filetype}"
    return os.path.join(addon_dir, "assets", "blank_files", "owots", name)


class OWOTS_OT_BatchExport(bpy.types.Operator):
    """OWOTS batch exporter"""
    bl_idname = "owots.batch_export"
    bl_label = "OWOTS Batch Export"
    bl_options = {'REGISTER'}

    def execute(self, context):
        show_console = console_export.get_preferences(context).show_console_on_batch_export
        with console_export.kept_open_for_export(show_console):
            return self._execute_with_triangulation(context)

    def _execute_with_triangulation(self, context):
        # Triangulation rides on the modifier stack: RE Mesh Editor exports
        # evaluated geometry, so the mesh data is never touched and the
        # modifiers come off again even if the export raises.
        settings = context.scene.mhw_suite_settings
        if not settings.owots_triangulate_face:
            return self._run_export(context)
        with export_prep.triangulated_for_export(context.scene.objects, 'owots') as touched:
            if touched:
                print("[OWOTS] triangulating {n} face mesh(es) for export".format(n=len(touched)))
            return self._run_export(context)

    def _run_export(self, context):
        scene = context.scene
        settings = scene.mhw_suite_settings

        if not re_mesh_op_available('exportfile'):
            self.report({'ERROR'}, "RE Mesh Editor not installed")
            return {'CANCELLED'}

        natives_root = scene.get("owots_natives_root", "")
        if not natives_root or not os.path.isdir(natives_root):
            self.report({'ERROR'}, "Set natives root directory first")
            return {'CANCELLED'}

        scheme_file = settings.owots_export_scheme
        if not scheme_file or scheme_file == 'NONE':
            self.report({'ERROR'}, "No export scheme selected")
            return {'CANCELLED'}

        scheme = _load_scheme(scheme_file)
        if not scheme:
            self.report({'ERROR'}, f"Failed to load: {scheme_file}")
            return {'CANCELLED'}

        character_id = scheme["character_id"]
        binding_character_id = get_binding_character_id(scene, scheme)
        base_path = scheme["base_path"].replace("\\", "/")
        use_simplified = scene.get("owots_use_simplified", False)
        use_blank = settings.owots_use_blank_export

        export_count = 0
        fail_count = 0
        skip_count = 0
        
        # Cache for identical exports: (func_name, target) -> exported_filepath
        export_cache = {}

        def make_full(rel, bp_override=None):
            bp = (bp_override or base_path).replace("/", os.sep)
            return os.path.join(natives_root, "natives", bp, rel.replace("/", os.sep))

        def try_export(func, filepath, target, label):
            nonlocal export_count, fail_count, skip_count
            if not target or target == "NONE":
                skip_count += 1
                return
            # Check target exists
            if func == _do_export_fbxskel:
                if target not in bpy.data.objects or bpy.data.objects[target].type != 'ARMATURE':
                    print(f"[OWOTS] SKIP {label}: armature '{target}' not found")
                    skip_count += 1
                    return
            else:
                if target not in bpy.data.collections:
                    print(f"[OWOTS] SKIP {label}: collection '{target}' not found")
                    skip_count += 1
                    return

            cache_key = (func.__name__, target)
            if cache_key in export_cache:
                try:
                    source_filepath = export_cache[cache_key]
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)
                    shutil.copy2(source_filepath, filepath)
                    print(f"[OWOTS] {label}: {target} [CACHED] -> {os.path.basename(filepath)}")
                    export_count += 1
                except Exception as err:
                    print(f"[OWOTS] FAILED {label} [CACHE COPY]: {err}")
                    fail_count += 1
                return

            try:
                print(f"[OWOTS] {label}: {target} -> {os.path.basename(filepath)}")
                func(filepath, target)
                export_cache[cache_key] = filepath
                export_count += 1
            except Exception as err:
                print(f"[OWOTS] FAILED {label}: {err}")
                fail_count += 1

        def try_blank(filetype, filepath, label, filename=None):
            nonlocal export_count, skip_count
            blank_src = _get_blank_path(filetype, filename)
            if os.path.isfile(blank_src):
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                shutil.copy2(blank_src, filepath)
                print(f"[OWOTS] {label}: BLANK -> {os.path.basename(filepath)}")
                export_count += 1
            else:
                print(f"[OWOTS] SKIP blank (file not found): {blank_src}")
                skip_count += 1

        # --- FBXSKEL ---
        fbxskel_path = scheme.get("fbxskel", "")
        if fbxskel_path:
            fbx_enabled = _get_enabled(scene, binding_character_id, "_fbxskel", "fbxskel")
            fbx_arm = _get_binding(scene, binding_character_id, "_fbxskel", "fbxskel")
            if fbx_enabled and fbx_arm:
                full = os.path.join(natives_root, "natives", "stm", fbxskel_path.replace("/", os.sep))
                native_path = os.path.join(_get_native_skeletons_dir(), os.path.basename(fbxskel_path))
                if os.path.isfile(native_path):
                    user_arm_obj = bpy.data.objects.get(fbx_arm)
                    if user_arm_obj is None or user_arm_obj.type != 'ARMATURE':
                        print(f"[OWOTS] SKIP FBXSKEL: armature '{fbx_arm}' not found")
                        skip_count += 1
                    else:
                        prev_active = context.view_layer.objects.active
                        prev_sel = [o for o in context.selected_objects]
                        for o in prev_sel:
                            o.select_set(False)
                        native_copy = None
                        try:
                            bpy.ops.re_fbxskel.importfile(filepath=native_path)
                            native_copy = context.view_layer.objects.active
                            if native_copy is None or native_copy.type != 'ARMATURE':
                                raise RuntimeError(f"Failed to import native skeleton: {native_path}")
                            native_copy.select_set(False)
                            # Align the native armature to the user's armature scale,
                            # then export the aligned copy without adding fake bones.
                            align_armatures_by_name(user_arm_obj, native_copy, mode='FULL')
                            print(f"[OWOTS] FBXSKEL (native alignment): {fbx_arm} -> {os.path.basename(full)}")
                            _do_export_fbxskel(full, native_copy.name)
                            export_count += 1
                        except Exception as err:
                            print(f"[OWOTS] FAILED FBXSKEL (native alignment): {err}")
                            fail_count += 1
                        finally:
                            if native_copy is not None and native_copy.name in bpy.data.objects:
                                bpy.data.objects.remove(native_copy, do_unlink=True)
                            context.view_layer.objects.active = prev_active
                            for o in prev_sel:
                                if o.name in bpy.data.objects:
                                    o.select_set(True)
                else:
                    try_export(_do_export_fbxskel, full, fbx_arm, "FBXSKEL")

        # --- Per entry ---
        for group in scheme["groups"]:
            group_name = group["name"]
            grp_bp = group.get("base_path")  # Optional per-group base_path override
            for entry in group["entries"]:
                entry_id = entry["id"]
                simp = entry.get("simplified", "user")
                simp_sfur = entry.get("simplified_sfur", "")

                if use_simplified:
                    # Determine mesh/mdf2 collection based on simplified rule
                    if simp == "skip":
                        continue
                    elif simp == "user":
                        mesh_col, mdf2_col = resolve_mesh_mdf2(scene, binding_character_id, group, entry, use_simplified)
                    elif simp == "empty":
                        if use_blank:
                            # Use built-in blank files directly for empty entries
                            if entry.get("mesh"):
                                try_blank("mesh", make_full(entry["mesh"], grp_bp), f"MESH {entry_id}",
                                          entry.get("blank_mesh"))
                            if entry.get("mdf2"):
                                for m in entry["mdf2"]:
                                    try_blank("mdf2", make_full(m, grp_bp), f"MDF2 {entry_id}")
                            if entry.get("gpuc"):
                                try_blank("gpuc.260209505", make_full(entry["gpuc"], grp_bp),
                                          f"GPUC {entry_id}", entry.get("blank_gpuc"))
                            if entry.get("sfur"):
                                try_blank("sfur", make_full(entry["sfur"], grp_bp), f"SFUR {entry_id}")
                            if entry.get("chain2"):
                                for c in (entry["chain2"] if isinstance(entry["chain2"], list) else [entry["chain2"]]):
                                    try_blank("chain2", make_full(c, grp_bp), f"CHAIN2 {entry_id}")
                            continue
                        else:
                            mesh_col, mdf2_col = resolve_mesh_mdf2(scene, binding_character_id, group, entry, use_simplified)
                    else:
                        continue

                    # sfur
                    sfur_col = ""
                    rule_sfur = entry.get("simplified_sfur", simp)
                    if rule_sfur == "user" and entry.get("sfur"):
                        sfur_col = _get_simplified_group_binding(scene, binding_character_id, group_name, "sfur")
                    elif rule_sfur == "empty" and entry.get("sfur"):
                        if use_blank:
                            try_blank("sfur", make_full(entry["sfur"], grp_bp), f"SFUR {entry_id}")
                        else:
                            sfur_col = _get_simplified_empty_binding(scene, binding_character_id, "sfur")

                    # chain2
                    chain2_col = ""
                    rule_chain2 = entry.get("simplified_chain2", simp)
                    if rule_chain2 == "user" and entry.get("chain2"):
                        chain2_col = _get_simplified_group_binding(scene, binding_character_id, group_name, "chain2")
                        if chain2_col:
                            for c in (entry["chain2"] if isinstance(entry["chain2"], list) else [entry["chain2"]]):
                                try_export(_do_export_chain2, make_full(c, grp_bp), chain2_col, f"CHAIN2 {entry_id}")
                    elif rule_chain2 == "empty" and entry.get("chain2"):
                        if use_blank:
                            for c in (entry["chain2"] if isinstance(entry["chain2"], list) else [entry["chain2"]]):
                                try_blank("chain2", make_full(c, grp_bp), f"CHAIN2 {entry_id}")
                        else:
                            chain2_col = _get_simplified_empty_binding(scene, binding_character_id, "chain2")
                            if chain2_col:
                                for c in (entry["chain2"] if isinstance(entry["chain2"], list) else [entry["chain2"]]):
                                    try_export(_do_export_chain2, make_full(c, grp_bp), chain2_col, f"CHAIN2 {entry_id}")

                    # clsp
                    clsp_col = ""
                    rule_clsp = entry.get("simplified_clsp", simp)
                    if rule_clsp == "user" and entry.get("clsp"):
                        clsp_col = _get_simplified_group_binding(scene, binding_character_id, group_name, "clsp")
                        if clsp_col:
                            for c in (entry["clsp"] if isinstance(entry["clsp"], list) else [entry["clsp"]]):
                                try_export(_do_export_clsp, make_full(c, grp_bp), clsp_col, f"CLSP {entry_id}")
                    elif rule_clsp == "empty" and entry.get("clsp"):
                        if use_blank:
                            for c in (entry["clsp"] if isinstance(entry["clsp"], list) else [entry["clsp"]]):
                                try_blank("clsp", make_full(c, grp_bp), f"CLSP {entry_id}")
                        else:
                            clsp_col = _get_simplified_empty_binding(scene, binding_character_id, "clsp")
                            if clsp_col:
                                for c in (entry["clsp"] if isinstance(entry["clsp"], list) else [entry["clsp"]]):
                                    try_export(_do_export_clsp, make_full(c, grp_bp), clsp_col, f"CLSP {entry_id}")

                    # Export mesh
                    if entry.get("mesh"):
                        if mesh_col:
                            try_export(_do_export_mesh, make_full(entry["mesh"], grp_bp), mesh_col, f"MESH {entry_id}")
                        elif use_blank:
                            try_blank("mesh", make_full(entry["mesh"], grp_bp), f"MESH {entry_id}",
                                      entry.get("blank_mesh"))

                    # Export mdf2s
                    if entry.get("mdf2"):
                        if mdf2_col:
                            for m in entry["mdf2"]:
                                try_export(_do_export_mdf2, make_full(m, grp_bp), mdf2_col, f"MDF2 {entry_id}")
                        elif use_blank:
                            for m in entry["mdf2"]:
                                try_blank("mdf2", make_full(m, grp_bp), f"MDF2 {entry_id}")

                    if entry.get("gpuc") and use_blank:
                        try_blank("gpuc.260209505", make_full(entry["gpuc"], grp_bp),
                                  f"GPUC {entry_id}", entry.get("blank_gpuc"))

                    # Export sfur (collection-bound; blank case handled above)
                    if entry.get("sfur") and sfur_col:
                        try_export(_do_export_sfur, make_full(entry["sfur"], grp_bp), sfur_col, f"SFUR {entry_id}")
                        
                    if entry.get("chain2") and chain2_col:
                        # This section was previously handling part of simplified export logic, 
                        # but actual export calls for simplified mode should be consolidated.
                        # (Removed redundant placeholder to avoid double export)
                        pass

                else:
                    # Normal mode: use per-entry bindings. When the default
                    # outfit's bindings are inherited, the entry id must be
                    # remapped onto the inherited character (ch001_50_00 ->
                    # ch001_00_00), otherwise every lookup misses.
                    lookup_id = binding_entry_id(entry_id, character_id, binding_character_id)
                    mesh_col, mdf2_col = resolve_mesh_mdf2(
                        scene, binding_character_id, group, entry, use_simplified, character_id)

                    mesh_en = _get_enabled(scene, binding_character_id, lookup_id, "mesh")
                    if entry.get("mesh"):
                        if mesh_en and mesh_col:
                            try_export(_do_export_mesh, make_full(entry["mesh"], grp_bp), mesh_col, f"MESH {entry_id}")
                        elif mesh_en and use_blank:
                            try_blank("mesh", make_full(entry["mesh"], grp_bp), f"MESH {entry_id}",
                                      entry.get("blank_mesh"))

                    mdf2_en = _get_enabled(scene, binding_character_id, lookup_id, "mdf2")
                    if entry.get("mdf2"):
                        if mdf2_en and mdf2_col:
                            for m in entry["mdf2"]:
                                try_export(_do_export_mdf2, make_full(m, grp_bp), mdf2_col, f"MDF2 {entry_id}")
                        elif mdf2_en and use_blank:
                            for m in entry["mdf2"]:
                                try_blank("mdf2", make_full(m, grp_bp), f"MDF2 {entry_id}")

                    sfur_en = _get_enabled(scene, binding_character_id, lookup_id, "sfur")
                    sfur_col = _get_binding(scene, binding_character_id, lookup_id, "sfur")
                    if entry.get("sfur"):
                        if sfur_en and sfur_col:
                            try_export(_do_export_sfur, make_full(entry["sfur"], grp_bp), sfur_col, f"SFUR {entry_id}")
                        elif sfur_en and use_blank:
                            try_blank("sfur", make_full(entry["sfur"], grp_bp), f"SFUR {entry_id}")

                    chain2_en = _get_enabled(scene, binding_character_id, lookup_id, "chain2")
                    chain2_col = _get_binding(scene, binding_character_id, lookup_id, "chain2")
                    if entry.get("chain2"):
                        if chain2_en and chain2_col:
                            for c in (entry["chain2"] if isinstance(entry["chain2"], list) else [entry["chain2"]]):
                                try_export(_do_export_chain2, make_full(c, grp_bp), chain2_col, f"CHAIN2 {entry_id}")
                        elif chain2_en and use_blank:
                            for c in (entry["chain2"] if isinstance(entry["chain2"], list) else [entry["chain2"]]):
                                try_blank("chain2", make_full(c, grp_bp), f"CHAIN2 {entry_id}")

                    clsp_en = _get_enabled(scene, binding_character_id, lookup_id, "clsp")
                    clsp_col = _get_binding(scene, binding_character_id, lookup_id, "clsp")
                    if entry.get("clsp"):
                        if clsp_en and clsp_col:
                            for c in (entry["clsp"] if isinstance(entry["clsp"], list) else [entry["clsp"]]):
                                try_export(_do_export_clsp, make_full(c, grp_bp), clsp_col, f"CLSP {entry_id}")
                        elif clsp_en and use_blank:
                            for c in (entry["clsp"] if isinstance(entry["clsp"], list) else [entry["clsp"]]):
                                try_blank("clsp", make_full(c, grp_bp), f"CLSP {entry_id}")

                    if entry.get("gpuc") and use_blank:
                        try_blank("gpuc.260209505", make_full(entry["gpuc"], grp_bp),
                                  f"GPUC {entry_id}", entry.get("blank_gpuc"))

        if fail_count > 0:
            self.report({'WARNING'}, f"Done: {export_count} exported, {fail_count} failed, {skip_count} skipped")
        else:
            self.report({'INFO'}, f"Done: {export_count} exported, {skip_count} skipped")
        return {'FINISHED'}


class OWOTS_OT_SetNativesRoot(bpy.types.Operator):
    bl_idname = "owots.set_natives_root"
    bl_label = "Set Natives Root"
    bl_options = {'REGISTER'}

    @classmethod
    def description(cls, context, properties):
        return T("owots.batch_export.set_natives_root_desc")

    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    def execute(self, context):
        path = self.directory.rstrip("/\\")
        if os.path.basename(path).lower() == "natives":
            path = os.path.dirname(path)
        context.scene["owots_natives_root"] = path
        self.report({'INFO'}, f"OWOTS Mod root: {path}")
        return {'FINISHED'}


classes = [
    OWOTS_OT_BatchExport,
    OWOTS_OT_SetNativesRoot,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
