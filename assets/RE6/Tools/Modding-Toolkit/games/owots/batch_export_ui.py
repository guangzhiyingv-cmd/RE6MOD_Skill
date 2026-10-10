import bpy
import os
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from ...core.i18n import T
from ...core import pre_export_check_ops as pec
from .batch_export import (
    _load_scheme, _get_binding, _set_binding,
    _get_enabled, _set_enabled, get_schemes_callback,
    _get_simplified_group_binding, _set_simplified_group_binding,
    _set_simplified_empty_binding,
    _get_native_skeletons_dir,
    get_binding_character_id,
    binding_entry_id,
    resolve_mesh_mdf2,
)

EXPORTER_WINDOW_WIDTH = 600


def _get_filtered_collections(suffix):
    result = []
    type_map = {"mesh": "RE_MESH_COLLECTION", "mdf2": "RE_MDF_COLLECTION", "sfur": "RE_SFUR_COLLECTION", "chain2": "RE_CHAIN_COLLECTION"}
    name_sfx_map = {"mesh": ".mesh", "mdf2": ".mdf2", "sfur": ".sfur", "chain2": ".chain", "clsp": ".clsp"}
    target_type = type_map.get(suffix, "")
    name_sfx = name_sfx_map.get(suffix, "")
    for c in bpy.data.collections:
        col_type = c.get("~TYPE", "")
        # Match by type
        if target_type and col_type == target_type:
            icon = f"COLLECTION_{c.color_tag}" if c.color_tag != "NONE" else "OUTLINER_COLLECTION"
            result.append((c.name, c.name, "", icon, len(result)))
            continue
        # Match by extension
        if name_sfx and (c.name.endswith(name_sfx) or f"{name_sfx}." in c.name):
            result.append((c.name, c.name, "", "OUTLINER_COLLECTION", len(result)))
    if not result:
        result.append(("NONE", "No matching collections", "", "ERROR", 0))
    return result


def _get_armatures():
    result = []
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            result.append((obj.name, obj.name, "", 'ARMATURE_DATA', len(result)))
    if not result:
        result.append(("NONE", "No armatures", "", "ERROR", 0))
    return result


# ============================================================
# Toggle operators
# ============================================================

class OWOTS_OT_ToggleEntry(bpy.types.Operator):
    bl_idname = "owots.toggle_entry"
    bl_label = "Toggle"
    bl_options = {'INTERNAL'}
    character_id: bpy.props.StringProperty()
    entry_id: bpy.props.StringProperty()
    suffix: bpy.props.StringProperty()
    def execute(self, context):
        current = _get_enabled(context.scene, self.character_id, self.entry_id, self.suffix)
        _set_enabled(context.scene, self.character_id, self.entry_id, self.suffix, not current)
        return {'FINISHED'}


class OWOTS_OT_ToggleSimplified(bpy.types.Operator):
    bl_idname = "owots.toggle_simplified"
    bl_label = "Toggle Simplified"
    bl_options = {'INTERNAL'}
    def execute(self, context):
        context.scene["owots_use_simplified"] = not context.scene.get("owots_use_simplified", False)
        return {'FINISHED'}


class OWOTS_OT_ToggleDefaultBindings(bpy.types.Operator):
    bl_idname = "owots.toggle_default_bindings"
    bl_label = "Toggle Default Outfit Bindings"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        current = context.scene.get("owots_use_default_bindings", False)
        context.scene["owots_use_default_bindings"] = not current
        return {'FINISHED'}


# ── Unified collection picker ─────────────────────────────────────────────────
# One operator for what used to be 15: 5 slots x 3 scopes, each a verbatim
# copy of this body differing only in a suffix string. The scope/slot pair now
# arrives as operator properties -- the same shape mhwi/mhws/mhrs have always
# used (see MHWS_OT_PickCollection's `filetype`), which is also why the dynamic
# `items=` callback reading self.slot is safe: those three already ship it.
#
# 'EMPTY' has no call site. The per-entry "empty" bindings are only ever cleared
# (owots.clear_se), never picked, so the 5 OWOTS_OT_PickSimplifiedEmpty*
# classes it replaces were unreachable. The scope is kept because the capability
# behind it exists -- wiring a button is now one line rather than a new class.

