# armature.py: Utilities to work with armature, shapekeys and animation
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix, Shoun, Kamil "HRY" Krzyśków
# License: GPL
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
from math import pi

import bpy
import mathutils
from mathutils import Matrix, Vector

from .scene import end_editmode, start_editmode, change_active_view_layer, MAX_SCENE_FRAME_LIMIT
from .types import is_valid_tuple_instance

# -----------------------------------------------
# Slotted-actions compatibility (Blender 4.4+/5.x)
#
# Blender 5.x removed the legacy `Action.fcurves` API: fcurves now live in a
# channelbag addressed by (layer, strip, slot). These helpers give the rest of
# this module one uniform fcurves collection for both old and new Blender.
# -----------------------------------------------


def _resolve_slot(animation_data, id_type, slot_name):
    """Make sure animation_data has an action slot assigned; return it."""
    action = animation_data.action
    slot = getattr(animation_data, "action_slot", None)
    if slot is None:
        suitable = getattr(animation_data, "action_suitable_slots", ())
        if len(suitable):
            slot = suitable[0]
        else:
            slot = action.slots.new(id_type=id_type, name=slot_name or "Slot")
        animation_data.action_slot = slot
    return slot


def action_fcurves(animation_data, id_type="OBJECT", slot_name=None):
    """Writable fcurves collection for animation_data's action (creates the
    slot/layer/strip/channelbag on demand under slotted actions)."""
    action = animation_data.action
    if hasattr(action, "fcurves"):  # Blender <= 4.x legacy API
        return action.fcurves

    slot = _resolve_slot(animation_data, id_type, slot_name)
    layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type="KEYFRAME")
    return strip.channelbag(slot, ensure=True).fcurves


def iter_action_fcurves(animation_data):
    """Read-only iteration over the fcurves of animation_data's action.
    Never creates layers/strips/channelbags; yields nothing if absent."""
    action = animation_data.action
    if action is None:
        return ()
    if hasattr(action, "fcurves"):
        return action.fcurves

    slot = getattr(animation_data, "action_slot", None)
    if slot is None:
        return ()
    for layer in action.layers:
        for strip in layer.strips:
            channelbag = strip.channelbag(slot)
            if channelbag is not None:
                return channelbag.fcurves
    return ()


def new_fcurve(fcurves, data_path, index=0, group=None):
    """fcurves.new() wrapper: channelbag fcurves may not accept action_group."""
    if group is not None:
        try:
            return fcurves.new(data_path, index=index, action_group=group)
        except TypeError:
            pass
    return fcurves.new(data_path, index=index)


# -----------------------------------------------
# Animation of object's transformation matrix  #
# -----------------------------------------------


