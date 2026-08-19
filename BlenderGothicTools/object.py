# object.py: Object utilities.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix, Shoun, Kamil "HRY" Krzyśków
# License: GPL
# -------------------------------------------------------------------------------------------------------
from math import radians

import bpy
import mathutils

from .armature import prepare_bone_matrix, split_bone_name, unprepare_bone_matrix
from .scene import change_active_view_layer, end_editmode, start_editmode
from .types import is_valid_tuple_instance

# Note:
# Some of the functions below get an argument named "extended object", (or "extended_obj", or "extparent", and so on)
# The "Extended object" may be one of:
# a) None means the root object
# b) value of type bpy.types.Object means any Blender's object
# c) (object, bone_name), i.e. a tuple of two values, the first is an object which contains an armature,
#    and the second is a bone's name


# Returns name of an extended object
def get_object_name(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        return extended_obj.name
    elif is_valid_tuple_instance(extended_obj):
        obj, bone_name = extended_obj
        if obj.name == "Armature":
            return bone_name
        else:
            return obj.name + bone_name
    else:
        raise TypeError(f"{__name__}: get_object_name: error with argument 1", type(extended_obj))


# Returns (extended) parent of an extended object
def get_parent(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        if extended_obj.parent_type == "BONE":
            # A normal object linked to a bone
            return extended_obj.parent, extended_obj.parent_bone
        else:
            # A normal object linked to another object
            return extended_obj.parent
    elif is_valid_tuple_instance(extended_obj):
        obj, bone_name = extended_obj
        bone = obj.data.bones[bone_name]
        if not bone.parent:
            # The root bone is considered to be linked to armature object
            return obj
        else:
            # Bone is linked to another bone
            return obj, bone.parent.name
    else:
        raise TypeError(f"{__name__}: get_parent: error with argument 1")


# Returns list of child objects for an extended object
def get_children(extended_obj):
    children = []
    if not extended_obj:
        # children of the root object = objects with no parent
        for scene in bpy.data.scenes:
            for scene_obj in scene.objects:
                if not scene_obj.parent:
                    children.append(scene_obj)
    elif isinstance(extended_obj, bpy.types.Object):
        if isinstance(extended_obj.data, bpy.types.Armature):
            # children of armature object are bones with no parent
            armature = extended_obj.data
            for bone in armature.bones:
                if not bone.parent:
                    children.append((extended_obj, bone.name))
        # children of object
        for scene in bpy.data.scenes:
            for scene_obj in scene.objects:
                if scene_obj.parent == extended_obj and scene_obj.parent_type == "OBJECT":
                    children.append(scene_obj)
    elif is_valid_tuple_instance(extended_obj):
        # children of bone
        obj, bone_name = extended_obj
        for bone in obj.data.bones:
            if bone.parent and bone.parent.name == bone_name:
                children.append((obj, bone.name))
        for scene in bpy.data.scenes:
            for scene_obj in scene.objects:
                if scene_obj.parent == obj and scene_obj.parent_type == "BONE" and scene_obj.parent_bone == bone_name:
                    children.append(scene_obj)
    else:
        raise TypeError(f"{__name__}: get_child_objects: error with argument 1")

    return children


# Changes parent of an extended object
def set_parent(extended_obj, extended_new_parent):
    if isinstance(extended_obj.data, bpy.types.Mesh):
        # clear the previous parent object
        deselect_all()
        select(extended_obj)
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")

        # set new parent
        if isinstance(extended_new_parent, bpy.types.Object):
            change_active_view_layer(extended_new_parent)
            bpy.ops.object.parent_set(type="OBJECT")
        elif is_valid_tuple_instance(extended_new_parent):
            new_parent, parent_bone_name = extended_new_parent
            armature = new_parent.data
            armature.bones.active = armature.bones[parent_bone_name]
            change_active_view_layer(new_parent)
            bpy.ops.object.parent_set(type="BONE")
        elif extended_new_parent:
            raise TypeError(f"{__name__}: set_parent_object: error with argument 2")

    elif is_valid_tuple_instance(extended_obj):
        obj, bone_name = extended_obj
        armature = obj.data
        start_editmode(obj)
        edited_bone = armature.edit_bones[bone_name]
        edited_bone.parent = None
        end_editmode()

        if is_valid_tuple_instance(extended_new_parent) and extended_new_parent[0] == obj:
            parent_bone_name = extended_new_parent[1]
            start_editmode(obj)
            edited_bone = armature.edit_bones[bone_name]
            edited_bone.parent = armature.edit_bones[parent_bone_name]
            end_editmode()
        elif extended_new_parent:
            raise TypeError(f"{__name__}: set_parent_object: error with argument 2")

    else:
        raise TypeError(f"{__name__}: clear_parent_object: error with argument 1", type(extended_obj.data))


# Returns an extended object's transformation matrix (4x4)
def get_transform(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        # prepare_matrix for Krx scripts
        return extended_obj.matrix_world.transposed()
    elif is_valid_tuple_instance(extended_obj):
        armature_obj, bone_name = extended_obj
        pose = armature_obj.pose
        pose_matrix = pose.bones[bone_name].matrix
        armature_matrix = armature_obj.matrix_world
        current_transform = armature_matrix @ pose_matrix
        return prepare_bone_matrix(current_transform).copy()
    else:
        raise TypeError("get_transform: error with argument 1")


# Changes an extended object's transformation matrix (4x4) - don't use it to create animation
def set_transform(extended_obj, new_transform: mathutils.Matrix, adjust_bone_roll=None):
    if isinstance(extended_obj, bpy.types.Object):
        # not a bone
        # unprepare_matrix for Blender usage
        extended_obj.matrix_basis = new_transform.transposed()
    elif is_valid_tuple_instance(extended_obj):
        # bone
        new_transform = unprepare_bone_matrix(new_transform)
        armature_obj, bone_name = extended_obj
        armature_matrix = armature_obj.matrix_world
        new_bone_matrix = armature_matrix.inverted() @ new_transform
        # if some mesh is linked to this armature, set pose; else edit armature's bone itself.
        armature = armature_obj.data
        start_editmode(armature_obj)
        bone_o = new_bone_matrix.col[3].to_3d()
        bone_y = new_bone_matrix.col[1].to_3d()
        bone_z = new_bone_matrix.col[2].to_3d()
        edit_bone = armature.edit_bones[bone_name]
        length = edit_bone.length
        edit_bone.head = bone_o
        edit_bone.tail = bone_o + bone_y * length
        edit_bone.align_roll(bone_z)
        if adjust_bone_roll:
            edit_bone.roll = edit_bone.roll + radians(adjust_bone_roll)
        end_editmode()
    else:
        raise TypeError(f"{__name__}: set_transform: error with argument 1")


# Deletes a specified extended object
# Note: Due to process creation on the scene, purging orphans inside this function will
# introduce race conditions, scripts expecting a model in advance will fail to find it, because userless object
# will be deleted from scene entirely!
def delete_object(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        if bpy.context.scene.objects.get(extended_obj.name) == extended_obj:
            bpy.context.collection.objects.unlink(extended_obj)
        if extended_obj.users == 0:
            bpy.data.objects.remove(extended_obj)
    elif is_valid_tuple_instance(extended_obj):
        obj, bone_name = extended_obj
        armature = obj.data
        start_editmode(obj)
        armature.edit_bones.remove(armature.edit_bones[bone_name])
        end_editmode()
    else:
        raise TypeError(f"{__name__}: delete_object: error with argument 1")


# Returns an object with a specified name.
# The function returns None if not found.
def find_object_by_name(name):
    obj_name, bone_name = split_bone_name(name)
    object = None
    for obj in bpy.data.objects:
        if obj.name.upper() == obj_name.upper():
            object = obj
            break

    if not bone_name:
        return object

    if isinstance(object.data, bpy.types.Armature):
        for bone in object.data.bones:
            if bone.name.upper() == bone_name.upper():
                return object, bone.name


# Returns number of vertices in object if this is a mesh; returns 0 if this is not a mesh.
def calculate_num_verts(extended_obj):
    if isinstance(extended_obj, bpy.types.Object) and isinstance(extended_obj.data, bpy.types.Mesh):
        return len(extended_obj.data.vertices)
    return 0


# Returns number of faces in object if this is a mesh; returns 0 if this is not a mesh.
def calculate_num_faces(extended_obj):
    if isinstance(extended_obj, bpy.types.Object) and isinstance(extended_obj.data, bpy.types.Mesh):
        return len(extended_obj.data.polygons)
    return 0


# Returns number of materials used by an object.
def calculate_num_materials(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        return len(extended_obj.material_slots)
    return 0


# Checks if an object is visible (in Editor).
def is_visible(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        return not extended_obj.hide_get()
    return False


# Sets if an object is visible (in Editor).
def show(extended_obj, value):
    if isinstance(extended_obj, bpy.types.Object):
        extended_obj.hide_set(not value)


# Checks if an object is visible (for Renderer).
def is_renderable(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        return not extended_obj.hide_render
    return False


# Sets if an object is visible (for Renderer).
def set_renderable(extended_obj, renderable):
    if isinstance(extended_obj, bpy.types.Object):
        extended_obj.hide_render = not renderable


# Checks if an object is drawn as bounding box.
def get_box_mode(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        return extended_obj.display_type == "BOUNDS"
    return False


# Sets if an object is drawn as bounding box.
def set_box_mode(extended_obj, boxmode):
    if isinstance(extended_obj, bpy.types.Object):
        if boxmode:
            extended_obj.display_type = "BOUNDS"
        else:
            extended_obj.display_type = "TEXTURED"


# Checks if an object is selected.
def is_selected(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        if isinstance(extended_obj.data, bpy.types.Armature):
            return False
        return extended_obj.select_get()
    elif is_valid_tuple_instance(extended_obj):
        obj = extended_obj[0]
        return obj.select_get()
    else:
        raise TypeError(f"{__name__}: select: error with argument 1")


# Selects an object.
def select(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        change_active_view_layer(extended_obj)
        extended_obj.select_set(True)
    elif is_valid_tuple_instance(extended_obj):
        obj, bone_name = extended_obj
        armature = obj.data
        obj.select_set(True)
        bone = armature.bones[bone_name]
        armature.bones.active = bone
    else:
        raise TypeError(f"{__name__}: select: error with argument 1")


# Deselects an object.
def deselect(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        extended_obj.select_set(False)
    else:
        raise TypeError(f"{__name__}: select: error with argument 1")


# Deselect all the objects.
def deselect_all():
    bpy.ops.object.select_all(action="DESELECT")
