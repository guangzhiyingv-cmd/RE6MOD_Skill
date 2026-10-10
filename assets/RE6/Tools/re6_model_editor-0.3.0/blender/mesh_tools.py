# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Dimcirui

"""Mesh / collection helper operators (the counterpart of the MHW Model Editor "Mesh Tools" panel)."""
import json
import re

import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator
from mathutils import Matrix

from . import common as C
from .i18n import rpt

_ROT_NEG90 = Matrix.Rotation(-1.5707963267948966, 4, 'X')


def _meshes(context):
    return [o for o in context.selected_objects if o.type == 'MESH']


def _needs_selection(cls, context):
    return bool(context.selected_objects)


def material_label(obj):
    """<material> part of the object name scheme Group_<g>_Sub_<k>__<material>: the name of the Blender material when it was
    given one, else the resolved name of its hash, else MAT_xxxxxxxx"""
    if obj.data.materials and obj.data.materials[0]:
        mat = obj.data.materials[0]
        name = mat.name.split('.', 1)[0].strip() if mat.name[-4:-3] == '.' else mat.name.strip()
        if name.startswith(C.MAT_PREFIX) and C.K_MAT_HASH in mat:
            try:
                return C.mat_label(int(str(mat[C.K_MAT_HASH]), 16))
            except ValueError:
                pass
        return name
    return 'NO_MATERIAL'


class RE6_OT_create_collection(Operator):
    bl_idname = 're6_mod.create_mod_collection'
    bl_label = 'Create Mod Collection'
    bl_description = ('Create a mod collection for putting armature and meshes into.'
                      '\nIf you are making new models, you can use this button.'
                      '\nOtherwise, it is highly recommended to import a mod file to inherit the custom properties of the '
                      'collection')
    bl_options = {'UNDO'}

    collectionName: StringProperty(name='Mod Name', default='pl0000',
                                   description='The name of the newly created mod collection.\nUse the same name as the '
                                               'mod file')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.collectionName.strip()
        if not name:
            self.report({'ERROR'}, rpt('Invalid mod collection name.'))
            return {'CANCELLED'}
        # the nested collection group of the model, else a new one
        parent = bpy.data.collections.get(name)
        if parent is None:
            parent = bpy.data.collections.new(name)
            context.scene.collection.children.link(parent)
        col = bpy.data.collections.new(name + '.mod')
        parent.children.link(col)
        col.color_tag = 'COLOR_01'
        col[C.TYPE] = C.T_MOD
        col[C.K_MATERIALS] = json.dumps([])
        context.scene.re6_mrl_toolpanel.modCollection = col
        self.report({'INFO'}, rpt('Created new mod collection.'))
        return {'FINISHED'}


class RE6_OT_create_nested_collections(Operator):
    bl_idname = 're6_mod.create_nested_collections'
    bl_label = 'Create Nested Collections'
    bl_description = ('Create nested collections containing mod, mrl and ctc collections.\nThis will make the collection '
                      'structure look clearer')
    bl_options = {'UNDO'}

    collectionName: StringProperty(name='Collection Name', default='pl0000',
                                   description='The name of the newly created nested collections')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from . import mrl_objects as MO
        name = self.collectionName.strip()
        if not name:
            self.report({'ERROR'}, rpt('Invalid collection name.'))
            return {'CANCELLED'}
        parent = bpy.data.collections.new(name)
        context.scene.collection.children.link(parent)
        mod = bpy.data.collections.new(name + '.mod')
        parent.children.link(mod)
        mod.color_tag = 'COLOR_01'
        mod[C.TYPE] = C.T_MOD
        mod[C.K_MATERIALS] = json.dumps([])
        mrl = MO.new_collection(parent, name + '.mrl', '')
        tp = context.scene.re6_mrl_toolpanel
        tp.modCollection, tp.mrlCollection = mod, mrl
        from . import ctc_functions as CF
        from . import objects as O
        ctc = O.create_collection(name + '.ctc', 'COLOR_02', C.T_CTC, parent)
        context.scene.re6_ctc_toolpanel.ctcCollection = ctc
        CF.create_header(ctc, 'CTC_HEADER %s.ctc' % name)
        self.report({'INFO'}, rpt('Created new nested collections.'))
        return {'FINISHED'}


