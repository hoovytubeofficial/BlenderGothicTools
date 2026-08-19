# scene.py: Scene utilities.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix, Shoun, Kamil "HRY" Krzyśków
# License: GPL
# -------------------------------------------------------------------------------------------------------
from pathlib import Path

import bpy
from enum import IntEnum
from .types import is_valid_tuple_instance


# The maximum frame index, which is possible to set in Blender.
MAX_SCENE_FRAME_LIMIT = 300000

class SceneMode(IntEnum):
    REPLACE = 1
    MERGE = 2
    LINK_SLOT_TO_OBJECT = 3
    LINK_BONE_TO_OBJECT = 4
    MERGE_SOFTSKIN_MESH = 5
    REPLACE_SOFTSKIN_MESH = 6
    REPLACE_ANIM = 7
    MERGE_ANIM = 8


def get_scene_filename() -> str:
    """Get the current scene's filename (which ends with ".blend"), without path."""
    return Path(bpy.data.filepath).name


def remove_loose_items(*, collection: bpy.types.bpy_prop_collection, max_users: int = 0) -> None:
    """Remove items from a given collection if they have `max_users` connected to them or less"""

    for item in reversed(collection):
        if item.users <= max_users:
            collection.remove(item)


def reset_scene() -> None:
    """Reset the current scene"""

    scene: bpy.types.Scene = bpy.context.scene

    for child_collection in scene.collection.children:
        scene.collection.children.unlink(child_collection)

    for child_object in scene.collection.objects:
        scene.collection.objects.unlink(child_object)

    bpy.ops.outliner.orphans_purge(do_recursive=True)


def change_active_view_layer(obj):
    if obj.name not in bpy.context.view_layer.objects:
        for scene in bpy.data.scenes:
            for in_scene_obj in scene.objects:
                if in_scene_obj == obj:
                    bpy.context.window.scene = scene
    bpy.context.view_layer.objects.active = obj


# Starts object's editing.
def start_editmode(extended_obj):
    if isinstance(extended_obj, bpy.types.Object):
        if bpy.context.edit_object != extended_obj:
            if bpy.context.edit_object:
                bpy.ops.object.editmode_toggle()
            change_active_view_layer(extended_obj)
            bpy.ops.object.editmode_toggle()
    elif is_valid_tuple_instance(extended_obj):
        obj, bone_name = extended_obj
        start_editmode(obj)
        armature = obj.data
        armature.bones.active = armature.bones[bone_name]
    else:
        raise TypeError("start_editmode: error with argument 1")


# Ends object's editing.
def end_editmode():
    if bpy.context.edit_object:
        bpy.ops.object.editmode_toggle()


# Starts object's editing.
def start_posemode(extended_obj):
    if isinstance(extended_obj, bpy.types.Object) and isinstance(extended_obj.data, bpy.types.Armature):
        if bpy.context.active_pose_bone not in list(extended_obj.pose.bones):
            if bpy.context.active_pose_bone:
                bpy.ops.object.posemode_toggle()
            change_active_view_layer(extended_obj)
            bpy.ops.object.posemode_toggle()
    elif is_valid_tuple_instance(extended_obj):
        obj = extended_obj[0]
        start_posemode(obj)
    else:
        raise TypeError("start_editmode: error with argument 1")


# Ends object's editing.
def end_posemode():
    if bpy.context.active_pose_bone:
        bpy.ops.object.posemode_toggle()
