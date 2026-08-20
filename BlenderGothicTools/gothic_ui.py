# gothic_ui.py: the Gothic sidebar tab (N-panel) - Import / Essemble / Developer.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import math
import os
from contextlib import contextmanager

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)
from mathutils import Euler, Matrix

from . import game_data, icons
from .gui import call_message_box
from .KrxManImp import SCALE_BLENDER_METRES, SCALE_SOURCE_UNITS
from .scene import SceneMode

# -------------------------------------------------------------------------------------------------------
# Format lore - long hover descriptions for every file type
# -------------------------------------------------------------------------------------------------------

FORMAT_LORE = {
    "mds": (
        "Model script (.mds).\n"
        "The text that gives a creature's animations their meaning: every clip's NAME, what follows it,\n"
        "which slice of which source file it is, whether it plays in reverse, and what happens during it -\n"
        "footsteps, effects, and the frames where a weapon leaves the belt and lands in the hand"
    ),
    "mdl": (
        "Compiled model (.mdl).\n"
        "A skeleton and its skinned body in ONE file: a .mdh hierarchy section with a whole .mdm after it.\n"
        "The only Gothic model that needs nothing beside it - no checksum lookup, no naming convention.\n"
        "Load one to get a rigged armor or prop in a single step"
    ),
    "mdm": (
        "Compiled model mesh (.mdm).\n"
        "The SKINNED body of a character or monster: geometry plus one weight table per vertex.\n"
        "Wolves, orcs, dragons and every naked human body ship only in this form.\n"
        "It stores its skeleton's checksum, so the matching .MDH is found and rigged automatically"
    ),
    "3ds": (
        "3D Studio mesh (.3ds).\n"
        "Classic static mesh format used by the Gothic modkit for items, weapons and world props.\n"
        "This is the SOURCE format artists exported from 3ds Max; the game itself compiles it to .MRM.\n"
        "Load one when you want to edit an item/prop mesh, e.g. a sword or a chest"
    ),
    "asc": (
        "ASCII model (.asc).\n"
        "Text-based export from 3ds Max holding meshes, skeletons (bones/slots), skinning and animations.\n"
        "Bodies, armors, heads and monsters are authored as ASC before compilation (MDH/MDM/MAN).\n"
        "Load one to get a rigged character/armor with its skeleton, or an animation to apply to a model"
    ),
    "msh": (
        "Compiled world mesh (.msh).\n"
        "Binary zEngine mesh with materials and lightmaps, produced when a world is compiled.\n"
        "It is the raw geometry part of a level, without VOBs or waynet.\n"
        "Load one to inspect level geometry when you don't need the full ZEN world"
    ),
    "mrm": (
        "Multi-resolution mesh (.mrm).\n"
        "Compiled progressive mesh the engine streams for items and VOBs (props).\n"
        "Every .3DS visual referenced by the scripts ships compiled as .MRM in Meshes/_compiled.\n"
        "Load one to view/edit the actual in-game version of any item or prop"
    ),
    "zen": (
        "ZenGin world archive (.zen).\n"
        "A complete level: world mesh, BSP, VOB tree (props, lights, spots) and waynet.\n"
        "Uncompiled ZENs live in _work/Data/Worlds (NewWorld, Addon, OldWorld).\n"
        "Load one to bring a whole game world (or a piece of it) into Blender"
    ),
    "mmb": (
        "Compiled MorphMesh binary (.mmb).\n"
        "Morph-animated mesh: heads with facial animation, bows that bend, flags that wave.\n"
        "The game only ships heads compiled - there are no source .ASC heads in a retail install.\n"
        "Mesh, materials AND the morph animations (expressions, blinking, visemes) import"
    ),
    "mdl": (
        "Compiled model (.mdl).\n"
        "Hierarchy + mesh in a single binary (equivalent to MDH+MDM together).\n"
        "Used for interactive objects and simpler models like MOBs, trees and beds.\n"
        "Import support is PLANNED - the next big milestone towards a standalone pipeline"
    ),
    "mds": (
        "Model script (.mds).\n"
        "Text registry that defines a model's skeleton source, meshes, animations and\n"
        "events - HUMANS.MDS lists every human animation and the .ASC it came from.\n"
        "Parsing support is PLANNED - it is the key to importing full animated characters"
    ),
    "msb": (
        "Compiled model script (.msb).\n"
        "The binary form of a .MDS: the registry of a model's skeleton, meshes,\n"
        "animations and events, as the game actually ships it.\n"
        "Parsing support is PLANNED"
    ),
    "man": (
        "Compiled animation (.man).\n"
        "One motion for one skeleton (e.g. HUMANS-S_RUN.MAN), sampled per frame.\n"
        "The matching skeleton (HUMANS.MDH) is read automatically so the animation's\n"
        "nodes map onto your armature's bones by name. Import a character first,\n"
        "then point this at its armature"
    ),
    "d": (
        "Daedalus script (.d).\n"
        "Gothic's game code. NPC scripts declare the whole look of a character:\n"
        "head mesh, face and body texture variants, armor instance, weapons and overlays.\n"
        "Essemble reads one and imports every referenced visual it can find"
    ),
}

PLANNED_FORMATS = (
    ("msb", "Compiled Model Script (.msb)"),
)


# -------------------------------------------------------------------------------------------------------
# Assemble core
# -------------------------------------------------------------------------------------------------------


@contextmanager
def collection_scope(name: str):
    """Create/reuse a collection and make it active so imports land inside it."""
    scene = bpy.context.scene
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if not any(child is collection for child in scene.collection.children):
        scene.collection.children.link(collection)

    view_layer = bpy.context.view_layer

    def find_layer(layer_collection):
        if layer_collection.collection is collection:
            return layer_collection
        for child in layer_collection.children:
            found = find_layer(child)
            if found:
                return found
        return None

    previous = view_layer.active_layer_collection
    target = find_layer(view_layer.layer_collection)
    if target is not None:
        view_layer.active_layer_collection = target
    try:
        yield collection
    finally:
        try:
            view_layer.active_layer_collection = previous
        except Exception:
            pass


_PLANNED_EXTENSIONS = {".man", ".msb"}
"""Formats the assembler will hit but cannot load yet"""


def import_asset_file(path: str, report: list, import_morphs: bool = False,
                      use_sample_meshes: bool = False, scale: float = SCALE_BLENDER_METRES,
                      armature=None, skeleton: str = None) -> bool:
    """Import one asset file with the matching scripting importer. Appends to report.

    Every part of a character has to come in at the SAME scale or the head lands
    somewhere other than the neck, so the recipe's scale is passed to each importer
    rather than each importer using its own default."""
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    try:
        if ext == ".3ds":
            from .Krx3dsImp import Krx3dsImp

            Krx3dsImp(path, scale=scale)
        elif ext == ".mrm":
            from .KrxMrmImp import KrxMrmImp

            KrxMrmImp(path, scale=scale)
        elif ext == ".mmb":
            from .KrxMrmImp import KrxMmbImp

            KrxMmbImp(path, scale=scale, import_morphs=import_morphs)
        elif ext == ".mdl":
            from .KrxMrmImp import KrxMdlImp

            # a .mdl brings its own skeleton, so it never needs the recipe's hint
            KrxMdlImp(path, scale=scale, armature_obj=armature)
        elif ext == ".mdm":
            from .KrxMrmImp import KrxMdmImp

            # Monster and orc bodies (and every naked human body) only exist as compiled
            # model meshes. The .mdm carries its own skeleton checksum, so it finds and
            # builds its skeleton itself - no toggle, no naming convention.
            KrxMdmImp(path, scale=scale, armature_obj=armature, skeleton=skeleton)
        elif ext == ".msh":
            from .KrxMshImp import KrxMshImp

            KrxMshImp(path, scale=scale)
        elif ext == ".zen":
            from .KrxZenImp import KrxZenImp

            KrxZenImp(path, scale=scale)
        elif ext == ".asc":
            from .BatAscImp import BatAscImp

            # sample meshes are troubleshooting placeholders (a blocky stand-in head
            # and weapons); a real character gets its actual head and weapons instead
            BatAscImp(path, sample_meshes_directory=True if use_sample_meshes else None,
                      scene_mode=SceneMode.MERGE, model_prefix="", scale=scale)
        elif ext in _PLANNED_EXTENSIONS:
            report.append(f"[planned] {base}: {ext} import is not supported yet")
            return False
        else:
            report.append(f"[skip] {base}: unknown format {ext}")
            return False
    except Exception as ex:
        from . import log

        log.exception(f"import of '{path}' failed")
        report.append(f"[error] {base}: {ex}")
        return False

    report.append(f"[ok] imported {base}")
    return True


