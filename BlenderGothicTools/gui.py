# ----------------------------------------------------
# Native message box / notifications
#
# The original KrxImpExp spawned an external
# DearPyGui process for every dialog. This
# edition uses native Blender UI: import/export
# options live in the file-browser sidebar
# (see operators.py) and message boxes are native
# popups. No external processes, no dependencies.
# ----------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import bpy

from .preferences import KrxImpExpPreferencesManager

_ICONS = {
    "E": "ERROR",
    "W": "ERROR",
    "I": "INFO",
}

_TITLES = {
    "E": "KrxImpExp - Error",
    "W": "KrxImpExp - Warning",
    "I": "KrxImpExp - Information",
}


def call_message_box(message_text: str = "Code ?: MessageBox was called from Blender without message!", message_type: str = "E"):
    """Show a native Blender popup with the given message (console-only in background mode)."""

    icon = _ICONS.get(message_type, "ERROR")
    title = _TITLES.get(message_type, _TITLES["E"])
    lines = [line for line in str(message_text).splitlines() if line.strip()]

    print(f"[{message_type}] {message_text}", level="WARN" if message_type == "E" else "INFO")
    try:
        KrxImpExpPreferencesManager.write_log(f"[{message_type}] {message_text}")
    except Exception:
        pass

    # popup_menu with no window crashes Blender (EXCEPTION_ACCESS_VIOLATION)
    if bpy.app.background or bpy.context.window is None:
        return None

    def draw(menu, _context):
        for line in lines:
            menu.layout.label(text=line)

    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
    return None
