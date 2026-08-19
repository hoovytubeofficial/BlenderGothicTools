# dance_party.py: the developer-only disco.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# Four random villagers, four random dances, a mirrored room and a camera doing laps.
# It exists because it exercises nearly the whole add-on in one click - script parsing,
# instance resolution, .ASC and .MMB and .MRM import, rigging, and .MAN animation - so
# when something quietly breaks, the party looks wrong immediately.
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import glob
import math
import os
import random

import bpy
import bmesh
from bpy.props import BoolProperty, FloatProperty, IntProperty
from mathutils import Euler, Matrix, Vector

from . import game_data
from .armature import iter_action_fcurves

COLLECTION = "Dance Party"

# --- the strobe -----------------------------------------------------------------------
# Photosensitive seizures are provoked by flashes above roughly 3 Hz, and by deep
# light/dark contrast. This one runs at 2 Hz, never drops below 55% of full brightness
# and eases between the two, so it reads as a pulse rather than a strobe. Do not raise
# STROBE_HZ past 2.0 or drop STROBE_FLOOR toward 0.
STROBE_HZ = 2.0
STROBE_FLOOR = 0.55


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in (bpy.data.armatures, bpy.data.actions, bpy.data.meshes,
                  bpy.data.cameras, bpy.data.lights):
        for item in list(block):
            block.remove(item)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)


def _party_collection():
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION)
        bpy.context.scene.collection.children.link(collection)
    return collection


def human_scripts(limit: int = 0):
    """Top-level NPC scripts - the humans. Monsters and orcs live in subfolders."""
    folders = []
    for master in game_data.get_master_folders():
        folder = os.path.join(master, r"_work\Data\Scripts\Content\Story\NPC")
        if os.path.isdir(folder):
            folders.append(folder)
    scripts = []
    for folder in folders:
        scripts.extend(sorted(glob.glob(os.path.join(folder, "*.d"))))
    return scripts[:limit] if limit else scripts


def dance_animations():
    animations = []
    for master in game_data.get_master_folders():
        folder = os.path.join(master, r"_work\Data\Anims\_compiled")
        if os.path.isdir(folder):
            animations.extend(sorted(glob.glob(os.path.join(folder, "HUMANS-T_DANCE_*.MAN"))))
    return animations


def _pick_dancers(count: int, rng) -> list:
    """Scripts that actually describe a human with a body, sampled at random."""
    candidates = human_scripts()
    rng.shuffle(candidates)
    chosen = []
    for path in candidates:
        if len(chosen) >= count:
            break
        try:
            npc = game_data.parse_npc_file(path)
        except OSError:
            continue
        if npc["kind"] != "HUMAN":
            continue
        if not (npc["armor_instance"] or npc["gender"]):
            continue
        chosen.append(path)
    return chosen


def _place(objects, location, turn):
    """Move a freshly imported character into its spot on the circle.

    Only the objects with no parent are moved: the meshes ride along on the rig."""
    placement = Matrix.Translation(location) @ Euler((0.0, 0.0, turn), "XYZ").to_matrix().to_4x4()
    for obj in objects:
        if obj.parent is None:
            obj.matrix_world = placement @ obj.matrix_world


def _mirror_room(collection, unit, report):
    """A gigantic box around the party: inside-out, mirror-finish, seamless corners.

    Normals point inward so the camera sees the inside, and the bevel rounds the corners
    so the reflections have no visible seam. The material is a mirror to camera and
    reflection rays but transparent to shadow rays, and the object casts no shadow at
    all, so the world light still reaches the dancers instead of being sealed out."""
    size = 60.0 * unit
    mesh = bpy.data.meshes.new("Disco Room")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bmesh.ops.reverse_faces(bm, faces=bm.faces)      # inside-out
    bm.to_mesh(mesh)
    bm.free()

    cube = bpy.data.objects.new("Disco Room", mesh)
    cube.location = (0.0, 0.0, size / 2.0)           # sitting on the ground
    collection.objects.link(cube)

    bevel = cube.modifiers.new(name="Seamless Corners", type="BEVEL")
    bevel.width = 6.0 * unit
    bevel.segments = 16
    bevel.limit_method = "ANGLE"

    material = bpy.data.materials.new("Disco Mirror")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (-200, 120)
    principled.inputs["Base Color"].default_value = (0.92, 0.94, 1.0, 1.0)
    principled.inputs["Metallic"].default_value = 1.0
    principled.inputs["Roughness"].default_value = 0.03

    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, -140)
    light_path = tree.nodes.new("ShaderNodeLightPath")
    light_path.location = (-200, 380)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (150, 0)

    tree.links.new(principled.outputs["BSDF"], mix.inputs[1])
    tree.links.new(transparent.outputs["BSDF"], mix.inputs[2])
    tree.links.new(light_path.outputs["Is Shadow Ray"], mix.inputs["Fac"])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])

    mesh.materials.append(material)
    cube.visible_shadow = False                      # let the world light straight through

    report.append(f"[room] {size:.0f} unit mirrored box, metallic 1.0, roughness 0.03, "
                  f"bevelled corners, transparent to shadow rays")
    return cube