def resolve_token(token: str, report: list, search_dir: str = ""):
    """Resolve a user token (file name or Daedalus instance) to an asset path.

    A recipe folder, when given, is searched first. Tokens with an extension are
    file references; bare tokens are tried as script instances FIRST so an armor
    resolves to its rigged .asc (visual_change) instead of the compiled pickup
    mesh of the same name."""
    token = token.strip().strip('"')
    if not token:
        return None

    if search_dir:
        path = game_data.find_in_dir(token, bpy.path.abspath(search_dir))
        if path:
            report.append(f"[folder] '{token}' found in {search_dir}")
            return path

    has_extension = bool(os.path.splitext(token)[1])

    if has_extension:
        path = game_data.find_asset(token)
        if path:
            return path

    path, note = game_data.resolve_instance_visual(token)
    if path:
        report.append(f"[resolved] {note}")
        return path

    if not has_extension:
        path = game_data.find_asset(token)
        if path:
            return path

    report.append(f"[missing] '{token}': {note if 'instance' in note else 'no matching file in the master folders'}")
    return None


# Images that represent bare skin (as opposed to cloth/armor) on a body mesh
_SKIN_PREFIXES = ("HUM_BODY_NAKED", "HUM_BODY_BABE", "HUM_HEAD", "HUM_MOUTH", "HUM_TEETH")


def _load_texture_image(name: str, kind: str, report: list):
    """Find a texture by name (preferred folder for its kind, then brute index) and load it."""
    path = game_data.find_texture(name, kind=kind)
    if not path:
        report.append(f"[missing] texture '{name}' not found (searched {kind or 'all'} folder + master folders)")
        return None
    if path.lower().endswith(".tex"):
        from .tex_convert import cached_dds

        converted = cached_dds(path)
        if not converted:
            report.append(f"[error] could not convert compiled texture {os.path.basename(path)}")
            return None
        report.append(f"[tex] converted {os.path.basename(path)} -> DDS")
        path = converted
    return bpy.data.images.load(path, check_existing=True)


def _swap_images(objects, image, wants_skin: bool, report: list, label: str):
    """Replace image-texture nodes on the given objects.
    wants_skin=True targets bare-skin textures, False targets everything else (cloth/armor)."""
    swapped = 0
    for obj in objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        for mat in obj.data.materials:
            if not mat or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type != "TEX_IMAGE" or node.image is None or node.image is image:
                    continue
                is_skin = node.image.name.upper().startswith(_SKIN_PREFIXES)
                if is_skin == wants_skin:
                    node.image = image
                    swapped += 1
    if swapped:
        report.append(f"[tex] {label} -> {image.name} on {swapped} material(s)")
    else:
        report.append(f"[note] {label}: nothing to replace with {image.name}")
    return swapped


def find_head_bone(armature_obj):
    """Weighted search for the head bone ('Bip01 Head', 'BIP HEAD', 'Head', ...)."""
    best, best_score = None, 0
    for bone in armature_obj.data.bones:
        compact = bone.name.upper().replace(" ", "").replace("_", "").replace(".", "")
        if compact == "BIP01HEAD":
            score = 100
        elif compact in ("BIPHEAD01", "BIPHEAD", "BIP01HEAD01"):
            score = 95
        elif compact.startswith("BIP") and compact.endswith("HEAD"):
            score = 90
        elif compact.endswith("HEAD"):
            score = 70
        elif "HEAD" in compact:
            score = 50
        else:
            continue
        if "NECK" in compact or "HEADNUB" in compact:
            score -= 30
        if score > best_score:
            best, best_score = bone, score
    return best


def attach_to_bone(obj, armature_obj, bone, report: list):
    """Skin a mesh to a single bone: place it in the bone's own frame, weight every
    vertex to that bone and drive it with an Armature modifier - the game-ready setup.

    Placement comes from the bone's REST matrix, not a hand-tuned angle. Gothic meshes
    come from 3ds Max where a node's X axis runs along the bone; Blender runs bones
    along Y, and the ASC importer reconciles the two by rotating every bone +90 deg
    about Z (armature.cached_bone_rotation_matrix). Applying that same rotation to the
    bone's rest matrix reproduces the node frame the head was authored in."""
    from .armature import cached_bone_rotation_matrix

    obj.matrix_world = (
        armature_obj.matrix_world
        @ bone.matrix_local
        @ cached_bone_rotation_matrix
    )

    group = obj.vertex_groups.get(bone.name) or obj.vertex_groups.new(name=bone.name)
    group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")

    modifier = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
    if modifier is None:
        modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature_obj
    modifier.use_vertex_groups = True

    # object-level parenting keeps the transform and follows the armature around
    matrix_world = obj.matrix_world.copy()
    obj.parent = armature_obj
    obj.parent_type = "OBJECT"
    obj.matrix_parent_inverse = armature_obj.matrix_world.inverted()
    obj.matrix_world = matrix_world

    location = obj.matrix_world.translation
    degrees = tuple(round(math.degrees(a)) for a in obj.matrix_world.to_euler("XYZ"))
    report.append(
        f"[rig] {obj.name} weighted 100% to '{bone.name}' + Armature modifier, "
        f"at ({location.x:.3f}, {location.y:.3f}, {location.z:.3f}) world euler {degrees}"
    )


def detect_armor_texture(body_objects) -> str:
    """The armor's own texture is baked into its mesh materials, never into the scripts.
    Read it back off the imported mesh (the non-skin image) so the recipe can show it."""
    for obj in body_objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        for mat in obj.data.materials:
            if not mat or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type != "TEX_IMAGE" or node.image is None:
                    continue
                if not node.image.name.upper().startswith(_SKIN_PREFIXES):
                    return node.image.name
    return ""


def add_root_bone(armature_obj, report: list, name: str = "Root", unit: float = 1.0):
    """Add a bone at the origin and parent the hierarchy's top bone(s) to it.

    Non-destructive: existing bones keep their positions and connections, they just
    gain a parent. The top of the hierarchy is auto-detected (any parentless bone),
    which is Bip01 on the human skeleton but works for monsters too."""
    if armature_obj is None or armature_obj.type != "ARMATURE":
        return None
    if armature_obj.data.bones.get(name) is not None:
        report.append(f"[rig] root bone '{name}' already present")
        return name

    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_mode = armature_obj.mode

    view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit_bones = armature_obj.data.edit_bones
        tops = [bone for bone in edit_bones if bone.parent is None and bone.name != name]

        root = edit_bones.new(name)
        root.head = (0.0, 0.0, 0.0)
        root.tail = (0.0, 0.0, 0.1 * unit)
        root.roll = 0.0

        for bone in tops:
            bone.parent = root
            bone.use_connect = False

        adopted = [bone.name for bone in tops]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.objects.active = previous_active
        if previous_mode != "OBJECT":
            try:
                armature_obj.mode_set(mode=previous_mode)
            except Exception:
                pass

    report.append(
        f"[rig] root bone '{name}' created at the origin; "
        f"parented: {', '.join(adopted) if adopted else '(nothing)'}"
    )
    return name


