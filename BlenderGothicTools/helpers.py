from .log import klog as print  # route console output through the add-on's log tag
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import List, Tuple, Union

import bpy
from mathutils import Matrix, Vector

from .file import TFile
from .mesh import Face, TVFace, is_mesh_object
from .numconv import string_to_int
from .object import (
    end_editmode,
    find_object_by_name,
    get_children,
    get_object_name,
    get_parent,
    is_selected,
    set_parent,
    start_editmode,
)
from .scene import MAX_SCENE_FRAME_LIMIT
from .types import EdgeVertexType, is_valid_tuple_instance

SLOTS_DATACLASS = dict(slots=True) if "slots" in dataclass.__kwdefaults__ else {}
"""This allows to use the same syntax for dataclasses in Python 3.10 and previous versions."""


class ObjectType(IntEnum):
    DUMMY = -1
    UNDEFINED = 0
    BONE = 1
    SLOT = 2
    MESH = 3

class AscType(IntEnum):
    MORPH_MESH = 1
    MORPH_ANIM = 2
    STATIC_MESH = 4
    DYNAMIC_MESH = 8
    DYNAMIC_ANIM = 16


class CallUIType(IntEnum):
    IMPORT_MORPH_MESH = AscType.MORPH_MESH
    IMPORT_MORPH_ANIM = AscType.MORPH_ANIM
    IMPORT_STATIC_MESH = AscType.STATIC_MESH
    IMPORT_DYNAMIC_MESH = AscType.DYNAMIC_MESH
    IMPORT_DYNAMIC_ANIM = AscType.DYNAMIC_ANIM
    IMPORT_ZEN = 32
    IMPORT_MSH = 64
    IMPORT_MRM = 128
    IMPORT_3DS = 256
    MESSAGE_BOX = 512
    EXPORT_ASC = 1024
    EXPORT_3DS = 2048


# --------- TRANSFORMS ---------


@dataclass(**SLOTS_DATACLASS)
class TTimeTransform:
    start_frame_in_file: int = 0
    end_frame_in_file: int = 100
    start_frame_in_scene: int = 0
    end_frame_in_scene: int = 100
    min_frame_in_file: int = -32768
    max_frame_in_file: int = 32767
    min_frame_in_scene: int = 0
    max_frame_in_scene: int = MAX_SCENE_FRAME_LIMIT

    def asdict(self):
        return asdict(self)


# --------- CHUNKS ---------


@dataclass(**SLOTS_DATACLASS)
class TZENChunk:
    position: int = 0
    size: int = 0
    name: str = ""
    class_name: str = ""
    class_version: int = 0
    object_index: int = 0


# --------- ARCHIVES ---------


