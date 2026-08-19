# icons.py: custom menu icons for the import/export menus.
# -------------------------------------------------------------------------------------------------------
# Loads every PNG from the bundled icons/ folder into a bpy.utils.previews
# collection. Keys are the lowercased file stems without the "format_" prefix,
# e.g. "Format_zen.png" -> "zen", "Format_revamped.png" -> "revamped".
# -------------------------------------------------------------------------------------------------------
from .system import PLUGIN_ROOT

_preview_collection = None


def _ensure_loaded():
    global _preview_collection

    if _preview_collection is None:
        import bpy.utils.previews

        _preview_collection = bpy.utils.previews.new()
        icons_dir = PLUGIN_ROOT / "icons"
        if icons_dir.is_dir():
            for png in sorted(icons_dir.glob("*.png")):
                key = png.stem.lower()
                for prefix in ("format_", "iconsupplement_", "tool_"):
                    if key.startswith(prefix):
                        key = key[len(prefix):]
                        break
                if key not in _preview_collection:
                    _preview_collection.load(key, str(png), "IMAGE")

    return _preview_collection


def icon_id(key: str) -> int:
    """Return the icon_value for a key ('zen', 'asc', 'revamped', ...), 0 if missing."""
    try:
        return _ensure_loaded()[key.lower()].icon_id
    except Exception:
        return 0


def unregister():
    global _preview_collection

    if _preview_collection is not None:
        import bpy.utils.previews

        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