class OWOTS_OT_PickBinding(bpy.types.Operator):
    bl_idname = "owots.pick_binding"
    bl_label = "Pick Collection"
    bl_options = {'INTERNAL'}
    bl_property = "collection_name"

    #: 'ENTRY' | 'GROUP' | 'EMPTY'. A plain string, not an enum, because
    #: bl_property already designates collection_name as the searched enum and
    #: no operator in this addon has been proven to work with a second one.
    scope: bpy.props.StringProperty(default="ENTRY")
    slot: bpy.props.StringProperty()
    character_id: bpy.props.StringProperty()
    entry_id: bpy.props.StringProperty()
    group_name: bpy.props.StringProperty()
    collection_name: bpy.props.EnumProperty(
        name="Collection",
        items=lambda self, ctx: _get_filtered_collections(self.slot)
    )

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.collection_name != "NONE":
            scene = context.scene
            if self.scope == "GROUP":
                _set_simplified_group_binding(scene, self.character_id,
                                              self.group_name, self.slot,
                                              self.collection_name)
            elif self.scope == "EMPTY":
                _set_simplified_empty_binding(scene, self.character_id, self.slot,
                                              self.collection_name)
            elif self.scope == "ENTRY":
                _set_binding(scene, self.character_id, self.entry_id, self.slot,
                             self.collection_name)
            else:
                # Silently doing nothing is how a typo'd scope would hide.
                self.report({'ERROR'}, f"unknown pick scope {self.scope!r}")
                return {'CANCELLED'}
        return {'FINISHED'}


class OWOTS_OT_PickArmature(bpy.types.Operator):
    bl_idname = "owots.pick_armature"
    bl_label = "Pick Armature"
    bl_options = {'INTERNAL'}
    bl_property = "armature_name"
    character_id: bpy.props.StringProperty()
    armature_name: bpy.props.EnumProperty(name="Armature",
        items=lambda self, ctx: _get_armatures())
    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}
    def execute(self, context):
        if self.armature_name != "NONE":
            _set_binding(context.scene, self.character_id, "_fbxskel", "fbxskel", self.armature_name)
        return {'FINISHED'}


    
class OWOTS_OT_ClearSimplifiedGroup(bpy.types.Operator):
    bl_idname = "owots.clear_sg"
    bl_label = "Clear Group Binding"
    bl_options = {'INTERNAL'}
    character_id: bpy.props.StringProperty()
    group_name: bpy.props.StringProperty()
    suffix: bpy.props.StringProperty()
    def execute(self, context):
        _set_simplified_group_binding(context.scene, self.character_id, self.group_name, self.suffix, "")
        return {'FINISHED'}

    
class OWOTS_OT_ClearSimplifiedEmpty(bpy.types.Operator):
    bl_idname = "owots.clear_se"
    bl_label = "Clear Empty Binding"
    bl_options = {'INTERNAL'}
    character_id: bpy.props.StringProperty()
    suffix: bpy.props.StringProperty()
    def execute(self, context):
        _set_simplified_empty_binding(context.scene, self.character_id, self.suffix, "")
        return {'FINISHED'}


# ── Copy/paste a group's bindings ───────────────────────────────────────────
# The master-detail layout makes this a natural fit: many groups share the
# same collections (costume variants, LOD-adjacent parts), and re-picking
# each one by hand is exactly the busywork this addon already avoids
# elsewhere. Module-level, not Scene data -- it is per-session clipboard
# state, not part of the .blend, same reasoning as pre_export_check_ops's
# _LAST_RUN.
_group_clipboard = {}

_GROUP_SLOTS = ("mesh", "mdf2", "sfur", "chain2", "clsp")


def _group_supported_slots(scheme, group_name):
    """Which of the 5 slots a group actually shows a picker for, mirroring
    _draw_group_detail_simplified's own gating -- needed again here so paste
    can skip a slot the target group has no picker for at all."""
    group = next((g for g in scheme["groups"] if g["name"] == group_name), None) if scheme else None
    if group is None:
        return {}
    entries = group["entries"]
    if not any(e.get("simplified") == "user" for e in entries):
        return {}
    return {
        "mesh": True,
        "mdf2": True,
        "sfur": any(e.get("sfur") and e.get("simplified_sfur", e.get("simplified", "user")) != "skip"
                   for e in entries),
        "chain2": any(e.get("chain2") and e.get("simplified_chain2", e.get("simplified", "user")) != "skip"
                     for e in entries),
        "clsp": any(e.get("clsp") and e.get("simplified_clsp", e.get("simplified", "user")) != "skip"
                   for e in entries),
    }