def _white_world(strength: float = 1.6):
    """An almost entirely white surround, so the mirrors have something to reflect."""
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("Dance Party")
        bpy.context.scene.world = world
    world.use_nodes = True
    background = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if background is None:
        background = world.node_tree.nodes.new("ShaderNodeBackground")
        output = next((n for n in world.node_tree.nodes if n.type == "OUTPUT_WORLD"), None)
        if output is None:
            output = world.node_tree.nodes.new("ShaderNodeOutputWorld")
        world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Color"].default_value = (0.96, 0.96, 0.97, 1.0)
    background.inputs["Strength"].default_value = strength
    return world


def _strobe(collection, unit, frame_start, frame_end, fps, report):
    """A white pulse overhead, kept below the photosensitivity threshold."""
    data = bpy.data.lights.new("Strobe", type="AREA")
    data.color = (1.0, 1.0, 1.0)
    data.size = 6.0 * unit
    peak = 4000.0 * (unit ** 2)

    light = bpy.data.objects.new("Strobe", data)
    light.location = (0.0, 0.0, 6.0 * unit)
    collection.objects.link(light)

    period = max(2, int(round(fps / STROBE_HZ)))     # frames per full pulse
    frame = frame_start
    while frame <= frame_end + period:
        data.energy = peak
        data.keyframe_insert("energy", frame=frame)
        data.energy = peak * STROBE_FLOOR
        data.keyframe_insert("energy", frame=frame + period // 2)
        frame += period

    # eased, not switched: a hard on/off is the part that causes trouble
    if data.animation_data:
        for curve in iter_action_fcurves(data.animation_data):
            for point in curve.keyframe_points:
                point.interpolation = "SINE"

    report.append(f"[strobe] {STROBE_HZ:g} Hz, {STROBE_FLOOR:.0%}-100% eased "
                  f"(under the ~3 Hz and high-contrast thresholds for photosensitivity)")
    return light


def _spinning_camera(collection, unit, frame_start, frame_end, radius, report):
    """A camera on a slow orbit, always looking at the middle of the floor."""
    pivot = bpy.data.objects.new("Party Pivot", None)
    pivot.empty_display_size = 0.5 * unit
    pivot.location = (0.0, 0.0, 1.05 * unit)          # about chest height
    collection.objects.link(pivot)

    data = bpy.data.cameras.new("Party Camera")
    data.lens = 35.0
    camera = bpy.data.objects.new("Party Camera", data)
    # far enough back that the whole ring fits with arms in the air, and never closer
    # than 5 m however small the ring is
    distance = max(radius * 3.4, 5.0 * unit)
    camera.location = (0.0, -distance, 2.6 * unit)
    collection.objects.link(camera)
    camera.parent = pivot

    track = camera.constraints.new("TRACK_TO")
    track.target = pivot
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"

    for frame, angle in ((frame_start, 0.0), (frame_end, 2.0 * math.pi)):
        pivot.rotation_euler = (0.0, 0.0, angle)
        pivot.keyframe_insert("rotation_euler", frame=frame)
    if pivot.animation_data:
        for curve in iter_action_fcurves(pivot.animation_data):
            curve.extrapolation = "LINEAR"            # keep turning past the last frame
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"

    bpy.context.scene.camera = camera
    report.append(f"[camera] one full turn over frames {frame_start}-{frame_end}")
    return camera


class KRX_OT_dance_party(bpy.types.Operator):
    """Throw a dance party. Developer toy, and a whole-pipeline smoke test.

Four random villagers are pulled out of the game scripts, given a random dance each, and
stood in a ring facing one another inside a gigantic mirrored box, lit by an almost-white
world and a slow white pulse, with the camera doing a lap around them.

It touches script parsing, instance resolution, .ASC / .MMB / .MRM import, rigging and
.MAN animation in one click, so when something quietly breaks it looks wrong immediately"""

    bl_idname = "krx.dance_party"
    bl_label = "Dance Party"
    bl_options = {"REGISTER", "UNDO"}

    dancers: IntProperty(
        name="Dancers",
        description="How many villagers to drag onto the floor",
        default=4, min=1, max=12,
    )
    radius: FloatProperty(
        name="Ring Radius",
        description="How far each dancer stands from the middle, in metres before the "
                    "recipe's unit scale",
        default=1.7, min=0.5, soft_max=10.0,
    )
    seed: IntProperty(
        name="Seed",
        description="0 draws a fresh cast every time. Any other number always gives the "
                    "same four dancers and the same four dances, so you can get a "
                    "particular party back",
        default=0,
        min=0,
    )
    clear_scene: BoolProperty(
        name="Clear the Scene First",
        description="Delete everything in the file before setting up. Turn this off to "
                    "throw the party alongside what you already have",
        default=True,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def execute(self, context):
        from .gothic_ui import unit_factor
        from .KrxManImp import import_man

        props = context.scene.krx_assemble
        unit = unit_factor(props.scale)
        # Blender keeps an operator's properties between runs, so a fixed seed would
        # hand back the same four villagers every single time. 0 means 'surprise me'.
        rng = random.Random(self.seed if self.seed else None)
        report = []

        dances = dance_animations()
        if not dances:
            self.report({"ERROR"}, "No HUMANS-T_DANCE_*.MAN found - set a master folder first")
            return {"CANCELLED"}
        scripts = _pick_dancers(self.dancers, rng)
        if not scripts:
            self.report({"ERROR"}, "No human NPC scripts found - set a master folder first")
            return {"CANCELLED"}

        if self.clear_scene:
            _clear_scene()
        collection = _party_collection()

        radius = self.radius * unit
        frame_end = 1
        placed = 0

        for index, script in enumerate(scripts):
            before = set(bpy.data.objects)
            try:
                bpy.ops.krx.assemble_from_d(filepath=script)
            except RuntimeError as err:
                report.append(f"[skip] {os.path.basename(script)}: {err}")
                continue
            fresh = [obj for obj in bpy.data.objects if obj not in before]
            if not fresh:
                report.append(f"[skip] {os.path.basename(script)}: nothing imported")
                continue

            # Evenly around the ring, each turned to face the middle. A character faces
            # -Y when it is imported, so standing at angle a it has to turn by a - 90.
            angle = 2.0 * math.pi * index / max(1, len(scripts))
            location = Vector((radius * math.cos(angle), radius * math.sin(angle), 0.0))
            _place(fresh, location, angle - math.pi / 2.0)

            armature = next((obj for obj in fresh if obj.type == "ARMATURE"), None)
            dance = rng.choice(dances)
            if armature is not None:
                result = import_man(dance, armature, scale=props.scale, frame_start=1)
                frame_end = max(frame_end, result["frame_end"])
                report.append(f"[dancer] {os.path.basename(script)} -> "
                              f"{os.path.basename(dance)} ({result['frame_end']} frames)")
            else:
                report.append(f"[dancer] {os.path.basename(script)}: no rig, standing still")

            for obj in fresh:
                for existing in list(obj.users_collection):
                    existing.objects.unlink(obj)
                collection.objects.link(obj)
            placed += 1

        if not placed:
            self.report({"ERROR"}, "Nobody made it onto the dance floor")
            return {"CANCELLED"}

        scene = context.scene
        scene.frame_start = 1
        scene.frame_end = frame_end

        _mirror_room(collection, unit, report)
        _white_world()
        _strobe(collection, unit, 1, frame_end, max(1, scene.render.fps), report)
        _spinning_camera(collection, unit, 1, frame_end, radius, report)

        for line in report:
            print(f"Dance Party: {line}")
        self.report({"INFO"}, f"{placed} dancer(s), {frame_end} frames - press play")
        return {"FINISHED"}


_CLASSES = (KRX_OT_dance_party,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