CHARACTER_PATH = "gothic2/characters"
PROP_HEIGHT = 0.1
"""Props are parked this far above the ground so they are easy to find and re-rig
(metres - multiply by unit_factor() when the character is imported at another scale)"""


def unit_factor(scale: float) -> float:
    """How much bigger this recipe's units are than metres.

    Everything the assembler adds by hand - the root bone, prop bones, the parking
    height - is written in metres. At Source/SFM scale a 0.1 m bone is a fiftieth of
    a unit and effectively invisible, so those lengths are multiplied by this."""
    return (scale or SCALE_BLENDER_METRES) / SCALE_BLENDER_METRES


def sanitize(name: str) -> str:
    """'Hum_Head_V14_C0.tga' / 'HUM_HEAD_V14_C0-C.dds' -> 'Hum_Head_V14_C0'."""
    stem = os.path.splitext(name)[0]
    if stem.upper().endswith("-C"):
        stem = stem[:-2]
    return stem.strip().strip("._-") or name


def _rename(obj, new_name: str, report: list, label: str):
    if obj is None or obj.name == new_name:
        return
    old = obj.name
    obj.name = new_name
    if getattr(obj, "data", None) is not None and obj.data.users == 1:
        obj.data.name = new_name
    report.append(f"[name] {label}: '{old}' -> '{obj.name}'")


def apply_game_ready_names(char_name: str, objects_by_kind: dict, collection, report: list):
    """Give everything a predictable, engine-friendly name keyed on the character."""
    for obj in collection.objects:
        if obj.type == "ARMATURE":
            _rename(obj, f"{CHARACTER_PATH}/{char_name}", report, "armature")

    for kind, label in (("body", "armor"), ("head", "head"), ("weapon", "prop"), ("extra", "prop")):
        for obj in objects_by_kind.get(kind, []):
            if obj.type != "MESH":
                continue
            _rename(obj, f"{char_name}_{sanitize(obj.name)}", report, label)

    # Material slots are named after their own texture. The Gothic name is the artist's
    # 3ds Max slot name and means nothing downstream; two meshes on the same texture end
    # up sharing one material instead of collecting a .001 suffix each.
    from .material import rename_materials_to_texture

    slots = rename_materials_to_texture(collection.objects)
    if slots:
        report.append(f"[name] {slots} material slot(s) named after their texture file")

    # textures carry the file name, minus extension and the compiled "-C" suffix
    renamed = 0
    for obj in collection.objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        for material in obj.data.materials:
            if not material or not material.node_tree:
                continue
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image is not None:
                    clean = sanitize(node.image.name)
                    if clean and node.image.name != clean:
                        node.image.name = clean
                        renamed += 1
    if renamed:
        report.append(f"[name] {renamed} texture(s) renamed to their sanitised file name")


def shade_smooth(objects, report: list, label: str = "character"):
    """Smooth-shade imported geometry. Gothic meshes are low poly, so flat shading
    shows every facet; smoothing is what makes them read as skin and cloth."""
    meshes = 0
    faces = 0
    for obj in objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        obj.data.update()
        meshes += 1
        faces += len(obj.data.polygons)
    if meshes:
        report.append(f"[shade] smooth shading applied to {meshes} {label} mesh(es), {faces} faces")
    return meshes


def add_prop_bone(armature_obj, bone_name: str, location, report: list, unit: float = 1.0):
    """Give a prop its own bone so its vertex group actually deforms.

    The bone is parented to the skeleton's top (the Root bone when there is one), so
    the prop travels with the character and can be re-parented or constrained to a
    hand afterwards without touching the mesh."""
    if armature_obj is None or armature_obj.type != "ARMATURE":
        return None
    if armature_obj.data.bones.get(bone_name) is not None:
        return bone_name

    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit_bones = armature_obj.data.edit_bones
        bone = edit_bones.new(bone_name)
        bone.head = location
        bone.tail = (location[0], location[1], location[2] + 0.1 * unit)
        bone.roll = 0.0

        top = None
        for candidate in edit_bones:
            if candidate.name != bone_name and candidate.parent is None:
                top = candidate
                break
        if top is not None:
            bone.parent = top
            bone.use_connect = False
        parent_name = top.name if top else None
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.objects.active = previous_active

    report.append(f"[rig] bone '{bone_name}' added"
                  + (f" under '{parent_name}'" if parent_name else " (no parent)"))
    return bone_name


def rig_prop(obj, armature_obj, char_name: str, report: list, unit: float = 1.0):
    """Park a prop above the origin, give it its own bone in the rig, and weight it
    to that bone so it moves with the character."""
    if obj is None or obj.type != "MESH" or armature_obj is None:
        return

    height = PROP_HEIGHT * unit
    location = (0.0, 0.0, height)
    obj.matrix_world = Matrix.Translation(location)

    bone_name = add_prop_bone(armature_obj, obj.name, location, report, unit)

    group_name = bone_name or obj.name
    group = obj.vertex_groups.get(group_name) or obj.vertex_groups.new(name=group_name)
    group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")

    modifier = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
    if modifier is None:
        modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature_obj
    modifier.use_vertex_groups = True

    matrix_world = obj.matrix_world.copy()
    obj.parent = armature_obj
    obj.parent_type = "OBJECT"
    obj.matrix_parent_inverse = armature_obj.matrix_world.inverted()
    obj.matrix_world = matrix_world

    report.append(
        f"[rig] prop '{obj.name}' parked {height:g} up, weighted 100% to its own "
        f"bone '{group.name}' on '{armature_obj.name}' "
        f"(parent that bone to a hand in Edit Mode to make the character carry it)"
    )


