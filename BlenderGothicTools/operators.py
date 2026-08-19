# operators.py: Native Blender import/export dialogs.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# Every dialog that used to be an external DearPyGui window is now a set of operator
# properties drawn natively in the file browser sidebar. The mixin classes below are
# composed into full operators by impexp.KrxImportExportManager.register().
#
# NOTE: enum items= and update= callbacks can be invoked by Blender on a bare RNA
# instance (e.g. while converting bpy.ops keyword arguments) which has the operator's
# *properties* but not the mixin's Python methods. All callback logic therefore lives
# in module-level functions with module-level caches; the caches also keep the enum
# item strings referenced (required by Blender).
# -------------------------------------------------------------------------------------------------------
import os
from typing import Optional

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from .helpers import AscType, ObjectType, TSceneAnalyzer, TTimeTransform
from .scene import MAX_SCENE_FRAME_LIMIT, SceneMode

# Sentinel for enum identifiers that represent the empty model prefix
EMPTY_PREFIX = "__EMPTY__"
NO_TARGETS = "__NONE__"

BONE_STYLE_ITEMS = (
    ("OCTAHEDRAL", "Octahedral", "Octahedral bone display"),
    ("STICK", "Stick", "Stick bone display"),
    ("BBONE", "BBone", "B-Bone display"),
    ("ENVELOPE", "Envelope", "Envelope bone display"),
    ("WIRE", "Wire", "Wire bone display"),
)

SCALE_IMPORT_DESC = (
    "Scaling is calculated as a ratio between importing and exporting an object. "
    "The scale chosen during import is reciprocated when exporting "
    "(import at 0.01 -> export at 100). Gothic default: 0.01 (file cm -> Blender m)"
)
SCALE_EXPORT_DESC = (
    "Scaling is calculated as a ratio between importing and exporting an object. "
    "Gothic default: 100 (Blender m -> file cm)"
)
COLOR_ADJUST_DESC = "Material diffuse color saturation boost in percent (0 = unchanged)"
SECTORED_DESC = (
    "Examine BSP sectored interior materials imported from the world (prefix 'S:'), "
    "remove them and reassign the faces to the normal materials"
)


def _prefix_display(prefix: str) -> str:
    return prefix if prefix else "(Empty)"


def _encode_prefix(prefix: str) -> str:
    return prefix if prefix else EMPTY_PREFIX


def _decode_prefix(identifier: str) -> str:
    return "" if identifier in (EMPTY_PREFIX, NO_TARGETS) else identifier


# -------------------------------------------------------------------------------------------------------
# Module-level caches (also keep enum item strings alive for Blender)
# -------------------------------------------------------------------------------------------------------

_ITEM_REFS = {}
"""Keeps the last returned items list per callback key referenced"""

_SCENE_CACHE = {"key": None, "analyzer": None}
_ASC_CACHE = {"key": None, "asc": None, "analyzer": None, "error": None}


def _scene_key():
    scene = bpy.context.scene
    try:
        return (scene.name, len(scene.objects), len(bpy.data.objects))
    except Exception:
        return None


def _scene_analyzer(force: bool = False) -> TSceneAnalyzer:
    """Cached scene analysis; recomputed when the scene shape changes or on force."""
    key = _scene_key()
    if force or _SCENE_CACHE["analyzer"] is None or _SCENE_CACHE["key"] != key:
        _SCENE_CACHE["key"] = key
        _SCENE_CACHE["analyzer"] = TSceneAnalyzer()
    return _SCENE_CACHE["analyzer"]


def _asc_analysis(filepath: str, force: bool = False):
    """Parse the ASC file and analyze the scene against it. Cached per (path, mtime, scene)."""
    from .BatAscImp import ASCParser

    try:
        mtime = os.path.getmtime(filepath)
    except OSError:
        return None, None, "Select an .ASC file to see its options"

    key = (filepath, mtime, _scene_key())
    if not force and _ASC_CACHE["key"] == key:
        return _ASC_CACHE["asc"], _ASC_CACHE["analyzer"], _ASC_CACHE["error"]

    _ASC_CACHE["key"] = key
    _ASC_CACHE["asc"] = None
    _ASC_CACHE["analyzer"] = None
    _ASC_CACHE["error"] = None

    try:
        asc = ASCParser(filename=filepath)
    except Exception as ex:
        _ASC_CACHE["error"] = f"Not a valid ASC file: {ex}"
        return None, None, _ASC_CACHE["error"]

    analyzer = TSceneAnalyzer()
    if asc.model_type == AscType.MORPH_ANIM:
        analyzer.find_appropriate_morph_meshes(asc.objects_stats)
    elif asc.model_type in (AscType.DYNAMIC_MESH, AscType.DYNAMIC_ANIM):
        analyzer.find_appropriate_dynamic_models(asc.objects_stats)

    _ASC_CACHE["asc"] = asc
    _ASC_CACHE["analyzer"] = analyzer
    return asc, analyzer, None


def _keep(key: str, items: list) -> list:
    _ITEM_REFS[key] = items
    return items


def _is_anim_type(asc) -> bool:
    return asc is not None and asc.model_type in (AscType.DYNAMIC_ANIM, AscType.MORPH_ANIM)


def _is_morph_mesh_type(asc) -> bool:
    return asc is not None and asc.model_type == AscType.MORPH_MESH


# -------------------------------------------------------------------------------------------------------
# Module-level enum item callbacks
# -------------------------------------------------------------------------------------------------------


def krx3ds_scene_mode_items(op, context):
    # "Replace scene" is disabled for all imports (troubleshooting measure) - merge only.
    analyzer = _scene_analyzer()
    items = [
        ("MERGE", "Merge", "Merge imported objects with the current scene", 1),
    ]
    if analyzer.scene_slot_names:
        items.append(("SLOT", "Replace Slot", "Replace a slot with the imported object", 2))
    if analyzer.scene_bone_names:
        items.append(("BONE", "Link to Bone", "Link the root of the imported object to a bone", 3))
    return _keep("krx3ds_scene_mode", items)


def scene_slot_items(op, context):
    analyzer = _scene_analyzer()
    selected = set(analyzer.selected_slot_names)
    items = [
        (name, name, "Slot in the current scene", "EMPTY_DATA", i)
        for i, name in enumerate(analyzer.scene_slot_names)
    ]
    items.sort(key=lambda entry: entry[0] not in selected)
    return _keep("scene_slots", items or [(NO_TARGETS, "(no slots)", "", 0)])


def scene_bone_items(op, context):
    analyzer = _scene_analyzer()
    selected = set(analyzer.selected_bone_names)
    items = [
        (name, name, "Bone in the current scene", "BONE_DATA", i)
        for i, name in enumerate(analyzer.scene_bone_names)
    ]
    items.sort(key=lambda entry: entry[0] not in selected)
    return _keep("scene_bones", items or [(NO_TARGETS, "(no bones)", "", 0)])


def batasc_action_items(op, context):
    # "Replace scene" is disabled for all imports (troubleshooting measure) - merge only.
    asc, analyzer, _error = _asc_analysis(op.filepath)

    if asc is None:
        items = [
            ("NEW_MERGE", "Merge", "Merge imported objects with the current scene", 1),
        ]
    elif _is_anim_type(asc):
        items = [
            ("ANIM_REPLACE", "Replace Animation", "Completely replace the current model's animation", 0),
            ("ANIM_MERGE", "Merge Animation", "Merge the imported animation with the current model's animation", 1),
        ]
    elif _is_morph_mesh_type(asc):
        items = [
            ("NEW_MERGE", "Merge", "Merge imported objects with the current scene", 1),
        ]
        if analyzer and analyzer.scene_slot_names:
            items.append(("LINK_SLOT", "Link to Slot", "Link the root of the imported object to a slot", 2))
        if analyzer and analyzer.scene_bone_names:
            items.append(("LINK_BONE", "Link to Bone", "Link the root of the imported object to a bone", 3))
    else:
        items = [
            ("NEW_MERGE", "New Model: Merge", "Merge the imported model with the current scene", 1),
        ]
        if analyzer and analyzer.appropriate_prefixes:
            items.append(
                ("SKIN_REPLACE", "Existing Model: Replace Skin",
                 "Replace the current model's skin with the imported skin", 2)
            )
            items.append(
                ("SKIN_MERGE", "Existing Model: Merge Skin",
                 "Merge the current model's skin with the imported skin", 3)
            )

    return _keep("batasc_action", items)


