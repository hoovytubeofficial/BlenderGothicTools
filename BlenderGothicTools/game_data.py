# game_data.py: knowledge about a Gothic installation's folder layout, assets and scripts.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# Pure logic (no UI): default browse folders per format, a recursive asset index,
# a Daedalus (.d) instance index for resolving item visuals, and an NPC .d parser
# used by the "Essemble Character" feature.
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .preferences import get_master_folders

# Format -> preferred browse folder inside a master folder (first existing wins)
_DEFAULT_SUBDIRS = {
    "zen": (r"_work\Data\Worlds",),
    "mrm": (r"_work\Data\Meshes\_compiled",),
    "msh": (r"_work\Data\Meshes\_compiled",),
    "mmb": (r"_work\Data\Anims\_compiled",),
    "mdm": (r"_work\Data\Anims\_compiled",),
    "man": (r"_work\Data\Anims\_compiled",),
    "mdl": (r"_work\Data\Anims\_compiled",),
    "mds": (r"_work\Data\Anims",),
    "msb": (r"_work\Data\Anims\_compiled",),
    "asc": (r"_work\Data\Anims\asc_bodies", r"_work\Data\Anims"),
    "3ds": (r"_work\Data\Meshes", r"_work\Data\Meshes\_compiled"),
    "d": (r"_work\Data\Scripts\Content\Story\NPC", r"_work\Data\Scripts\Content"),
}

ASSET_EXTENSIONS = (".asc", ".3ds", ".mrm", ".msh", ".mmb", ".mdm", ".mdl", ".man", ".mds",
                    ".msb", ".zen")

# When a script references an extension we can't find (or import), try these instead.
# Gothic itself does the same dance: a ".3DS" visual is served from the compiled .MRM.
_EXTENSION_FALLBACKS = {
    ".3ds": (".3ds", ".mrm"),
    ".asc": (".asc", ".mdm", ".mdl", ".mmb"),
    ".mrm": (".mrm", ".3ds"),
    ".mmb": (".mmb", ".asc"),
    ".mdm": (".mdm", ".asc", ".mdl"),
    # A monster body ("Wol_Body") is a bare token that only exists as a compiled .mdm,
    # so .mdm has to be in the bare-token chain or every monster resolves to nothing.
    "": (".asc", ".mdm", ".3ds", ".mrm", ".mmb", ".mdl"),
}


# Where the bundled examples live, used only when the game folders are unavailable
_SAMPLE_SUBDIRS = {
    "man": "animations",
    "mdh": "animations",
    "mmb": "meshes",
    "mrm": "meshes",
    "msh": "meshes",
    "asc": "models",
    "d": "scripts",
    "3ds": "slot_meshes",
}


def sample_dir(extension: str) -> Optional[str]:
    """The bundled _Samples folder for a format, if it exists."""
    from .system import SAMPLES_DIR

    subdir = _SAMPLE_SUBDIRS.get(extension.lower().lstrip("."))
    if subdir:
        candidate = SAMPLES_DIR / subdir
        if candidate.is_dir():
            return str(candidate)
    return str(SAMPLES_DIR) if SAMPLES_DIR.is_dir() else None


def browse_folders_locked() -> bool:
    """True when the user asked the dialogs to stay where they are (see preferences)."""
    import bpy

    # preferences.addons.get(), not [...] - the add-on is not registered while Blender
    # is still loading modules, and [] raises there.
    entry = bpy.context.preferences.addons.get(__package__)
    return bool(entry and getattr(entry.preferences, "lock_browse_folders", False))


def default_import_dir(extension: str) -> Optional[str]:
    """Best existing browse folder for a format.

    The real game folders win; the bundled _Samples folder is only a fallback for when
    no master folder is configured (or does not contain that kind of file). Returns None
    when browse-folder retargeting is switched off, which leaves the dialog wherever the
    user was last instead of sending it to whatever install is configured."""
    if browse_folders_locked():
        return None

    extension = extension.lower().lstrip(".")

    for master in get_master_folders():
        for subdir in _DEFAULT_SUBDIRS.get(extension, ()):
            candidate = os.path.join(master, subdir)
            if os.path.isdir(candidate):
                return candidate

    for master in get_master_folders():
        if os.path.isdir(master):
            return master

    return sample_dir(extension)


# -------------------------------------------------------------------------------------------------------
# Asset index: every model file under the master folders, by upper-case stem
# -------------------------------------------------------------------------------------------------------

_ASSET_CACHE = {"key": None, "index": None}