def assemble_character(char_name: str, parts: list, report: list, options: dict = None) -> dict:
    """Import a character.

    `parts` is a list of (kind, token) with kind in {"body", "head", "weapon", "extra"};
    `options` carries the texture overrides, search folders and rigging toggles.
    Returns {"imported": int, "armor_texture": str}.
    """
    options = options or {}
    folders = options.get("folders", {})
    scale = options.get("scale") or SCALE_BLENDER_METRES
    unit = unit_factor(scale)
    if abs(scale - SCALE_BLENDER_METRES) > 1e-9:
        report.append(f"[scale] every part imported at {scale:.6g} "
                      f"({unit:.4g}x the default centimetres-to-metres)")
    imported = 0
    objects_by_kind = {}

    with collection_scope(char_name) as collection:
        armature_obj = None
        for kind, token in parts:
            before = set(bpy.data.objects)
            path = resolve_token(token, report, search_dir=folders.get(kind, ""))
            if not path:
                continue
            if import_asset_file(path, report, import_morphs=options.get("import_head_morphs", False)
                                 if kind == "head" else False, scale=scale,
                                 armature=armature_obj, skeleton=options.get("skeleton")):
                imported += 1
            new_objects = [obj for obj in bpy.data.objects if obj not in before]
            objects_by_kind.setdefault(kind, []).extend(new_objects)
            # A monster body builds its own skeleton from its .MDH; anything skinned
            # afterwards has to land on THAT rig instead of building a second one.
            if armature_obj is None:
                armature_obj = next((obj for obj in new_objects if obj.type == "ARMATURE"), None)

        body_objects = objects_by_kind.get("body", [])
        head_objects = objects_by_kind.get("head", [])

        detected_armor = detect_armor_texture(body_objects)
        if detected_armor:
            report.append(f"[tex] armor texture detected on the mesh: {detected_armor}")

        for obj in head_objects:
            if obj.type == "MESH" and obj.data is not None:
                keys = obj.data.shape_keys
                if keys:
                    report.append(
                        f"[morph] {obj.name}: {len(keys.key_blocks) - 1} morph shape key(s) "
                        f"(Object Data Properties > Shape Keys)"
                    )
                elif options.get("import_head_morphs"):
                    report.append(f"[note] {obj.name}: no morph animations in this file")

        # ---- texture variants ----
        if options.get("body_texture") and body_objects:
            image = _load_texture_image(options["body_texture"], "body", report)
            if image:
                _swap_images(body_objects, image, True, report, "body skin")

        if options.get("armor_texture") and body_objects:
            image = _load_texture_image(options["armor_texture"], "armor", report)
            if image:
                _swap_images(body_objects, image, False, report, "armor")

        if options.get("head_texture") and head_objects:
            image = _load_texture_image(options["head_texture"], "head", report)
            if image:
                # only the face itself, never the mouth/teeth sub-materials
                for obj in head_objects:
                    if obj.type != "MESH" or obj.data is None:
                        continue
                    for mat in obj.data.materials:
                        if not mat or not mat.node_tree:
                            continue
                        for node in mat.node_tree.nodes:
                            if (
                                node.type == "TEX_IMAGE"
                                and node.image is not None
                                and node.image.name.upper().startswith("HUM_HEAD")
                                and node.image is not image
                            ):
                                node.image = image
                report.append(f"[tex] head skin -> {image.name}")

        # ---- rigging: put the head on the head bone ----
        if options.get("attach_head") and head_objects:
            armatures = [obj for obj in collection.objects if obj.type == "ARMATURE"]
            if not armatures:
                report.append("[note] head attachment skipped - no armature in the character")
            else:
                armature_obj = armatures[0]
                bone = find_head_bone(armature_obj)
                if bone is None:
                    report.append(f"[note] no head bone found on '{armature_obj.name}'")
                else:
                    for obj in head_objects:
                        if obj.type == "MESH":
                            attach_to_bone(obj, armature_obj, bone, report)

        if options.get("create_root_bone"):
            for obj in collection.objects:
                if obj.type == "ARMATURE":
                    add_root_bone(obj, report, options.get("root_bone_name") or "Root", unit)

        # ---- presentation, naming and prop rigging ----
        if options.get("smooth_shading", True):
            shade_smooth(collection.objects, report)

        armature_obj = next((obj for obj in collection.objects if obj.type == "ARMATURE"), None)
        if options.get("game_ready_names", True):
            apply_game_ready_names(char_name, objects_by_kind, collection, report)

        if options.get("rig_props", True) and armature_obj is not None:
            for kind in ("weapon", "extra"):
                for obj in objects_by_kind.get(kind, []):
                    rig_prop(obj, armature_obj, char_name, report, unit)

        # get_armature() reuses any object literally called "Armature", so a rig left
        # with the default name would capture the NEXT character's meshes. The
        # game-ready pass usually renames it already; this is the fallback.
        for obj in collection.objects:
            if obj.type == "ARMATURE" and obj.name.split(".")[0] == "Armature":
                rig_name = f"{CHARACTER_PATH}/{char_name}"
                obj.name = rig_name
                if obj.data is not None:
                    obj.data.name = rig_name
                report.append(f"[rig] armature renamed to '{obj.name}' so the next character gets its own")

    return {"imported": imported, "armor_texture": detected_armor}


def _finish_report(operator, char_name: str, imported: int, report: list):
    for line in report:
        print(f"Essemble: {line}", level="ERROR" if line.startswith("[error]") else "INFO")
    summary = f"'{char_name}': {imported} asset(s) imported"
    problems = [line for line in report if line.startswith(("[missing]", "[error]", "[planned]"))]
    if problems:
        call_message_box(message_text="\n".join([summary] + problems), message_type="I")
    operator.report({"INFO"}, summary)


# -------------------------------------------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------------------------------------------


class KRX_OT_planned_format(bpy.types.Operator):
    """Placeholder for a Gothic format that is not importable yet"""

    bl_idname = "krx.planned_format"
    bl_label = "Planned Format"
    bl_options = {"INTERNAL"}

    format: StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context, properties):
        lore = FORMAT_LORE.get(properties.format, "Gothic file format.")
        return f"{lore}.\n\n(This button is a placeholder - the importer is not written yet)"

    def execute(self, context):
        label = dict(PLANNED_FORMATS).get(self.format, self.format)
        call_message_box(
            message_text=f"{label} import is planned but not implemented yet.\n"
                         f"The goal is a fully standalone Gothic pipeline - this format is on the list.",
            message_type="I",
        )
        return {"FINISHED"}


_NPC_CACHE = {"key": None, "npc": None}


def _npc_preview(filepath: str):
    """Parse an NPC script for the file-browser sidebar, cached per path+mtime."""
    try:
        key = (filepath, os.path.getmtime(filepath))
    except OSError:
        return None
    if _NPC_CACHE["key"] == key:
        return _NPC_CACHE["npc"]
    try:
        npc = game_data.parse_npc_file(filepath)
    except Exception:
        npc = None
    _NPC_CACHE["key"] = key
    _NPC_CACHE["npc"] = npc
    return npc


def _options_from_props(props) -> dict:
    return {
        "scale": props.scale,
        "create_root_bone": props.create_root_bone,
        "root_bone_name": props.root_bone_name,
        "body_texture": props.body_texture.strip(),
        "head_texture": props.head_texture.strip(),
        "armor_texture": props.armor_texture.strip(),
        "attach_head": props.attach_head,
        "import_head_morphs": props.import_head_morphs,
        "game_ready_names": props.game_ready_names,
        "rig_props": props.rig_props,
        "smooth_shading": props.smooth_shading,
    }


RECIPE_KEYS = (
    "character_name", "body_or_armor", "head", "weapons", "extras",
    "body_texture", "head_texture", "armor_texture",
    "scale_preset", "scale",
    "attach_head", "import_head_morphs", "create_root_bone", "root_bone_name",
    "game_ready_names", "smooth_shading", "rig_props",
)
"""Everything that makes up a character - what a saved .json round-trips"""


def recipe_to_dict(props) -> dict:
    return {key: getattr(props, key) for key in RECIPE_KEYS}


def recipe_from_dict(props, data: dict) -> int:
    """Apply a saved recipe. Unknown keys are ignored so old files keep loading."""
    applied = 0
    for key in RECIPE_KEYS:
        if key not in data:
            continue
        try:
            setattr(props, key, data[key])
            applied += 1
        except (TypeError, ValueError):
            print(f"saved character: ignoring '{key}' = {data[key]!r}", level="WARN")
    return applied


def essemble_dir() -> str:
    from .system import ESSEMBLE_DIR

    ESSEMBLE_DIR.mkdir(parents=True, exist_ok=True)
    return str(ESSEMBLE_DIR)


