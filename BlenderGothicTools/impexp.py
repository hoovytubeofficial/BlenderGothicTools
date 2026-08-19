# impexp.py: Helper utilities to make importers and exporters.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix, Shoun, Kamil "HRY" Krzyśków
# License: GPL
# -------------------------------------------------------------------------------------------------------
# Operators are regular Blender operators whose options draw natively
# in the file-browser sidebar. Each importer/exporter supplies a mixin class with
# operator properties, an optional draw()/krx_on_invoke(), and a krx_run() method.
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import os.path
import time
from typing import Callable, Dict, List, Tuple, Type, Union

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import icons
from .material import loaded_texture_paths
from .preferences import KrxImpExpPreferences
from .scene import end_editmode, end_posemode
from .gui import call_message_box
from .helpers import BugNotice


class OperatorPlaceholder:
    """Used as a placeholder while the global OPERATOR isn't in use"""

    def report(self, *args, **kwargs):
        ...


OPERATOR = OperatorPlaceholder()
UPSTREAM_BUGS: List[BugNotice] = [
    BugNotice(
        text="Since version {first_seen} of Blender there is a crash with bmesh.ops.split.\n{resolution}",
        first_seen=(4, 4, 0),
        resolved_in=(4, 5, 1)
    )
]


class OperatorSettingsHelper:
    """Class provides Operator properties/settings based on the mixin class name"""

    bl_idname_prefix: str
    bl_label_prefix: str
    current_dict: Dict[str, Tuple[Callable, Type[bpy.types.Operator]]]
    file_exists_check: bool
    helper_mixin: Type[Union[ImportHelper, ExportHelper]]

    def __init__(self, function_name: str):
        if "imp" in function_name.lower():
            self.bl_idname_prefix = "import_scene"
            self.bl_label_prefix = "Import"
            self.current_dict = KrxImportExportManager.registered_importers
            self.current_menu = bpy.types.TOPBAR_MT_file_import
            self.file_exists_check = True
            self.helper_mixin = ImportHelper
        elif "exp" in function_name.lower():
            self.bl_idname_prefix = "export_scene"
            self.bl_label_prefix = "Export"
            self.current_dict = KrxImportExportManager.registered_exporters
            self.current_menu = bpy.types.TOPBAR_MT_file_export
            self.file_exists_check = False
            self.helper_mixin = ExportHelper
        else:
            raise NotImplementedError(
                f"{function_name} is not a valid class name and can't be registered using the manager"
            )


class KRX_MT_import_gothic(bpy.types.Menu):
    """File > Import > Gothic submenu"""

    bl_idname = "KRX_MT_import_gothic"
    bl_label = "Gothic"

    def draw(self, context):
        for extension, label, bl_idname in KrxImportExportManager.import_entries:
            self.layout.operator(bl_idname, text=label, icon_value=icons.icon_id(extension))
        if hasattr(bpy.types, "KRX_OT_assemble_from_d"):
            self.layout.separator()
            self.layout.operator("krx.assemble_from_d", text="Essemble from .D (NPC script)",
                                 icon_value=icons.icon_id("d"))


class KRX_MT_export_gothic(bpy.types.Menu):
    """File > Export > Gothic submenu"""

    bl_idname = "KRX_MT_export_gothic"
    bl_label = "Gothic"

    def draw(self, context):
        for extension, label, bl_idname in KrxImportExportManager.export_entries:
            self.layout.operator(bl_idname, text=label, icon_value=icons.icon_id(extension))


def _import_menu_draw(self, _context):
    self.layout.menu(KRX_MT_import_gothic.bl_idname, icon_value=icons.icon_id("krx"))


def _export_menu_draw(self, _context):
    self.layout.menu(KRX_MT_export_gothic.bl_idname, icon_value=icons.icon_id("krx"))


def _menu_label(description: str, extension: str) -> str:
    """'Kerrax ZenGin World' + 'ZEN' -> 'ZenGin World (.zen)'"""
    label = description
    for prefix in ("Kerrax ", "Shoun's "):
        if label.startswith(prefix):
            label = label[len(prefix):]
    return f"{label} (.{extension.lower()})"


