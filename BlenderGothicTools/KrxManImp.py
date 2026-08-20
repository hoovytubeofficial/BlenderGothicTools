# KrxManImp.py: compiled animation (.MAN) and model hierarchy (.MDH) reader.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# .MAN is one motion for one skeleton, sampled per frame. Layout (verified against
# HUMANS-S_RUN.MAN, 50 frames x 34 nodes):
#
#   0xA000 marker | 0xA010 source | 0xA020 header | 0xA030 events | 0xA090 data
#
#   header: uint16 version | name (LF) | uint32 layer | uint32 frames | uint32 nodes
#           float fps | float fps_source | float sample_position_range_min
#           float sample_position_scalar | float[6] bbox | next-animation name (LF)
#   data:   uint32 checksum | uint32[nodes] node indices
#           then frames * nodes samples of 12 bytes:
#               uint16 rotation[3] -> (v - 32767) * 2.1/65536, w = sqrt(1 - x^2-y^2-z^2)
#               uint16 position[3] -> v * position_scalar + position_range_min
#           Samples are PARENT-RELATIVE and ordered frame-major.
#
# The node indices address the skeleton's node list, which lives in the .MDH beside it
# (HUMANS-S_RUN.MAN -> HUMANS.MDH). The MDH gives each node a name, so animations map
# onto Blender bones by name rather than by a fragile index.
#
# The .MDH hierarchy chunk continues after the node array with two bounding boxes, the
# skeleton's root translation and a checksum - see read_mdh().
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import math
import os
import struct
from pathlib import Path
from typing import Dict, List, Optional

import bpy
from mathutils import Matrix, Quaternion, Vector

from .armature import cached_bone_rotation_matrix, cached_bone_rotation_matrix_inverted

# Unit presets. Gothic files are in centimetres.
SCALE_BLENDER_METRES = 0.01
SOURCE_UNIT_IN_METRES = 0.01905          # 1 Source/SFM unit = 0.75 inch
SCALE_SOURCE_UNITS = 0.01 / SOURCE_UNIT_IN_METRES   # cm -> Source units (~0.5249)

# File space is Y-up; Blender is Z-up. Swapping two axes is its own inverse.
AXIS_SWAP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))

# The compiled skeleton faces +Y with its LEFT side at -X, while a model imported from
# an .ASC faces -Y with its left at +X - the two are half a turn apart. Without this the
# animation drives "Bip01 L Thigh" to the right hip and the leg bones visibly cross,
# even though every joint is internally consistent.
# Measured on HUMANS-S_WALKL: raw puts the left thigh at -0.079 and walks toward +Y;
# after the turn the left thigh is at +0.079 and the walk runs toward -Y, which is
# where the character's face and left hand say it should go.
GOTHIC_YAW = Matrix.Rotation(math.pi, 4, "Z")

CHUNK_MARKER = 0xA000
CHUNK_SOURCE = 0xA010
CHUNK_HEADER = 0xA020
CHUNK_EVENTS = 0xA030
CHUNK_DATA = 0xA090

# Rotation quantisation. A sample stores the quaternion's vector part as three uint16
# around a midpoint, with a range of +-1.05 rather than the +-1.0 the maths needs.
# Measured over 400 retail .MAN files (244,670 samples): the packed values span exactly
# 1560..63974 - i.e. 32767 +- 31207 - and 31207 * 2.1/65536 = 0.99998. 32767 is by far
# the most common code (185,515 hits), which is the "no rotation on this axis" value.
# Decoding with the obvious (v - 0x8000)/0x8000 caps |v| at 0.9524, so w can never fall
# below 0.305 and no rotation past 143.5 degrees is representable; everything beyond
# folds back, which is where the ~71 degree per-bone artefact in every animation came
# from. Cross-check: the .MDH rest angle for BIP01 L THIGH is 176.9 degrees and the
# first frame of T_DANCE_03 decodes to 177.1 with this scale, 144.4 with the old one.
SAMPLE_ROTATION_MID = 32767
SAMPLE_ROTATION_SCALE = 2.1 / 65536.0

MDH_CHUNK_HIERARCHY = 0xD100


class ManError(Exception):
    """Raised when a .MAN or .MDH file cannot be read"""


def _read_line(data: bytes, at: int):
    start = at
    while at < len(data) and data[at] != 0x0A:
        at += 1
    return data[start:at].decode("Windows-1250", "replace"), at + 1


# -------------------------------------------------------------------------------------------------------
# Model hierarchy (.MDH)
# -------------------------------------------------------------------------------------------------------


def read_mdh(path) -> List[dict]:
    """[{name, parent, matrix}] in node order - the order .MAN node indices address.

    The hierarchy chunk does not end with the node array: it carries a bounding box, a
    collision bounding box, the skeleton's root translation and a checksum (exactly 64
    trailing bytes, verified against HUMANS.MDH). The root translation is what lifts the
    skeleton off the floor - BIP01's own local matrix has no translation at all - so it
    is folded into every parentless node, the way the engine does it."""
    data = Path(path).read_bytes()
    pos = 0
    while pos + 6 <= len(data):
        chunk_id, chunk_size = struct.unpack_from("<HI", data, pos)
        body = pos + 6
        if chunk_id == MDH_CHUNK_HIERARCHY:
            at = body
            _version = struct.unpack_from("<I", data, at)[0]
            at += 4
            count = struct.unpack_from("<H", data, at)[0]
            at += 2

            nodes = []
            for _ in range(count):
                name, at = _read_line(data, at)
                parent = struct.unpack_from("<h", data, at)[0]
                at += 2
                values = struct.unpack_from("<16f", data, at)
                at += 64
                nodes.append({
                    "name": name.strip(),
                    "parent": parent,
                    "matrix": Matrix((values[0:4], values[4:8], values[8:12], values[12:16])),
                })

            end = body + chunk_size
            root_translation = Vector((0.0, 0.0, 0.0))
            if end - at >= 64:          # bbox(24) + collision bbox(24) + root(12) + sum(4)
                root_translation = Vector(struct.unpack_from("<3f", data, at + 48))
                for node in nodes:
                    if node["parent"] < 0:
                        node["matrix"].translation = (
                            node["matrix"].translation + root_translation
                        )
            else:
                print(f"{Path(path).name}: hierarchy chunk carries no root translation",
                      level="WARN")
            for node in nodes:
                node["root_translation"] = root_translation
            return nodes
        pos = body + chunk_size
        if chunk_size == 0 and chunk_id != 0xD000:
            break
    raise ManError(f"No hierarchy chunk in {Path(path).name}")