class KRX_OT_save_character(bpy.types.Operator):
    """Save the recipe above as a .json in the add-on's _Essemble folder.

Only the recipe is saved - the file names, textures, scale and rigging options - so it
stays a few lines of readable text you can hand to someone else"""

    bl_idname = "krx.save_character"
    bl_label = "Save Character"
    bl_options = {"REGISTER"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        props = context.scene.krx_assemble
        name = sanitize(props.character_name.strip() or "Character")
        self.filepath = os.path.join(essemble_dir(), f"{name}.json")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import json

        props = context.scene.krx_assemble
        path = self.filepath if self.filepath.lower().endswith(".json") else self.filepath + ".json"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(recipe_to_dict(props), handle, indent=2)
        except OSError as err:
            self.report({"ERROR"}, f"Could not save: {err}")
            return {"CANCELLED"}

        print(f"character saved: {path}")
        self.report({"INFO"}, f"Saved {os.path.basename(path)}")
        return {"FINISHED"}


class KRX_OT_load_character(bpy.types.Operator):
    """Load a saved character recipe (.json) back into the fields above.

Opens in the add-on's _Essemble folder. Loading only fills the fields in - press
Essemble Character afterwards to actually build it"""

    bl_idname = "krx.load_character"
    bl_label = "Load Character"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    build: BoolProperty(
        name="Essemble It Straight Away",
        description="Import the character as soon as the recipe is loaded, instead of "
                    "just filling in the fields",
        default=False,
    )

    def invoke(self, context, event):
        self.filepath = essemble_dir() + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        self.layout.prop(self, "build")

    def execute(self, context):
        import json

        if not os.path.isfile(self.filepath):
            self.report({"ERROR"}, f"Not a file: {self.filepath}")
            return {"CANCELLED"}
        try:
            with open(self.filepath, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as err:
            self.report({"ERROR"}, f"Could not read: {err}")
            return {"CANCELLED"}
        if not isinstance(data, dict):
            self.report({"ERROR"}, "That .json is not a character recipe")
            return {"CANCELLED"}

        props = context.scene.krx_assemble
        applied = recipe_from_dict(props, data)
        print(f"character loaded: {self.filepath} ({applied} field(s))")

        if self.build:
            return bpy.ops.krx.assemble_character()
        self.report({"INFO"}, f"Loaded {os.path.basename(self.filepath)}")
        return {"FINISHED"}


class KRX_OT_assemble_character(bpy.types.Operator):
    """Essemble a character from the fields below.
Each field takes a file name (Armor_Vlk_L.asc) or a Daedalus instance (ITAR_VLK_L, ItMw_Schwert3).
Everything found in the master folders is imported into a collection named after the character"""

    bl_idname = "krx.assemble_character"
    bl_label = "Essemble Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.krx_assemble
        char_name = props.character_name.strip() or "Character"

        report = []
        parts = []
        if props.body_or_armor.strip():
            parts.append(("body", props.body_or_armor))
        if props.head.strip():
            parts.append(("head", props.head))
        parts.extend(("weapon", token) for token in props.weapons.split(",") if token.strip())
        parts.extend(("extra", token) for token in props.extras.split(",") if token.strip())

        if not parts:
            self.report({"ERROR"}, "All fields are empty - nothing to essemble")
            return {"CANCELLED"}

        result = assemble_character(char_name, parts, report, _options_from_props(props))
        if result["armor_texture"] and not props.armor_texture.strip():
            props.armor_texture = result["armor_texture"]
        _finish_report(self, char_name, result["imported"], report)
        return {"FINISHED"}


class KRX_OT_assemble_from_d(bpy.types.Operator):
    """Essemble a character from an NPC Daedalus script (.d).
Reads B_SetNpcVisual, EquipItem and overlay calls, resolves every referenced armor,
weapon and mesh through the game scripts, computes the face/body texture variants
from the script constants, and imports everything into a collection named after the NPC"""

    bl_idname = "krx.assemble_from_d"
    bl_label = "Essemble from .D"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.d", options={"HIDDEN"})

    def invoke(self, context, event):
        if not self.filepath:
            default = game_data.default_import_dir("d")
            if default:
                self.filepath = default + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        props = context.scene.krx_assemble

        npc = _npc_preview(self.filepath)
        box = layout.box()
        if npc is None:
            box.label(text="Select an NPC script (.d)", icon="TEXT")
            return

        box.label(text=f"{npc['name'] or npc['instance'] or '?'}", icon="OUTLINER_OB_ARMATURE")
        info = box.column(align=True)
        info.scale_y = 0.85
        info.label(text=f"instance: {npc['instance']}")
        if npc["kind"] == "MONSTER":
            info.label(text="kind: monster / orc")
            info.label(text=f"body mesh: {npc['body_mesh'] or '?'}")
            info.label(text=f"skeleton: {npc['skeleton'] or '?'}")
            info.label(text=f"head: {npc['head'] or '(part of the body)'}")
        else:
            info.label(text=f"gender: {npc['gender'] or '?'}")
            info.label(text=f"armor: {npc['armor_instance'] or '(none - naked body)'}")
            info.label(text=f"head: {npc['head'] or '(none)'}")
        if npc["equipped"]:
            info.label(text=f"equipped: {', '.join(npc['equipped'])}")

        textures = game_data.npc_texture_names(npc)
        if textures:
            tex = box.column(align=True)
            tex.scale_y = 0.85
            tex.label(text=f"body skin: {textures.get('body', '-')}")
            if npc["kind"] != "MONSTER":
                tex.label(text=f"head skin: {textures.get('head', '-')}")
        for overlay in npc["overlays"]:
            row = box.row()
            row.enabled = False
            row.label(text=f"overlay {overlay} (needs .MDS)", icon="INFO")

        col = layout.column()
        draw_texture_fields(col, props)

        draw_scale(layout, props)

        rig = layout.box()
        rig.label(text="Rigging", icon="ARMATURE_DATA")
        rig.prop(props, "import_head_morphs")
        rig.prop(props, "attach_head")
        rig.prop(props, "create_root_bone")
        if props.create_root_bone:
            rig.prop(props, "root_bone_name")
        rig.prop(props, "game_ready_names")
        rig.prop(props, "rig_props")
        rig.prop(props, "smooth_shading")

    def execute(self, context):
        if not os.path.isfile(self.filepath):
            self.report({"ERROR"}, f"Not a file: {self.filepath}")
            return {"CANCELLED"}

        props = context.scene.krx_assemble
        npc = game_data.parse_npc_file(self.filepath)
        char_name = npc["name"] or npc["instance"] or os.path.splitext(os.path.basename(self.filepath))[0]

        report = [f"[npc] {npc['instance']} '{npc['name']}' kind={npc['kind'].lower()} "
                  f"gender={npc['gender'] or '-'}"]
        if npc["skeleton"]:
            report.append(f"[skeleton] {npc['skeleton']}")
        parts = []

        if npc["kind"] == "MONSTER" and npc["body_mesh"]:
            # Mdl_SetVisualBody names a compiled body mesh ("Wol_Body", "Orc_BodyWarrior")
            # rather than an armor instance; it resolves to the .mdm and brings its own rig.
            parts.append(("body", npc["body_mesh"]))
        elif npc["armor_instance"]:
            parts.append(("body", npc["armor_instance"]))
        elif npc["gender"] in game_data.DEFAULT_BODIES:
            body = game_data.DEFAULT_BODIES[npc["gender"]]
            report.append(f"[note] no armor - trying naked body '{body}'")
            parts.append(("body", body))

        if npc["head"]:
            parts.append(("head", npc["head"]))

        parts.extend(("weapon", token) for token in npc["equipped"])

        for overlay in npc["overlays"]:
            report.append(f"[planned] overlay '{overlay}' needs .MDS support")

        if not parts:
            self.report({"ERROR"}, "No visual references found in this .d file")
            return {"CANCELLED"}

        textures = game_data.npc_texture_names(npc)
        if textures:
            report.append(
                f"[tex] variants from script: {', '.join(sorted(textures.values()))} "
                f"(face '{npc['face']}', body '{npc['body_tex']}')"
            )

        options = _options_from_props(props)
        # Mdl_SetVisual names the skeleton ("Golem.mds"). Bodies built only from parts
        # bolted to bones carry no skeleton checksum, so this is the only way to rig them.
        options["skeleton"] = npc["skeleton"]
        options["body_texture"] = textures.get("body", "")
        options["head_texture"] = textures.get("head", "")
        # The armor's own texture is baked into its mesh, so the mesh is authoritative
        # here. Never reuse whatever is left in the field from a previous character.
        options["armor_texture"] = ""

        result = assemble_character(char_name, parts, report, options)

        props.character_name = char_name
        props.body_or_armor = npc["armor_instance"]
        props.head = npc["head"]
        props.weapons = ", ".join(npc["equipped"])
        props.body_texture = textures.get("body", "")
        props.head_texture = textures.get("head", "")
        # the armor's texture only exists on the mesh - show what was actually used
        props.armor_texture = result["armor_texture"] or props.armor_texture

        _finish_report(self, char_name, result["imported"], report)
        return {"FINISHED"}


class KRX_OT_open_master_folder(bpy.types.Operator):
    """Open the first configured master folder (the Gothic installation) in the file explorer"""

    bl_idname = "krx.open_master_folder"
    bl_label = "Open Master Folder"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from .preferences import get_master_folders

        masters = get_master_folders()
        if not masters:
            self.report({"ERROR"}, "No existing master folder configured (see addon preferences)")
            return {"CANCELLED"}
        import subprocess

        subprocess.Popen(["explorer", masters[0]], shell=True)
        return {"FINISHED"}


class KRX_OT_rebuild_indexes(bpy.types.Operator):
    """Rebuild the cached asset / script / texture indexes.
Use after adding files to the game folders while Blender is running"""

    bl_idname = "krx.rebuild_indexes"
    bl_label = "Rebuild Indexes"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from . import material

        game_data.clear_caches()
        material.loaded_texture_paths.clear()
        assets = len(game_data.asset_index(force=True))
        scripts = len(game_data.script_index(force=True))
        self.report({"INFO"}, f"Indexes rebuilt: {assets} assets, {scripts} script instances")
        return {"FINISHED"}


class KRX_OT_clear_tex_cache(bpy.types.Operator):
    """Delete the converted .TEX -> DDS files cached inside the addon folder"""

    bl_idname = "krx.clear_tex_cache"
    bl_label = "Clear .TEX Cache"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from .tex_convert import clear_cache

        self.report({"INFO"}, f"Removed {clear_cache()} cached DDS file(s)")
        return {"FINISHED"}


class KRX_OT_tex_preview(bpy.types.Operator):
    """Import a texture onto a preview sphere.
Loads a Gothic .TEX (converted to DDS on the fly), .tga, .dds or .png and maps it
onto a new UV sphere at the 3D cursor - the quickest way to check that a compiled
texture converted correctly and looks the way it should"""

    bl_idname = "krx.tex_preview"
    bl_label = "Import TEX to Sphere"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.tex;*.TEX;*.tga;*.dds;*.png;*.jpg", options={"HIDDEN"})

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if not self.filepath:
            # this button is for compiled textures, so open where they live
            folder = game_data.texture_dir("compiled") or game_data.texture_dir("head")
            if folder:
                self.filepath = folder + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        paths = (
            [os.path.join(self.directory, f.name) for f in self.files if f.name]
            if self.files and self.directory
            else [self.filepath]
        )
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            self.report({"ERROR"}, "No texture file selected")
            return {"CANCELLED"}

        made = 0
        for index, path in enumerate(paths):
            label = os.path.splitext(os.path.basename(path))[0]
            source = path
            info = ""
            if path.lower().endswith(".tex"):
                from .tex_convert import TexError, cached_dds, read_header

                try:
                    header = read_header(path)
                    info = (f"{header['width']}x{header['height']} "
                            f"format {header['format']} mips {header['mipmaps']}")
                except TexError as err:
                    self.report({"ERROR"}, str(err))
                    continue
                converted = cached_dds(path)
                if not converted:
                    self.report({"ERROR"}, f"Could not convert {os.path.basename(path)}")
                    continue
                source = converted

            try:
                image = bpy.data.images.load(source, check_existing=True)
            except RuntimeError as err:
                self.report({"ERROR"}, f"{os.path.basename(path)}: {err}")
                continue

            location = list(context.scene.cursor.location)
            location[0] += index * 0.35
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=48, ring_count=24, radius=0.12, calc_uvs=True, location=location
            )
            sphere = context.active_object
            sphere.name = f"TEX Preview - {label}"
            for polygon in sphere.data.polygons:
                polygon.use_smooth = True

            material = bpy.data.materials.new(f"TEXPreview_{label}")
            material.use_nodes = True
            tree = material.node_tree
            principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            tex_node = tree.nodes.new("ShaderNodeTexImage")
            tex_node.image = image
            tex_node.location = (-400, 0)
            if principled:
                tree.links.new(tex_node.outputs["Color"], principled.inputs["Base Color"])
                principled.inputs["Roughness"].default_value = 1.0
                principled.inputs["Metallic"].default_value = 0.0
                from .material import image_has_alpha

                if image_has_alpha(image):
                    tree.links.new(tex_node.outputs["Alpha"], principled.inputs["Alpha"])
                    material.blend_method = "CLIP"
            sphere.data.materials.append(material)
            sphere["krx_texture_source"] = path
            made += 1
            print(f"TEX preview '{label}' {info} -> {image.size[0]}x{image.size[1]}")

        if not made:
            return {"CANCELLED"}
        self.report({"INFO"}, f"{made} preview sphere(s) created")
        return {"FINISHED"}


class KRX_OT_copy_report(bpy.types.Operator):
    """Copy a diagnostics report to the clipboard and save it next to the add-on.
Includes versions, master folders, index sizes and the whole GOTHIC TOOLS console log -
paste it into a bug report when something goes wrong"""

    bl_idname = "krx.copy_report"
    bl_label = "Copy Diagnostics Report"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from . import bl_info, log, material
        from .preferences import get_master_folders
        from .system import PLUGIN_ROOT
        from .tex_convert import CACHE_DIR

        header = [
            f"add-on      : {bl_info['name']} {bl_info['version']}",
            f"blender     : {bpy.app.version_string} ({bpy.app.build_hash})",
            f"platform    : {bpy.app.build_platform}",
            f"plugin root : {PLUGIN_ROOT}",
        ]
        masters = get_master_folders()
        header.append(f"master folders ({len(masters)}):")
        header.extend(f"  - {folder}" for folder in masters) or header.append("  (none configured!)")

        try:
            header.append(f"asset index   : {len(game_data.asset_index())} stems")
            header.append(f"script index  : {len(game_data.script_index())} instances, "
                          f"{len(game_data.constants_index())} constants")
        except Exception as err:
            header.append(f"index error   : {err}")

        textures = len(material.loaded_texture_paths)
        header.append(f"texture index : {textures} entries "
                      f"({'built' if textures else 'not built yet'})")
        cached = len(list(CACHE_DIR.glob('*.dds'))) if CACHE_DIR.is_dir() else 0
        header.append(f"tex cache     : {cached} converted DDS file(s)")

        addon_entry = bpy.context.preferences.addons.get(__package__)
        if addon_entry:
            prefs = addon_entry.preferences
            header.append(f"developer mode: {prefs.developer_mode}")
            header.append(f"texture dirs  : {len(prefs.texture_directories)} configured")

        header.append("")
        header.append(f"SCENE ({len(bpy.data.objects)} objects, {len(bpy.data.collections)} collections, "
                      f"{len(bpy.data.materials)} materials, {len(bpy.data.images)} images)")
        for collection in bpy.data.collections:
            header.append(f"  collection '{collection.name}': {len(collection.objects)} object(s)")
            for obj in collection.objects:
                bits = [obj.type.lower()]
                if obj.type == "MESH" and obj.data:
                    bits.append(f"{len(obj.data.vertices)}v/{len(obj.data.polygons)}f")
                    if obj.data.shape_keys:
                        bits.append(f"{len(obj.data.shape_keys.key_blocks)} shape keys")
                    if obj.vertex_groups:
                        bits.append(f"{len(obj.vertex_groups)} groups")
                    mats = [m.name for m in obj.data.materials if m]
                    if mats:
                        bits.append("mats: " + ", ".join(mats[:4]))
                elif obj.type == "ARMATURE" and obj.data:
                    bits.append(f"{len(obj.data.bones)} bones")
                    if obj.animation_data and obj.animation_data.action:
                        action = obj.animation_data.action
                        bits.append(f"action '{action.name}' "
                                    f"{tuple(round(v) for v in action.frame_range)}")
                mods = [m.type for m in obj.modifiers]
                if mods:
                    bits.append("modifiers: " + ",".join(mods))
                header.append(f"    - {obj.name}: " + " | ".join(bits))

        images = [i for i in bpy.data.images if i.name != "Render Result"]
        if images:
            header.append("")
            header.append(f"IMAGES ({len(images)})")
            for image in images[:40]:
                exists = os.path.exists(bpy.path.abspath(image.filepath)) if image.filepath else False
                header.append(f"    - {image.name}: {image.size[0]}x{image.size[1]} "
                              f"{'on disk' if exists else 'MISSING FILE'} {image.filepath}")

        text = log.report_text(header)

        from .system import LOG_DIR

        path = LOG_DIR / "KRX_IMPORT_report.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf8")
            saved = str(path)
        except OSError as err:
            saved = f"(could not write file: {err})"

        context.window_manager.clipboard = text
        log.info(f"diagnostics report copied to clipboard and written to {saved}")
        self.report({"INFO"}, f"Report copied to clipboard - saved to {saved}")
        return {"FINISHED"}