class RE6_OT_rename_meshes(Operator):
    bl_idname = 're6_mod.rename_meshes'
    bl_label = 'Rename Meshes'
    bl_description = 'Renames selected meshes to mod mesh naming scheme (Example: Group_0_Sub_0__pl_skin)'
    bl_options = {'UNDO'}

    poll = classmethod(_needs_selection)

    def execute(self, context):
        counters = {}
        n = 0
        for obj in _meshes(context):
            m = re.search(r'Group_(\d+)', obj.name)
            gid = int(m.group(1)) if m else 0
            k = counters.get(gid, 0)
            counters[gid] = k + 1
            obj.name = 'Group_%d_Sub_%d__%s' % (gid, k, material_label(obj))
            n += 1
        self.report({'INFO'}, rpt('Renamed %d mesh object(s) to mod mesh format.') % n if n
                    else rpt('There are no meshes in selected objects.'))
        return {'FINISHED'}


class RE6_OT_set_group_id(Operator):
    bl_idname = 're6_mod.set_mesh_group_id'
    bl_label = 'Set Mesh Group ID'
    bl_description = 'Quickly set mesh group ID on selected meshes'
    bl_options = {'UNDO'}

    groupID: IntProperty(name='Group ID', description='', default=0, min=0, max=4095)

    poll = classmethod(_needs_selection)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        meshes = _meshes(context)
        n = 0
        for obj in meshes:
            m = re.search(r'(Group_)(\d+)(.*)', obj.name)
            if m:
                obj.name = '%s%d%s' % (m.group(1), self.groupID, m.group(3))
                n += 1
            else:
                print('Could not parse group ID of %s, skipping...' % obj.name)
        if meshes:
            self.report({'INFO'}, rpt('Set group ID %d on %d mesh object(s).') % (self.groupID, n) if n
                        else rpt('There are no meshes that can be parsed group ID.'))
        else:
            self.report({'INFO'}, rpt('There are no meshes in selected objects.'))
        return {'FINISHED'}


class RE6_OT_delete_loose(Operator):
    bl_idname = 're6_mod.delete_loose_geometry'
    bl_label = 'Delete Loose Geometry'
    bl_description = 'Deletes loose vertices and edges with no faces on selected meshes'
    bl_options = {'UNDO'}

    poll = classmethod(_needs_selection)

    def execute(self, context):
        n = 0
        for obj in _meshes(context):
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            loose = [v for v in bm.verts if not v.link_faces]
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
            bm.to_mesh(obj.data)
            bm.free()
            obj.data.update()
            n += 1
        self.report({'INFO'}, rpt('Deleted loose geometry on %d mesh object(s).') % n if n
                    else rpt('There are no meshes in selected objects.'))
        return {'FINISHED'}


class RE6_OT_remove_empty_groups(Operator):
    bl_idname = 're6_mod.remove_empty_vertex_groups'
    bl_label = 'Remove Empty Vertex Groups'
    bl_description = 'Remove all vertex groups that have no weight assigned to them'
    bl_options = {'UNDO'}

    poll = classmethod(_needs_selection)

    def execute(self, context):
        n = 0
        for obj in _meshes(context):
            used = set()
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.weight > 0.0:
                        used.add(g.group)
            for vg in [g for g in obj.vertex_groups if g.index not in used][::-1]:
                obj.vertex_groups.remove(vg)
            n += 1
        self.report({'INFO'}, rpt('Removed empty vertex groups on %d mesh object(s).') % n if n
                    else rpt('There are no meshes in selected objects.'))
        return {'FINISHED'}


def limit_and_normalize(obj, limit):
    """keep the `limit` strongest weights of every vertex and normalise them to 1"""
    n_changed = 0
    groups = obj.vertex_groups
    for v in obj.data.vertices:
        ws = [(g.weight, g.group) for g in v.groups if g.weight > 0.0]
        if not ws:
            continue
        ws.sort(key=lambda t: (-t[0], t[1]))
        keep = ws[:limit]
        total = sum(w for w, _ in keep)
        for w, gi in ws[limit:]:
            groups[gi].remove([v.index])
            n_changed += 1
        for w, gi in keep:
            nw = w / total
            if abs(nw - w) > 1e-7:
                groups[gi].add([v.index], nw, 'REPLACE')
    return n_changed