def find_hierarchy_for(man_path) -> Optional[str]:
    """HUMANS-S_RUN.MAN -> HUMANS.MDH in the same folder."""
    man_path = Path(man_path)
    stem = man_path.stem
    candidates = []
    if "-" in stem:
        candidates.append(stem.split("-", 1)[0])
    candidates.append(stem)
    for candidate in candidates:
        for name in (f"{candidate}.MDH", f"{candidate}.mdh"):
            hierarchy = man_path.parent / name
            if hierarchy.is_file():
                return str(hierarchy)
    return None


# -------------------------------------------------------------------------------------------------------
# Animation (.MAN)
# -------------------------------------------------------------------------------------------------------


def read_man(path) -> dict:
    """Parse a compiled animation into {header fields, node_indices, samples}."""
    data = Path(path).read_bytes()
    header = None
    node_indices: List[int] = []
    samples: List[tuple] = []

    pos = 0
    while pos + 6 <= len(data):
        chunk_id, chunk_size = struct.unpack_from("<HI", data, pos)
        body = pos + 6

        if chunk_id == CHUNK_HEADER:
            at = body
            version = struct.unpack_from("<H", data, at)[0]
            at += 2
            name, at = _read_line(data, at)
            layer, frame_count, node_count = struct.unpack_from("<3I", data, at)
            at += 12
            fps, fps_source, position_min, position_scalar = struct.unpack_from("<4f", data, at)
            at += 16
            bbox = struct.unpack_from("<6f", data, at)
            at += 24
            next_animation, at = _read_line(data, at)
            header = {
                "version": version,
                "name": name.strip(),
                "layer": layer,
                "frame_count": frame_count,
                "node_count": node_count,
                "fps": fps,
                "fps_source": fps_source,
                "position_min": position_min,
                "position_scalar": position_scalar,
                "bbox": bbox,
                "next": next_animation.strip(),
            }

        elif chunk_id == CHUNK_DATA:
            if header is None:
                raise ManError(f"{Path(path).name}: data chunk before header")
            at = body
            _checksum = struct.unpack_from("<I", data, at)[0]
            at += 4
            node_count = header["node_count"]
            node_indices = list(struct.unpack_from(f"<{node_count}I", data, at))
            at += node_count * 4

            total = header["frame_count"] * node_count
            needed = total * 12
            if at + needed > len(data):
                raise ManError(
                    f"{Path(path).name}: expected {needed} sample bytes, only {len(data) - at} left"
                )
            flat = struct.unpack_from(f"<{total * 6}H", data, at)
            samples = [tuple(flat[i * 6:i * 6 + 6]) for i in range(total)]
            break

        pos = body + chunk_size
        if chunk_size == 0 and chunk_id != CHUNK_MARKER:
            break

    if header is None:
        raise ManError(f"{Path(path).name} is not a compiled animation (.MAN)")
    if not samples:
        raise ManError(f"{Path(path).name}: no sample data")

    return {"header": header, "node_indices": node_indices, "samples": samples}


# Troubleshooting: how the file's rotation vector maps onto Blender's axes.
# "AUTO" is the derived mapping (conjugate for Max's row-vector convention, then the
# Y<->Z swap, whose sign flips cancel). The others exist so a wrong-looking animation
# can be diagnosed by eye in seconds instead of by guesswork.
ROTATION_MAPPINGS = {
    "AUTO": lambda w, x, y, z: (w, x, z, y),
    "NEG_ALL": lambda w, x, y, z: (w, -x, -z, -y),
    "NO_SWAP": lambda w, x, y, z: (w, x, y, z),
    "NEG_X": lambda w, x, y, z: (w, -x, z, y),
    "NEG_YZ": lambda w, x, y, z: (w, x, -z, -y),
    "CONJUGATE": lambda w, x, y, z: (w, -x, -y, -z),
}

ACTIVE_MAPPING = "AUTO"