class OWOTS_OT_CopyGroupBindings(bpy.types.Operator):
    bl_idname = "owots.copy_group_bindings"
    bl_label = "Copy Group Bindings"
    bl_options = {'INTERNAL'}
    character_id: bpy.props.StringProperty()
    group_name: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return T("owots.batch_export_ui.copy_bindings_desc")

    def execute(self, context):
        global _group_clipboard
        scene = context.scene
        clip = {}
        for slot in _GROUP_SLOTS:
            cur = _get_simplified_group_binding(scene, self.character_id, self.group_name, slot)
            if cur:
                clip[slot] = cur
        _group_clipboard = clip
        if clip:
            self.report({'INFO'}, T("owots.batch_export_ui.copy_done").format(
                n=len(clip), group=self.group_name))
        else:
            self.report({'WARNING'}, T("owots.batch_export_ui.copy_nothing").format(
                group=self.group_name))
        return {'FINISHED'}


class OWOTS_OT_PasteGroupBindings(bpy.types.Operator):
    bl_idname = "owots.paste_group_bindings"
    bl_label = "Paste Group Bindings"
    bl_options = {'INTERNAL', 'UNDO'}
    character_id: bpy.props.StringProperty()
    group_name: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return T("owots.batch_export_ui.paste_bindings_desc")

    @classmethod
    def poll(cls, context):
        return bool(_group_clipboard)

    def execute(self, context):
        scene = context.scene
        settings = scene.mhw_suite_settings
        scheme = _load_scheme(settings.owots_export_scheme)
        supported = _group_supported_slots(scheme, self.group_name)

        applied = 0
        for slot, value in _group_clipboard.items():
            if not supported.get(slot):
                continue
            _set_simplified_group_binding(scene, self.character_id, self.group_name, slot, value)
            applied += 1

        if applied:
            self.report({'INFO'}, T("owots.batch_export_ui.paste_done").format(
                n=applied, group=self.group_name))
        else:
            self.report({'WARNING'}, T("owots.batch_export_ui.paste_nothing").format(
                group=self.group_name))
        return {'FINISHED'}


class OWOTS_OT_ClearNormalBinding(bpy.types.Operator):
    bl_idname = "owots.clear_normal_binding"
    bl_label = "Clear"
    bl_options = {'INTERNAL'}
    character_id: bpy.props.StringProperty()
    entry_id: bpy.props.StringProperty()
    suffix: bpy.props.StringProperty()
    def execute(self, context):
        _set_binding(context.scene, self.character_id, self.entry_id, self.suffix, "")
        return {'FINISHED'}


class OWOTS_OT_ClearAllBindings(bpy.types.Operator):
    bl_idname = "owots.clear_all_bindings"
    bl_label = "Clear All Currently Selected"
    bl_description = "Clear all mesh/mdf/sfur/chain2 bindings for this scheme"
    bl_options = {'REGISTER', 'UNDO'}
    scheme_file: bpy.props.StringProperty()
    def execute(self, context):
        scheme = _load_scheme(self.scheme_file)
        if not scheme: return {'CANCELLED'}
        char_id = scheme["character_id"]
        scene = context.scene

        # Clear FBXSKEL
        _set_binding(scene, char_id, "_fbxskel", "fbxskel", "")

        for group in scheme["groups"]:
            group_name = group["name"]
            # Clear Simplified Group Bindings
            for sfx in ["mesh", "mdf2", "sfur", "chain2", "clsp"]:
                _set_simplified_group_binding(scene, char_id, group_name, sfx, "")
            
            # Clear Normal Entry Bindings
            for entry in group["entries"]:
                eid = entry["id"]
                for sfx in ["mesh", "mdf2", "sfur", "chain2", "clsp"]:
                    _set_binding(scene, char_id, eid, sfx, "")
        
        # Clear Simplified Empty Bindings
        for sfx in ["mesh", "mdf2", "sfur", "chain2", "clsp"]:
            _set_simplified_empty_binding(scene, char_id, sfx, "")

        self.report({'INFO'}, "All bindings for this scheme cleared")
        return {'FINISHED'}