def asset_index(force: bool = False) -> Dict[str, Dict[str, str]]:
    """{STEM_UPPER: {ext: path}} for every asset file under the master folders' _work\\Data."""
    masters = tuple(get_master_folders())
    if not force and _ASSET_CACHE["index"] is not None and _ASSET_CACHE["key"] == masters:
        return _ASSET_CACHE["index"]

    index: Dict[str, Dict[str, str]] = {}
    for master in masters:
        data_root = Path(master) / "_work" / "Data"
        if not data_root.is_dir():
            data_root = Path(master)
        for path in data_root.rglob("*"):
            ext = path.suffix.lower()
            if ext in ASSET_EXTENSIONS and path.is_file():
                index.setdefault(path.stem.upper(), {}).setdefault(ext, str(path))

    _ASSET_CACHE["key"] = masters
    _ASSET_CACHE["index"] = index
    print(f"asset index built - {len(index)} stems")
    return index


def find_asset(name: str) -> Optional[str]:
    """Find an asset file by name ('Armor_Vlk_L.asc', 'ItMw_045_1h_Sword_Bastard_01.3DS'
    or a bare stem). Falls back across related extensions like the game engine does."""
    if not name:
        return None
    stem, ext = os.path.splitext(name.strip().strip('"'))
    entry = asset_index().get(stem.upper())
    if not entry:
        return None
    for candidate in _EXTENSION_FALLBACKS.get(ext.lower(), (ext.lower(),) if ext else _EXTENSION_FALLBACKS[""]):
        if candidate in entry:
            return entry[candidate]
    # last resort: any file with that stem
    return next(iter(entry.values()), None)


# -------------------------------------------------------------------------------------------------------
# Daedalus script index: instance name -> visuals (for items, armors, ...)
# -------------------------------------------------------------------------------------------------------

_SCRIPT_CACHE = {"key": None, "instances": None, "constants": None}

_RE_INSTANCE_SPLIT = re.compile(r"^[ \t]*instance[ \t]+([A-Za-z0-9_, \t]+?)\s*\(", re.IGNORECASE | re.MULTILINE)
_RE_VISUAL = re.compile(r'\bvisual\s*=\s*"([^"]+)"', re.IGNORECASE)
_RE_VISUAL_CHANGE = re.compile(r'\bvisual_change\s*=\s*"([^"]+)"', re.IGNORECASE)
_RE_CONST_INT = re.compile(r"^\s*const\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;", re.IGNORECASE | re.MULTILINE)


def _build_script_data(masters):
    instances: Dict[str, Dict[str, str]] = {}
    constants: Dict[str, int] = {}
    for master in masters:
        content = Path(master) / "_work" / "Data" / "Scripts" / "Content"
        if not content.is_dir():
            continue
        for path in content.rglob("*.d"):
            try:
                text = path.read_text(encoding="Windows-1250", errors="replace")
            except OSError:
                continue

            for const in _RE_CONST_INT.finditer(text):
                constants.setdefault(const.group(1).upper(), int(const.group(2)))

            matches = list(_RE_INSTANCE_SPLIT.finditer(text))
            for i, match in enumerate(matches):
                block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                block = text[match.end():block_end]
                visual = _RE_VISUAL.search(block)
                visual_change = _RE_VISUAL_CHANGE.search(block)
                for raw_name in match.group(1).split(","):
                    name = raw_name.strip()
                    if not name:
                        continue
                    instances.setdefault(name.upper(), {
                        "file": str(path),
                        "visual": visual.group(1) if visual else "",
                        "visual_change": visual_change.group(1) if visual_change else "",
                    })

    _SCRIPT_CACHE["key"] = masters
    _SCRIPT_CACHE["instances"] = instances
    _SCRIPT_CACHE["constants"] = constants
    print(f"script index built - {len(instances)} instances, {len(constants)} constants")


def script_index(force: bool = False) -> Dict[str, Dict[str, str]]:
    """{INSTANCE_UPPER: {"file":, "visual":, "visual_change":}} from all Content .d scripts."""
    masters = tuple(get_master_folders())
    if force or _SCRIPT_CACHE["instances"] is None or _SCRIPT_CACHE["key"] != masters:
        _build_script_data(masters)
    return _SCRIPT_CACHE["instances"]


def constants_index(force: bool = False) -> Dict[str, int]:
    """{CONST_NAME_UPPER: int value} for every 'const int' in the Content scripts."""
    masters = tuple(get_master_folders())
    if force or _SCRIPT_CACHE["constants"] is None or _SCRIPT_CACHE["key"] != masters:
        _build_script_data(masters)
    return _SCRIPT_CACHE["constants"]