def decode_rotation(sample):
    """Unpack a sample's rotation into (w, x, y, z) already in Blender's axis order.

    Only the vector part is stored; w is reconstructed as sqrt(1 - |v|^2) and is always
    POSITIVE. The compressor only ever emits the positive hemisphere, which is free to
    do because q and -q are the same rotation - so there is no sign to guess here. (An
    earlier version reconstructed |w| and picked the sign of w by continuity; that was
    compensating for the wrong quantisation scale, and flipping w alone is not a
    quaternion identity anyway. Hemisphere continuity for the F-curves is handled where
    it belongs, on the finished keys, by negating all four components.)"""
    x, y, z = ((value - SAMPLE_ROTATION_MID) * SAMPLE_ROTATION_SCALE for value in sample[0:3])
    length = x * x + y * y + z * z
    if length > 1.0:  # quantisation nudged it outside the unit sphere: it is a half turn
        norm = math.sqrt(length)
        x, y, z = x / norm, y / norm, z / norm
        w = 0.0
    else:
        w = math.sqrt(1.0 - length)

    # Two corrections that happen to cancel:
    #  - the file stores the transform in 3ds Max's row-vector convention, so its
    #    rotation is the conjugate of the column-vector one Blender wants;
    #  - swapping Y and Z flips handedness, which negates the vector part again.
    # Conjugate then swizzle = swizzle the vector part and keep every sign.
    return ROTATION_MAPPINGS.get(ACTIVE_MAPPING, ROTATION_MAPPINGS["AUTO"])(w, x, y, z)


def sample_position(sample, position_scalar: float, position_min: float, scale: float) -> Vector:
    return Vector((
        sample[3] * position_scalar + position_min,
        sample[5] * position_scalar + position_min,   # file Z -> Blender Y
        sample[4] * position_scalar + position_min,   # file Y -> Blender Z
    )) * scale


def sample_to_matrix(sample, position_scalar: float, position_min: float, scale: float) -> Matrix:
    """One 12-byte sample -> a Blender-space, parent-relative transform.

    (Verified: composing the chain this way puts the hips at 97.7 cm, the neck at 152 cm
    and the head at 158.8 cm for HUMANS-S_RUN, which is a real skeleton.)"""
    rotation = Quaternion(decode_rotation(sample)).to_matrix().to_4x4()
    rotation.translation = sample_position(sample, position_scalar, position_min, scale)
    return rotation


def hierarchy_rest_matrices(nodes: List[dict], scale: float) -> Dict[int, Matrix]:
    """Model-space rest matrices for every MDH node, in Blender coordinates.

    MDH stores column-vector matrices (translation in the last column, offsets along
    the node's local X like the animation samples), so they compose parent-first."""
    world: Dict[int, Matrix] = {}
    for index, node in enumerate(nodes):
        local = node["matrix"].copy()
        parent = node["parent"]
        world[index] = (world[parent] @ local) if parent in world else local

    converted = {}
    for index, matrix in world.items():
        blender = AXIS_SWAP @ matrix @ AXIS_SWAP
        blender.translation = blender.translation * scale
        converted[index] = GOTHIC_YAW @ blender
    return converted


def build_armature_from_hierarchy(nodes: List[dict], scale: float, name: str = "Gothic Skeleton"):
    """Create an armature from a .MDH so an animation can be imported on its own."""
    armature_data = bpy.data.armatures.new(name)
    armature_obj = bpy.data.objects.new(name, armature_data)
    armature_obj.show_in_front = True
    bpy.context.collection.objects.link(armature_obj)

    rest = hierarchy_rest_matrices(nodes, scale)

    children: Dict[int, List[int]] = {}
    for index, node in enumerate(nodes):
        children.setdefault(node["parent"], []).append(index)

    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit_bones = armature_data.edit_bones
        created = {}
        for index, node in enumerate(nodes):
            bone = edit_bones.new(node["name"])

            # length: reach to the first child, else a fraction of the parent's length
            own_children = children.get(index, [])
            if own_children:
                length = max(
                    (rest[child].translation - rest[index].translation).length
                    for child in own_children
                )
            else:
                length = 0.0
            if length < 1e-4:
                length = 0.05 * (scale / 0.01)

            bone.head = (0.0, 0.0, 0.0)
            bone.tail = (0.0, length, 0.0)
            # match the ASC importer's bone convention so the same maths applies
            bone.matrix = rest[index] @ cached_bone_rotation_matrix_inverted
            bone.length = length
            created[index] = bone

        for index, node in enumerate(nodes):
            parent = node["parent"]
            if parent in created:
                created[index].parent = created[parent]
                created[index].use_connect = False
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.objects.active = previous_active

    print(f"built armature '{armature_obj.name}' with {len(nodes)} bones from the skeleton")
    return armature_obj


def read_mdh_checksum(path) -> Optional[int]:
    """The checksum at the end of a .MDH hierarchy chunk.

    A compiled body mesh (.mdm/.mdl) stores this same number in its 0xD030 chunk, so it
    is how a mesh names its skeleton - no file-naming convention needed."""
    data = Path(path).read_bytes()
    pos = 0
    while pos + 6 <= len(data):
        chunk_id, chunk_size = struct.unpack_from("<HI", data, pos)
        body = pos + 6
        if chunk_id == MDH_CHUNK_HIERARCHY:
            at = body + 4
            count = struct.unpack_from("<H", data, at)[0]
            at += 2
            for _ in range(count):
                _name, at = _read_line(data, at)
                at += 2 + 64
            end = body + chunk_size
            if end - at >= 64:
                return struct.unpack_from("<I", data, at + 60)[0]
            return None
        pos = body + chunk_size
        if chunk_size == 0 and chunk_id != 0xD000:
            break
    return None


_SKELETON_CACHE = {"key": None, "by_checksum": None, "by_stem": None}