class KRX_OT_pick_asset(bpy.types.Operator):
    """Browse for a mesh file. Opens in the folder that kind of part normally lives in.

Weapons and Extras take several files, so a multi-selection is appended to whatever is
already in the field rather than replacing it"""

    bl_idname = "krx.pick_asset"
    bl_label = "Pick Asset"
    bl_options = {"INTERNAL"}

    filepath: StringProperty(subtype="FILE_PATH")
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN"})
    filter_glob: StringProperty(
        default="*.asc;*.mdm;*.mmb;*.mrm;*.3ds;*.mdl;*.msh",
        options={"HIDDEN"},
    )
    target: StringProperty(options={"HIDDEN"})
    kind: StringProperty(options={"HIDDEN"})
    append: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        folder = game_data.part_dir(self.kind) if self.kind else None
        if not folder:
            folder = game_data.default_import_dir("asc")
        if folder:
            self.filepath = folder + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = context.scene.krx_assemble
        chosen = [item.name for item in self.files if item.name] or (
            [os.path.basename(self.filepath)] if self.filepath else []
        )
        if not chosen:
            return {"CANCELLED"}

        if self.append:
            current = [token.strip() for token in getattr(props, self.target).split(",")]
            current = [token for token in current if token]
            for name in chosen:
                if name not in current:
                    current.append(name)
            setattr(props, self.target, ", ".join(current))
        else:
            setattr(props, self.target, chosen[0])
        return {"FINISHED"}