def resolve_instance_visual(instance_name: str) -> Tuple[Optional[str], str]:
    """Resolve a Daedalus instance ('ItMw_Schwert3', 'ITAR_VLK_L') to an asset file path.
    Armors prefer visual_change (the wearable .asc) over visual (the pickup mesh).
    Returns (path or None, note)."""
    entry = script_index().get(instance_name.upper())
    if not entry:
        return None, f"instance '{instance_name}' not found in scripts"

    for key in ("visual_change", "visual"):
        reference = entry[key]
        if reference:
            path = find_asset(reference)
            if path:
                return path, f"{instance_name} -> {reference} -> {os.path.basename(path)}"
            return None, f"{instance_name} -> {reference} (file not found)"
    return None, f"instance '{instance_name}' has no visual"


# -------------------------------------------------------------------------------------------------------
# NPC .d parser
# -------------------------------------------------------------------------------------------------------

_RE_NPC_NAME = re.compile(r'\bname\s*=\s*"([^"]+)"', re.IGNORECASE)
_RE_NPC_INSTANCE = re.compile(r"^\s*instance\s+([A-Za-z0-9_]+)\s*\(", re.IGNORECASE | re.MULTILINE)
_RE_NPC_VISUAL = re.compile(
    r'B_SetNpcVisual\s*\(\s*self\s*,\s*(\w+)\s*,\s*"([^"]+)"\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)',
    re.IGNORECASE,
)
# Mdl_SetVisual names the .MDS animation script, i.e. the skeleton ("Wolf.mds").
# It must NOT also match Mdl_SetVisualBody, whose first string is the body mesh.
_RE_MDL_VISUAL = re.compile(r'Mdl_SetVisual\s*\(\s*self\s*,\s*"([^"]+)"', re.IGNORECASE)
# Monsters and orcs skip B_SetNpcVisual and call this directly:
#   Mdl_SetVisualBody(self, body-mesh, body-tex, skin, head-MMS, head-tex, teeth-tex, armor)
_RE_MDL_VISUAL_BODY = re.compile(
    r'Mdl_SetVisualBody\s*\(\s*self\s*,\s*"([^"]*)"\s*,\s*([\w-]+)\s*,\s*([\w-]+)\s*,'
    r'\s*"([^"]*)"\s*,\s*([\w-]+)\s*,\s*([\w-]+)\s*,\s*([\w-]+)\s*\)',
    re.IGNORECASE,
)
_RE_SET_VISUALS_CALL = re.compile(r"^\s*(B_SetVisuals_\w+)\s*\(", re.IGNORECASE | re.MULTILINE)
_RE_SET_VISUALS_FUNC = re.compile(r"\bfunc\s+void\s+(B_SetVisuals_\w+)\s*\(", re.IGNORECASE)
_RE_OVERLAY = re.compile(r'Mdl_ApplyOverlayMds\s*\(\s*self\s*,\s*"([^"]+)"', re.IGNORECASE)
_RE_EQUIP = re.compile(r"EquipItem\s*\(\s*self\s*,\s*(\w+)\s*\)", re.IGNORECASE)
_RE_INV = re.compile(r"CreateInvItems?\s*\(\s*self\s*,\s*(\w+)", re.IGNORECASE)


def _unique(tokens) -> List[str]:
    """De-duplicate case-insensitively, keeping the first spelling and the order."""
    seen = set()
    result = []
    for token in tokens:
        key = token.upper()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return result


def _apply_npc_visual(result: dict, visual) -> None:
    """Fill a recipe from a B_SetNpcVisual(self, gender, head, face, bodyTex, armor) match."""
    result["gender"] = visual.group(1).upper()
    result["head"] = visual.group(2)
    result["face"] = visual.group(3)
    result["body_tex"] = visual.group(4)
    result["armor_instance"] = visual.group(5)
    if result["armor_instance"].upper() in ("NO_ARMOR", "0", "-1"):
        result["armor_instance"] = ""