class TZENArchive:
    __slots__ = ("__zen_version", "__zen_mode", "__save_game", "__num_objects")

    def __init__(self):
        self.__zen_version = 0
        self.__zen_mode = 0
        self.__num_objects = 0

    @staticmethod
    def __read_line(file: TFile):
        return file.ReadString(terminator=0x0A)

    def __read_common_header(self, file: TFile):
        if self.__read_line(file) != "ZenGin Archive":
            raise RuntimeError(f"{__name__}: File is not a ZenGin archive.\n" f'File name: "{file.GetPath()}".')

        pos = file.GetPos()
        line = self.__read_line(file)

        if not line.startswith("ver"):
            raise RuntimeError(
                f"{__name__}: "
                f'Cannot read archive, "ver" keyword expected.\n'
                f'Position: {"0x" + hex(pos)[2:].upper()}.\n'
                f'File name: "{file.GetPath()}".'
            )

        self.__zen_version = string_to_int(line[3:])

        file.SkipString(terminator=0x0A)

        pos = file.GetPos()
        line = self.__read_line(file)

        if line == "BINARY":
            self.__zen_mode = 0
        elif line == "ASCII":
            self.__zen_mode = 1
        elif line == "BIN_SAFE":
            self.__zen_mode = 3
        else:
            raise RuntimeError(
                f"{__name__}: "
                f"Unknown archive mode.\n"
                f'Position: {"0x" + hex(pos)[2:].upper()}.\n'
                f'File name: "{file.GetPath()}".'
            )

        while not file.Eof():
            pos = file.GetPos()
            line = self.__read_line(file)
            if line == "END":
                break
            elif line.startswith("objects") and self.__zen_version == 0:
                file.SetPos(pos)
                break

    def __read_header_ascii_binary(self, file: TFile):
        pos = file.GetPos()
        line = self.__read_line(file)
        self.__num_objects = 0

        if line.startswith("objects"):
            self.__num_objects = string_to_int(line[7:].split(" ")[0])
        else:
            raise RuntimeError(
                f"{__name__}: "
                f'Cannot read archive, "objects" keyword expected.\n'
                f'Position: {"0x" + hex(pos)[2:].upper()}.\n'
                f'File name: "{file.GetPath()}".'
            )

        file.SkipString(terminator=0x0A)
        file.SkipString(terminator=0x0A)

    def __read_header_bin_safe(self, file: TFile):
        file.SkipByOffset(12)
        # _ = file.ReadUnsignedLong()  # binSafeVersion
        # self.__num_objects = file.ReadUnsignedLong()
        # _ = file.ReadUnsignedLong()  # mapPos

    def __read_string_ascii(self, file: TFile):
        return self.__read_line(file).lstrip()

    @staticmethod
    def __read_string_binary(file: TFile):
        return file.ReadString()

    @staticmethod
    def __read_string_bin_safe(file: TFile):
        pos = file.GetPos()
        type_char = file.ReadUnsignedChar()
        if type_char != 0x01:
            raise RuntimeError(
                f"{__name__}: "
                f"A string expected.\n"
                f'Position: {"0x" + hex(pos)[2:].upper()}.\n'
                f'File name: "{file.GetPath()}".'
            )

        string_len = file.ReadUnsignedShort()
        return file.ReadString(buffer_size=string_len, terminator=None)

    def __read_string(self, file: TFile):
        if self.__zen_mode == 1:
            return self.__read_string_ascii(file)
        elif self.__zen_mode == 0:
            return self.__read_string_binary(file)
        else:
            return self.__read_string_bin_safe(file)

    def __read_chunk_start_ascii_bin_safe(self, file: TFile):
        # Shoun: I think this seems simplified enough
        zen_chunk = TZENChunk()
        zen_chunk.position = file.GetPos()
        string = self.__read_string(file)

        if string == "[]":
            return TZENChunk()

        if not string.startswith("[") or not string.endswith("]"):
            return TZENChunk()

        chunk_stats = string.strip("[]").split()
        if len(chunk_stats) != 4:
            return TZENChunk()

        zen_chunk.name = chunk_stats[0]
        zen_chunk.class_name = chunk_stats[1]
        zen_chunk.class_version = string_to_int(chunk_stats[2])
        zen_chunk.object_index = string_to_int(chunk_stats[3])
        return zen_chunk

    @staticmethod
    def __read_chunk_start_binary(file: TFile):
        zen_chunk = TZENChunk()
        zen_chunk.position = file.GetPos()
        zen_chunk.size = file.ReadUnsignedLong()
        zen_chunk.class_version = file.ReadUnsignedShort()
        zen_chunk.object_index = file.ReadUnsignedLong()
        zen_chunk.name = file.ReadString()
        zen_chunk.class_name = file.ReadString()
        return zen_chunk

    @staticmethod
    def __read_chunk_end_binary(file, zen_chunk):
        file.SetPos(zen_chunk.position + zen_chunk.size)

    def ReadHeader(self, file: TFile):
        self.__read_common_header(file)
        if self.__zen_mode == 3:
            self.__read_header_bin_safe(file)
        else:
            self.__read_header_ascii_binary(file)

    def ReadString(self, file: TFile):
        return self.__read_string(file)

    def ReadChunkStart(self, file: TFile):
        if self.__zen_mode == 0:
            return self.__read_chunk_start_binary(file)
        else:
            return self.__read_chunk_start_ascii_bin_safe(file)

    def ReadChunkEnd(self, file, zen_chunk):
        if self.__zen_mode == 0:
            self.__read_chunk_end_binary(file, zen_chunk)


