# zen_vobs.py: the VOB tree of a ZenGin archive (.zen).
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# A .zen holds two independent things: the compiled world mesh ("MeshAndBsp") and a tree
# of VOBs - every prop, light, sound and effect placed in the level, each with a name, a
# visual and a world transform. KrxMshImp reads the first; this reads the second.
#
# The small .zen files in _work/Data/Worlds (FireTree_Lamp, ItLsTorchBurning, ...) are
# VOB trees with NO world mesh at all - they are prefabs, a torch plus its flame plus its
# light saved as one object - so loading them used to fail outright on the missing mesh.
#
# ASCII archives only. The retail worlds are BIN_SAFE, whose VOB tree needs the binary
# archive reader; their world mesh still loads exactly as before.
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import os
import re
import struct
from pathlib import Path
from typing import List, Optional

import bpy
from mathutils import Matrix, Vector

from . import game_data

# File space is Y-up, Blender is Z-up - the same swap the mesh readers use.
AXIS_SWAP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))

_RE_BLOCK_OPEN = re.compile(r"^\s*\[\s*(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s*\]\s*$")
_RE_BLOCK_CLOSE = re.compile(r"^\s*\[\s*\]\s*$")
_RE_KEY = re.compile(r"^\s*(\w+)\s*=\s*(\w*)\s*:\s*(.*)$")

# Visuals we can actually put geometry on screen for. Everything else (a .pfx particle
# system, a .tga decal, a sound) becomes an empty so its position is not lost.
MESH_VISUALS = (".3ds", ".mrm", ".mms", ".mmb", ".asc", ".mdl", ".mdm", ".msh")


def is_ascii_archive(path) -> bool:
    """True for a zCArchiverGeneric ASCII .zen - the only kind whose VOBs we can read."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(200)
    except OSError:
        return False
    return b"ZenGin Archive" in head and b"ASCII" in head


def _decode_matrix(raw: str) -> Matrix:
    """trafoOSToWSRot is nine floats as raw hex, row-major."""
    try:
        values = struct.unpack("<9f", bytes.fromhex(raw.strip()))
    except (ValueError, struct.error):
        return Matrix.Identity(4)
    rotation = Matrix((values[0:3], values[3:6], values[6:9])).to_4x4()
    return rotation


def _decode_vec3(text: str) -> Vector:
    parts = text.split()
    if len(parts) < 3:
        return Vector((0.0, 0.0, 0.0))
    try:
        return Vector((float(parts[0]), float(parts[1]), float(parts[2])))
    except ValueError:
        return Vector((0.0, 0.0, 0.0))


def _decode_color(text: str):
    parts = text.split()
    try:
        return tuple(min(1.0, max(0.0, int(v) / 255.0)) for v in parts[:3])
    except ValueError:
        return (1.0, 1.0, 1.0)


def parse_ascii_vobs(path) -> List[dict]:
    """Every VOB in an ASCII .zen, flattened, in file order.

    Blocks nest - a vob owns a [visual ...] and an [ai ...] child - so keys are written
    into whichever block is currently open, and only blocks that carry a world transform
    are reported as VOBs."""
    text = Path(path).read_text(encoding="Windows-1250", errors="replace")
    stack: List[dict] = []
    vobs: List[dict] = []

    for line in text.replace("\x00", "").splitlines():
        opened = _RE_BLOCK_OPEN.match(line)
        if opened:
            stack.append({"_name": opened.group(1), "_class": opened.group(2)})
            continue
        if _RE_BLOCK_CLOSE.match(line):
            if stack:
                block = stack.pop()
                if "trafoOSToWSPos" in block:
                    vobs.append(block)
            continue
        key = _RE_KEY.match(line)
        if key and stack:
            stack[-1][key.group(1)] = key.group(3).rstrip()

    for block in stack:                      # a truncated file leaves blocks open
        if "trafoOSToWSPos" in block:
            vobs.append(block)
    return vobs


def vob_matrix(vob: dict, scale: float) -> Matrix:
    """The VOB's world transform, converted into Blender space."""
    matrix = _decode_matrix(vob.get("trafoOSToWSRot", ""))
    matrix.translation = _decode_vec3(vob.get("trafoOSToWSPos", ""))
    blender = AXIS_SWAP @ matrix @ AXIS_SWAP
    blender.translation = blender.translation * scale
    return blender