def batasc_target_prefix_items(op, context):
    _asc, analyzer, _error = _asc_analysis(op.filepath)
    prefixes = list(analyzer.appropriate_prefixes) if analyzer else []
    items = [
        (_encode_prefix(prefix), _prefix_display(prefix), "Compatible model in the scene", i)
        for i, prefix in enumerate(prefixes)
    ]
    return _keep("batasc_target", items or [(NO_TARGETS, "(no compatible models)", "", 0)])


def ascexp_prefix_items(op, context):
    analyzer = _scene_analyzer()
    items = [
        (_encode_prefix(h.model_prefix), _prefix_display(h.model_prefix), "Model in the scene", i)
        for i, h in enumerate(analyzer.model_hierarchies)
    ]
    return _keep("ascexp_prefix", items or [(NO_TARGETS, "(no models to export)", "", 0)])


# -------------------------------------------------------------------------------------------------------
# Module-level update callbacks / populate helpers
# -------------------------------------------------------------------------------------------------------


def ascexp_repopulate(op, force_scene: bool = False):
    """Fill the ASC exporter's object list for the chosen model prefix and export type."""
    analyzer = _scene_analyzer(force=force_scene)
    prefix = _decode_prefix(op.model_prefix)

    hierarchy = None
    for candidate in analyzer.model_hierarchies:
        if candidate.model_prefix == prefix:
            hierarchy = candidate
            break
    if hierarchy is None and analyzer.model_hierarchies:
        hierarchy = analyzer.model_hierarchies[0]

    op.export_objects.clear()
    if hierarchy is None:
        return None

    anim = op.export_type == "ANIM"
    dynamic = hierarchy.model_type == int(AscType.DYNAMIC_MESH + AscType.DYNAMIC_ANIM)

    depths = {}
    for name, parent, otype in zip(hierarchy.objects, hierarchy.object_parents, hierarchy.object_types):
        depth = depths.get(parent, -1) + 1
        depths[name] = depth

        item = op.export_objects.add()
        item.name = name
        item.depth = depth
        item.otype = int(otype)
        is_bone_or_slot = int(otype) in (int(ObjectType.BONE), int(ObjectType.SLOT))

        if anim and dynamic:
            # animation export: bones/slots selectable, meshes not exported
            item.hidden = int(otype) == int(ObjectType.MESH)
            item.export = not item.hidden
            item.locked = False
        elif dynamic:
            # mesh export: bones/slots always exported, meshes selectable
            item.hidden = False
            item.export = True
            item.locked = is_bone_or_slot
        else:
            # morph / static: everything exported
            item.hidden = False
            item.export = True
            item.locked = True

    return hierarchy


def ascexp_on_selector_changed(op, context):
    ascexp_repopulate(op)


def item3ds_invalid(item, allow_german_chars: bool) -> bool:
    if item.verts > 65535 or item.tris > 65535 or item.verts_in_file > 65535:
        return True
    if item.nonascii:
        return True
    if item.german_chars and not allow_german_chars:
        return True
    return False


# -------------------------------------------------------------------------------------------------------
# Property groups / UI lists
# -------------------------------------------------------------------------------------------------------


class KrxExportObjectItem(bpy.types.PropertyGroup):
    """One row in an export object list"""

    # 'name' comes from PropertyGroup
    export: BoolProperty(name="Export", default=True)
    locked: BoolProperty(default=False)
    hidden: BoolProperty(default=False)
    depth: IntProperty(default=0)
    otype: IntProperty(default=0)
    verts: IntProperty(default=0)
    tris: IntProperty(default=0)
    verts_in_file: IntProperty(default=0)
    nonascii: BoolProperty(default=False)
    german_chars: BoolProperty(default=False)


_TYPE_ICONS = {
    int(ObjectType.BONE): "BONE_DATA",
    int(ObjectType.SLOT): "EMPTY_DATA",
    int(ObjectType.MESH): "MESH_DATA",
    int(ObjectType.DUMMY): "EMPTY_AXIS",
}


class KRX_UL_asc_export_objects(bpy.types.UIList):
    """Model hierarchy list for the ASC exporter"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.hidden:
            layout.label(text="")
            return
        row = layout.row(align=True)
        row.separator(factor=0.4 + 1.2 * item.depth)
        sub = row.row(align=True)
        sub.enabled = not item.locked
        sub.prop(item, "export", text="")
        row.label(text=item.name, icon=_TYPE_ICONS.get(item.otype, "DOT"))


class KRX_UL_3ds_export_objects(bpy.types.UIList):
    """Mesh list with statistics for the 3DS exporter"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        operator = getattr(context, "active_operator", None)
        if operator is None and getattr(context, "space_data", None) is not None:
            operator = getattr(context.space_data, "active_operator", None)
        allow_german = bool(getattr(operator, "allow_german_chars", False)) if operator else False
        invalid = item3ds_invalid(item, allow_german)

        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = not invalid
        sub.prop(item, "export", text="")
        row.label(text=item.name, icon="MESH_DATA")
        stats = row.row(align=True)
        stats.alignment = "RIGHT"
        if invalid:
            stats.alert = True
            over_limit = item.verts > 65535 or item.tris > 65535 or item.verts_in_file > 65535
            icon_name = "ERROR" if (over_limit or item.nonascii) else "FONT_DATA"
            stats.label(text=f"{item.verts}v / {item.tris}t / {item.verts_in_file}fv", icon=icon_name)
        else:
            stats.label(text=f"{item.verts}v / {item.tris}t / {item.verts_in_file}fv")


# -------------------------------------------------------------------------------------------------------
# Shared option mixins
# -------------------------------------------------------------------------------------------------------


class _ImportScaleMixin:
    scale: FloatProperty(
        name="Scale",
        description=SCALE_IMPORT_DESC,
        default=0.01,
        min=0.000001,
        soft_min=0.0001,
        soft_max=100.0,
        precision=6,
    )


class _ExportScaleMixin:
    scale: FloatProperty(
        name="Scale",
        description=SCALE_EXPORT_DESC,
        default=100.0,
        min=0.000001,
        soft_min=0.01,
        soft_max=100000.0,
        precision=3,
    )


class _ColorAdjustMixin:
    color_adjustment: IntProperty(
        name="Saturation Boost",
        description=COLOR_ADJUST_DESC,
        default=0,
        min=0,
        max=100,
        subtype="PERCENTAGE",
    )