def _skeleton_index():
    """{checksum: path} and {STEM: path} for every .MDH under the master folders.

    Several skeletons share a checksum because the animation overlays (HUMANS_RELAXED,
    HUMANS_TORCH, ...) are the same skeleton with different motions; the shortest name
    wins, which is the base one."""
    from .preferences import get_master_folders

    masters = tuple(get_master_folders())
    if _SKELETON_CACHE["by_checksum"] is not None and _SKELETON_CACHE["key"] == masters:
        return _SKELETON_CACHE["by_checksum"], _SKELETON_CACHE["by_stem"]

    by_checksum: Dict[int, str] = {}
    by_stem: Dict[str, str] = {}
    for master in masters:
        root = Path(master) / "_work" / "Data"
        if not root.is_dir():
            root = Path(master)
        for candidate in root.rglob("*.[mM][dD][hH]"):
            by_stem.setdefault(candidate.stem.upper(), str(candidate))
            try:
                value = read_mdh_checksum(candidate)
            except (OSError, struct.error):
                continue
            if value is None:
                continue
            current = by_checksum.get(value)
            if current is None or len(candidate.stem) < len(Path(current).stem):
                by_checksum[value] = str(candidate)

    _SKELETON_CACHE["key"] = masters
    _SKELETON_CACHE["by_checksum"] = by_checksum
    _SKELETON_CACHE["by_stem"] = by_stem
    print(f"skeleton index built - {len(by_stem)} .MDH files, "
          f"{len(by_checksum)} distinct checksums")
    return by_checksum, by_stem


def hierarchy_by_checksum(checksum: int) -> Optional[str]:
    """The .MDH whose hierarchy checksum matches a compiled mesh's 0xD030 chunk."""
    if not checksum:
        return None
    return _skeleton_index()[0].get(checksum)


def hierarchy_by_name(name: str) -> Optional[str]:
    """The .MDH called `name` - a bare stem, 'Golem.mds' or a full path all work.

    Meshes made only of parts bolted to bones (chests, golems, the meatbug) carry no
    skeleton checksum at all, so they need a name instead: a world mob's .MDH sits
    beside it under the same stem, and a monster's is named by its script's
    Mdl_SetVisual ("Golem.mds" -> GOLEM.MDH)."""
    if not name:
        return None
    stem = Path(str(name).strip().strip('"')).stem.upper()
    return _skeleton_index()[1].get(stem)


def find_hierarchy_for_mesh(result: dict, mesh_path: str = None,
                            skeleton: str = None) -> Optional[str]:
    """The .MDH belonging to a compiled mesh, by checksum, then by name.

    A soft-skinned body stores its skeleton's checksum and needs nothing else. A mesh
    that is only rigid parts bolted to bones has no checksum, so it falls back to the
    .MDH sitting beside it under the same stem (world mobs: chests, anvils) and then to
    the skeleton its script names (Mdl_SetVisual "Golem.mds")."""
    return (hierarchy_by_checksum(result.get("checksum", 0))
            or (hierarchy_by_name(Path(mesh_path).stem) if mesh_path else None)
            or hierarchy_by_name(skeleton))