class RE6_OT_limit_normalize(Operator):
    bl_idname = 're6_mod.limit_total_normalize'
    bl_label = 'Limit Total and Normalize All'
    bl_description = ('Limits the amount of bones influences per vertex to 4 and normalizes the weights of all vertex groups '
                      'for all selected meshes')
    bl_options = {'UNDO'}

    limit: IntProperty(name='Max Influences', default=4, min=1, max=8)

    poll = classmethod(_needs_selection)

    def execute(self, context):
        objs = _meshes(context)
        for obj in objs:
            limit_and_normalize(obj, self.limit)
            obj.data.update()
        self.report({'INFO'}, rpt('Limited and normalized weights on %d mesh object(s).') % len(objs) if objs
                    else rpt('There are no meshes in selected objects.'))
        return {'FINISHED'}


_SWIZZLE = (('+Z', '+Z', '+Z'), ('-Z', '-Z', '-Z'), ('+Y', '+Y', '+Y'), ('-Y', '-Y', '-Y'), ('+X', '+X', '+X'),
            ('-X', '-X', '-X'))


def _swizzle(n, code):
    i = 'XYZ'.index(code[1])
    return n[:, i] if code[0] == '+' else -n[:, i]


class RE6_OT_bake_normal_color(Operator):
    bl_idname = 're6_mod.bake_normal_to_vertex_color'
    bl_label = 'Bake Normal To Vertex Color'
    bl_description = ('Bakes the world normal to vertex color on selected meshes.'
                      '\nBaked vertex color will be saved in the channel called "World Space Normal".'
                      '\nIf you select too many meshes, this operation may consume a lot of time')
    bl_options = {'UNDO'}

    layer_name: StringProperty(name='Layer Name', description='', default='World Space Normal')
    space: EnumProperty(name='Space', default='WORLD',
                        items=(('WORLD', 'World', 'Normals are encoded in world space'),
                               ('LOCAL', 'Local', 'Normals are encoded in local space')))
    swizzle_x: EnumProperty(name='red / x-Axis', items=_SWIZZLE, default='+X')
    swizzle_y: EnumProperty(name='green / y-Axis', items=_SWIZZLE, default='+Y')
    swizzle_z: EnumProperty(name='blue / z-Axis', items=_SWIZZLE, default='+Z')

    poll = classmethod(_needs_selection)

    def execute(self, context):
        objs = _meshes(context)
        for obj in objs:
            me = obj.data
            ca = me.color_attributes.get(self.layer_name)
            if ca is None:
                ca = me.color_attributes.new(self.layer_name, 'BYTE_COLOR', 'CORNER')
            me.color_attributes.active_color = ca
            n = np.empty(len(me.loops) * 3, np.float32)
            me.corner_normals.foreach_get('vector', n)
            n = n.reshape(-1, 3).astype(np.float64)
            if self.space == 'WORLD':
                m = np.array((_ROT_NEG90 @ obj.matrix_world).to_3x3())
                n = n @ m.T
                n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            src = n.copy()
            out = np.stack([_swizzle(src, self.swizzle_x), _swizzle(src, self.swizzle_y), _swizzle(src, self.swizzle_z)], 1)
            rgba = np.ones((len(n), 4), np.float32)
            rgba[:, :3] = out * 0.5 + 0.5
            ca.data.foreach_set('color', rgba.ravel())
            me.update()
        self.report({'INFO'}, rpt('Baked normal to vertex color on %d mesh object(s).') % len(objs) if objs
                    else rpt('There are no meshes in selected objects.'))
        return {'FINISHED'}


CLASSES = (RE6_OT_create_collection, RE6_OT_create_nested_collections, RE6_OT_rename_meshes, RE6_OT_set_group_id, RE6_OT_delete_loose,
           RE6_OT_remove_empty_groups, RE6_OT_limit_normalize, RE6_OT_bake_normal_color)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