# --------- ANALYZERS ---------


class TSceneAnalyzer:
    __slots__ = (
        "scene_object_names",
        "selected_object_names",
        "scene_meshes_by_type",
        "selected_meshes_by_type",
        "scene_slot_names",
        "selected_slot_names",
        "scene_bone_names",
        "selected_bone_names",
        "scene_mesh_names",
        "selected_mesh_names",
        "scene_dummies",
        "selected_dummies",
        "scene_prefixes",
        "selected_prefixes",
        "unique_prefix",
        "objects_by_prefix",
        "model_hierarchies",
        "appropriate_prefixes",
        "selected_appropriate_prefixes",
    )

    def __init__(self):
        self.scene_object_names = []
        self.selected_object_names = []
        self.scene_meshes_by_type = []
        self.selected_meshes_by_type = []
        self.scene_slot_names = []
        self.selected_slot_names = []
        self.scene_bone_names = []
        self.selected_bone_names = []
        self.scene_mesh_names = []
        self.selected_mesh_names = []
        self.scene_dummies = []
        self.selected_dummies = []
        self.scene_prefixes = []
        self.selected_prefixes = []
        self.unique_prefix = ""
        self.objects_by_prefix = []
        self.model_hierarchies = []
        self.appropriate_prefixes = []
        self.selected_appropriate_prefixes = []
        self.prepare_scene_objects(None)
        self.prepare_model_hierarchies()
        self.prepare_unique_prefix()

    def append_prefix(self, prefix, selected):
        if prefix not in self.scene_prefixes:
            self.scene_prefixes.append(prefix)

        if selected and prefix not in self.selected_prefixes:
            self.selected_prefixes.append(prefix)

    def prepare_scene_objects(self, obj):
        if obj is None:
            for child in get_children(obj):
                self.prepare_scene_objects(child)
            return

        obj_name = get_object_name(obj)
        selected = is_selected(obj)
        is_mesh_by_type = is_mesh_object(obj)
        name_analyzer = TNameAnalyzer(obj_name)
        self.scene_object_names.append(obj_name)
        if selected:
            self.selected_object_names.append(obj_name)

        if is_mesh_by_type:
            self.scene_meshes_by_type.append(obj_name)
            if selected:
                self.selected_meshes_by_type.append(obj_name)

        prefix = name_analyzer.prefix
        obj_type = name_analyzer.type
        if obj_type == ObjectType.SLOT:
            self.scene_slot_names.append(obj_name)
            if selected:
                self.selected_slot_names.append(obj_name)

            self.append_prefix(prefix, selected)
        elif obj_type == ObjectType.BONE:
            self.scene_bone_names.append(obj_name)
            if selected:
                self.selected_bone_names.append(obj_name)

            self.append_prefix(prefix, selected)
        elif obj_type == ObjectType.MESH:
            self.scene_mesh_names.append(obj_name)
            if selected:
                self.selected_mesh_names.append(obj_name)

            self.append_prefix(prefix, selected)
        else:
            self.scene_dummies.append(obj_name)
            if selected:
                self.selected_dummies.append(obj_name)

        for child in get_children(obj):
            self.prepare_scene_objects(child)

    def get_objects_by_prefix(self, prefix):
        self.objects_by_prefix = []

        conflict_prefixes = []
        for scene_prefix in self.scene_prefixes:
            if scene_prefix != prefix and scene_prefix.startswith(prefix):
                conflict_prefixes.append(scene_prefix)

        for obj_name in self.scene_object_names:
            if obj_name.startswith(prefix):
                no_conflicts = True
                for confict_prefix in conflict_prefixes:
                    if obj_name.startswith(confict_prefix):
                        no_conflicts = False
                        break

                if no_conflicts:
                    self.objects_by_prefix.append(obj_name)

        return self.objects_by_prefix

    def prepare_model_hierarchies(self):
        self.model_hierarchies = []

        for prefix in self.scene_prefixes:
            num_bones = 0
            num_slots = 0
            num_meshes = 0
            num_dummies = 0
            bip01_found = False
            object_names = self.get_objects_by_prefix(prefix)
            object_types = []
            object_parents = []
            modelType = 0

            for j, obj_name in enumerate(object_names):
                name_analyzer = TNameAnalyzer(obj_name)
                obj_type = name_analyzer.type

                if obj_type == ObjectType.BONE:
                    num_bones += 1
                    if name_analyzer.short_name.upper() == "BIP01":
                        bip01_found = True
                elif obj_type == ObjectType.SLOT:
                    num_slots += 1
                elif obj_type == ObjectType.MESH:
                    num_meshes += 1
                elif obj_type == ObjectType.DUMMY:  # Treat as dummies
                    num_dummies += 1

                object_names[j] = obj_name[len(prefix) : len(obj_name)]
                object_types.append(obj_type)

            if num_meshes == 1 and num_bones == 0 and num_slots == 0:
                modelType = 1 + 2
            elif bip01_found:
                modelType = 8 + 16
            else:
                modelType = 4

            if modelType == 8 + 16:
                root_bone_index = -1

                for j, obj_type in enumerate(object_types):
                    if obj_type == 1:
                        root_bone_index = j
                        break

                if root_bone_index not in [-1, 0]:
                    object_names[0], object_names[root_bone_index] = (object_names[root_bone_index], object_names[0])
                    object_types[0], object_types[root_bone_index] = (object_types[root_bone_index], object_types[0])

                without_lead_dummies = []
                without_lead_dummies2 = []

                for j, obj_type in enumerate(object_types):
                    if obj_type != 0 or j > root_bone_index:  # hierarchical dummies such as root of armature
                        if obj_type != -1:  # mesh dummies linked to bones
                            without_lead_dummies.append(object_names[j])
                            without_lead_dummies2.append(obj_type)

                object_names = without_lead_dummies
                object_types = without_lead_dummies2

            for j, obj_name in enumerate(object_names):
                obj = find_object_by_name(prefix + obj_name)
                parent_name = object_names[0] if (modelType == 8 + 16) and j > 0 else ""

                if obj is None:
                    object_parents.append(parent_name)
                    continue

                parent = get_parent(obj)

                if parent is None:
                    object_parents.append(parent_name)
                    continue

                real_parent_name = get_object_name(parent)
                name = real_parent_name.replace(prefix, "")

                if name in object_names[:j]:
                    parent_name = name

                object_parents.append(parent_name)

            self.model_hierarchies.append(
                TModelHierarchy(prefix, modelType, object_names, object_parents, object_types)
            )

    def get_model_hierarchy_by_prefix(self, prefix):
        for model_hierarchy in self.model_hierarchies:
            if model_hierarchy.model_prefix == prefix:
                return model_hierarchy

        return self.model_hierarchies[-1]

    def check_prefix_for_unique(self, prefix):
        return prefix not in self.scene_prefixes

    def prepare_unique_prefix(self):
        """TODO simplify"""
        prefix = ""
        a = ord("A")
        z = ord("Z")
        codes = []

        while not self.check_prefix_for_unique(prefix):
            pos = len(codes) - 1
            while True:
                if pos == -1:
                    for i in range(len(codes)):
                        codes[i] = a

                    codes.append(a)
                    break
                elif codes[pos] < z:
                    codes[pos] += 1
                    break
                else:
                    codes[pos] = a
                    pos -= 1

            prefix = ""
            for code in codes:
                prefix = prefix + chr(code)

            if prefix:
                prefix = f"{prefix} "

        self.unique_prefix = prefix

    def find_appropriate_dynamic_models(self, objects_desc):
        self.appropriate_prefixes = []
        self.selected_appropriate_prefixes = []
        for prefix in self.scene_prefixes:
            appropriate = False

            for desc in objects_desc:
                obj_name = desc.object_name
                name_analyzer = TNameAnalyzer(obj_name)
                obj_type = name_analyzer.type

                if obj_type == ObjectType.BONE:
                    if f"{prefix}{obj_name}" in self.scene_bone_names:
                        appropriate = True
                    elif f"{prefix}{obj_name.upper()}" in self.scene_bone_names:
                        appropriate = True
                    break
                elif obj_type == ObjectType.SLOT:
                    if f"{prefix}{obj_name}" in self.scene_slot_names:
                        appropriate = True
                    elif f"{prefix}{obj_name.upper()}" in self.scene_slot_names:
                        appropriate = True
                    break

            if appropriate:
                self.appropriate_prefixes.append(prefix)
                if prefix in self.selected_prefixes:
                    self.selected_appropriate_prefixes.append(prefix)

    def find_appropriate_morph_meshes(self, objects_desc, start_obj=None):
        for obj in get_children(start_obj):
            for desc in objects_desc:
                if len(desc.morph_track.sample_verts) == 0 or not is_mesh_object(obj):
                    continue

                if len(obj.data.vertices) == len(desc.morph_track.sample_verts[0]):
                    scene_obj_name = get_object_name(obj)
                    if scene_obj_name not in self.appropriate_prefixes:
                        self.appropriate_prefixes.append(scene_obj_name)
                        # THIS WON'T RETURN PREFIXES PER SE FROM NOW ON! TODO: change the naming scheme
            self.find_appropriate_morph_meshes(objects_desc, obj)