def skin_softskin_meshes(result: dict, scale: float = 0.01, armature_obj=None,
                         hierarchy_path: str = None, mesh_path: str = None,
                         skeleton: str = None) -> dict:
    """Give the meshes of a .MDM their vertex groups and an Armature modifier.

    `result` is what TMRMFileLoader.ReadMDMFile returned. The skeleton is located by
    find_hierarchy_for_mesh unless `hierarchy_path` says otherwise; with no skeleton the
    meshes are still imported, just unrigged (their bind pose is already correct)."""
    objects = result.get("objects") or []
    weights = result.get("weights") or {}

    if hierarchy_path is None:
        hierarchy_path = find_hierarchy_for_mesh(result, mesh_path, skeleton)
    if not hierarchy_path:
        if result.get("attachments"):
            print("no skeleton found for this model mesh - its loose parts are stored "
                  "relative to their bones, so without the .MDH they stay at the origin",
                  level="WARN")
        else:
            print("no skeleton found for this model mesh - imported unrigged "
                  "(the bind pose is still correct)", level="WARN")
        return {"armature": armature_obj, "hierarchy": None, "skinned": 0, "placed": 0,
                "positioned": set()}

    nodes = read_mdh(hierarchy_path)
    print(f"skeleton {Path(hierarchy_path).name}: {len(nodes)} nodes")

    if armature_obj is None:
        armature_obj = build_armature_from_hierarchy(
            nodes, scale, name=f"{Path(hierarchy_path).stem} Skeleton"
        )

    bones_by_name = {bone.name.upper(): bone.name for bone in armature_obj.data.bones}
    node_by_name = {node["name"].upper(): index for index, node in enumerate(nodes)}
    attachment_bone = {obj: bone for obj, bone in (result.get("attachments") or [])}

    # Where the loose parts go. An attached mesh's vertices are stored RELATIVE TO ITS
    # BONE - a skeleton warrior's thigh is a 42 cm blob lying along local +X at the
    # origin, because Gothic bones run along local X - so the .MDH rest pose of the bone
    # named in the .mdm's 0xD020 chunk IS the placement. Without it every loose part
    # piles up flat at (0, 0, 0).
    rest_world = hierarchy_rest_matrices(nodes, scale)
    positioned = set()
    placed = 0
    rebuilt = 0
    worst_move = 0.0

    skinned = 0
    missing = set()
    for obj in objects:
        groups = {}

        bone = attachment_bone.get(obj)
        if bone is not None:
            # A rigid part - a shoulder plate, a claw, a troll's slab of shoulder - rides
            # one bone at full weight, and has to be moved into that bone's frame first.
            node_index = node_by_name.get(bone.upper())
            if node_index is not None and node_index in rest_world:
                obj.data.transform(rest_world[node_index])
                positioned.add(obj)
                placed += 1
            else:
                print(f"'{obj.name}' hangs on '{bone}', which is not in the skeleton - "
                      f"left at the origin", level="WARN")

            name = bones_by_name.get(bone.upper())
            if name is None:
                missing.add(bone)
            else:
                group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
                group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")
                groups[name] = group

        # Where the vertices really belong. A soft-skin vertex is defined as the weighted
        # sum of its position in each of its bones' spaces - that IS the bind pose, and it
        # is what the engine skins from. The zCProgMeshProto array the mesh was built from
        # is only an approximation of it: 94 of the 119 retail bodies disagree with their
        # own weights, HUM_BODY_BABE0 by 15 cm and the dragons by 85, which is what put a
        # character next to its armature instead of inside it.
        moved = {}
        for vertex_indices, entries in weights.get(obj, []):
            position = Vector((0.0, 0.0, 0.0))
            total = 0.0
            for weight, local, node_index in entries:
                matrix = rest_world.get(node_index)
                if matrix is None:
                    continue
                # file (x, y, z) -> Blender (x, z, y), the same swap the mesh reader uses
                point = Vector((local[0], local[2], local[1])) * scale
                position += (matrix @ point) * weight
                total += weight
            if total > 1.0e-6:
                if abs(total - 1.0) > 1.0e-3:
                    position /= total          # a few meshes do not quite sum to one
                for vertex_index in vertex_indices:
                    moved[vertex_index] = position

        for vertex_index, position in moved.items():
            vertex = obj.data.vertices[vertex_index]
            worst_move = max(worst_move, (vertex.co - position).length)
            vertex.co = position
        if moved:
            obj.data.update()
            positioned.add(obj)
            rebuilt += 1

        for vertex_indices, entries in weights.get(obj, []):
            for weight, _local, node_index in entries:
                if not (0 <= node_index < len(nodes)):
                    continue
                bone_name = bones_by_name.get(nodes[node_index]["name"].upper())
                if bone_name is None:
                    missing.add(nodes[node_index]["name"])
                    continue
                group = groups.get(bone_name)
                if group is None:
                    group = obj.vertex_groups.get(bone_name) or obj.vertex_groups.new(name=bone_name)
                    groups[bone_name] = group
                for vertex_index in vertex_indices:
                    group.add([vertex_index], weight, "REPLACE")

        modifier = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
        if modifier is None:
            modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = armature_obj
        modifier.use_vertex_groups = True

        obj.parent = armature_obj
        obj.matrix_parent_inverse = armature_obj.matrix_world.inverted()
        skinned += 1
        print(f"skinned '{obj.name}' to '{armature_obj.name}': {len(groups)} vertex group(s)")

    if rebuilt:
        print(f"bind pose of {rebuilt} mesh(es) rebuilt from the skinning table "
              f"(largest correction {worst_move * 100:.1f} cm)")
    if placed:
        print(f"placed {placed} attached part(s) at their bone's rest transform")
    if missing:
        print(f"skeleton nodes with no matching bone ({len(missing)}): "
              f"{', '.join(sorted(missing)[:8])}", level="WARN")

    return {"armature": armature_obj, "hierarchy": hierarchy_path, "skinned": skinned,
            "placed": placed, "positioned": positioned}


def _yaw_only(rotation: Quaternion) -> Quaternion:
    """The part of a rotation that turns about the world Z axis, and nothing else.

    Swing-twist: the twist about an axis is the quaternion's component along that axis,
    renormalised. Dropping the swing is what keeps a transposed creature on its feet."""
    twist = Quaternion((rotation.w, 0.0, 0.0, rotation.z))
    return twist.normalized() if twist.magnitude > 1.0e-6 else Quaternion()


def transpose_man(man: dict, nodes: List[dict], scale: float, root_upright: bool = True):
    """Per frame, how far each joint has turned from rest IN WORLD SPACE, plus the root's
    travel. Rig-independent: this is what lets a human dance play on a dragon.

    The delta is deliberately measured in WORLD space, not in each bone's own frame. Two
    skeletons that share a bone name rarely orient it the same way - a human's upper arm
    hangs down its side, a dragon's is the root of a wing pointing out and back - so a
    delta expressed in the bone's own frame turns the target about completely different
    axes. Measuring in world space means "raised 100 degrees about world X" transfers as
    "raised 100 degrees about world X", and, crucially, world UP means the same thing to
    both creatures, so the target stays on its feet instead of folding onto the floor.
    (For a target built from the SAME skeleton the two are identical, which is what the
    self-test against a plain import checks.)

    `root_upright` keeps the root's turn but throws away its lean: a lean that reads as a
    shift of weight on a 1.8 m human tips a 12 m dragon onto its face.

    Returns (frames, root_index, root_rest_height) where frames is a list of
    {node index: (world-space delta Quaternion, Vector root travel or None)}."""
    header = man["header"]

    rest_world = hierarchy_rest_matrices(nodes, scale)
    pose_world = _pose_matrices(man, nodes, scale, rest_world)

    rest_rotation = {index: matrix.to_quaternion() for index, matrix in rest_world.items()}
    root_index = next((index for index, node in enumerate(nodes) if node["parent"] < 0), 0)
    root_rest = rest_world.get(root_index, Matrix.Identity(4)).translation.copy()

    frames = []
    for world in pose_world:
        per_node = {}
        for node_index, matrix in world.items():
            rest = rest_rotation.get(node_index)
            if rest is None:
                continue
            delta = matrix.to_quaternion() @ rest.inverted()
            travel = None
            if node_index == root_index:
                if root_upright:
                    delta = _yaw_only(delta)
                travel = matrix.translation - root_rest
            per_node[node_index] = (delta, travel)
        frames.append(per_node)

    return frames, root_index, root_rest.z