# ============================================================
# Group list -- left column of the master-detail layout
# ============================================================
# A scheme can run to over a dozen groups and 50+ entries (Leon.json is
# 13/55); drawing every group's entries inline, expanded or not, is what made
# this dialog outgrow the screen. Only the selected group's detail is drawn
# now, so the popup's height is bounded by its biggest single group instead of
# the whole scheme.

class OWOTS_GroupListItem(bpy.types.PropertyGroup):
    group_name: StringProperty()
    entry_count: IntProperty()
    #: Set from the aggregated pre-export check right before this list draws,
    #: so a group with a problem is visible without opening the report.
    has_issues: BoolProperty()


class OWOTS_UL_Groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        layout.label(text=f"{item.group_name} ({item.entry_count})",
                     icon='ERROR' if item.has_issues else 'FILE_FOLDER')


def _gather_check_pairs(scene, character_id, scheme, use_simplified):
    """``[(label, mdf_col, mesh_col), ...]`` for whatever this batch would
    actually export -- reads through the same ``resolve_mesh_mdf2`` the real
    export loop uses, so an entry that resolves to nothing there (skipped,
    disabled, or a blank-export fallback) contributes nothing to check either.

    Simplified mode checks once per group (the group-level binding covers
    every "user" entry in it together); normal mode checks once per entry.
    """
    pairs = []
    for group in scheme["groups"]:
        group_name = group["name"]
        if use_simplified:
            user_entries = [e for e in group["entries"] if e.get("simplified") == "user"]
            if not user_entries or not any(e.get("mdf2") for e in user_entries):
                continue
            mesh_name, mdf_name = resolve_mesh_mdf2(
                scene, character_id, group, {"simplified": "user"}, True)
            mdf_col = bpy.data.collections.get(mdf_name) if mdf_name else None
            if mdf_col is None:
                continue
            mesh_col = bpy.data.collections.get(mesh_name) if mesh_name else None
            pairs.append((group_name, mdf_col, mesh_col))
        else:
            for entry in group["entries"]:
                if not entry.get("mdf2"):
                    continue
                mesh_name, mdf_name = resolve_mesh_mdf2(
                    scene, character_id, group, entry, False, scheme["character_id"])
                mdf_col = bpy.data.collections.get(mdf_name) if mdf_name else None
                if mdf_col is None:
                    continue
                mesh_col = bpy.data.collections.get(mesh_name) if mesh_name else None
                pairs.append((f"{group_name} / {entry['id']}", mdf_col, mesh_col))
    return pairs


def _mark_group_issues(groups, entries):
    """Set ``has_issues`` on each ``OWOTS_GroupListItem`` by checking whether any
    check entry's label was prefixed with that group's pair label -- either
    the bare group name (simplified) or ``"<group> / <entry>"`` (normal)."""
    for item in groups:
        prefix_group = item.group_name + " ·"
        prefix_entry = item.group_name + " / "
        item.has_issues = any(
            e['label'].startswith(prefix_group) or e['label'].startswith(prefix_entry)
            for e in entries)


# ============================================================
# Main dialog
# ============================================================

