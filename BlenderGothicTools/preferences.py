"""
preferences.py: provides all of the functions and classes related to user add-on preferences.
This file also provides GUI for each preference toggle. (it was formely in image_search.py)
-------------------------------------------------------------------------------------------------------
Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
Author: Vitaly Baranov
License: GPL
-------------------------------------------------------------------------------------------------------
"""
import os
import typing

import bpy

from . import system


def _sync_developer_mode(preferences):
    from . import log

    log.set_developer(preferences.developer_mode)


class TextureDirectory(bpy.types.PropertyGroup):
    """
    Subclass of the PropertyGroup used for the CollectionProperty type in the preferences class.
    Represents a texture directory item with the path
    """

    name: str
    """Unique name of the item and the directory path itself"""


class KrxImpExpPreferences(bpy.types.AddonPreferences):
    """AddonPreferences class of the KrxImpExp add-on"""

    bl_idname: str = __package__

    create_log_file: bpy.props.BoolProperty(
        name="Create Log File",
        description="Toggle creating log file with verbose debug data.\nMost operations won't output debug data",
        default=True,
    )

    rescan_textures_every_time: bpy.props.BoolProperty(
        name="Search textures on every import",
        description="Toggle re-scanning texture directories on every import instead of only the first import.\n"
        "Useful when you add / remove texture files while Blender is still running",
        default=True,
    )

    developer_mode: bpy.props.BoolProperty(
        name="Developer Mode (verbose console)",
        description="Print DEBUG detail to the console - per-chunk file parsing, index "
                    "contents, bone and vertex counts. Noisy, but the same detail always "
                    "goes into the diagnostics report whether this is on or not",
        default=False,
        update=lambda self, context: _sync_developer_mode(self),
    )

    lock_browse_folders: bpy.props.BoolProperty(
        name="Do Not Retarget Browse Folders",
        description="Stop the import dialogs from jumping to the game folders under the "
                    "master folders. With this on, every browser simply opens wherever "
                    "you were last - useful when shipping the add-on, so it never points "
                    "at whichever install happened to be configured on the machine it was "
                    "built on. Leave it off for normal use",
        default=False,
    )

    master_folder_1: bpy.props.StringProperty(
        name="Master Folder 1",
        description="Game installation root searched recursively for .tga textures by 'Brute Search for Textures'",
        subtype="DIR_PATH",
        default=r"C:\Program Files (x86)\Steam\steamapps\common\Gothic II",
    )

    master_folder_2: bpy.props.StringProperty(
        name="Master Folder 2",
        description="Second game installation root searched recursively for .tga textures",
        subtype="DIR_PATH",
        default=r"D:\Program Files (x86)\Steam\steamapps\common\Gothic II",
    )

    master_folder_3: bpy.props.StringProperty(
        name="Master Folder 3",
        description="Third game installation root searched recursively for .tga textures",
        subtype="DIR_PATH",
        default="",
    )

    texture_directories: bpy.props.CollectionProperty(
        name="Texture directories",
        description="Collection of texture directories",
        type=TextureDirectory,
    )

    selected_texture_directory: bpy.props.IntProperty(
        name="Selected texture directory",
        description="Index of the selected directory",
        default=-1,
        min=-1,
    )

    def draw(self, context: bpy.types.Context):
        KrxImpExpPreferencesManager.draw_preferences_panel(slf=self, context=context)


_DEFAULT_MASTER_FOLDERS = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Gothic II",
    r"D:\Program Files (x86)\Steam\steamapps\common\Gothic II",
)


def get_master_folders() -> list:
    """Existing master-folder paths from the addon preferences (property defaults as fallback)."""
    addon_entry = bpy.context.preferences.addons.get(__package__)
    if addon_entry:
        prefs = addon_entry.preferences
        candidates = (prefs.master_folder_1, prefs.master_folder_2, prefs.master_folder_3)
    else:
        candidates = _DEFAULT_MASTER_FOLDERS
    return [c for c in candidates if c and os.path.isdir(bpy.path.abspath(c))]