class _TextureSearchMixin:
    """'Brute Search for Textures' + PBR factors, applied to every imported material."""

    game_ready_material_names: BoolProperty(
        name="Name Materials After Their Texture",
        description="Rename every material slot to its own texture file "
                    "(HUM_HEAD_V14_C0-C.DDS -> Hum_Head_V14_C0) and merge slots that end "
                    "up on the same texture. Gothic material names are the artist's 3ds "
                    "Max slot names and carry nothing an engine or another tool can use",
        default=True,
    )

    brute_search_textures: BoolProperty(
        name="Brute Search for Textures",
        description=(
            "Search the master folders (see addon preferences) recursively for matching "
            ".tga textures and connect them; the alpha channel is plugged automatically "
            "when the image contains transparency"
        ),
        default=True,
    )

    tex_metallic: FloatProperty(
        name="Metallic",
        description="Metallic factor applied to imported materials",
        default=0.0,
        min=0.0,
        max=1.0,
    )

    tex_roughness: FloatProperty(
        name="Roughness",
        description="Roughness factor applied to imported materials",
        default=1.0,
        min=0.0,
        max=1.0,
    )

    tex_ior: FloatProperty(
        name="IOR",
        description="Index of refraction applied to imported materials",
        default=1.45,
        min=1.0,
        soft_max=3.0,
        precision=3,
    )

    def krx_apply_texture_settings(self):
        from . import material

        rebuild = material.IMPORT_SETTINGS["brute_search"] != self.brute_search_textures
        material.IMPORT_SETTINGS["brute_search"] = self.brute_search_textures
        material.IMPORT_SETTINGS["metallic"] = self.tex_metallic
        material.IMPORT_SETTINGS["roughness"] = self.tex_roughness
        material.IMPORT_SETTINGS["ior"] = self.tex_ior
        if rebuild:
            material.loaded_texture_paths.clear()

    def draw_texture_options(self, layout):
        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        box.prop(self, "brute_search_textures")
        box.prop(self, "game_ready_material_names")
        col = box.column(align=True)
        col.prop(self, "tex_metallic")
        col.prop(self, "tex_roughness")
        col.prop(self, "tex_ior")


# -------------------------------------------------------------------------------------------------------
# Static mesh importers: ZEN / MSH / MRM
# -------------------------------------------------------------------------------------------------------


