"""Blender Gothic Tools - add-on info and initialization.

Based on KrxImpExp by Vitaly (Kerrax) Baranov, Patrix, Shoun, HRY.
Native Blender dialogs (file-browser sidebar options),
no external GUI processes, no third-party dependencies. Blender 4.2+ / 5.x.

Licence: GPL
"""
import bpy

from . import game_data, gothic_ui, icons, log, operators, preferences, tex_convert
from .impexp import KrxImportExportManager
from .operators import (
    BatAscImpGUI,
    Krx3dsExpGUI,
    Krx3dsImpGUI,
    KrxAscExpGUI,
    KrxManImpGUI,
    KrxMdlImpGUI,
    KrxMdmImpGUI,
    KrxMmbImpGUI,
    KrxMrmImpGUI,
    KrxMshImpGUI,
    KrxZenImpGUI,
)
from .system import prune_logs

bl_info = {
    "name": "Blender Gothic Tools",
    "description": "Gothic 1 / Gothic 2a import-export, based on KrxImpExp",
    "author": "Kerrax, Patrix, Shoun, HRY, HoovyTube",
    "version": (3, 17, 2),
    "blender": (4, 2, 0),
    "location": "File > Import-Export",
    "doc_url": "https://github.com/hoovytubeofficial/BlenderGothicTools",
    "tracker_url": "https://github.com/hoovytubeofficial/BlenderGothicTools/issues",
    "support": "COMMUNITY",
    "category": "Import-Export",
}


def register():
    """Register preferences, support classes and all import/export operators"""

    preferences.register()
    operators.register()

    version = ".".join(str(part) for part in bl_info["version"])
    log.info(f"=== {bl_info['name']} v{version} loaded (Blender {bpy.app.version_string}) ===")

    addon_entry = bpy.context.preferences.addons.get(__package__)
    if addon_entry:
        log.set_developer(addon_entry.preferences.developer_mode)

    KrxImportExportManager.register(Krx3dsImpGUI, "3DS", "Kerrax 3D Studio Mesh")
    KrxImportExportManager.register(BatAscImpGUI, "ASC", "Shoun's ASCII Model")
    KrxImportExportManager.register(KrxMshImpGUI, "MSH", "Kerrax Compiled Mesh")
    KrxImportExportManager.register(KrxMrmImpGUI, "MRM", "Kerrax Multi-Resolution Mesh")
    KrxImportExportManager.register(KrxZenImpGUI, "ZEN", "Kerrax ZenGin World")
    KrxImportExportManager.register(KrxMmbImpGUI, "MMB", "Kerrax MorphMesh Binary")
    KrxImportExportManager.register(KrxMdmImpGUI, "MDM", "Kerrax Model Mesh")
    KrxImportExportManager.register(KrxMdlImpGUI, "MDL", "Kerrax Model")
    KrxImportExportManager.register(KrxManImpGUI, "MAN", "Kerrax Compiled Animation")

    KrxImportExportManager.register(Krx3dsExpGUI, "3DS", "Kerrax 3D Studio Mesh")
    KrxImportExportManager.register(KrxAscExpGUI, "ASC", "Kerrax ASCII Model")

    KrxImportExportManager.register_menus()
    gothic_ui.register()


def unregister():
    """Unregister each feature that was previously registered"""

    # keep the session's console output next to the logs so it can be read later
    from . import log
    from .system import LOG_DIR, LOG_FILE_NAME

    log.dump_to(LOG_DIR / LOG_FILE_NAME)

    gothic_ui.unregister()
    KrxImportExportManager.unregister_all()
    operators.unregister()
    preferences.unregister()
    icons.unregister()

    prune_logs()