class TNameAnalyzer:
    __slots__ = ("full_name", "prefix", "short_name", "type")

    def __init__(self, full_name):
        self.full_name = full_name
        self.short_name = full_name
        self.prefix: str = ""
        self.type: ObjectType = ObjectType.UNDEFINED

        full_len = len(full_name)
        slot = full_name.upper().find("ZS_")
        mesh = full_name.upper().find("ZM_")
        bone = full_name.upper().find("BIP01")
        dummy = full_name.upper().find("DUMMY")
        if slot != -1:
            self.prefix = full_name[:slot]
            self.short_name = full_name[slot:full_len]
            self.type = ObjectType.SLOT
        elif mesh != -1:
            self.prefix = full_name[:mesh]
            self.short_name = full_name[mesh:full_len]
            self.type = ObjectType.MESH
        elif bone != -1:
            self.prefix = full_name[:bone]
            self.short_name = full_name[bone:full_len]
            self.type = ObjectType.BONE
        elif dummy != -1:
            self.prefix = full_name[:dummy]
            self.short_name = full_name[dummy:full_len]
            self.type = ObjectType.DUMMY


# --------- SOFTSKIN MESH AND MODEL ---------


@dataclass(**SLOTS_DATACLASS)
class TModelHierarchy:
    model_prefix: str = ""
    model_type: int = 0
    objects: List[str] = field(default_factory=list)
    object_parents: List[str] = field(default_factory=list)
    object_types: List[str] = field(default_factory=list)

    def asdict(self):
        return asdict(self)