def _pose_matrices(man: dict, nodes: List[dict], scale: float, rest: Dict[int, Matrix] = None):
    """Compose the parent-relative samples into per-frame, model-space matrices.

    An animation only carries the nodes it actually moves - T_DANCE_03 animates 25 of
    the skeleton's 34 - listed in node_indices, so parents are resolved through the
    hierarchy instead of by assuming slot order. A node whose parent is not animated
    falls back to that parent's rest matrix."""
    header = man["header"]
    frame_count = header["frame_count"]
    node_count = header["node_count"]
    samples = man["samples"]
    node_indices = man["node_indices"]

    frames = []
    for frame in range(frame_count):
        base = frame * node_count
        locals_by_node: Dict[int, Matrix] = {}
        for slot in range(node_count):
            matrix = sample_to_matrix(
                samples[base + slot], header["position_scalar"], header["position_min"], scale
            )
            locals_by_node[node_indices[slot]] = matrix

        world: Dict[int, Matrix] = {}

        def resolve(node_index, _world=world, _locals=locals_by_node, _seen=None):
            if node_index in _world:
                return _world[node_index]
            _seen = _seen or set()
            if node_index in _seen:                       # guard against a cyclic file
                return Matrix.Identity(4)
            _seen.add(node_index)

            local = _locals.get(node_index)
            if local is None:
                # not animated: leave it where the skeleton puts it
                _world[node_index] = rest.get(node_index, Matrix.Identity(4)) if rest else Matrix.Identity(4)
                return _world[node_index]

            parent = nodes[node_index]["parent"] if node_index < len(nodes) else -1
            if 0 <= parent < len(nodes):
                _world[node_index] = resolve(parent, _world, _locals, _seen) @ local
            else:
                _world[node_index] = local
            return _world[node_index]

        for node_index in node_indices:
            resolve(node_index)

        # turn the finished pose to face the same way as an .ASC-imported character
        frames.append({index: GOTHIC_YAW @ matrix for index, matrix in world.items()})

    return frames


def import_man(
    filename: str,
    armature_obj,
    hierarchy_path: str = None,
    scale: float = 0.01,
    frame_start: int = 1,
    set_scene_range: bool = True,
    action_name: str = None,
    rotation_mapping: str = "AUTO",
    yaw_180: bool = True,
    reuse_action=None,
    add_marker: bool = False,
    transpose: bool = False,
    root_upright: bool = True,
    frame_step: float = 1.0,
    interpolation: str = "AUTO",
):
    """Import a compiled animation onto an existing armature. Returns a summary dict."""
    global ACTIVE_MAPPING, GOTHIC_YAW

    ACTIVE_MAPPING = rotation_mapping if rotation_mapping in ROTATION_MAPPINGS else "AUTO"
    previous_yaw = GOTHIC_YAW
    if not yaw_180:
        GOTHIC_YAW = Matrix.Identity(4)
    try:
        return _import_man(filename, armature_obj, hierarchy_path, scale, frame_start,
                           set_scene_range, action_name, reuse_action, add_marker,
                           transpose, root_upright, frame_step, interpolation)
    finally:
        GOTHIC_YAW = previous_yaw
        ACTIVE_MAPPING = "AUTO"