class KRX_OT_clear_field(bpy.types.Operator):
    """Empty this field"""

    bl_idname = "krx.clear_field"
    bl_label = "Clear"
    bl_options = {"INTERNAL"}

    target: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        setattr(context.scene.krx_assemble, self.target, "")
        return {"FINISHED"}


class KRX_OT_pick_texture(bpy.types.Operator):
    """Browse for a texture file. Opens in the matching Gothic texture folder"""

    bl_idname = "krx.pick_texture"
    bl_label = "Pick Texture"
    bl_options = {"INTERNAL"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.tga;*.tex;*.dds;*.png", options={"HIDDEN"})
    target: StringProperty(options={"HIDDEN"})
    kind: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        folder = game_data.texture_dir(self.kind) if self.kind else None
        if folder:
            self.filepath = folder + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if self.target:
            setattr(context.scene.krx_assemble, self.target, os.path.basename(self.filepath))
        return {"FINISHED"}


# -------------------------------------------------------------------------------------------------------
# Scene properties + panel
# -------------------------------------------------------------------------------------------------------


def _assemble_scale_preset_update(props, context):
    if props.scale_preset == "METRES":
        props.scale = SCALE_BLENDER_METRES
    elif props.scale_preset == "SOURCE":
        props.scale = SCALE_SOURCE_UNITS


# Every recipe field: the text box, a browse button that opens where that kind of part
# lives, and a clear button. Weapons and Extras hold a comma-separated list, so their
# browser appends a multi-selection instead of overwriting.
RECIPE_FIELDS = (
    ("body_or_armor", "body", False),
    ("head", "head", False),
    ("weapons", "weapon", True),
    ("extras", "extra", True),
)

TEXTURE_FIELDS = (
    ("body_texture", "body"),
    ("head_texture", "head"),
    ("armor_texture", "armor"),
)


def draw_recipe_fields(layout, props):
    for field, kind, append in RECIPE_FIELDS:
        row = layout.row(align=True)
        row.prop(props, field)
        op = row.operator("krx.pick_asset", text="", icon="FILEBROWSER")
        op.target, op.kind, op.append = field, kind, append
        op = row.operator("krx.clear_field", text="", icon="X")
        op.target = field


def draw_texture_fields(layout, props):
    for field, kind in TEXTURE_FIELDS:
        row = layout.row(align=True)
        row.prop(props, field)
        op = row.operator("krx.pick_texture", text="", icon="FILEBROWSER")
        op.target, op.kind = field, kind
        op = row.operator("krx.clear_field", text="", icon="X")
        op.target = field


def draw_scale(layout, props):
    """The Units preset + the scale field it drives, shared by both Essemble dialogs."""
    box = layout.box()
    box.label(text="Scale", icon="FIXED_SIZE")
    box.prop(props, "scale_preset")
    row = box.row()
    row.enabled = props.scale_preset == "CUSTOM"
    row.prop(props, "scale")


class KRXAssembleProps(bpy.types.PropertyGroup):
    character_name: StringProperty(
        name="Name",
        description="Character name - the imported assets are placed in a collection with this name",
        default="",
    )
    body_or_armor: StringProperty(
        name="Body / Armor",
        description="Armor or body mesh: a file (Armor_Vlk_L.asc) or an armor instance (ITAR_VLK_L).\n"
                    "In Gothic an armor REPLACES the naked body mesh, so one entry is enough",
        default="",
    )
    head: StringProperty(
        name="Head",
        description="Head mesh (Hum_Head_Thief). Retail heads are compiled .MMB and import "
                    "with their morph animations",
        default="",
    )
    weapons: StringProperty(
        name="Weapons",
        description="Comma-separated weapon files or instances (ItMw_Schwert3, ItRw_Bow_L_01.3ds)",
        default="",
    )
    extras: StringProperty(
        name="Extras",
        description="Comma-separated additional meshes to pull into the character collection",
        default="",
    )
    body_texture: StringProperty(
        name="Body Skin",
        description="Body skin texture applied to the bare-skin materials of the body/armor mesh.\n"
                    "A file name (Hum_Body_Naked_V1_C0.tga) - the .D assembler fills this in "
                    "automatically from the script's bodyTex constant",
        default="",
    )
    head_texture: StringProperty(
        name="Head Skin",
        description="Face texture applied to the head mesh (Hum_Head_V14_C0.tga).\n"
                    "The .D assembler computes it from the script's faceTex constant",
        default="",
    )
    armor_texture: StringProperty(
        name="Armor Skin",
        description="Optional override for the armor's own (non-skin) texture, e.g. Buerger2_1.tga.\n"
                    "Gothic bakes this into the armor mesh - the scripts do NOT store an armor "
                    "texture - so this field is a manual override",
        default="",
    )
    scale_preset: EnumProperty(
        name="Units",
        description="Unit preset for every mesh of the character. Gothic files are in "
                    "centimetres, and the whole recipe is imported at one scale so the "
                    "head, armor and weapons still line up",
        items=(
            ("METRES", "Blender Metres",
             "Gothic centimetres to metres (0.01) - a human comes out about 1.8 m tall"),
            ("SOURCE", "Source / SFM Units",
             "Gothic centimetres to Source engine units (1 unit = 0.01905 m), the same "
             "preset the .ASC and .MAN importers use - for SFM ports"),
            ("CUSTOM", "Custom", "Type the scale by hand below"),
        ),
        default="METRES",
        update=_assemble_scale_preset_update,
    )
    scale: FloatProperty(
        name="Scale",
        description="Multiplier applied to every imported mesh of the character",
        default=SCALE_BLENDER_METRES,
        min=0.000001,
        soft_min=0.0001,
        soft_max=100.0,
        precision=6,
    )
    attach_head: BoolProperty(
        name="Skin Head to Bone",
        description="Place the imported head in the head bone's own frame (Bip01 Head), weight "
                    "every vertex to that bone and add an Armature modifier - so the head follows "
                    "animation like the rest of the body",
        default=True,
    )
    import_head_morphs: BoolProperty(
        name="Import Head Morphs",
        description="Import the head's morph animations (expressions, blinking, lip-sync visemes) "
                    "as shape keys",
        default=True,
    )
    create_root_bone: BoolProperty(
        name="Create Root Bone",
        description="Add a bone at the world origin and parent the top of the skeleton to it "
                    "(Bip01 on humans, auto-detected otherwise). Non-destructive: no existing "
                    "bone moves, they only gain a parent",
        default=True,
    )
    root_bone_name: StringProperty(
        name="Root Name",
        description="Name for the root bone added at the origin",
        default="Root",
    )
    game_ready_names: BoolProperty(
        name="Game-Ready Names",
        description="Rename everything after the character: the rig becomes "
                    "gothic2/characters/<Name>, meshes become <Name>_<file>, and textures "
                    "lose their extension and compiled suffix",
        default=True,
    )
    smooth_shading: BoolProperty(
        name="Shade Smooth",
        description="Smooth-shade every mesh of the assembled character. Gothic models "
                    "are low poly, so flat shading shows every facet",
        default=True,
    )
    rig_props: BoolProperty(
        name="Rig Props",
        description="Park weapons and extras 0.1 m above the origin, attach them to the "
                    "rig and give each its own vertex group at weight 1",
        default=True,
    )


class KRX_PT_gothic(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Gothic"
    bl_label = "Gothic Tools"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        row = layout.row(align=True)
        row.prop(wm, "krx_gothic_tab", expand=True)

        tab = wm.krx_gothic_tab

        if tab == "IMPORT":
            col = layout.column(align=True)
            col.operator("import_scene.krxzenimpgui", text="ZenGin World (.zen)", icon_value=icons.icon_id("zen"))
            col.operator("import_scene.krxmshimpgui", text="Compiled Mesh (.msh)", icon_value=icons.icon_id("msh"))
            col.operator("import_scene.krxmrmimpgui", text="Multi-Res Mesh (.mrm)", icon_value=icons.icon_id("mrm"))
            col.operator("import_scene.krx3dsimpgui", text="3D Studio Mesh (.3ds)", icon_value=icons.icon_id("3ds"))
            col.operator("import_scene.batascimpgui", text="ASCII Model (.asc)", icon_value=icons.icon_id("asc"))
            col.operator("import_scene.krxmmbimpgui", text="MorphMesh (.mmb)", icon_value=icons.icon_id("mmb"))
            col.operator("import_scene.krxmdmimpgui", text="Model Mesh (.mdm)", icon_value=icons.icon_id("mrm"))
            col.operator("import_scene.krxmdlimpgui", text="Model (.mdl)", icon_value=icons.icon_id("mrm"))
            col.operator("import_scene.krxmanimpgui", text="Animation (.man)", icon_value=icons.icon_id("man"))
            col.operator("import_scene.krxmdsimpgui", text="Model Script (.mds)", icon_value=icons.icon_id("mds_or_msb"))
            col.operator("krx.tex_preview", text="Texture to Sphere (.tex)", icon_value=icons.icon_id("tex"))

            layout.separator()
            box = layout.box()
            box.label(text="Planned (standalone goal)", icon="TOOL_SETTINGS")
            col = box.column(align=True)
            for key, label in PLANNED_FORMATS:
                op = col.operator("krx.planned_format", text=label, icon_value=icons.icon_id(key))
                op.format = key

            layout.separator()
            col = layout.column(align=True)
            col.label(text="Export")
            col.operator("export_scene.krx3dsexpgui", text="3D Studio Mesh (.3ds)", icon_value=icons.icon_id("3ds"))
            col.operator("export_scene.krxascexpgui", text="ASCII Model (.asc)", icon_value=icons.icon_id("asc"))

        elif tab == "ESSEMBLE":
            props = context.scene.krx_assemble

            box = layout.box()
            box.label(text="Character Recipe", icon="OUTLINER_OB_ARMATURE")
            col = box.column()
            col.prop(props, "character_name")
            draw_recipe_fields(col, props)

            box = layout.box()
            box.label(text="Textures", icon="TEXTURE")
            draw_texture_fields(box.column(), props)

            draw_scale(layout, props)

            box = layout.box()
            box.label(text="Rigging", icon="ARMATURE_DATA")
            box.prop(props, "import_head_morphs")
            box.prop(props, "attach_head")
            box.prop(props, "create_root_bone")
            if props.create_root_bone:
                box.prop(props, "root_bone_name")
            box.prop(props, "game_ready_names")
            box.prop(props, "rig_props")
            box.prop(props, "smooth_shading")

            layout.operator("krx.assemble_character", icon_value=icons.icon_id("e"))

            row = layout.row(align=True)
            row.operator("krx.save_character", icon="FILE_TICK")
            row.operator("krx.load_character", icon="FILE_FOLDER")

            layout.separator()
            box = layout.box()
            box.label(text="From Game Scripts", icon="TEXT")
            box.operator("krx.assemble_from_d", icon_value=icons.icon_id("d"))

        elif tab == "DEVELOPER":
            col = layout.column(align=True)
            col.operator("krx.open_master_folder", icon="FILE_FOLDER")
            col.operator("krxpref.open_plugin_directory", text="Open Plugin Directory", icon="WINDOW")
            col.operator("krx.rebuild_indexes", icon="FILE_REFRESH")
            col.operator("krx.tex_preview", icon_value=icons.icon_id("tex"))
            col.operator("krx.clear_tex_cache", icon="TRASH")
            col.separator()
            col.operator("krx.copy_report", icon="TEXT")

            box = layout.box()
            box.label(text="Toys", icon="OUTLINER_OB_LIGHT")
            box.operator("krx.dance_party", icon="ARMATURE_DATA")
            note = box.column(align=True)
            note.scale_y = 0.8
            note.enabled = False
            note.label(text="Four villagers, four dances, one")
            note.label(text="mirrored room. Also a whole-")
            note.label(text="pipeline smoke test in one click.")

            box = layout.box()
            box.label(text="Standalone roadmap", icon="INFO")
            col = box.column(align=True)
            col.scale_y = 0.8
            col.label(text="Done: MRM MSH ZEN 3DS ASC MMB")
            col.label(text="Done: MDM/MDL, MAN/MDH, TEX->DDS")
            col.label(text="Done: .D scripts (human + monster)")
            col.label(text="Done: MDS scripts (names + events)")
            col.label(text="Next: VDF archives, GOTHIC.DAT")


_CLASSES = (
    KRXAssembleProps,
    KRX_OT_planned_format,
    KRX_OT_assemble_character,
    KRX_OT_assemble_from_d,
    KRX_OT_save_character,
    KRX_OT_load_character,
    KRX_OT_pick_asset,
    KRX_OT_clear_field,
    KRX_OT_open_master_folder,
    KRX_OT_rebuild_indexes,
    KRX_OT_clear_tex_cache,
    KRX_OT_tex_preview,
    KRX_OT_copy_report,
    KRX_OT_pick_texture,
    KRX_PT_gothic,
)


def register():
    from . import dance_party

    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    dance_party.register()

    bpy.types.WindowManager.krx_gothic_tab = EnumProperty(
        name="Gothic Tab",
        items=(
            ("IMPORT", "Import", "Import and export Gothic files"),
            ("ESSEMBLE", "Essemble", "Assemble a full character from files, instances or a .d script"),
            ("DEVELOPER", "Developer", "Folders, caches and roadmap"),
        ),
        default="IMPORT",
    )
    bpy.types.Scene.krx_assemble = PointerProperty(type=KRXAssembleProps)


def unregister():
    from . import dance_party

    dance_party.unregister()
    del bpy.types.Scene.krx_assemble
    del bpy.types.WindowManager.krx_gothic_tab
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