class KrxImpExpPreferencesManager:
    """
    Class which manages the KrxImpExp add-on preferences.
    There should be no instance of this class.
    The class properties are synced with the KrxImpExpPreferences data on each drawn frame.
    """

    def __init__(self):
        raise NotImplementedError(self.__doc__)

    addon_pref: KrxImpExpPreferences
    texture_dirs: bpy.props.CollectionProperty
    selected_index: bpy.props.IntProperty

    @classmethod
    def write_log(cls, message: str, mode: str = "a") -> None:
        addon_entry = bpy.context.preferences.addons.get(__package__)
        preferences = addon_entry.preferences if addon_entry else None

        if preferences is not None and not preferences.create_log_file:
            return

        system.write_log(message, mode)

    @classmethod
    def add_path(cls, *, path: str) -> None:
        """Add a path to the end of the list"""

        if not path:
            return

        path = os.path.join(path, "")

        if path not in cls.texture_dirs:
            item = cls.texture_dirs.add()
            item.name = path
            cls.addon_pref.selected_texture_directory = len(cls.texture_dirs) - 1

    @classmethod
    def add_path_with_subpaths(cls, *, path: str):
        """Add multiple paths to the end of the list"""

        if not path:
            return

        for root, _, __ in os.walk(path, followlinks=True):
            cls.add_path(path=root)

    @classmethod
    def move_path(cls, *, direction: int) -> None:
        """Move the currently selected path in a certain direction"""

        new_index = cls.selected_index + direction

        if 0 <= new_index < len(cls.texture_dirs):
            cls.texture_dirs.move(cls.selected_index, new_index)
            cls.addon_pref.selected_texture_directory = new_index

    @classmethod
    def remove_path(cls) -> None:
        """Remove the currently selected path"""

        if not cls.texture_dirs or cls.selected_index not in range(len(cls.texture_dirs)):
            return

        cls.texture_dirs.remove(cls.selected_index)

        if cls.selected_index >= len(cls.texture_dirs):
            cls.addon_pref.selected_texture_directory = len(cls.texture_dirs) - 1

    @classmethod
    def remove_all_paths(cls) -> None:
        """Remove all paths"""

        while cls.texture_dirs:
            cls.texture_dirs.remove(0)

        cls.addon_pref.selected_texture_directory = -1

    @classmethod
    def draw_preferences_panel(cls, *, slf: bpy.types.Panel, context: bpy.types.Context) -> None:
        layout: bpy.types.UILayout = slf.layout

        # sync the changes from the UIList with data in the Manager class
        preferences: bpy.types.Preferences = context.preferences
        cls.addon_pref = preferences.addons[__package__].preferences
        cls.texture_dirs = cls.addon_pref.texture_directories
        cls.selected_index = cls.addon_pref.selected_texture_directory

        layout.operator(operator="krxpref.open_plugin_directory", text="Open Plugin Directory", icon="WINDOW")
        layout.prop(data=cls.addon_pref, property="create_log_file")
        layout.prop(data=cls.addon_pref, property="rescan_textures_every_time")
        layout.prop(data=cls.addon_pref, property="developer_mode", icon="CONSOLE")
        layout.prop(data=cls.addon_pref, property="lock_browse_folders", icon="FILE_FOLDER")

        box = layout.box()
        box.label(text="Master folders (game roots, searched recursively for textures)", icon="FILE_FOLDER")
        box.prop(data=cls.addon_pref, property="master_folder_1")
        box.prop(data=cls.addon_pref, property="master_folder_2")
        box.prop(data=cls.addon_pref, property="master_folder_3")

        layout.label(text="List of texture directories (used by the KrxImpExp scripts)", icon="OUTLINER")

        row_textures_content: bpy.types.UILayout = layout.row()
        row_textures_content.template_list(
            listtype_name="KRXPREF_UL_list_slot",
            list_id="",
            dataptr=cls.addon_pref,
            propname="texture_directories",
            active_dataptr=cls.addon_pref,
            active_propname="selected_texture_directory",
            rows=5,
            maxrows=5,
        )
        col_textures_right: bpy.types.UILayout = row_textures_content.column(align=True)
        col_textures_right.scale_x = 0.75
        col_textures_right.operator(operator="krxpref.add_path", text="Add path", icon="ADD")
        col_textures_right.operator(
            operator="krxpref.add_path_with_subpaths", text="Add path with subpaths", icon="PLUS"
        )
        col_textures_right.operator(operator="krxpref.move_path", text="Move path up", icon="TRIA_UP").direction = -1
        col_textures_right.operator(operator="krxpref.move_path", text="Move path down", icon="TRIA_DOWN").direction = 1
        col_textures_right.operator(operator="krxpref.remove_path", text="Remove path", icon="REMOVE")
        col_textures_right.operator(operator="krxpref.remove_all_paths", text="Remove all paths", icon="X")

        total: str = f"Total: {len(cls.texture_dirs)}"
        if cls.selected_index in range(len(cls.texture_dirs)):
            total += f" | Selected: {cls.selected_index + 1} - {cls.texture_dirs[cls.selected_index].name}"

        layout.label(text=total)

        if context.scene.get("texture_directories") is not None:
            layout.label(text="The current scene contains a deprecated directory list:", icon="ERROR")
            layout.operator(operator="krxpref.remove_legacy", text="Remove old paths from all scenes", icon="X")