@dataclass(**SLOTS_DATACLASS)
class TMeshDesc:
    _face_mat_ids: List[int] = field(default_factory=list)
    _edge_vis: List[EdgeVertexType] = field(default_factory=list)
    _faces: List[Face] = field(default_factory=list)
    verts: List[Vector] = field(default_factory=list)
    tverts: List[Vector] = field(default_factory=list)
    tvfaces: List[TVFace] = field(default_factory=list)
    num_vertex: int = 0
    num_tvertex: int = 0
    num_faces: int = 0
    num_tvfaces: int = 0

    @property
    def faces(self) -> List[Face]:
        return self._faces

    @faces.setter
    def faces(self, value: Union[int, List[Face]]):
        self._faces = value
        self._face_mat_ids = [0 for _ in value]
        self._edge_vis = [[True, True, True] for _ in value]

    @property
    def face_material_ids(self) -> List[int]:
        return self._face_mat_ids

    @property
    def edge_vis(self) -> List[EdgeVertexType]:
        return self._edge_vis


@dataclass(**SLOTS_DATACLASS)
class TSoftSkinVert:
    bones: List[str] = field(default_factory=list)
    weights: List[int] = field(default_factory=list)

    def SetNumWeights(self, num_weights):
        if len(self.bones) != num_weights:
            self.bones = []
            self.weights = []
            for _ in range(num_weights):
                self.bones.append("")
                self.weights.append(0)