def _transpose_onto(man, nodes, node_to_bone, armature_obj, scale, frame_start,
                    set_scene_range, action_name, reuse_action, add_marker, filename,
                    missing, root_upright=True, frame_step=1.0, interpolation="AUTO"):
    """Put a source animation on a foreign rig: rotations everywhere, travel on the root.

    Nothing about the source skeleton's proportions is carried over - each bone is simply
    turned as far from ITS OWN rest pose as the source bone was from its own, which is
    why a human dance reads on a dragon. Only bones the two skeletons share by name move;
    the dragon's wings, having no counterpart, stay at rest.

    The root's travel is scaled by how high the two rigs hold their root above the ground
    (a dragon's is over twice a human's), so a step keeps its size relative to the body
    instead of the character shuffling on the spot or sliding across the map."""
    header = man["header"]
    frames, root_index, source_root_height = transpose_man(
        man, nodes, scale, root_upright=root_upright
    )

    root_bone = node_to_bone.get(root_index)
    travel_scale = 1.0
    if root_bone is not None and abs(source_root_height) > 1.0e-6:
        travel_scale = root_bone.bone.matrix_local.translation.z / source_root_height

    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()
    if reuse_action is not None:
        action = reuse_action
    else:
        action = bpy.data.actions.new(
            action_name or f"{header['name'] or Path(filename).stem} (transposed)"
        )
    armature_obj.animation_data.action = action

    if add_marker:
        scene = bpy.context.scene
        if frame_start not in {marker.frame for marker in scene.timeline_markers}:
            scene.timeline_markers.new(header["name"] or Path(filename).stem, frame=frame_start)

    for bone in node_to_bone.values():
        bone.rotation_mode = "QUATERNION"

    keyed = 0
    hemisphere_fixes = 0
    previous_rotation: Dict[int, Quaternion] = {}

    # basis(b) = Rt(b)^-1 . (W_parent^-1 . W_b) . Rt(b)
    #   W      - the source joint's world-space turn away from its own rest
    #   Rt(b)  - the TARGET bone's rest orientation (bone.matrix_local)
    # Derivation: we want every driven bone to end up at W_b . Rt_world(b). Expanding
    # Blender's pose_world(b) = pose_world(parent) . rest_offset(b) . basis(b) and
    # substituting the parent's own desired pose collapses to the line above, so no
    # ordered traversal is needed. A parent the source does not animate contributes
    # identity, which is what leaves a dragon's wings and tail sitting at rest.
    rest_rotation = {
        index: bone.bone.matrix_local.to_quaternion() for index, bone in node_to_bone.items()
    }
    parent_of_node = {index: nodes[index]["parent"] for index in node_to_bone}

    for offset, per_node in enumerate(frames):
        frame = frame_start + offset * frame_step
        for index, pose_bone in node_to_bone.items():
            entry = per_node.get(index)
            if entry is None:
                continue
            world_delta, travel = entry

            parent_index = parent_of_node.get(index, -1)
            parent_entry = per_node.get(parent_index)
            if parent_entry is not None:
                world_delta = parent_entry[0].inverted() @ world_delta

            target_rest = rest_rotation[index]
            rotation = target_rest.inverted() @ world_delta @ target_rest

            last = previous_rotation.get(index)
            if last is not None and rotation.dot(last) < 0.0:
                rotation.negate()
                hemisphere_fixes += 1
            previous_rotation[index] = rotation

            pose_bone.rotation_quaternion = rotation
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame)

            if travel is not None and pose_bone is root_bone:
                # world-space travel -> the root bone's own basis
                basis = pose_bone.bone.matrix_local.to_3x3().inverted() @ (travel * travel_scale)
                pose_bone.location = basis
                pose_bone.keyframe_insert("location", frame=frame)
            keyed += 1

    curves = _apply_interpolation(
        armature_obj.animation_data, interpolation, retimed=frame_step != 1.0
    )

    frame_end = int(round(frame_start + (header["frame_count"] - 1) * frame_step))
    if set_scene_range:
        scene = bpy.context.scene
        scene.frame_start = min(scene.frame_start, frame_start) if reuse_action else frame_start
        scene.frame_end = max(scene.frame_end, frame_end) if reuse_action else frame_end
        if header["fps"] > 0 and reuse_action is None and frame_step == 1.0:
            scene.render.fps = max(1, int(round(header["fps"])))

    print(f"TRANSPOSED '{header['name']}' onto '{armature_obj.name}': "
          f"{header['frame_count']} frames, {len(node_to_bone)}/{len(nodes)} bones shared "
          f"by name, root travel x{travel_scale:.3f}, "
          f"root {'yaw only' if root_upright else 'fully rotated'}, {keyed} keyframe sets")
    if missing:
        print(f"source bones with no counterpart on the target ({len(missing)}): "
              f"{', '.join(missing[:8])}" + (" ..." if len(missing) > 8 else ""),
              level="WARN")
    if hemisphere_fixes:
        print(f"kept {hemisphere_fixes} quaternion key(s) on the same hemisphere")

    return {
        "action": action,
        "armature": armature_obj,
        "name": header["name"],
        "frame_start": frame_start,
        "frame_end": frame_end,
        "matched": len(node_to_bone),
        "nodes": len(nodes),
        "missing": missing,
        "fps": header["fps"],
        "transposed": True,
        "travel_scale": travel_scale,
    }


def _apply_interpolation(animation_data, mode: str, retimed: bool) -> int:
    """Set every key's interpolation, and return how many curves were touched.

    The file holds ONE SAMPLE PER FRAME, so at its own rate linear interpolation
    reproduces it exactly and bezier would only round off the sharp poses. Spread those
    keys out to play at a higher frame rate and the opposite is true: linear in-betweens
    read as a stepped, robotic slowdown, and bezier is what makes the extra frames look
    like motion. "AUTO" picks whichever fits how the clip was laid down."""
    from .armature import iter_action_fcurves

    if mode == "AUTO":
        mode = "BEZIER" if retimed else "LINEAR"

    curves = 0
    for curve in iter_action_fcurves(animation_data):
        for point in curve.keyframe_points:
            point.interpolation = mode
            if mode == "BEZIER":
                # AUTO_CLAMPED will not overshoot between neighbouring samples, which
                # matters on curves this dense
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"
        curves += 1
    return curves