class OWOTS_OT_BatchExportDialog(bpy.types.Operator):
    """OWOTS batch export dialog"""
    bl_idname = "owots.batch_export_dialog"
    bl_label = "OWOTS Batch Exporter"
    bl_options = {'REGISTER'}

    groups: CollectionProperty(type=OWOTS_GroupListItem)
    group_index: IntProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=EXPORTER_WINDOW_WIDTH)

    def _sync_groups(self, scheme, scheme_file):
        """Repopulate the group list only when the selected character scheme
        actually changed since the last draw -- rebuilding it every redraw
        would reset ``group_index`` (and the scroll position with it) on
        every unrelated property change."""
        if getattr(self, '_groups_scheme_file', None) == scheme_file:
            return
        self._groups_scheme_file = scheme_file
        self.groups.clear()
        for group in scheme["groups"]:
            item = self.groups.add()
            item.group_name = group["name"]
            item.entry_count = len(group["entries"])
        self.group_index = 0

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.mhw_suite_settings

        layout.prop(settings, "owots_export_scheme", text="Character")

        # Natives root
        natives_root = scene.get("owots_natives_root", "")
        row = layout.row(align=True)
        row.operator("owots.set_natives_root", text="Natives Root", icon='FILE_FOLDER')
        if natives_root:
            parts = natives_root.replace("\\", "/").rstrip("/").split("/")
            short = "/".join(parts[-3:]) if len(parts) > 3 else natives_root
            row.label(text=f".../{short}")
        else:
            row.label(text="Not set", icon='ERROR')

        scheme_file = settings.owots_export_scheme
        if not scheme_file or scheme_file == 'NONE':
            layout.label(text="Select a character scheme", icon='INFO')
            return
        scheme = _load_scheme(scheme_file)
        if not scheme:
            layout.label(text="Failed to load scheme", icon='ERROR')
            return

        character_id = scheme["character_id"]
        binding_character_id = get_binding_character_id(scene, scheme)
        use_simplified = scene.get("owots_use_simplified", False)
        inherited_character_id = scheme.get("inherit_bindings_from", "")
        use_default_bindings = bool(
            inherited_character_id and scene.get("owots_use_default_bindings", False)
        )
        self._sync_groups(scheme, scheme_file)

        # Simplified toggle
        layout.separator()
        row = layout.row()
        simp_icon = 'CHECKBOX_HLT' if use_simplified else 'CHECKBOX_DEHLT'
        row.operator("owots.toggle_simplified", text="", icon=simp_icon, emboss=False)
        row.label(text="Use Simplified Export", icon='SORTBYEXT')

        if inherited_character_id:
            row = layout.row()
            shared_icon = 'CHECKBOX_HLT' if use_default_bindings else 'CHECKBOX_DEHLT'
            row.operator("owots.toggle_default_bindings", text="", icon=shared_icon, emboss=False)
            row.label(text=T("owots.batch_export_ui.use_default_bindings"), icon='LINKED')

        # --- FBXSKEL (always shown) ---
        fbxskel_path = scheme.get("fbxskel", "")
        if fbxskel_path:
            layout.separator()
            box = layout.box()
            row = box.row(align=True)
            fbx_en = _get_enabled(scene, character_id, "_fbxskel", "fbxskel")
            op = row.operator("owots.toggle_entry", text="",
                              icon='CHECKBOX_HLT' if fbx_en else 'CHECKBOX_DEHLT', emboss=False)
            op.character_id = character_id; op.entry_id = "_fbxskel"; op.suffix = "fbxskel"
            row.label(text="FBXSKEL", icon='ARMATURE_DATA')
            cur_arm = _get_binding(scene, character_id, "_fbxskel", "fbxskel")
            op_p = row.operator("owots.pick_armature", text=cur_arm if cur_arm else "Select armature...",
                                icon='DOWNARROW_HLT')
            op_p.character_id = character_id

            native_path = os.path.join(_get_native_skeletons_dir(), os.path.basename(fbxskel_path))
            row2 = box.row(align=True)
            if os.path.isfile(native_path):
                row2.label(text=T("owots.batch_export_ui.will_align_native_skeleton").format(name=os.path.basename(native_path)),
                           icon='ARMATURE_DATA')
            else:
                row2.label(text=T("owots.batch_export_ui.direct_skeleton_export"), icon='ARMATURE_DATA')

        layout.separator()
        row = layout.row()
        op_c = row.operator("owots.clear_all_bindings", text="Clear All Selected", icon='TRASH')
        op_c.scheme_file = scheme_file

        layout.prop(settings, "owots_use_blank_export", text=T("ui.prop.use_blank_export"), icon='FILE_BLANK')
        layout.prop(settings, "owots_triangulate_face", text=T("ui.prop.triangulate_face"), icon='MOD_TRIANGULATE')

        # Run the check before the list draws, so each row can carry its own
        # issue icon; the summary line at the bottom reuses the same result.
        pairs = _gather_check_pairs(scene, binding_character_id, scheme, use_simplified)
        entries = pec.ensure_checked(self, context, 'OWOTS', pairs, natives_root)
        _mark_group_issues(self.groups, entries)

        layout.separator()
        split = layout.split(factor=0.35)
        col1, col2 = split.column(), split.column()
        col1.template_list("OWOTS_UL_Groups", "", self, "groups", self, "group_index",
                           rows=max(4, min(len(self.groups), 10)))

        selected_group = None
        if self.groups and 0 <= self.group_index < len(self.groups):
            item = self.groups[self.group_index]
            selected_group = next((g for g in scheme["groups"] if g["name"] == item.group_name), None)

        # Below the list rather than inside the detail box: sitting next to
        # the MESH/MDF2/... pickers made it easy to mistake for one more
        # slot rather than an action on the whole group. Simplified-mode
        # only -- normal mode's bindings are per-entry, not per-group, so
        # there is nothing group-level here for copy/paste to act on.
        if use_simplified and selected_group is not None and not use_default_bindings:
            user_entries = [e for e in selected_group["entries"] if e.get("simplified") == "user"]
            if user_entries:
                row = col1.row(align=True)
                op_copy = row.operator("owots.copy_group_bindings",
                                       text=T("owots.batch_export_ui.copy_bindings"), icon='COPYDOWN')
                op_copy.character_id = character_id; op_copy.group_name = selected_group["name"]
                op_paste = row.operator("owots.paste_group_bindings",
                                        text=T("owots.batch_export_ui.paste_bindings"), icon='PASTEDOWN')
                op_paste.character_id = character_id; op_paste.group_name = selected_group["name"]

        if selected_group is not None:
            box = col2.box()
            box.enabled = not use_default_bindings
            if use_simplified:
                self._draw_group_detail_simplified(box, scene, binding_character_id, selected_group)
            else:
                self._draw_group_detail_normal(box, scene, binding_character_id, selected_group,
                                               character_id)

        pec.draw_summary_row(self, layout)

    def _draw_group_detail_simplified(self, layout, scene, character_id, group):
        group_name = group["name"]
        user_entries = [e for e in group["entries"] if e.get("simplified") == "user"]
        empty_count = sum(1 for e in group["entries"] if e.get("simplified") == "empty")
        skip_count  = sum(1 for e in group["entries"] if e.get("simplified") == "skip")

        layout.label(text=group_name, icon='FILE_FOLDER')

        if not user_entries:
            info = []
            if empty_count: info.append(f"{empty_count} empty")
            if skip_count:  info.append(f"{skip_count} skip")
            layout.label(text=f"({', '.join(info)})" if info else "—")
            return

        # Label above the picker, not beside it: this box is only 35% of the
        # dialog's width, and a collection name long enough to fill a sibling
        # row's leftover space gets silently ellipsis-clipped by the button
        # widget -- measured against a real "ch03_..._RE4.mdf2 hair"-style name.
        layout.label(text="MESH:", icon='OUTLINER_OB_MESH')
        row = layout.row(align=True)
        cur = _get_simplified_group_binding(scene, character_id, group_name, "mesh")
        op = row.operator("owots.pick_binding", text=cur if cur else "Select...", icon='DOWNARROW_HLT')
        op.scope = "GROUP"; op.slot = "mesh"
        op.character_id = character_id; op.group_name = group_name
        if cur:
            op_c = row.operator("owots.clear_sg", text="", icon='X')
            op_c.character_id = character_id; op_c.group_name = group_name; op_c.suffix = "mesh"

        layout.label(text="MDF2:", icon='MATERIAL')
        row = layout.row(align=True)
        cur = _get_simplified_group_binding(scene, character_id, group_name, "mdf2")
        op = row.operator("owots.pick_binding", text=cur if cur else "Select...", icon='DOWNARROW_HLT')
        op.scope = "GROUP"; op.slot = "mdf2"
        op.character_id = character_id; op.group_name = group_name
        if cur:
            op_c = row.operator("owots.clear_sg", text="", icon='X')
            op_c.character_id = character_id; op_c.group_name = group_name; op_c.suffix = "mdf2"

        # SFUR (only if the group has any entries with sfur and simplified != skip)
        if any(e.get("sfur") and e.get("simplified_sfur", e.get("simplified", "user")) != "skip" for e in group["entries"]):
            layout.label(text="SFUR:", icon='OUTLINER_OB_CURVES')
            row = layout.row(align=True)
            cur = _get_simplified_group_binding(scene, character_id, group_name, "sfur")
            op = row.operator("owots.pick_binding", text=cur if cur else "Select...", icon='DOWNARROW_HLT')
            op.scope = "GROUP"; op.slot = "sfur"
            op.character_id = character_id; op.group_name = group_name
            if cur:
                op_c = row.operator("owots.clear_sg", text="", icon='X')
                op_c.character_id = character_id; op_c.group_name = group_name; op_c.suffix = "sfur"

        # CHAIN2
        if any(e.get("chain2") and e.get("simplified_chain2", e.get("simplified", "user")) != "skip" for e in group["entries"]):
            layout.label(text="CHAIN2:", icon='PHYSICS')
            row = layout.row(align=True)
            cur = _get_simplified_group_binding(scene, character_id, group_name, "chain2")
            op = row.operator("owots.pick_binding", text=cur if cur else "Select...", icon='DOWNARROW_HLT')
            op.scope = "GROUP"; op.slot = "chain2"
            op.character_id = character_id; op.group_name = group_name
            if cur:
                op_c = row.operator("owots.clear_sg", text="", icon='X')
                op_c.character_id = character_id; op_c.group_name = group_name; op_c.suffix = "chain2"

        # CLSP
        if any(e.get("clsp") and e.get("simplified_clsp", e.get("simplified", "user")) != "skip" for e in group["entries"]):
            layout.label(text="CLSP:", icon='PHYSICS')
            row = layout.row(align=True)
            cur = _get_simplified_group_binding(scene, character_id, group_name, "clsp")
            op = row.operator("owots.pick_binding", text=cur if cur else "Select...", icon='DOWNARROW_HLT')
            op.scope = "GROUP"; op.slot = "clsp"
            op.character_id = character_id; op.group_name = group_name
            if cur:
                op_c = row.operator("owots.clear_sg", text="", icon='X')
                op_c.character_id = character_id; op_c.group_name = group_name; op_c.suffix = "clsp"

        info = [f"{len(user_entries)} user"]
        if empty_count: info.append(f"{empty_count} empty")
        if skip_count:  info.append(f"{skip_count} skip")
        layout.separator()
        layout.label(text=f"Entries: {', '.join(info)}")

    def _draw_group_detail_normal(self, layout, scene, character_id, group,
                                  scheme_character_id=None):
        layout.label(text=group["name"], icon='FILE_FOLDER')
        for entry in group["entries"]:
            entry_id = entry["id"]
            # Bindings may be inherited from the default outfit, whose entry
            # ids carry a different character prefix; map before reading so the
            # greyed-out preview shows what the export will actually use.
            lookup = binding_entry_id(entry_id, scheme_character_id, character_id)
            header = entry_id
            note = entry.get("note", "")
            if note: header += f"  [{note}]"
            entry_box = layout.box()
            entry_box.label(text=header)

            # Label on its own row, picker+clear on the next: this box is only
            # 35% of the dialog's width, and a long collection name sharing a
            # row with the label gets silently ellipsis-clipped.
            if entry.get("mesh"):
                cur = _get_binding(scene, character_id, lookup, "mesh")
                ic = 'OUTLINER_OB_MESH'
                if cur and cur in bpy.data.collections:
                    ct = bpy.data.collections[cur].color_tag
                    if ct != "NONE": ic = f"COLLECTION_{ct}"
                entry_box.label(text="MESH", icon=ic)
                row = entry_box.row(align=True)
                op_p = row.operator("owots.pick_binding",
                                    text=cur if cur else "Select...", icon='DOWNARROW_HLT')
                op_p.scope = "ENTRY"; op_p.slot = "mesh"
                op_p.character_id = character_id; op_p.entry_id = entry_id
                if cur:
                    op_c = row.operator("owots.clear_normal_binding", text="", icon='X')
                    op_c.character_id = character_id; op_c.entry_id = entry_id; op_c.suffix = "mesh"

            if entry.get("mdf2"):
                cur = _get_binding(scene, character_id, lookup, "mdf2")
                ic = 'MATERIAL'
                if cur and cur in bpy.data.collections:
                    ct = bpy.data.collections[cur].color_tag
                    if ct != "NONE": ic = f"COLLECTION_{ct}"
                entry_box.label(text=f"MDF2 x{len(entry['mdf2'])}", icon=ic)
                row = entry_box.row(align=True)
                op_p = row.operator("owots.pick_binding",
                                    text=cur if cur else "Select...", icon='DOWNARROW_HLT')
                op_p.scope = "ENTRY"; op_p.slot = "mdf2"
                op_p.character_id = character_id; op_p.entry_id = entry_id
                if cur:
                    op_c = row.operator("owots.clear_normal_binding", text="", icon='X')
                    op_c.character_id = character_id; op_c.entry_id = entry_id; op_c.suffix = "mdf2"

            if entry.get("sfur"):
                cur = _get_binding(scene, character_id, lookup, "sfur")
                ic = 'OUTLINER_OB_CURVES'
                if cur and cur in bpy.data.collections:
                    ct = bpy.data.collections[cur].color_tag
                    if ct != "NONE": ic = f"COLLECTION_{ct}"
                entry_box.label(text="SFUR", icon=ic)
                row = entry_box.row(align=True)
                op_p = row.operator("owots.pick_binding",
                                    text=cur if cur else "Select...", icon='DOWNARROW_HLT')
                op_p.scope = "ENTRY"; op_p.slot = "sfur"
                op_p.character_id = character_id; op_p.entry_id = entry_id
                if cur:
                     op_c = row.operator("owots.clear_normal_binding", text="", icon='X')
                     op_c.character_id = character_id; op_c.entry_id = entry_id; op_c.suffix = "sfur"

            if entry.get("chain2"):
                cur = _get_binding(scene, character_id, lookup, "chain2")
                ic = 'PHYSICS'
                if cur and cur in bpy.data.collections:
                    ct = bpy.data.collections[cur].color_tag
                    if ct != "NONE": ic = f"COLLECTION_{ct}"
                entry_box.label(text="CHAIN2", icon=ic)
                row = entry_box.row(align=True)
                op_p = row.operator("owots.pick_binding",
                                    text=cur if cur else "Select...", icon='DOWNARROW_HLT')
                op_p.scope = "ENTRY"; op_p.slot = "chain2"
                op_p.character_id = character_id; op_p.entry_id = entry_id
                if cur:
                    op_c = row.operator("owots.clear_normal_binding", text="", icon='X')
                    op_c.character_id = character_id; op_c.entry_id = entry_id; op_c.suffix = "chain2"

            if entry.get("clsp"):
                cur = _get_binding(scene, character_id, lookup, "clsp")
                ic = 'PHYSICS'
                if cur and cur in bpy.data.collections:
                    ct = bpy.data.collections[cur].color_tag
                    if ct != "NONE": ic = f"COLLECTION_{ct}"
                entry_box.label(text="CLSP", icon=ic)
                row = entry_box.row(align=True)
                op_p = row.operator("owots.pick_binding",
                                    text=cur if cur else "Select...", icon='DOWNARROW_HLT')
                op_p.scope = "ENTRY"; op_p.slot = "clsp"
                op_p.character_id = character_id; op_p.entry_id = entry_id
                if cur:
                    op_c = row.operator("owots.clear_normal_binding", text="", icon='X')
                    op_c.character_id = character_id; op_c.entry_id = entry_id; op_c.suffix = "clsp"

    def execute(self, context):
        bpy.ops.owots.batch_export()
        return {'FINISHED'}


classes = [
    OWOTS_GroupListItem,
    OWOTS_UL_Groups,
    OWOTS_OT_PickBinding,
    OWOTS_OT_ToggleEntry,
    OWOTS_OT_ToggleSimplified,
    OWOTS_OT_ToggleDefaultBindings,
    OWOTS_OT_PickArmature,
    OWOTS_OT_BatchExportDialog,
    OWOTS_OT_ClearSimplifiedGroup,
    OWOTS_OT_ClearSimplifiedEmpty,
    OWOTS_OT_CopyGroupBindings,
    OWOTS_OT_PasteGroupBindings,
    OWOTS_OT_ClearNormalBinding,
    OWOTS_OT_ClearAllBindings,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