class KrxImportExportManager:
    """
    Class to manage the registering of custom Kerrax Import/Export operators.
    There should be no instance of this class.
    """

    registered_importers: Dict[str, Tuple[Callable, Type[bpy.types.Operator]]] = {}
    registered_exporters: Dict[str, Tuple[Callable, Type[bpy.types.Operator]]] = {}

    import_entries: List[Tuple[str, str, str]] = []
    """(extension, menu label, operator bl_idname) rows for the Gothic import submenu"""
    export_entries: List[Tuple[str, str, str]] = []
    """(extension, menu label, operator bl_idname) rows for the Gothic export submenu"""

    _menus_registered: bool = False

    def __init__(self):
        raise NotImplementedError(self.__doc__)

    @classmethod
    def register(cls, mixin: type, extension: str, description: str):
        """Registers either an importer or exporter operator and adds it to the appropriate menu.

        ``mixin`` must provide a ``krx_run(self, context)`` method; it may also provide
        operator property annotations, ``draw()`` and ``krx_on_invoke(context)``.
        """

        name: str = mixin.__name__.split(".").pop()
        operator_settings: OperatorSettingsHelper = OperatorSettingsHelper(name)

        if name in operator_settings.current_dict:
            raise RuntimeError(f"'{name}' was already registered in the dict")

        class CustomOperator(mixin, operator_settings.helper_mixin, bpy.types.Operator):
            bl_idname: str = f"{operator_settings.bl_idname_prefix}.{name.lower()}"
            bl_label: str = f"{operator_settings.bl_label_prefix} {extension.upper()}"
            bl_options = {"UNDO", "PRESET"}

            check_file: bool = operator_settings.file_exists_check
            filename_ext: str = f".{extension}"
            filter_glob: StringProperty(default=f"*.{extension};*.{extension.lower()}", options={"HIDDEN"})

            if operator_settings.bl_label_prefix == "Export":
                check_extension: bool = False
            else:
                # Multi-file import: the file browser allows selecting several files
                files: bpy.props.CollectionProperty(
                    type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"}
                )
                directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

            def invoke(self, context: bpy.types.Context, event):
                if hasattr(mixin, "krx_on_invoke"):
                    self.krx_on_invoke(context)
                if operator_settings.bl_label_prefix == "Import" and not self.filepath:
                    # First use: open the browser in the game's folder for this format
                    from . import game_data

                    default_dir = game_data.default_import_dir(extension)
                    if default_dir:
                        self.filepath = default_dir.rstrip("\\/") + os.sep
                return operator_settings.helper_mixin.invoke(self, context, event)

            def _krx_filepaths(self) -> List[str]:
                """All files to process: the multi-selection if present, else the single filepath."""
                if operator_settings.bl_label_prefix == "Import":
                    file_elements = getattr(self, "files", None)
                    directory = getattr(self, "directory", "")
                    if file_elements and len(file_elements) and directory:
                        paths = [os.path.join(directory, element.name) for element in file_elements if element.name]
                        if paths:
                            # a predictable order matters when several files are laid
                            # out one after another (batch animation import)
                            return sorted(paths, key=lambda path: os.path.basename(path).upper())
                return [self.filepath]

            def execute(self, context: bpy.types.Context):
                filepaths = self._krx_filepaths()

                if self.check_file:
                    missing = [fp for fp in filepaths if not os.path.exists(fp)]
                    filepaths = [fp for fp in filepaths if os.path.exists(fp)]
                    for fp in missing:
                        self.report({"WARNING"}, f"File not found: {fp}")
                    if not filepaths:
                        self.report({"ERROR"}, "No existing files to process")
                        return {"CANCELLED"}

                global OPERATOR
                placeholder = OPERATOR
                OPERATOR = self

                end_posemode()
                end_editmode()

                # Store the name of selected objects to preserve the selection
                selected_obj_names: List[str] = [obj.name for obj in bpy.context.view_layer.objects if obj.select_get()]

                addon_entry = bpy.context.preferences.addons.get(__package__)
                addon_pref: KrxImpExpPreferences = addon_entry.preferences if addon_entry else None

                if operator_settings.bl_label_prefix == "Export":
                    filepath, ext = os.path.splitext(self.filepath)
                    if not ext:
                        self.filepath = f"{filepath}{self.filename_ext}"
                    filepaths = [self.filepath]

                if (
                    operator_settings.bl_label_prefix == "Import"
                    and (addon_pref is None or addon_pref.rescan_textures_every_time)
                    and loaded_texture_paths
                ):
                    print("Clearing the loaded texture paths")
                    loaded_texture_paths.clear()

                if hasattr(self, "krx_apply_texture_settings"):
                    self.krx_apply_texture_settings()

                for bug in UPSTREAM_BUGS:
                    if bpy.app.version in bug and not bug.has_been_informed:
                        if bug.scope and f"{operator_settings.bl_label_prefix.lower()}{extension.upper()}" not in bug.scope:
                            continue
                        call_message_box(message_text=str(bug), message_type="I")
                        bug.has_been_informed = True

                result = None
                for filepath in filepaths:
                    self.filepath = filepath
                    print(f"START {name}: Processing the '{self.filepath}' file")
                    start_time = time.perf_counter()

                    try:
                        result = self.krx_run(context)
                    except Exception as ex:
                        from . import log

                        log.exception(f"{name} FAILED on '{self.filepath}'")
                        call_message_box(message_text=str(ex))
                        raise

                    print(f"END {name}, time {time.perf_counter() - start_time}: File processing FINISHED")

                # Restore the selection based on the stored names
                for obj in bpy.context.view_layer.objects:
                    obj.select_set(obj.name in selected_obj_names)

                OPERATOR = placeholder

                try:
                    bpy.ops.outliner.orphans_purge(do_recursive=True)
                except RuntimeError:
                    # No suitable context in background mode; purge manually
                    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
                        for block in list(collection):
                            if block.users == 0:
                                collection.remove(block)

                if result == {"CANCELLED"}:
                    return {"CANCELLED"}
                return {"FINISHED"}

        CustomOperator.__name__ = f"KRX_OT_{name.lower()}"
        CustomOperator.__doc__ = getattr(mixin, "__doc__", None) or f"Kerrax {extension} {operator_settings.bl_label_prefix}er"

        bpy.utils.register_class(CustomOperator)
        operator_settings.current_dict[name] = (None, CustomOperator)

        entry = (extension.lower(), _menu_label(description, extension), CustomOperator.bl_idname)
        if operator_settings.bl_label_prefix == "Import":
            cls.import_entries.append(entry)
        else:
            cls.export_entries.append(entry)

    @classmethod
    def register_menus(cls):
        """Register the Gothic submenus and hook them into File > Import / Export.
        Call once, after all operators have been registered."""

        if cls._menus_registered:
            return

        bpy.utils.register_class(KRX_MT_import_gothic)
        bpy.utils.register_class(KRX_MT_export_gothic)
        bpy.types.TOPBAR_MT_file_import.append(_import_menu_draw)
        bpy.types.TOPBAR_MT_file_export.append(_export_menu_draw)
        cls._menus_registered = True

    @classmethod
    def unregister_all(cls):
        """Unregisters the menus and each registered importer and exporter"""

        if cls._menus_registered:
            bpy.types.TOPBAR_MT_file_import.remove(_import_menu_draw)
            bpy.types.TOPBAR_MT_file_export.remove(_export_menu_draw)
            bpy.utils.unregister_class(KRX_MT_import_gothic)
            bpy.utils.unregister_class(KRX_MT_export_gothic)
            cls._menus_registered = False

        cls.import_entries.clear()
        cls.export_entries.clear()

        importers = list(cls.registered_importers.keys())
        for key in importers:
            _, operator_class = cls.registered_importers.pop(key)
            bpy.utils.unregister_class(operator_class)

        exporters = list(cls.registered_exporters.keys())
        for key in exporters:
            _, operator_class = cls.registered_exporters.pop(key)
            bpy.utils.unregister_class(operator_class)