def _import_man(
    filename: str,
    armature_obj,
    hierarchy_path: str = None,
    scale: float = 0.01,
    frame_start: int = 1,
    set_scene_range: bool = True,
    action_name: str = None,
    reuse_action=None,
    add_marker: bool = False,
    transpose: bool = False,
    root_upright: bool = True,
    frame_step: float = 1.0,
    interpolation: str = "AUTO",
):
    man = read_man(filename)
    frame_step = frame_step if frame_step > 0.0 else 1.0
    header = man["header"]

    if hierarchy_path is None:
        hierarchy_path = find_hierarchy_for(filename)

    nodes = []
    if hierarchy_path:
        try:
            nodes = read_mdh(hierarchy_path)
            print(f"hierarchy {Path(hierarchy_path).name}: {len(nodes)} nodes")
        except ManError as err:
            print(f"{err}", level="WARN")

    if armature_obj is None and transpose:
        raise ManError(
            "Rig Transposer needs a target armature - select the rig you want the "
            "animation transposed ONTO before importing"
        )

    if armature_obj is None:
        if not nodes:
            raise ManError(
                f"No armature to animate and no skeleton file beside "
                f"{Path(filename).name} to build one from"
            )
        armature_obj = build_armature_from_hierarchy(
            nodes, scale, name=f"{Path(filename).stem} Skeleton"
        )

    if armature_obj.type != "ARMATURE":
        raise ManError(f"'{armature_obj.name}' is not an armature")

    if not nodes:
        # No skeleton file: fall back to the armature's own bone order
        nodes = [{"name": bone.name, "parent": -1} for bone in armature_obj.data.bones]
        print(
            f"no .MDH beside {Path(filename).name} - falling back to bone order; "
            f"node parenting and naming may be wrong",
            level="WARN",
        )

    # node index -> pose bone (by name, case-insensitive)
    bones_by_name = {bone.name.upper(): bone for bone in armature_obj.pose.bones}
    node_to_bone = {}
    missing = []
    for index, node in enumerate(nodes):
        bone = bones_by_name.get(node["name"].upper())
        if bone is not None:
            node_to_bone[index] = bone
        else:
            missing.append(node["name"])

    if not node_to_bone:
        raise ManError(
            f"None of the {len(nodes)} skeleton nodes match a bone on '{armature_obj.name}'"
        )

    if transpose:
        return _transpose_onto(
            man, nodes, node_to_bone, armature_obj, scale, frame_start, set_scene_range,
            action_name, reuse_action, add_marker, filename, missing, root_upright,
            frame_step, interpolation,
        )

    rest_world = hierarchy_rest_matrices(nodes, scale) if nodes else {}
    frames = _pose_matrices(man, nodes, scale, rest_world)

    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()

    if reuse_action is not None:
        # batch import: keep stacking onto the same action further along the timeline
        action = reuse_action
    else:
        action = bpy.data.actions.new(action_name or header["name"] or Path(filename).stem)
    armature_obj.animation_data.action = action

    if add_marker:
        scene = bpy.context.scene
        existing = {marker.frame for marker in scene.timeline_markers}
        if frame_start not in existing:
            scene.timeline_markers.new(header["name"] or Path(filename).stem, frame=frame_start)

    rest = {index: bone.bone.matrix_local for index, bone in node_to_bone.items()}
    parent_of = {index: nodes[index]["parent"] for index in node_to_bone}

    for bone in node_to_bone.values():
        bone.rotation_mode = "QUATERNION"

    keyed = 0
    # q and -q are the same rotation, but F-curves interpolate component-wise: if two
    # neighbouring keys sit on opposite hemispheres the pose travels the long way round
    # and the bone visibly snaps. Keep every bone's keys on one hemisphere.
    previous_rotation: Dict[int, Quaternion] = {}
    hemisphere_fixes = 0

    for offset, world in enumerate(frames):
        frame = frame_start + offset * frame_step
        for index, pose_bone in node_to_bone.items():
            if index not in world:
                continue

            # model-space node frame -> Blender bone pose (undo the Max bone convention)
            pose = world[index] @ cached_bone_rotation_matrix_inverted

            parent_index = parent_of[index]
            parent_bone = node_to_bone.get(parent_index)
            if parent_bone is not None and parent_index in world:
                parent_pose = world[parent_index] @ cached_bone_rotation_matrix_inverted
                rest_offset = rest[parent_index].inverted() @ rest[index]
                basis = rest_offset.inverted() @ parent_pose.inverted() @ pose
            else:
                basis = rest[index].inverted() @ pose

            location, rotation, _scale = basis.decompose()

            last = previous_rotation.get(index)
            if last is not None and rotation.dot(last) < 0.0:
                rotation.negate()
                hemisphere_fixes += 1
            previous_rotation[index] = rotation

            pose_bone.location = location
            pose_bone.rotation_quaternion = rotation
            pose_bone.keyframe_insert("location", frame=frame)
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame)
            keyed += 1

    curves = _apply_interpolation(
        armature_obj.animation_data, interpolation, retimed=frame_step != 1.0
    )

    frame_end = int(round(frame_start + (header["frame_count"] - 1) * frame_step))
    if set_scene_range:
        scene = bpy.context.scene
        # a batch keeps extending the range rather than resetting it
        scene.frame_start = min(scene.frame_start, frame_start) if reuse_action else frame_start
        scene.frame_end = max(scene.frame_end, frame_end) if reuse_action else frame_end
        # retiming exists to keep the scene's rate, so it must not be overwritten
        if header["fps"] > 0 and reuse_action is None and frame_step == 1.0:
            scene.render.fps = max(1, int(round(header["fps"])))

    from . import log

    log.debug(f"MAN header: {header}")
    log.debug(f"node indices ({len(man['node_indices'])}): {man['node_indices']}")
    log.debug("node -> bone mapping: " + ", ".join(
        f"{nodes[i]['name']}->{b.name}" for i, b in sorted(node_to_bone.items())))
    print(
        f"animation '{header['name']}': {header['frame_count']} frames, "
        f"{len(node_to_bone)}/{len(nodes)} nodes matched on '{armature_obj.name}', "
        f"{keyed} keyframe sets, {header['fps']:g} fps, {curves} curves set to linear"
    )
    if hemisphere_fixes:
        print(f"kept {hemisphere_fixes} quaternion key(s) on the same hemisphere "
              f"(prevents bones snapping the long way between frames)")
    if missing:
        print(f"nodes without a matching bone ({len(missing)}): {', '.join(missing[:8])}"
              + (" ..." if len(missing) > 8 else ""), level="WARN")

    return {
        "action": action,
        "armature": armature_obj,
        "name": header["name"],
        "frame_start": frame_start,
        "frame_end": frame_end,
        "matched": len(node_to_bone),
        "nodes": len(nodes),
        "missing": missing,
        "fps": header["fps"],
    }


# For calling outside KrxImpExp module
def KrxManImp(
    filename: str,
    armature_obj=None,
    scale: float = 0.01,
    frame_start: int = 1,
):
    if armature_obj is None:
        armature_obj = next(
            (obj for obj in bpy.context.selected_objects if obj.type == "ARMATURE"),
            bpy.context.view_layer.objects.active,
        )
    return import_man(filename, armature_obj, scale=scale, frame_start=frame_start)