class KRXPREF_PT_panel(bpy.types.Panel):
    """Panel to hold all of the KrxImpExp preferences"""

    bl_space_type: str = "PREFERENCES"
    bl_region_type: str = "WINDOW"
    bl_label: str = "KrxImpExp Add-on Preferences"

    def draw(self, context: bpy.types.Context):
        KrxImpExpPreferencesManager.draw_preferences_panel(slf=self, context=context)


class KRXPREF_OT_open_plugin_directory(bpy.types.Operator):
    """Opens plugin root directory, which could contain logs"""

    bl_idname = "krxpref.open_plugin_directory"
    bl_label = "Open Plugin Directory"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        system.open_plugin_directory()
        return {"FINISHED"}


class KRXPREF_UL_list_slot(bpy.types.UIList):
    """List of texture directories"""

    def draw_item(
        self,
        context: bpy.types.Context,
        layout: bpy.types.UILayout,
        data: bpy.types.AnyType,
        item: TextureDirectory,
        icon: int,
        active_data: bpy.types.AnyType,
        active_property: str,
        index: int = 0,
        flt_flag: int = 0,
    ):
        layout.label(text=item.name)


class KRXPREF_OT_add_path(bpy.types.Operator):
    """Add a path to the list"""

    bl_idname: str = "krxpref.add_path"
    bl_label: str = "Add path"

    directory: bpy.props.StringProperty(name="Directory Path")
    """This property is used by the file browser in `invoke`"""

    def invoke(self, context: bpy.types.Context, _: bpy.types.Event) -> typing.Union[typing.Set[str], typing.Set[int]]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, _: bpy.types.Context) -> typing.Union[typing.Set[str], typing.Set[int]]:
        KrxImpExpPreferencesManager.add_path(path=self.directory)
        bpy.ops.wm.save_userpref()
        return {"FINISHED"}


class KRXPREF_OT_add_path_with_subpaths(bpy.types.Operator):
    """Add a path with subpaths to the list"""

    bl_idname: str = "krxpref.add_path_with_subpaths"
    bl_label: str = "Add path with subpaths"

    directory: bpy.props.StringProperty(name="Directory Path")
    """This property is used by the file browser in `invoke`"""

    def invoke(self, context: bpy.types.Context, _: bpy.types.Event) -> typing.Union[typing.Set[str], typing.Set[int]]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, _: bpy.types.Context) -> typing.Union[typing.Set[str], typing.Set[int]]:
        KrxImpExpPreferencesManager.add_path_with_subpaths(path=self.directory)
        bpy.ops.wm.save_userpref()
        return {"FINISHED"}