def _label(vob: dict) -> str:
    """Best readable name: the vob's own, else its visual, else its preset ("TORCH")."""
    name = (vob.get("vobName") or "").strip()
    visual = (vob.get("visual") or "").strip()
    preset = (vob.get("presetName") or "").strip()
    return (name
            or (os.path.splitext(visual)[0] if visual else "")
            or preset
            or vob.get("_class", "VOB").split(":")[0])


def _add_light(vob: dict, matrix: Matrix, scale: float, collection, report: list):
    data = bpy.data.lights.new(_label(vob), type="POINT")
    data.color = _decode_color(vob.get("color", "255 255 255"))
    try:
        # ZenGin's range is a radius in centimetres; Blender wants a power, and a torch
        # reading as a torch matters more here than a physical conversion.
        radius = float(vob.get("range", "500")) * scale
    except ValueError:
        radius = 5.0
    data.energy = max(10.0, radius * radius * 8.0)
    data.shadow_soft_size = max(0.05, radius * 0.05)

    light = bpy.data.objects.new(data.name, data)
    light.matrix_world = matrix
    collection.objects.link(light)
    report.append(f"[light] {light.name} range {radius:.2f}")
    return light


def _add_marker(vob: dict, matrix: Matrix, collection, report: list, kind: str):
    empty = bpy.data.objects.new(f"{kind}_{_label(vob)}", None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = 0.25
    empty.matrix_world = matrix
    empty["krx_vob_class"] = vob.get("_class", "")
    empty["krx_vob_visual"] = vob.get("visual", "")
    collection.objects.link(empty)
    report.append(f"[{kind.lower()}] {empty.name} ({vob.get('visual') or 'no visual'})")
    return empty


def import_vobs(path, scale: float = 0.01, collection=None, report: list = None,
                import_meshes: bool = True, import_lights: bool = True,
                place_markers: bool = True) -> dict:
    """Import the VOB tree of an ASCII .zen: props, lights and effect markers.

    Every VOB keeps its world transform, so a prefab (a torch, its flame and its light)
    comes in assembled rather than as a heap at the origin."""
    report = report if report is not None else []
    collection = collection or bpy.context.collection

    vobs = parse_ascii_vobs(path)
    meshes = lights = markers = missing = 0

    for vob in vobs:
        matrix = vob_matrix(vob, scale)
        visual = (vob.get("visual") or "").strip()
        extension = os.path.splitext(visual)[1].lower()
        is_light = "LIGHT" in vob.get("_class", "").upper()

        if visual and extension in MESH_VISUALS and import_meshes:
            asset = game_data.find_asset(visual)
            if asset is None:
                missing += 1
                report.append(f"[missing] {visual} (VOB '{_label(vob)}')")
                if place_markers:
                    _add_marker(vob, matrix, collection, report, "MISSING")
                    markers += 1
                continue

            from .gothic_ui import import_asset_file

            before = set(bpy.data.objects)
            if import_asset_file(asset, report, scale=scale):
                fresh = [obj for obj in bpy.data.objects if obj not in before]
                for obj in fresh:
                    if obj.parent is None:
                        obj.matrix_world = matrix @ obj.matrix_world
                    for existing in list(obj.users_collection):
                        existing.objects.unlink(obj)
                    collection.objects.link(obj)
                meshes += 1
        elif is_light and import_lights:
            _add_light(vob, matrix, scale, collection, report)
            lights += 1
        elif place_markers:
            kind = "FX" if visual else "VOB"
            _add_marker(vob, matrix, collection, report, kind)
            markers += 1

    print(f"VOB tree of {Path(path).name}: {len(vobs)} vob(s) -> {meshes} mesh(es), "
          f"{lights} light(s), {markers} marker(s)"
          + (f", {missing} visual(s) not found" if missing else ""))
    return {"vobs": len(vobs), "meshes": meshes, "lights": lights,
            "markers": markers, "missing": missing}