@dataclass(**SLOTS_DATACLASS)
class TSoftSkinVerts:
    verts: List[TSoftSkinVert] = field(default_factory=list)

    def SetNumVerts(self, num_verts):
        if len(self.verts) != num_verts:
            self.verts = [TSoftSkinVert() for _ in range(num_verts)]


# --------- ANIMATION ---------
@dataclass(**SLOTS_DATACLASS)
class TPosTrack:
    sample_positions: List[Vector] = field(default_factory=list)

    def SetNumSamples(self, num_samples):
        if len(self.sample_positions) != num_samples:
            self.sample_positions = [Vector((0, 0, 0)) for _ in range(num_samples)]


@dataclass(**SLOTS_DATACLASS)
class TRotTrack:
    sample_axes: List[Vector] = field(default_factory=list)
    sample_angles: List[int] = field(default_factory=list)

    def SetNumSamples(self, num_samples):
        if len(self.sample_axes) != num_samples:
            self.sample_axes = []
            self.sample_angles = []
            for _ in range(num_samples):
                self.sample_axes.append(Vector((0, 0, 0)))
                self.sample_angles.append(0)


@dataclass(**SLOTS_DATACLASS)
class TMorphTrack:
    sample_verts: List[list] = field(default_factory=list)

    def SetNumSamples(self, num_samples):
        if len(self.sample_verts) != num_samples:
            self.sample_verts = [[] for _ in range(num_samples)]


@dataclass(**SLOTS_DATACLASS)
class TObjectDesc:
    object_name: str = ""
    object_type: ObjectType = ObjectType.UNDEFINED
    parent_name: str = ""
    _transform: Matrix = field(default_factory=Matrix)
    material_reference: int = 0
    mesh_description: TMeshDesc = field(default_factory=TMeshDesc)
    softskin: TSoftSkinVerts = field(default_factory=TSoftSkinVerts)
    position_track: TPosTrack = field(default_factory=TPosTrack)
    rotation_track: TRotTrack = field(default_factory=TRotTrack)
    morph_track: TMorphTrack = field(default_factory=TMorphTrack)

    @property
    def transform(self):
        return self._transform.normalized()

    @transform.setter
    def transform(self, value):
        self._transform = value


# Special class to setup the armature modifier.
class SkinData:
    __slots__ = ("__skin_obj", "__mesh", "__armature_obj")

    def __init__(self, skin_obj):
        if not isinstance(skin_obj, bpy.types.Object):
            raise TypeError(f"{__name__}: SkinData.__init__: error with argument 2")
        self.__skin_obj = skin_obj
        self.__mesh: bpy.types.Mesh = skin_obj.data
        self.__armature_obj = skin_obj.find_armature()

    def add_bones(self, extended_bones):
        for extended_bone in extended_bones:
            if not is_valid_tuple_instance(extended_bone):
                raise TypeError(f"{__name__}: SkinData.add_bones: error with argument 2")

            if not self.__armature_obj:
                self.__armature_obj = extended_bone[0]
                new_modifier = self.__skin_obj.modifiers.new("Armature", "ARMATURE")
                new_modifier.object = self.__armature_obj
                new_modifier.use_vertex_groups = True
                set_parent(self.__skin_obj, self.__armature_obj)

            self.__skin_obj.vertex_groups.new(name=extended_bone[1])

    def get_num_verts(self):
        return len(self.__mesh.vertices)

    def set_vert_weights(self, vert_index, extbones, weights):
        for i, extbone in enumerate(extbones):
            weight = weights[i]
            bone_name = extbone[1]
            self.__skin_obj.vertex_groups[bone_name].add([vert_index], weight, "REPLACE")

    def get_vert_num_weights(self, vert_index):
        return len(self.__mesh.vertices[vert_index].groups)

    def get_vert_weight(self, vert_index, vert_bone_index):
        return self.__mesh.vertices[vert_index].groups[vert_bone_index].weight

    def get_vert_weight_bone(self, vert_index, vert_bone_index):
        group_index = self.__mesh.vertices[vert_index].groups[vert_bone_index].group
        bone_name = self.__skin_obj.vertex_groups[group_index].name
        return self.__armature_obj, bone_name