class KRXPREF_OT_move_path(bpy.types.Operator):
    """Change order of the list items"""

    bl_idname: str = "krxpref.move_path"
    bl_label: str = "Move path"

    direction: bpy.props.IntProperty(name="Index offset", default=0, min=-1, max=1)

    def invoke(self, _: bpy.types.Context, __: bpy.types.Event) -> typing.Union[typing.Set[str], typing.Set[int]]:
        KrxImpExpPreferencesManager.move_path(direction=self.direction)
        bpy.ops.wm.save_userpref()
        return {"FINISHED"}


class KRXPREF_OT_remove_path(bpy.types.Operator):
    """Remove a path from the list"""

    bl_idname: str = "krxpref.remove_path"
    bl_label: str = "Remove path"

    def invoke(self, _: bpy.types.Context, __: bpy.types.Event) -> typing.Union[typing.Set[str], typing.Set[int]]:
        KrxImpExpPreferencesManager.remove_path()
        bpy.ops.wm.save_userpref()
        return {"FINISHED"}


class KRXPREF_OT_remove_all_paths(bpy.types.Operator):
    """Remove all paths from the list"""

    bl_idname: str = "krxpref.remove_all_paths"
    bl_label: str = "Remove all paths"

    def invoke(self, _: bpy.types.Context, __: bpy.types.Event) -> typing.Union[typing.Set[str], typing.Set[int]]:
        KrxImpExpPreferencesManager.remove_all_paths()
        bpy.ops.wm.save_userpref()
        return {"FINISHED"}


class KRXPREF_OT_remove_legacy(bpy.types.Operator):
    """Remove legacy `texture_directories` from all scenes"""

    bl_idname: str = "krxpref.remove_legacy"
    bl_label: str = "Remove old paths from all scenes"

    def invoke(self, _: bpy.types.Context, __: bpy.types.Event) -> typing.Union[typing.Set[str], typing.Set[int]]:
        for scene in bpy.data.scenes:
            if scene.get("texture_directories") is not None:
                del scene["texture_directories"]
            if scene.get("selected_texture_directory") is not None:
                del scene["selected_texture_directory"]

        return {"FINISHED"}


def register():
    bpy.utils.register_class(TextureDirectory)
    bpy.utils.register_class(KrxImpExpPreferences)

    bpy.utils.register_class(KRXPREF_OT_open_plugin_directory)

    bpy.utils.register_class(KRXPREF_PT_panel)
    bpy.utils.register_class(KRXPREF_UL_list_slot)
    bpy.utils.register_class(KRXPREF_OT_add_path)
    bpy.utils.register_class(KRXPREF_OT_add_path_with_subpaths)
    bpy.utils.register_class(KRXPREF_OT_move_path)
    bpy.utils.register_class(KRXPREF_OT_remove_path)
    bpy.utils.register_class(KRXPREF_OT_remove_all_paths)
    bpy.utils.register_class(KRXPREF_OT_remove_legacy)


def unregister():
    bpy.utils.unregister_class(KrxImpExpPreferences)
    bpy.utils.unregister_class(TextureDirectory)

    bpy.utils.unregister_class(KRXPREF_OT_open_plugin_directory)

    bpy.utils.unregister_class(KRXPREF_OT_remove_legacy)
    bpy.utils.unregister_class(KRXPREF_OT_remove_all_paths)
    bpy.utils.unregister_class(KRXPREF_OT_remove_path)
    bpy.utils.unregister_class(KRXPREF_OT_move_path)
    bpy.utils.unregister_class(KRXPREF_OT_add_path_with_subpaths)
    bpy.utils.unregister_class(KRXPREF_OT_add_path)
    bpy.utils.unregister_class(KRXPREF_UL_list_slot)
    bpy.utils.unregister_class(KRXPREF_PT_panel)