def _find_visuals_source(path: str, text: str) -> Optional[str]:
    """Text of the file that defines the B_SetVisuals_<X>() this script only calls.

    MST_Skeleton_Lord and friends set no visual of their own - they call a helper that
    lives in another monster script. Looking only at the calling file would leave them
    with no body at all."""
    wanted = {match.group(1).upper() for match in _RE_SET_VISUALS_CALL.finditer(text)}
    if not wanted:
        return None
    folder = Path(path).parent
    for sibling in sorted(folder.glob("*.d")) + sorted(folder.parent.rglob("*.d")):
        if str(sibling) == str(path):
            continue
        try:
            other = sibling.read_text(encoding="Windows-1250", errors="replace")
        except OSError:
            continue
        if any(match.group(1).upper() in wanted for match in _RE_SET_VISUALS_FUNC.finditer(other)):
            return other
    return None


def parse_npc_file(path: str) -> dict:
    """Extract the visual recipe out of an NPC Daedalus script."""
    text = Path(path).read_text(encoding="Windows-1250", errors="replace")

    result = {
        "instance": "",
        "name": "",
        "kind": "HUMAN",
        "gender": "",
        "head": "",
        "face": "",
        "body_mesh": "",
        "body_tex": "",
        "skeleton": "",
        "armor_instance": "",
        "overlays": [],
        "equipped": [],
        "inventory": [],
    }

    instance = _RE_NPC_INSTANCE.search(text)
    if instance:
        result["instance"] = instance.group(1)

    name = _RE_NPC_NAME.search(text)
    if name:
        result["name"] = name.group(1)

    visual = _RE_NPC_VISUAL.search(text)
    if visual:
        _apply_npc_visual(result, visual)

    # Monsters and orcs: Mdl_SetVisualBody instead of B_SetNpcVisual. The call normally
    # sits in a B_SetVisuals_<X>() helper in the same file, so searching the whole text
    # finds it whichever instance calls it; a handful of scripts borrow the helper from
    # another file, which _find_visuals_source() chases down.
    body = _RE_MDL_VISUAL_BODY.search(text)
    if body is None and not visual:
        borrowed = _find_visuals_source(path, text)
        if borrowed:
            body = _RE_MDL_VISUAL_BODY.search(borrowed)
            if visual is None:
                visual = _RE_NPC_VISUAL.search(borrowed)
                if visual:
                    _apply_npc_visual(result, visual)
            text = text + "\n" + borrowed

    if body is not None and not result["armor_instance"] and not result["head"]:
        result["kind"] = "MONSTER"
        result["body_mesh"] = body.group(1).strip()
        result["body_tex"] = body.group(2).strip()
        result["head"] = body.group(4).strip()
        armor = body.group(7).strip()
        result["armor_instance"] = "" if armor.upper() in ("-1", "0", "NO_ARMOR") else armor

    mdl_visual = _RE_MDL_VISUAL.search(text)
    if mdl_visual:
        result["skeleton"] = mdl_visual.group(1)

    result["overlays"] = _unique(result["overlays"]
                                 + [match.group(1) for match in _RE_OVERLAY.finditer(text)])

    # One script file holds every variant of a creature - MST_OrcWarrior.d has ten
    # instances, each equipping an axe - so the same item is matched over and over.
    result["equipped"] = _unique(match.group(1) for match in _RE_EQUIP.finditer(text))

    equipped_upper = {e.upper() for e in result["equipped"]}
    result["inventory"] = [
        token for token in _unique(match.group(1) for match in _RE_INV.finditer(text))
        if token.upper() not in equipped_upper
    ]

    return result


# Default naked bodies used when an NPC has no armor
DEFAULT_BODIES = {
    "MALE": "Hum_Body_Naked0",
    "FEMALE": "Hum_Body_Babe0",
}


# Preferred texture folders per kind. These are the decompiled/source folders of a modkit
# install - handy for browsing - and are searched before the recursive brute index.
TEXTURE_SUBDIRS = {
    "head": r"_work\Data\Textures\NPCs\Head",
    "body": r"_work\Data\Textures\NPCs\Body",
    "monster": r"_work\Data\Textures\NPCs\Monster",
    "armor": r"_work\Data\Textures\NPCs\Armor",
    # every compiled zTEX in one place - where a .TEX browser should open
    "compiled": r"_work\Data\Textures\_compiled",
}


# Where each part of a character recipe is normally found
PART_SUBDIRS = {
    "body": (r"_work\Data\Anims\asc_bodies\armor", r"_work\Data\Anims\asc_bodies"),
    "head": (r"_work\Data\Anims\_compiled",),
    "weapon": (r"_work\Data\Meshes\_compiled", r"_work\Data\Meshes"),
    "extra": (r"_work\Data\Meshes\_compiled", r"_work\Data\Meshes"),
}