# --------- UTILITY ---------


def remove_sectored_materials(obj):
    # Standalone (slow) removal of sectored materials. Not sure if it works properly anymore
    portal_names = set()
    material_slots = obj.material_slots
    material_lookup = dict()

    print(f"{obj.type} {obj.name}: Removing sectored materials...")
    print(f"{obj.type} {obj.name}: This could take a while...")

    for i, slot in enumerate(material_slots):
        slotname = slot.name.upper()
        if slotname.startswith(("PI:", "P:", "PN:")):
            portal_names.update(slotname.removeprefix("P:").removeprefix("PI:").removeprefix("PN:").split("_"))
        if slotname.startswith("S:"):
            portal_names.add(slotname.removeprefix("S:").split("_")[0])
        material_lookup[slotname] = i

    if not portal_names:
        return
    if "" in portal_names:
        portal_names.remove("")  # byproduct of split()

    start_editmode(obj)
    for slot_id, slot in enumerate(material_slots):
        slotname = slot.name.upper()
        if slotname.startswith("S:"):
            for portal_name in portal_names:
                if portal_name in slotname:
                    target_name = slotname.removeprefix(f"S:{portal_name}_")
                    if target_name.startswith("S:"):
                        continue

                    print("Processing:", slotname, "into", target_name)

                    try:
                        target_id = material_lookup[target_name]
                        bpy.ops.mesh.select_all(action="DESELECT")
                        obj.active_material_index = slot_id
                        bpy.ops.object.material_slot_select()
                        obj.active_material_index = target_id
                        bpy.ops.object.material_slot_assign()
                        print(f"Reassigning faces from {slotname} to material {target_name}")
                    except KeyError:
                        old_name = slot.material.name
                        target_material = bpy.data.materials.get(target_name)
                        if target_material is None:
                            slot.material.name = target_name
                        else:
                            slot.material = target_material
                        material_lookup[target_name] = slot_id
                        print(f"Replacing {old_name} with material {target_name} from scene")

    end_editmode()
    obj.select_set(True)
    bpy.ops.object.material_slot_remove_unused()
    obj.select_set(False)


@dataclass(**SLOTS_DATACLASS)
class BugNotice:
    """Infomation about a known bug"""
    text: str
    first_seen: Tuple[int, int, int]
    scope: Tuple[str] = ()
    resolved_in: Tuple[int, int, int] = None
    has_been_informed: bool = False

    def __str__(self):
        first_seen: str = '.'.join(map(str, self.first_seen))
        resolved_in: str = '.'.join(map(str, self.resolved_in)) if self.resolved_in else ""

        if self.resolved_in is None:
            resolution = f"Please downgrade to version below {first_seen}"
        else:
            resolution = f"Please update to version {resolved_in}"

        return self.text.format(
            first_seen=first_seen,
            resolution=resolution
        )

    def __contains__(self, version: Tuple[int, int, int]):
        """Override 'in' operator to check if version is 'in' the range of the bug"""
        assert isinstance(version, tuple)

        if self.resolved_in is None:
            return version >= self.first_seen
        else:
            return version >= self.first_seen and version < self.resolved_in