def has_transform_animation(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        animation_data = extended_obj.animation_data
        if not animation_data or not animation_data.action:
            return False

        curvepath_pos = "location"
        curvepath_rot = "rotation_quaternion"
        curvepath_scale = "rotation_quaternion"
        for fc in iter_action_fcurves(animation_data):
            if fc.data_path in (curvepath_pos, curvepath_rot, curvepath_scale):
                return True

        return False

    elif is_valid_tuple_instance(extended_obj):
        armature_obj, bone_name = extended_obj
        animation_data = armature_obj.animation_data
        if not animation_data or not animation_data.action:
            return False

        curvepath_pos = f'pose.bones["{bone_name}"].location'
        curvepath_rot = f'pose.bones["{bone_name}"].rotation_quaternion'
        curvepath_scale = f'pose.bones["{bone_name}"].scale'
        for fc in iter_action_fcurves(animation_data):
            if fc.data_path in (curvepath_pos, curvepath_rot, curvepath_scale):
                return True

        return False

    else:
        raise TypeError(f"{__name__}: has_transform_animation: error with argument 1")


def animate_transform(extended_obj, transform: mathutils.Matrix):
    if isinstance(extended_obj, bpy.types.Object):
        # unprepare_matrix for Blender
        new_transform = transform.transposed()
        if extended_obj.parent and extended_obj.parent_type == "BONE":
            armature_obj = extended_obj.parent
            armature_matrix: mathutils.Matrix = armature_obj.matrix_world
            armature: bpy.types.Armature = armature_obj.data
            new_obj_matrix = armature_matrix.inverted() @ new_transform
            parent_bone_name = extended_obj.parent_bone
            parent_bone_pose_matrix: mathutils.Matrix = armature_obj.pose.bones[parent_bone_name].matrix
            parent_bone_armature_matrix = armature.bones[parent_bone_name].matrix_local
            final_matrix = parent_bone_armature_matrix @ (parent_bone_pose_matrix.inverted() @ new_obj_matrix)
        else:
            final_matrix = new_transform

        pos = final_matrix.to_translation()
        rot = final_matrix.to_quaternion()

        animation_data = (
            extended_obj.animation_data_create() if not extended_obj.animation_data else extended_obj.animation_data
        )

        if not animation_data.action:
            animation_data.action = bpy.data.actions.new(f"{extended_obj.name}Action")

        fcurves = action_fcurves(animation_data, id_type="OBJECT", slot_name=extended_obj.name)

        curvepath_pos = "location"
        curvepath_rot = "rotation_quaternion"
        curve_pos = [None, None, None]
        curve_rot = [None, None, None, None]
        for fc in fcurves:
            if fc.data_path == curvepath_pos:
                curve_pos[fc.array_index] = fc
            elif fc.data_path == curvepath_rot:
                curve_rot[fc.array_index] = fc

        for i in range(len(curve_pos)):
            if not curve_pos[i]:
                curve_pos[i] = new_fcurve(fcurves, curvepath_pos, index=i, group="LocRotScale")
            curve_pos[i].keyframe_points.insert(bpy.context.scene.frame_current, pos[i])
        for i in range(len(curve_rot)):
            if curve_rot[i] is None:
                curve_rot[i] = new_fcurve(fcurves, curvepath_rot, index=i, group="LocRotScale")
            curve_rot[i].keyframe_points.insert(bpy.context.scene.frame_current, rot[i])
    elif is_valid_tuple_instance(extended_obj):
        new_transform = unprepare_bone_matrix(transform)
        armature_obj, bone_name = extended_obj
        armature = armature_obj.data

        armature_matrix = armature_obj.matrix_world
        new_bone_matrix = armature_matrix.inverted() @ new_transform

        armature_bone = armature.bones[bone_name]
        bone_armature_matrix = armature_bone.matrix_local
        if armature_bone.parent:
            parent_bone_armature_matrix = armature_bone.parent.matrix_local
        else:
            parent_bone_armature_matrix = Matrix.Identity(4)

        pose_bone = armature_obj.pose.bones[bone_name]
        if pose_bone.parent:
            parent_bone_pose_matrix = pose_bone.parent.matrix
        else:
            parent_bone_pose_matrix = Matrix.Identity(4)

        final_matrix = (bone_armature_matrix.inverted() @ parent_bone_armature_matrix) @ (
            parent_bone_pose_matrix.inverted() @ new_bone_matrix
        )

        pos = final_matrix.to_translation()
        rot = final_matrix.to_quaternion()

        animation_data = (
            armature_obj.animation_data_create() if not armature_obj.animation_data else armature_obj.animation_data
        )

        if not animation_data.action:
            animation_data.action = bpy.data.actions.new(f"{armature_obj.name}Action")

        fcurves = action_fcurves(animation_data, id_type="OBJECT", slot_name=f"{armature_obj.name}Slot")

        curvepath_pos = f'pose.bones["{bone_name}"].location'
        curvepath_rot = f'pose.bones["{bone_name}"].rotation_quaternion'
        curve_pos = [None, None, None]
        curve_rot = [None, None, None, None]
        for fc in fcurves:
            if fc.data_path == curvepath_pos:
                curve_pos[fc.array_index] = fc
            elif fc.data_path == curvepath_rot:
                curve_rot[fc.array_index] = fc

        for i in range(len(curve_pos)):
            if not curve_pos[i]:
                curve_pos[i] = new_fcurve(fcurves, curvepath_pos, index=i, group=bone_name)
            curve_pos[i].keyframe_points.insert(bpy.context.scene.frame_current, pos[i])
        for i in range(len(curve_rot)):
            if curve_rot[i] is None:
                curve_rot[i] = new_fcurve(fcurves, curvepath_rot, index=i, group=bone_name)
            curve_rot[i].keyframe_points.insert(bpy.context.scene.frame_current, rot[i])
    else:
        raise TypeError(f"{__name__}: animate_transform: error with argument 1")


# --------------------------------------------------
# Animation of mesh's vertices (morph animation)  #
# --------------------------------------------------


def has_vertex_animation(extended_obj):
    if isinstance(extended_obj, bpy.types.Object) and isinstance(extended_obj.data, bpy.types.Mesh):
        if extended_obj.data and extended_obj.data.shape_keys:
            return True
    return False

def animate_vertices(extended_obj, points):
    if not isinstance(extended_obj, bpy.types.Object) or not isinstance(extended_obj.data, bpy.types.Mesh):
        return
    change_active_view_layer(extended_obj)
    bpy.ops.object.shape_key_add()
    key = extended_obj.data.shape_keys
    key.use_relative = True

    current_shape_key = key.key_blocks[-1]
    for i in range(len(points)):
        current_shape_key.data[i].co = points[i]

    animation_data = key.animation_data_create() if not key.animation_data else key.animation_data

    if not animation_data.action:
        animation_data.action = bpy.data.actions.new(f"{extended_obj.name}Action")

    fcurves = action_fcurves(animation_data, id_type="KEY", slot_name=f"{extended_obj.name}Key")

    frame_current = bpy.context.scene.frame_current

    for shapekey in key.key_blocks:
        curvepath = f'key_blocks["{shapekey.name}"].value'
        curve = None
        for fc in fcurves:
            if fc.data_path == curvepath:
                curve = fc
                break

        if shapekey == current_shape_key:
            if not curve:
                curve = new_fcurve(fcurves, curvepath)
            if frame_current != 0:
                curve.keyframe_points.insert(frame_current - 1, 0.0)
            curve.keyframe_points.insert(frame_current, 1.0)
            if frame_current != MAX_SCENE_FRAME_LIMIT:
                curve.keyframe_points.insert(frame_current + 1, 0.0)
        elif curve:
            for keyframe_point in curve.keyframe_points:
                if keyframe_point.co[0] == frame_current:
                    keyframe_point.co[1] = 0


# Split a bone's full name into two parts: prefix (before "BIP") and name itself.
def split_bone_name(name):
    bip_pos = name.upper().find("BIP01")
    if bip_pos == -1:
        prefix = name
        bone_name = ""
    elif bip_pos == 0:
        prefix = "Armature"
        bone_name = name
    else:
        prefix = name[:bip_pos]
        bone_name = name[bip_pos:]
    return prefix, bone_name


# Matrix to rotate local axes of all bones for better looking of models in Blender which were created in 3ds max.
# (In 3dsmax any bone lies along its local X direction, however in Blender any bone lies along its local Y direction,
# so we can want to rotate axes around Z direction to match the directions).
cached_bone_rotation_matrix = mathutils.Quaternion(mathutils.Vector((0, 0, 1)), pi / 2).to_matrix().to_4x4()
cached_bone_rotation_matrix_inverted = cached_bone_rotation_matrix.inverted()


def prepare_bone_matrix(matrix: mathutils.Matrix):
    """Prepare bone matrix for using by KrxImpExp's scripts after getting it from Blender."""
    return prepare_unprepare_bone_matrix(matrix.transposed(), cached_bone_rotation_matrix)


def unprepare_bone_matrix(matrix: mathutils.Matrix):
    """Prepare bone matrix for using by Blender after calculating it in KrxImpExp' scripts."""
    return prepare_unprepare_bone_matrix(matrix.copy(), cached_bone_rotation_matrix_inverted).transposed()


def prepare_unprepare_bone_matrix(mat_r: mathutils.Matrix, multiplier_matrix: mathutils.Matrix):
    mat_r.transpose()
    mat_r = mat_r @ multiplier_matrix
    mat_r.transpose()
    return mat_r


# Gets armature, armature's name is the model_prefix, creates armature if it doesn't exist.
def get_armature(model_prefix, bone_style="STICK"):
    if not model_prefix:
        model_prefix = "Armature"

    armature = bpy.data.armatures.get(model_prefix)
    if not armature:
        armature = bpy.data.armatures.new(model_prefix)
        armature.display_type = bone_style
        # Gothic bones run along their local X, so an axis tripod on every bone draws
        # long lines across the model that read as crossed limbs. Off by default -
        # turn it back on per armature in Object Data Properties > Viewport Display.
        armature.show_axes = False

    try: # Faster than .get
        obj = bpy.data.objects[model_prefix]
    except (IndexError, KeyError):
        obj = None

    if not obj:
        obj = bpy.data.objects.new(model_prefix, object_data=armature)
        obj.show_in_front = True
        obj.rotation_mode = "QUATERNION"
        bpy.context.collection.objects.link(obj)
    return obj

def create_bone(bone_name, parent_bone_name, armature_obj, bound_box_min=0, bound_box_max=0):
    if armature_obj.type != "ARMATURE" and not isinstance(armature_obj, bpy.types.Object):
        raise RuntimeError(f"{__name__}: create_bone: This {armature_obj} object is not an armature object!")

    # Switch to edit mode
    start_editmode(armature_obj)

    # Re-use a bone of the same name instead of letting Blender make "Bip01 Head.001".
    # Importing the same skeleton twice into one armature used to pile up duplicates.
    edit_bone_obj = armature_obj.data.edit_bones.get(bone_name)
    if edit_bone_obj is None:
        edit_bone_obj = armature_obj.data.edit_bones.new(bone_name)

    if parent_bone_name:
        if parent_bone_name in armature_obj.data.edit_bones:
            edit_bone_obj.parent = armature_obj.data.edit_bones[parent_bone_name]
        else:
            print(f"WARNING: Parent bone '{parent_bone_name}' not found for '{bone_name}'")
    
    edit_bone_obj.head = Vector((bound_box_min.x, 0, 0))
    edit_bone_obj.tail = Vector((bound_box_max.x, 0, 0))
    edit_bone_obj.roll = 0

    # Switch back to object mode
    end_editmode()