class _StaticMeshImportMixin(_ImportScaleMixin, _ColorAdjustMixin, _TextureSearchMixin):
    # Imported objects always MERGE into the current scene
    # ("replace scene" is disabled while import troubleshooting is ongoing).

    remove_sectored_materials: BoolProperty(
        name="Remove Sectored Materials",
        description=SECTORED_DESC,
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header = layout.box()
        header.label(text=self.krx_title, icon="IMPORT")

        col = layout.column()
        col.prop(self, "remove_sectored_materials")
        col.prop(self, "color_adjustment")
        col.prop(self, "scale")

        self.draw_texture_options(layout)

    def _krx_run_static(self, read_function):
        before = set(bpy.data.objects)
        read_function(
            self.filepath,
            self.scale,
            self.remove_sectored_materials,
            self.color_adjustment if self.color_adjustment else None,
        )
        if getattr(self, "game_ready_material_names", False):
            from .material import rename_materials_to_texture

            # only what this import brought in - never touch the rest of the scene
            renamed = rename_materials_to_texture(set(bpy.data.objects) - before)
            if renamed:
                print(f"named {renamed} material slot(s) after their texture file")


class KrxZenImpGUI(_StaticMeshImportMixin):
    """Import a ZenGin world (.zen).
A ZEN is a complete level: world mesh, VOB tree (props, lights, spots) and waynet.
Uncompiled ZENs live in _work/Data/Worlds (NewWorld, Addon, OldWorld).
Load one to bring a whole game world - or a piece of it - into Blender.
Textures are found automatically via Brute Search when master folders are set"""

    krx_title = "ZenGin World"

    import_vobs: BoolProperty(
        name="Import VOB Tree (prefabs)",
        description="A .zen holds a world mesh and a tree of VOBs - every prop, light "
                    "and effect placed in the level. The small archives in Data/Worlds "
                    "are VOB trees with NO world mesh: a torch plus its flame plus its "
                    "light, saved as one prefab. With this on they import as their props, "
                    "lights and effect markers instead of failing on the missing mesh.\n\n"
                    "Only ASCII archives - the retail worlds are BIN_SAFE and load their "
                    "world mesh either way",
        default=True,
    )

    def draw(self, context):
        super().draw(context)
        box = self.layout.box()
        box.label(text="VOBs", icon="OUTLINER_OB_GROUP_INSTANCE")
        box.prop(self, "import_vobs")

    def krx_run(self, context):
        from .KrxMshImp import zenginWorldLoader

        self._krx_run_static(
            lambda *args: zenginWorldLoader().ReadZENFile(*args, import_vobs=self.import_vobs)
        )


class KrxMshImpGUI(_StaticMeshImportMixin):
    """Import a compiled world mesh (.msh).
Binary zEngine mesh with materials, produced when a world is compiled.
It is the raw geometry part of a level, without VOBs or waynet - lighter than a full ZEN.
Load one to inspect or edit level geometry alone"""

    krx_title = "Compiled Mesh"

    def krx_run(self, context):
        from .KrxMshImp import zenginWorldLoader

        self._krx_run_static(lambda *args: zenginWorldLoader().ReadMSHFile(*args))


class KrxMrmImpGUI(_StaticMeshImportMixin):
    """Import a multi-resolution mesh (.mrm).
Compiled progressive mesh the engine streams for items and props (VOBs).
Every .3DS visual referenced by the game scripts ships compiled as .MRM in Meshes/_compiled,
so this is the actual in-game version of any item, weapon or prop.
Load one when the .3DS source does not exist in your install"""

    krx_title = "Multi-Resolution Mesh"

    def krx_run(self, context):
        from .KrxMrmImp import TMRMFileLoader

        self._krx_run_static(lambda *args: TMRMFileLoader().ReadMRMFile(*args))


NEW_ARMATURE = "__NEW__"
AUTO_ARMATURE = "__AUTO__"


def selected_armature(context=None):
    """The armature the user is working on: active object first, then the selection."""
    context = context or bpy.context
    active = context.view_layer.objects.active if context.view_layer else None
    if active is not None and active.type == "ARMATURE":
        return active
    for obj in getattr(context, "selected_objects", ()) or ():
        if obj.type == "ARMATURE":
            return obj
    if active is not None and active.parent is not None and active.parent.type == "ARMATURE":
        return active.parent
    return None


def _armature_items(op, context):
    current = selected_armature(context)
    items = [
        (AUTO_ARMATURE,
         f"Selected: {current.name}" if current else "Selected Armature (none - will build one)",
         "Use the armature you have selected. With nothing selected a skeleton is built "
         "from the animation's own .MDH", "RESTRICT_SELECT_OFF", 0),
        (NEW_ARMATURE, "Always Build New Armature",
         "Ignore the selection and build a fresh armature from the animation's .MDH "
         "skeleton", "ADD", 1),
    ]
    items.extend(
        (obj.name, obj.name, "Armature in the scene", "ARMATURE_DATA", i + 2)
        for i, obj in enumerate(o for o in bpy.data.objects if o.type == "ARMATURE")
    )
    return _keep("armatures", items)


_MAN_FPS_CACHE = {}


def _man_source_fps(filepath: str) -> float:
    """The fps in a .MAN header, without parsing the samples. Cached per path+mtime."""
    import os

    try:
        key = (filepath, os.path.getmtime(filepath))
    except OSError:
        return 0.0
    if key in _MAN_FPS_CACHE:
        return _MAN_FPS_CACHE[key]

    from .KrxManImp import ManError, read_man

    try:
        fps = float(read_man(filepath)["header"]["fps"])
    except (ManError, OSError, ValueError, KeyError):
        fps = 0.0
    _MAN_FPS_CACHE.clear()
    _MAN_FPS_CACHE[key] = fps
    return fps


def _man_scale_preset_update(op, context):
    from .KrxManImp import SCALE_BLENDER_METRES, SCALE_SOURCE_UNITS

    if op.scale_preset == "METRES":
        op.scale = SCALE_BLENDER_METRES
    elif op.scale_preset == "SOURCE":
        op.scale = SCALE_SOURCE_UNITS


class KrxManImpGUI(_ImportScaleMixin):
    """Import a compiled animation (.man).
One motion for one skeleton, sampled per frame (HUMANS-S_RUN.MAN and friends live in
Anims/_compiled). The matching skeleton file (HUMANS.MDH) is found automatically beside
it and used to map the animation's nodes onto your armature's bones BY NAME, so import
a character first and then point this at it"""

    krx_title = "Compiled Animation"

    target_armature: EnumProperty(
        name="Armature",
        description="Armature that receives the animation. By default the one you have "
                    "selected; with nothing selected a skeleton is built from the .MDH",
        items=_armature_items,
    )

    remembered_armature: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    """Selection captured when the dialog opened - the file browser has its own context"""

    scale_preset: EnumProperty(
        name="Units",
        description="Unit preset for the animation. Gothic files are in centimetres",
        items=(
            ("METRES", "Blender Metres", "Gothic centimetres to metres (0.01) - matches the importers"),
            ("SOURCE", "Source / SFM Units",
             "Gothic centimetres to Source engine units (1 unit = 0.01905 m), for animating "
             "oversized SFM ports of these characters"),
            ("CUSTOM", "Custom", "Type the scale by hand below"),
        ),
        default="METRES",
        update=_man_scale_preset_update,
    )

    frame_start: IntProperty(
        name="Start Frame",
        description="Scene frame the first animation frame lands on",
        default=1,
    )

    set_scene_range: BoolProperty(
        name="Set Scene Range and FPS",
        description="Set the scene's frame range and playback rate from the animation header",
        default=True,
    )

    rotation_mapping: EnumProperty(
        name="Rotation Mapping",
        description="TROUBLESHOOTING. How the file's packed rotation maps onto Blender's "
                    "axes. 'Derived' is the correct one for every animation tested - the "
                    "others are here so a wrong-looking animation can be identified by eye",
        items=(
            ("AUTO", "Derived (correct)", "Conjugate for 3ds Max's row-vector convention, then the Y/Z swap"),
            ("NEG_ALL", "Negate vector", "(w, -x, -z, -y)"),
            ("NO_SWAP", "No axis swap", "(w, x, y, z)"),
            ("NEG_X", "Negate X", "(w, -x, z, y)"),
            ("NEG_YZ", "Negate Y and Z", "(w, x, -z, -y)"),
            ("CONJUGATE", "Conjugate only", "(w, -x, -y, -z)"),
        ),
        default="AUTO",
    )

    batch_gap: IntProperty(
        name="Gap Between Animations",
        description="When several animations are selected they are laid out one after "
                    "another on the timeline with this many frames between them, in one "
                    "action, with a timeline marker at the start of each",
        default=10,
        min=0,
        soft_max=120,
    )

    yaw_180: BoolProperty(
        name="Turn 180 deg to Face the Model",
        description="The compiled skeleton faces +Y with its left at -X, while .ASC models "
                    "face -Y with their left at +X. Turn this off to see the animation in "
                    "its own untouched orientation",
        default=True,
    )

    transpose: BoolProperty(
        name="Rig Transposer (any skeleton)",
        description="Put this animation on a DIFFERENT creature's rig - a human dance on "
                    "a dragon, an orc walk on a wolf.\n\n"
                    "Every bone the two skeletons share by NAME is turned as far from its "
                    "own rest pose as the source bone was from its own; nothing about the "
                    "source's proportions comes across, and bones with no counterpart "
                    "(a dragon's wings) simply stay at rest. Only the root travels, scaled "
                    "by how high the two rigs hold it above the ground, so a step keeps its "
                    "size relative to the body.\n\n"
                    "Select the target rig first. It is nonsense by design - leave it off "
                    "for a normal import",
        default=False,
    )

    root_upright: BoolProperty(
        name="Keep the Target Upright",
        description="Give the root bone only the source's TURN, not its lean.\n\n"
                    "A root carries the whole body's orientation, so a lean that reads as "
                    "a shift of weight on a 1.8 m human tips a 12 m dragon onto its face "
                    "and through the floor. Turn this off for a literal transposition of "
                    "every rotation, including the root's",
        default=True,
    )

    frame_rate: EnumProperty(
        name="Frame Rate",
        description="Gothic animations are authored at their own rate - most dances at "
                    "15 fps, most combat at 25 - with ONE SAMPLE PER FRAME",
        items=(
            ("SOURCE", "Use the Animation's Rate",
             "Lay one key on every frame and set the scene to the animation's own fps. "
             "Exact, but the scene changes rate with every clip you load"),
            ("SCENE", "Retime to the Scene's Rate",
             "Keep the scene's fps and spread the keys to match, so the clip plays at "
             "its authored SPEED on your timeline: a 15 fps dance on a 30 fps scene gets "
             "a key every 2 frames with real in-betweens, instead of 300 keys in a row "
             "you have to rescale by hand"),
        ),
        default="SOURCE",
    )

    interpolation: EnumProperty(
        name="Interpolation",
        description="How the curves behave between samples",
        items=(
            ("AUTO", "Automatic",
             "Linear when there is a key on every frame, bezier once they are spread out - "
             "which is what each case actually wants"),
            ("LINEAR", "Linear",
             "Exactly the samples in the file. At the animation's own rate this is "
             "perfect; slowed down it looks stepped"),
            ("BEZIER", "Bezier (smooth)",
             "Eased in-betweens, with clamped handles so dense curves cannot overshoot. "
             "This is what makes a slowed-down clip read as motion"),
        ),
        default="AUTO",
    )

    def krx_on_invoke(self, context):
        current = selected_armature(context)
        self.remembered_armature = current.name if current else ""
        self._krx_batch_action = None
        self._krx_batch_armature = None
        self._krx_next_frame = None
        self._krx_batch_names = []
        # the browse folder is left to default_import_dir(): the game's
        # Anims/_compiled first, the bundled _Samples/animations as a fallback

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header = layout.box()
        header.label(text=self.krx_title, icon="IMPORT")

        col = layout.column()
        col.prop(self, "target_armature")
        if self.target_armature == NEW_ARMATURE or (
            self.target_armature == AUTO_ARMATURE and not self.remembered_armature
        ):
            note = layout.box()
            note.label(text="A skeleton will be built from the .MDH", icon="INFO")
        elif self.target_armature == AUTO_ARMATURE:
            note = layout.box()
            note.label(text=f"Animating '{self.remembered_armature}'", icon="ARMATURE_DATA")
        col.prop(self, "frame_start")
        col.prop(self, "set_scene_range")

        timing = layout.box()
        timing.label(text="Timing", icon="TIME")
        timing.prop(self, "frame_rate")
        timing.prop(self, "interpolation")
        note = timing.column(align=True)
        note.scale_y = 0.85
        note.enabled = False
        if self.frame_rate == "SCENE":
            note.label(text=f"Keys spread to fit {context.scene.render.fps} fps;")
            note.label(text="the clip keeps its authored speed.")
        else:
            note.label(text="One key per frame, scene fps set")
            note.label(text="from the file (15 for dances, 25 combat).")

        batch = layout.box()
        batch.label(text="Multiple Files", icon="SEQUENCE")
        batch.prop(self, "batch_gap")
        row = batch.row()
        row.enabled = False
        row.label(text="Selected files play one after another, in order")

        units = layout.box()
        units.label(text="Units", icon="DRIVER_DISTANCE")
        units.prop(self, "scale_preset")
        row = units.row()
        row.enabled = self.scale_preset == "CUSTOM"
        row.prop(self, "scale")

        transposer = layout.box()
        transposer.label(text="Rig Transposer", icon="CON_TRANSLIKE")
        transposer.prop(self, "transpose")
        if self.transpose:
            info = transposer.column(align=True)
            info.scale_y = 0.85
            info.enabled = False
            info.label(text="Rotations onto every bone shared BY NAME;")
            info.label(text="travel on the root only, scaled by rig height.")
            transposer.prop(self, "root_upright")
            if self.target_armature in (NEW_ARMATURE, NO_TARGETS):
                warn = transposer.row()
                warn.alert = True
                warn.label(text="Pick the target rig above", icon="ERROR")

        orient = layout.box()
        orient.label(text="Orientation (troubleshooting)", icon="ORIENTATION_GIMBAL")
        orient.prop(self, "yaw_180")
        orient.prop(self, "rotation_mapping")

    def krx_run(self, context):
        from .KrxManImp import import_man

        armature_obj = None
        if self.target_armature == AUTO_ARMATURE:
            # the selection captured before the file browser took over the context
            armature_obj = bpy.data.objects.get(self.remembered_armature) if self.remembered_armature else None
            if armature_obj is None:
                armature_obj = selected_armature(context)
        elif self.target_armature not in (NEW_ARMATURE, NO_TARGETS):
            armature_obj = bpy.data.objects.get(self.target_armature)
            if armature_obj is None:
                raise RuntimeError(f"Armature '{self.target_armature}' not found")

        # In a batch, every file after the first goes onto the armature the first one
        # used - including a skeleton that was built from scratch, which would
        # otherwise be rebuilt once per file.
        batch_armature = getattr(self, "_krx_batch_armature", None)
        if batch_armature is not None and batch_armature.name in bpy.data.objects:
            armature_obj = batch_armature

        # Several files selected? Lay them end to end on one action instead of each
        # overwriting the last. State lives on the operator, which survives the
        # file loop in impexp's execute().
        batch_action = getattr(self, "_krx_batch_action", None)
        next_frame = getattr(self, "_krx_next_frame", None)
        start = self.frame_start if next_frame is None else next_frame

        # One sample per frame at the file's own rate. To keep the scene's rate instead,
        # the samples are spread by the ratio so the clip still plays at its real speed.
        frame_step = 1.0
        if self.frame_rate == "SCENE":
            source_fps = _man_source_fps(self.filepath)
            if source_fps > 0:
                frame_step = context.scene.render.fps / source_fps

        result = import_man(
            self.filepath,
            armature_obj,
            scale=self.scale,
            frame_start=start,
            set_scene_range=self.set_scene_range,
            rotation_mapping=self.rotation_mapping,
            yaw_180=self.yaw_180,
            reuse_action=batch_action,
            add_marker=True,
            transpose=self.transpose,
            root_upright=self.root_upright,
            frame_step=frame_step,
            interpolation=self.interpolation,
        )

        self._krx_batch_action = result["action"]
        self._krx_batch_armature = result["armature"]
        self._krx_next_frame = result["frame_end"] + max(1, self.batch_gap)
        names = getattr(self, "_krx_batch_names", [])
        names.append(result["name"])
        self._krx_batch_names = names

        if len(names) > 1:
            result["action"].name = f"{names[0]} +{len(names) - 1}"
            self.report(
                {"INFO"},
                f"{len(names)} animations laid out to frame {result['frame_end']} "
                f"with {self.batch_gap}-frame gaps: {', '.join(names)}",
            )
        else:
            self.report(
                {"INFO"},
                f"'{result['name']}' - {result['frame_end'] - result['frame_start'] + 1} frames, "
                f"{result['matched']}/{result['nodes']} nodes matched",
            )


class KrxMmbImpGUI(_StaticMeshImportMixin):
    """Import a compiled MorphMesh binary (.mmb).
Morph-animated mesh: heads with facial animation, bows that bend, flags that wave.
The game only ships heads compiled, so this is THE way to get an NPC's face into Blender.
Optionally imports the morph animations (expressions, blinking, lip-sync visemes)
as shape keys"""

    krx_title = "MorphMesh Binary"

    import_morphs: BoolProperty(
        name="Import Morph Animations",
        description=(
            "Read the morph animation table and create one shape key per frame "
            "(S_ANGRY, S_FRIENDLY, R_EYESBLINK, VISEME_000...). "
            "Single-frame entries are plain expression shape keys; multi-frame ones "
            "are numbered so you can key through them"
        ),
        default=True,
    )

    def draw(self, context):
        super().draw(context)
        box = self.layout.box()
        box.label(text="MorphMesh", icon="SHAPEKEY_DATA")
        box.prop(self, "import_morphs")

    def krx_run(self, context):
        from .KrxMrmImp import TMRMFileLoader

        loader = TMRMFileLoader()
        self._krx_run_static(
            lambda *args: loader.ReadMMBFile(*args, import_morphs=self.import_morphs)
        )


class _SkinnedModelImportMixin(_StaticMeshImportMixin):
    """Options shared by the two skinned-model formats, .mdm and .mdl."""

    build_armature: BoolProperty(
        name="Build Skeleton and Skin",
        description="Find the .MDH named by the file's own skeleton checksum, build an "
                    "armature from it and weight the mesh to it. Turn off to get the bare "
                    "bind-pose geometry (which is already correctly placed)",
        default=True,
    )

    reuse_selected_armature: BoolProperty(
        name="Use the Selected Armature",
        description="Skin onto the armature you have selected instead of building a new "
                    "one - so a second body part lands on the same rig",
        default=True,
    )

    yaw_180: BoolProperty(
        name="Turn 180 deg to Face the Model",
        description="A compiled .mdm sits in the skeleton's own space, half a turn from "
                    "the way .ASC models face. Leave this on to keep every character in "
                    "the scene facing the same way",
        default=True,
    )

    def draw(self, context):
        super().draw(context)
        box = self.layout.box()
        box.label(text="Skinning", icon="ARMATURE_DATA")
        box.prop(self, "build_armature")
        if self.build_armature:
            box.prop(self, "reuse_selected_armature")
        box.prop(self, "yaw_180")

    def krx_on_invoke(self, context):
        current = selected_armature(context)
        self.remembered_armature = current.name if current else ""

    remembered_armature: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    """Selection captured when the dialog opened - the file browser has its own context"""

    def _krx_target_armature(self):
        if not (self.build_armature and self.reuse_selected_armature):
            return None
        armature = bpy.data.objects.get(self.remembered_armature)
        return armature if armature is not None and armature.type == "ARMATURE" else None

    def _krx_run_model(self, read_function):
        read_function(
            self.filepath,
            self.scale,
            self.remove_sectored_materials,
            color_adjustment=self.color_adjustment if self.color_adjustment else None,
            armature_obj=self._krx_target_armature(),
            skin=self.build_armature,
            yaw_180=self.yaw_180,
        )


class KrxMdmImpGUI(_SkinnedModelImportMixin):
    """Import a compiled model mesh (.mdm).
The SKINNED body of a character or monster - wolves, orcs, dragons and every naked human
body exist only in this form (Anims/_compiled). The file carries its own skeleton
checksum, so the matching .MDH is found automatically, an armature is built from it and
every vertex gets its weights: the result is a rigged mesh you can pose or drop a .MAN
animation onto"""

    krx_title = "Model Mesh"

    def krx_run(self, context):
        from .KrxMrmImp import KrxMdmImp

        self._krx_run_model(KrxMdmImp)


class KrxMdlImpGUI(_SkinnedModelImportMixin):
    """Import a compiled model (.mdl).
A SKELETON AND ITS BODY IN ONE FILE - a .mdh hierarchy section with a whole .mdm glued on
after it. It is the only Gothic model that needs nothing beside it: the skeleton comes out
of the same file instead of being matched by checksum, so armors and props import rigged
in one step. A couple of them (the fireplace and wash interaction slots) are skeleton and
no geometry at all, and import as a bare armature"""

    krx_title = "Model"

    def krx_run(self, context):
        from .KrxMrmImp import KrxMdlImp

        self._krx_run_model(KrxMdlImp)


# -------------------------------------------------------------------------------------------------------
# 3DS importer (adds slot / bone replacement)
# -------------------------------------------------------------------------------------------------------


class Krx3dsImpGUI(_ImportScaleMixin, _ColorAdjustMixin, _TextureSearchMixin):
    """Import a 3D Studio mesh (.3ds).
Classic static source format used by the Gothic modkit for items, weapons and props -
what artists exported from 3ds Max before the game compiled it to .MRM.
Can also replace a slot or attach to a bone of an existing model in the scene"""

    krx_title = "3D Studio Mesh"

    scene_mode: EnumProperty(
        name="Scene",
        description="What to do with the current scene",
        items=krx3ds_scene_mode_items,
    )

    target_slot: EnumProperty(
        name="Slot",
        description="Slot to replace with the imported object",
        items=scene_slot_items,
    )

    target_bone: EnumProperty(
        name="Bone",
        description="Bone to link the imported object to",
        items=scene_bone_items,
    )

    remove_sectored_materials: BoolProperty(
        name="Remove Sectored Materials",
        description=SECTORED_DESC,
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header = layout.box()
        header.label(text=self.krx_title, icon="IMPORT")

        col = layout.column()
        col.prop(self, "scene_mode")
        if self.scene_mode == "SLOT":
            col.prop(self, "target_slot")
        elif self.scene_mode == "BONE":
            col.prop(self, "target_bone")
        col.prop(self, "remove_sectored_materials")
        col.prop(self, "color_adjustment")
        col.prop(self, "scale")

        self.draw_texture_options(layout)

    def krx_run(self, context):
        from .Krx3dsImp import T3DSFileLoader

        scene_mode = self.scene_mode
        target_slot = self.target_slot if scene_mode == "SLOT" else None
        target_bone = self.target_bone if scene_mode == "BONE" else None

        loader = T3DSFileLoader(
            self.filepath,
            self.scale,
            self.remove_sectored_materials,
            self.color_adjustment if self.color_adjustment else None,
        )

        if target_slot and target_slot != NO_TARGETS:
            loader.ReplaceObjectWithLoaded(target_slot)
        elif target_bone and target_bone != NO_TARGETS:
            loader.ReplaceObjectWithLoaded(target_bone)

        _SCENE_CACHE["key"] = None  # scene changed


# -------------------------------------------------------------------------------------------------------
# ASC importer (static / dynamic / morph meshes and animations)
# -------------------------------------------------------------------------------------------------------


class BatAscImpGUI(_ImportScaleMixin, _ColorAdjustMixin, _TextureSearchMixin):
    """Import an ASCII model (.asc).
Text export from 3ds Max holding meshes, skeletons (bones/slots), skinning and animations.
Bodies, armors and monsters are authored as ASC before compilation.
The dialog adapts to what the file contains: static/dynamic/morph mesh or an animation
to apply onto a matching model already in the scene"""

    krx_title = "ASCII Model"

    action: EnumProperty(
        name="Action",
        description="What to do with the current scene / model",
        items=batasc_action_items,
    )

    target_prefix: EnumProperty(
        name="Model",
        description="Compatible model (by prefix) in the current scene",
        items=batasc_target_prefix_items,
    )

    target_slot: EnumProperty(name="Slot", description="Slot to link the imported object to", items=scene_slot_items)
    target_bone: EnumProperty(name="Bone", description="Bone to link the imported object to", items=scene_bone_items)

    auto_prefix: BoolProperty(
        name="Auto Prefix",
        description="Generate a unique model prefix automatically",
        default=True,
    )

    model_prefix: StringProperty(
        name="Prefix",
        description=(
            "A model prefix is inserted before every object's name; it allows importing "
            "and editing more than one model in the same scene"
        ),
        default="",
    )

    slot_transparency: IntProperty(
        name="Slot Transparency",
        description="Display transparency of the imported slot objects",
        default=100,
        min=0,
        max=100,
        subtype="PERCENTAGE",
    )

    connect_bones: BoolProperty(
        name="Try to Connect Bones",
        description="Try to connect child bones to their parents",
        default=True,
    )

    use_sample_dir: BoolProperty(
        name="Use Sample Meshes",
        description="Fill slots using sample meshes from a folder",
        default=True,
    )

    sample_meshes_directory: StringProperty(
        name="Samples Folder",
        description="Folder with sample meshes used to fill slots (leave empty for the bundled samples)",
        subtype="DIR_PATH",
        default="",
    )

    bone_style: EnumProperty(
        name="Bone Style",
        description="Display style for created bones",
        items=BONE_STYLE_ITEMS,
        default="STICK",
    )

    auto_time: BoolProperty(
        name="Entire Animation",
        description="Import the whole frame range of the file, mapped 1:1 to scene frames",
        default=True,
    )

    start_frame_in_file: IntProperty(name="File Start", default=0, min=-32768, max=32767)
    end_frame_in_file: IntProperty(name="File End", default=100, min=-32768, max=32767)
    start_frame_in_scene: IntProperty(name="Scene Start", default=0, min=0, max=MAX_SCENE_FRAME_LIMIT)
    end_frame_in_scene: IntProperty(name="Scene End", default=100, min=0, max=MAX_SCENE_FRAME_LIMIT)

    # ---------------- UI ----------------

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        asc, analyzer, error = _asc_analysis(self.filepath)

        header = layout.box()
        if asc is None:
            header.label(text=self.krx_title, icon="IMPORT")
            header.label(text=error or "", icon="INFO")
            return

        type_names = {
            AscType.MORPH_MESH: "MorphMesh Model",
            AscType.MORPH_ANIM: "MorphMesh Animation",
            AscType.STATIC_MESH: "Static Model",
            AscType.DYNAMIC_MESH: "Dynamic Model",
            AscType.DYNAMIC_ANIM: "Dynamic Animation",
        }
        header.label(text=type_names.get(asc.model_type, "ASCII Model"), icon="IMPORT")
        stats = header.column(align=True)
        stats.label(text=f"Meshes: {asc.num_meshes}   Slots: {asc.num_slots}   Bones: {asc.num_bones}")

        anim = _is_anim_type(asc)

        col = layout.column()
        col.prop(self, "action")

        if anim:
            if analyzer and not analyzer.appropriate_prefixes:
                warn = layout.box()
                warn.alert = True
                warn.label(text="No compatible models in the scene", icon="ERROR")
            else:
                col.prop(self, "target_prefix")

            frames = layout.box()
            frames.label(text=f"File Frames: {asc.time_transform.min_frame_in_file} - "
                              f"{asc.time_transform.max_frame_in_file}   FPS: {asc.framerate}", icon="TIME")
            frames.prop(self, "auto_time")
            if not self.auto_time:
                sub = frames.column(align=True)
                sub.prop(self, "start_frame_in_file")
                sub.prop(self, "end_frame_in_file")
                sub.prop(self, "start_frame_in_scene")
                sub.prop(self, "end_frame_in_scene")
        else:
            if self.action in {"SKIN_REPLACE", "SKIN_MERGE"}:
                col.prop(self, "target_prefix")
            elif self.action == "LINK_SLOT":
                col.prop(self, "target_slot")
            elif self.action == "LINK_BONE":
                col.prop(self, "target_bone")

            if self.action == "NEW_MERGE" or (_is_morph_mesh_type(asc) and self.action.startswith("NEW")):
                prefix_col = layout.column()
                prefix_col.prop(self, "auto_prefix")
                if not self.auto_prefix:
                    prefix_col.prop(self, "model_prefix")
                    if analyzer and self.model_prefix and self.model_prefix in analyzer.scene_prefixes:
                        warn = prefix_col.box()
                        warn.alert = True
                        warn.label(text="Prefix already exists in the scene!", icon="ERROR")

            if not _is_morph_mesh_type(asc):
                bones = layout.box()
                bones.label(text="Bones and Slots", icon="ARMATURE_DATA")
                bones.prop(self, "slot_transparency")
                bones.prop(self, "connect_bones")
                bones.prop(self, "bone_style")
                bones.prop(self, "use_sample_dir")
                if self.use_sample_dir:
                    bones.prop(self, "sample_meshes_directory")

            col2 = layout.column()
            col2.prop(self, "color_adjustment")

            self.draw_texture_options(layout)

        layout.prop(self, "scale")

    # ---------------- execution ----------------

    def krx_run(self, context):
        from .BatAscImp import DEFAULT_SAMPLE_MESH_DIR, load_asc

        asc, analyzer, error = _asc_analysis(self.filepath, force=True)
        if asc is None:
            raise RuntimeError(error or "Invalid ASC file")

        anim = _is_anim_type(asc)

        selected_slot = None
        selected_bone = None
        prefix: Optional[str] = None

        if anim:
            if not analyzer.appropriate_prefixes:
                which = "MorphMesh" if asc.model_type == AscType.MORPH_ANIM else "dynamic"
                raise RuntimeError(f"No valid {which} models were found for this animation to import.")
            scene_mode = SceneMode.REPLACE_ANIM if self.action == "ANIM_REPLACE" else SceneMode.MERGE_ANIM
            prefix = _decode_prefix(self.target_prefix)
        else:
            if self.action == "NEW_MERGE":
                scene_mode = SceneMode.MERGE
                prefix = analyzer.unique_prefix if self.auto_prefix else self.model_prefix
                if prefix in analyzer.scene_prefixes:
                    raise RuntimeError(f"Prefix '{prefix}' already exists in the scene!")
            elif self.action == "SKIN_REPLACE":
                # Note: mapping preserved from the original dialog
                scene_mode = SceneMode.MERGE_SOFTSKIN_MESH
                prefix = _decode_prefix(self.target_prefix)
            elif self.action == "SKIN_MERGE":
                scene_mode = SceneMode.REPLACE_SOFTSKIN_MESH
                prefix = _decode_prefix(self.target_prefix)
            elif self.action == "LINK_SLOT":
                scene_mode = SceneMode.LINK_SLOT_TO_OBJECT
                selected_slot = _decode_prefix(self.target_slot) or None
                prefix = analyzer.unique_prefix if self.auto_prefix else self.model_prefix
            elif self.action == "LINK_BONE":
                scene_mode = SceneMode.LINK_BONE_TO_OBJECT
                selected_bone = _decode_prefix(self.target_bone) or None
                prefix = analyzer.unique_prefix if self.auto_prefix else self.model_prefix
            else:
                scene_mode = SceneMode.MERGE
                prefix = ""

        if self.auto_time:
            start_file = asc.time_transform.min_frame_in_file
            end_file = asc.time_transform.max_frame_in_file
            start_scene = max(0, start_file)
            end_scene = max(start_scene, end_file)
        else:
            start_file = self.start_frame_in_file
            end_file = self.end_frame_in_file
            start_scene = self.start_frame_in_scene
            end_scene = self.end_frame_in_scene

        sample_dir = bpy.path.abspath(self.sample_meshes_directory) if self.sample_meshes_directory else DEFAULT_SAMPLE_MESH_DIR

        asc.scale = self.scale
        asc.color_adjustment = self.color_adjustment if self.color_adjustment else None
        asc.model_prefix = prefix if prefix is not None else analyzer.unique_prefix

        load_asc(
            asc,
            scene_mode,
            selected_slot,
            selected_bone,
            self.bone_style,
            0.01 * self.slot_transparency,
            self.connect_bones,
            self.use_sample_dir,
            sample_dir,
            start_file,
            end_file,
            start_scene,
            end_scene,
        )

        _SCENE_CACHE["key"] = None  # scene changed
        _ASC_CACHE["key"] = None


# -------------------------------------------------------------------------------------------------------
# ASC exporter
# -------------------------------------------------------------------------------------------------------


class KrxAscExpGUI(_ExportScaleMixin):
    """Export an ASCII model (.asc).
Produces the modkit source format Gothic tools compile into MDH/MDM/MMB/MAN.
Export the mesh (initial pose) or the animation of a model in the scene;
reference the resulting file from your .MDS or .MMS script"""

    krx_title = "ASCII Model"

    model_prefix: EnumProperty(
        name="Model",
        description="Model (by prefix) to export",
        items=ascexp_prefix_items,
        update=ascexp_on_selector_changed,
    )

    export_type: EnumProperty(
        name="Type",
        description="Type of export",
        items=(
            ("MESH", "Mesh", "Export the model's mesh in its initial pose"),
            ("ANIM", "Animation", "Export the model's animation"),
        ),
        default="MESH",
        update=ascexp_on_selector_changed,
    )

    export_objects: CollectionProperty(type=KrxExportObjectItem)
    export_objects_index: IntProperty(default=0)

    auto_time: BoolProperty(
        name="Entire Scene Range",
        description="Export the scene's whole frame range, mapped 1:1 to file frames",
        default=True,
    )

    start_frame_in_scene: IntProperty(name="Scene Start", default=0, min=0, max=MAX_SCENE_FRAME_LIMIT)
    end_frame_in_scene: IntProperty(name="Scene End", default=100, min=0, max=MAX_SCENE_FRAME_LIMIT)
    start_frame_in_file: IntProperty(name="File Start", default=0, min=-32768, max=32767)
    end_frame_in_file: IntProperty(name="File End", default=100, min=-32768, max=32767)

    def krx_on_invoke(self, context):
        analyzer = _scene_analyzer(force=True)
        selected = list(analyzer.selected_prefixes) or list(analyzer.scene_prefixes)
        if selected and analyzer.model_hierarchies:
            try:
                self.model_prefix = _encode_prefix(selected[0])
            except TypeError:
                pass
        ascexp_repopulate(self)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header = layout.box()
        header.label(text=self.krx_title, icon="EXPORT")

        analyzer = _scene_analyzer()
        if not analyzer.model_hierarchies:
            warn = layout.box()
            warn.alert = True
            warn.label(text="No valid models to export", icon="ERROR")
            return

        col = layout.column()
        col.prop(self, "model_prefix")
        col.prop(self, "export_type")

        if not len(self.export_objects):
            ascexp_repopulate(self)

        box = layout.box()
        box.label(text="Objects to Export", icon="OUTLINER")
        box.template_list(
            "KRX_UL_asc_export_objects", "",
            self, "export_objects",
            self, "export_objects_index",
            rows=8,
        )
        count = sum(1 for item in self.export_objects if item.export and not item.hidden)
        box.label(text=f"Selected: {count}")

        if self.export_type == "ANIM":
            frames = layout.box()
            frames.label(text="Time Transformation", icon="TIME")
            frames.prop(self, "auto_time")
            if not self.auto_time:
                sub = frames.column(align=True)
                sub.prop(self, "start_frame_in_scene")
                sub.prop(self, "end_frame_in_scene")
                sub.prop(self, "start_frame_in_file")
                sub.prop(self, "end_frame_in_file")

        layout.prop(self, "scale")

    def krx_run(self, context):
        from .KrxAscExp import TASCFileSaver

        analyzer = _scene_analyzer(force=True)
        if not analyzer.model_hierarchies:
            raise RuntimeError("No valid models to export.")

        if len(self.export_objects) == 0:
            hierarchy = ascexp_repopulate(self, force_scene=False)
        else:
            hierarchy = analyzer.get_model_hierarchy_by_prefix(_decode_prefix(self.model_prefix))

        if hierarchy is None:
            raise RuntimeError("No valid models to export.")

        selected_objects = [item.name for item in self.export_objects if item.export and not item.hidden]
        if not selected_objects:
            raise RuntimeError("Cannot export, no objects selected.")

        export_animation = self.export_type == "ANIM"

        scene = bpy.context.scene
        if self.auto_time:
            start_scene = scene.frame_start
            end_scene = scene.frame_end
            start_file = scene.frame_start
            end_file = scene.frame_end
        else:
            start_scene = self.start_frame_in_scene
            end_scene = self.end_frame_in_scene
            start_file = self.start_frame_in_file
            end_file = self.end_frame_in_file

        TASCFileSaver().WriteASCFile(
            self.filepath,
            hierarchy,
            selected_objects,
            export_animation,
            TTimeTransform(
                min_frame_in_scene=scene.frame_start,
                max_frame_in_scene=scene.frame_end,
                min_frame_in_file=-32768,
                max_frame_in_file=32767,
                start_frame_in_scene=start_scene,
                end_frame_in_scene=end_scene,
                start_frame_in_file=start_file,
                end_frame_in_file=end_file,
            ),
            self.scale,
        )


# -------------------------------------------------------------------------------------------------------
# 3DS exporter
# -------------------------------------------------------------------------------------------------------


class Krx3dsExpGUI(_ExportScaleMixin):
    """Export a 3D Studio mesh (.3ds).
The static source format for items, weapons and world props.
Respects 3DS limits (65535 verts/tris per mesh) and can rename materials
against a Gothic matlib.ini material library"""

    krx_title = "3D Studio Mesh"

    use_local_cs: EnumProperty(
        name="Coordinates",
        description="Coordinate system used for the exported vertices",
        items=(
            ("WORLD", "World",
             "Transform vertices into the world coordinate system. Use this to save levels; "
             "you won't need to attach all objects to a single mesh before exporting"),
            ("LOCAL", "Local",
             "Keep vertices in the local coordinate system. Use this to save items and MOBs; "
             "vertices are saved relative to the object's pivot"),
        ),
        default="LOCAL",
    )

    allow_german_chars: BoolProperty(
        name="Allow Non-ASCII Ö and Ü",
        description=(
            "Allow the German characters Ö and Ü in material names. "
            "Use exclusively with vanilla Gothic 1 meshes - do NOT use with Gothic 2"
        ),
        default=False,
    )

    rename_materials: BoolProperty(
        name="Rename Materials (matlib.ini)",
        description="Rename exported materials using a Gothic matlib.ini material library",
        default=False,
    )

    matlib_path: StringProperty(
        name="matlib.ini",
        description="Path to the matlib.ini material library file",
        subtype="FILE_PATH",
        default="",
    )

    matlib_autorenaming: BoolProperty(
        name="Auto-Name Unknown Materials",
        description="Assign names to unknown materials based on the file name of the diffuse texture map",
        default=False,
    )

    export_objects: CollectionProperty(type=KrxExportObjectItem)
    export_objects_index: IntProperty(default=0)

    def _compute_stats(self):
        from .Krx3dsExp import process_objects

        analyzer = _scene_analyzer(force=True)
        selected = analyzer.selected_meshes_by_type
        if len(selected) == 0:
            selected = analyzer.scene_meshes_by_type

        processed_objects = []
        processed_materials = []
        process_objects(None, processed_objects, processed_materials)
        self._krx_processed = (processed_objects, processed_materials)

        self.export_objects.clear()
        for name, obj, mesh_data in processed_objects:
            nonascii = False
            german_chars = False

            for material in obj.material_slots:
                material_name = material.name.upper()
                if not material_name.isascii():
                    german_chars = "Ö" in material_name or "Ü" in material_name
                    valid_name = material_name.replace("Ö", "x").replace("Ü", "x")
                    if not valid_name.isascii():
                        nonascii = True
                        german_chars = False
                        break

            if not name.upper().isascii() or not obj.data.name.upper().isascii():
                nonascii = True

            item = self.export_objects.add()
            item.name = name
            item.otype = int(ObjectType.MESH)
            item.verts = len(obj.data.vertices)
            item.tris = len(mesh_data.faces)
            item.verts_in_file = len(mesh_data.verts)
            item.nonascii = nonascii
            item.german_chars = german_chars
            item.export = name in selected and not item3ds_invalid(item, self.allow_german_chars)

        self.use_local_cs = "LOCAL" if sum(1 for i in self.export_objects if i.export) <= 1 else "WORLD"

    def krx_on_invoke(self, context):
        self._compute_stats()

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        header = layout.box()
        header.label(text=self.krx_title, icon="EXPORT")

        if not len(self.export_objects):
            warn = layout.box()
            warn.alert = True
            warn.label(text="No valid meshes to export", icon="ERROR")
            return

        col = layout.column()
        col.prop(self, "use_local_cs")
        col.prop(self, "allow_german_chars")

        box = layout.box()
        box.label(text="Objects to Export  (verts / tris / file verts)", icon="OUTLINER")
        box.template_list(
            "KRX_UL_3ds_export_objects", "",
            self, "export_objects",
            self, "export_objects_index",
            rows=6,
        )
        count = sum(
            1 for item in self.export_objects
            if item.export and not item3ds_invalid(item, self.allow_german_chars)
        )
        box.label(text=f"Selected: {count}")
        if any(item3ds_invalid(item, self.allow_german_chars) for item in self.export_objects):
            box.label(text="Red rows exceed 3DS limits (65535) or contain invalid names", icon="ERROR")

        matlib = layout.box()
        matlib.prop(self, "rename_materials")
        if self.rename_materials:
            matlib.prop(self, "matlib_path")
            matlib.prop(self, "matlib_autorenaming")

        layout.prop(self, "scale")

    def krx_run(self, context):
        from .Krx3dsExp import T3DSFileSaver

        if getattr(self, "_krx_processed", None) is None or not len(self.export_objects):
            self._compute_stats()

        selected_objects = [
            item.name for item in self.export_objects
            if item.export and not item3ds_invalid(item, self.allow_german_chars)
        ]
        if not selected_objects:
            raise RuntimeError("Cannot export, no valid objects selected.")

        matlib_path = None
        autorenaming = False
        if self.rename_materials and self.matlib_path:
            matlib_path = bpy.path.abspath(self.matlib_path)
            autorenaming = self.matlib_autorenaming

        T3DSFileSaver(
            self.filepath,
            selected_objects,
            self.use_local_cs == "LOCAL",
            self.scale,
            self._krx_processed,
            matlib_path,
            matlib_autonaming=autorenaming,
            german_char_exception=self.allow_german_chars,
        )


# -------------------------------------------------------------------------------------------------------
# Registration of the support classes (the operators themselves are built by the manager)
# -------------------------------------------------------------------------------------------------------

_CLASSES = (
    KrxExportObjectItem,
    KRX_UL_asc_export_objects,
    KRX_UL_3ds_export_objects,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _ITEM_REFS.clear()
    _SCENE_CACHE["key"] = None
    _SCENE_CACHE["analyzer"] = None
    _ASC_CACHE["key"] = None
    _ASC_CACHE["asc"] = None
    _ASC_CACHE["analyzer"] = None