def part_dir(kind: str) -> Optional[str]:
    """First existing folder where a recipe part of this kind normally lives."""
    for master in get_master_folders():
        for subdir in PART_SUBDIRS.get(kind.lower(), ()):
            candidate = os.path.join(master, subdir)
            if os.path.isdir(candidate):
                return candidate
    return None


def find_in_dir(name: str, directory: str) -> Optional[str]:
    """Look for a file in one folder: exact name first, then the stem with any known extension."""
    if not name or not directory or not os.path.isdir(directory):
        return None
    name = name.strip().strip('"')
    candidate = os.path.join(directory, name)
    if os.path.isfile(candidate):
        return candidate

    stem = os.path.splitext(name)[0].upper()
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    for entry in entries:
        entry_stem, entry_ext = os.path.splitext(entry)
        if entry_stem.upper() == stem and entry_ext.lower() in ASSET_EXTENSIONS:
            return os.path.join(directory, entry)
    return None


def texture_dir(kind: str) -> Optional[str]:
    """First existing preferred texture folder for a kind ('head', 'body', 'monster', 'armor')."""
    subdir = TEXTURE_SUBDIRS.get(kind.lower())
    if not subdir:
        return None
    for master in get_master_folders():
        candidate = os.path.join(master, subdir)
        if os.path.isdir(candidate):
            return candidate
    return None


def find_texture(name: str, kind: str = None) -> Optional[str]:
    """Locate a texture file. Looks in the preferred folder for its kind first, then falls
    back to the brute-search index (which also covers compiled .TEX files)."""
    if not name:
        return None
    name = name.strip().strip('"')
    if not os.path.splitext(name)[1]:
        name += ".tga"

    if kind:
        folder = texture_dir(kind)
        if folder:
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate

    from . import material

    if len(material.loaded_texture_paths) <= 1:
        material._build_texture_index()
    return material.loaded_texture_paths.get(name.upper())


def resolve_int(token: str) -> Optional[int]:
    """Turn a literal int or a 'const int' name (Face_N_Lares, BodyTex_N) into its value."""
    if token is None or token == "":
        return None
    try:
        return int(token)
    except (TypeError, ValueError):
        return constants_index().get(str(token).upper())


def body_texture_name(variant: int, skin: int = 0, gender: str = "MALE") -> str:
    """Gothic's multitexture naming: V = texture variation, C = skin colour.
    B_SetNpcVisual shifts female variants 0-3 into the 4-7 range."""
    if gender == "FEMALE" and 0 <= variant <= 3:
        variant += 4
    return f"Hum_Body_Naked_V{variant}_C{skin}.tga"


def head_texture_name(variant: int, skin: int = 0) -> str:
    return f"Hum_Head_V{variant}_C{skin}.tga"


def monster_texture_name(body_mesh: str, variant: int) -> str:
    """Monster body variants are '<mesh>_V<n>.tga' - no _C skin-colour half.

    (Gob_Body_V0..V3 and Orc_BodyWarrior_V0 exist as source TGA; the human
    Hum_Body_Naked_V*_C* pairing is a human-only rule.)"""
    return f"{body_mesh}_V{variant}.tga"


def npc_texture_names(npc: dict, skin: int = 0) -> Dict[str, str]:
    """Compute the actual .tga names for a parsed NPC's body and head skins.
    Mirrors B_SetNpcVisual, which always passes skin colour 0 - override with `skin`.

    Note: there is NO armor texture in the scripts. An armor's look is baked into its
    mesh materials (Armor_Vlk_L.asc references Buerger2_1.tga); the only script-side
    knob is the item's `visual_skin`, which no vanilla Gothic 2 armor uses."""
    textures: Dict[str, str] = {}

    body = resolve_int(npc.get("body_tex"))
    if npc.get("kind") == "MONSTER":
        # A monster's skin is baked into its mesh materials; only the handful with a
        # numbered variant (the goblins) have a texture to swap in, and only if it exists.
        mesh = npc.get("body_mesh", "")
        if body is not None and mesh:
            name = monster_texture_name(mesh, body)
            if find_texture(name, "monster"):
                textures["body"] = name
        return textures

    if body is not None:
        textures["body"] = body_texture_name(body, skin, npc.get("gender", "MALE"))

    face = resolve_int(npc.get("face"))
    if face is not None:
        textures["head"] = head_texture_name(face, skin)

    return textures


def clear_caches():
    _ASSET_CACHE["key"] = None
    _ASSET_CACHE["index"] = None
    _SCRIPT_CACHE["key"] = None
    _SCRIPT_CACHE["instances"] = None
