from .log import klog as print  # route console output through the add-on's log tag
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Union

import bpy
import mathutils
from mathutils import Matrix, Vector

from .armature import (
    animate_transform,
    animate_vertices,
    has_transform_animation,
    has_vertex_animation,
    get_armature,
    create_bone,
)
from .gui import call_message_box
from .helpers import (
    SLOTS_DATACLASS,
    TSceneAnalyzer,
    AscType,
    ObjectType,
    SkinData,
    TNameAnalyzer,
    TObjectDesc,
    TTimeTransform,
)
from .Krx3dsImp import Krx3dsImp
from .material import link_texture_to_material, new_material
from .math_utils import (
    get_row0,
    get_row1,
    get_row2,
    get_translation_part,
    set_row0,
    set_row1,
    set_row2,
    set_translation_part,
    translation_matrix,
)
from .mesh import MeshData, new_mesh_object
from .object import (
    deselect_all,
    find_object_by_name,
    get_children,
    get_object_name,
    get_parent,
    get_transform,
    is_valid_tuple_instance,
    set_box_mode,
    set_parent,
    set_renderable,
    set_transform,
    show,
)
from .scene import SceneMode, reset_scene, MAX_SCENE_FRAME_LIMIT
from .system import PLUGIN_ROOT

IEEE_TOKENS_RE = re.compile(r"#(?:SNAN0|QNAN0|NINF0|PINF0|INF0|NINF0|NN0|INN0|ND0|IND0|NZ0|INZ0|PZ0|IPZ0|PD0|IPD0|PN0|IPN0)", re.IGNORECASE)
"""Various NaN or Inf error tokens which will be converted to 0 later"""

@dataclass(**SLOTS_DATACLASS)
class ASCParser:
    # Load
    data: List[str] = field(default_factory=list)  # used in reversed order with pop(), shockingly deque is slower...
    filename: str = None
    # Process
    materials: List[Tuple[str, Tuple[float, float, float, float], str, None]] = field(default_factory=list)
    objects_stats: List[TObjectDesc] = field(default_factory=list)
    model_type: AscType = AscType.STATIC_MESH # STATIC_MESH or DYNAMIC_MESH, doesn't really matter that much a UI backend is the same for those two.
    num_meshes: int = 0
    num_slots: int = 0
    num_bones: int = 0
    framerate: int = 0
    # Model Creation
    scale: float = 0.01
    color_adjustment: float = None
    model_prefix: str = ""
    # IT"S NOT BPY OBJECT! ITS EXTENDED_OBJ BULLSHIT
    imported_objects: Dict[str, Union[bpy.types.Object, tuple]] = field(default_factory=dict)
    time_transform: TTimeTransform = field(default_factory=TTimeTransform)
    animated_hierarchy: List[bool] = field(default_factory=list)
    dynamic_anim_transforms: List[mathutils.Matrix] = field(default_factory=list)

    def __post_init__(self):
        with open(self.filename, mode="rt", encoding="Windows-1250") as file:
            self.data = file.readlines()
        if len(self.data) == 0 or not self.data:
            raise RuntimeError(__name__, "File is empty.")

        self.read_header()
        self.read_scene_info()
        if self.model_type in (AscType.STATIC_MESH, AscType.MORPH_MESH, AscType.DYNAMIC_MESH):
            self.read_material_list()
        self.read_top_level_geometry_data()

    # ------------------------------------------------------------------------------------------
    # ------------------------------------Top Level Functions-----------------------------------
    # ------------------------------------------------------------------------------------------

    def read_header(self):
        # vVv this might seem like a waste of time, but it takes like 0.001s at most. This also removes sooo much of the complexity down the line
        self.data = [x.strip() for x in self.data]
        for i, line in enumerate(self.data):
            # Fix IEEE tokens
            self.data[i] = IEEE_TOKENS_RE.sub("0", line)

            if "*TM_ANIMATION" in line:
                self.model_type = AscType.DYNAMIC_ANIM
            elif "*MESH_ANIMATION " in line:
                self.model_type = AscType.MORPH_ANIM
        self.data = list(reversed(self.data))

        version = self.data.pop()
        if "*3DSMAX_ASCIIEXPORT	" not in version:
            raise RuntimeError(__name__, "Not an ASC model.")
        self.data.pop()  # Comment

    def read_scene_info(self):
        line = self.data.pop()
        if "*SCENE {" in line:
            while len(self.data) > 0:
                line = self.data.pop()
                if line.startswith("}"):
                    return
                elif "*SCENE_FIRSTFRAME" in line:
                    self.time_transform.min_frame_in_file = int(line.split()[1])
                elif "*SCENE_LASTFRAME" in line:
                    self.time_transform.max_frame_in_file = int(line.split()[1])
                elif "*SCENE_FRAMESPEED" in line:
                    self.framerate = int(line.split()[1])
        else:
            raise RuntimeError(__name__, "Scene info not found.")

    def read_material_list(self):
        # There's way too much data inside *SCENE with some ASCs, we have to skip it here.
        data = self.data.copy()
        matlist_parsed = False
        while len(self.data) > 0:
            line = self.data.pop()
            if "*MATERIAL_LIST {" in line:
                count = int(self.data.pop().split()[1])
                self.materials = [[] for _ in range(count)]
                for i in range(count):
                    line = self.data.pop()
                    if "*MATERIAL " in line:
                        idx = int(line.split()[1])
                        self.materials[idx] = self.read_material()
                matlist_parsed = True
                break
        if matlist_parsed is False:
            self.data = data
            print(f"Warning: Material list not found in {self.filename}")

    def read_top_level_geometry_data(self):
        while len(self.data) > 0:
            line = self.data.pop()
            if "*HELPEROBJECT {" in line:
                self.read_geometry_object()
            elif "*GEOMOBJECT {" in line:
                self.read_geometry_object()
            elif "*MESH_SOFTSKINVERTS {" in line:
                self.read_softskin_data()

    # ------------------------------------------------------------------------------------------
    # -----------------------------------Processing Functions-----------------------------------
    # ------------------------------------------------------------------------------------------

    def read_submaterial(self):
        name, diffuse, bitmap, map_reflect = "", (0, 0, 0, 1), "", None
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                return (name, diffuse, bitmap, map_reflect)
            elif "*MATERIAL_NAME " in line:
                name = line.split('"')[1]
            elif "*MATERIAL_DIFFUSE " in line:
                words = line.split()
                diffuse = float(words[1]), float(words[2]), float(words[3]), 1
            elif "*MAP_AMBIENT {" in line:
                self.read_diffusemap()  # Skip for [Zengine ASC Exporter v1.06]
            elif "*MAP_BUMP {" in line:
                self.read_diffusemap()  # Skip for [Zengine ASC Exporter v1.08]
            elif "*MAP_DIFFUSE {" in line:
                bitmap, map_reflect = self.read_diffusemap()

    def read_material(self):
        name, diffuse, bitmap, map_reflect = "", (0, 0, 0, 1), "", None
        submaterials = list()
        submaterial_count = 0
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                if submaterial_count == 0:
                    submaterials.append((name, diffuse, bitmap, map_reflect))
                return submaterials
            elif "*MATERIAL_NAME " in line:
                name = line.split('"')[1]
            elif "*MATERIAL_DIFFUSE " in line:
                words = line.split()
                diffuse = float(words[1]), float(words[2]), float(words[3]), 1
            elif "*MAP_AMBIENT {" in line:
                self.read_diffusemap()  # Skip for [Zengine ASC Exporter v1.06]
            elif "*MAP_BUMP {" in line:
                self.read_diffusemap()  # Skip for [Zengine ASC Exporter v1.08]
            elif "*MAP_DIFFUSE {" in line:
                bitmap, map_reflect = self.read_diffusemap()
            elif "*NUMSUBMTLS" in line:
                submaterial_count = int(line.split()[1])
                submaterials = [[] for _ in range(submaterial_count)]
            elif "*SUBMATERIAL " in line:
                sub_idx = int(line.split()[1])
                submaterials[sub_idx] = self.read_submaterial()

    def read_diffusemap(self):
        map_reflect = None
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                return bitmap, map_reflect
            elif "*BITMAP " in line:
                bitmap = line.split('"')[1]

                # TODO: This seems like a thing that should be moved into material.py's load_image()... Consult with HRY what to do.
                if "\\" in bitmap:
                    temp = bitmap.split("\\")
                    if "" in temp:
                        temp.remove("")
                    bitmap = temp[-1]

            elif "*MAP_REFLECT " in line:
                map_reflect = float(line.split()[1])

    def read_geometry_object(self):
        desc = TObjectDesc()
        skip_obj = False
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                if not skip_obj:
                    if (
                        self.num_meshes == 1
                        and self.num_bones == 0
                        and self.num_slots == 0
                        and self.model_type != AscType.MORPH_ANIM
                    ):
                        self.model_type = AscType.MORPH_MESH
                    elif self.model_type not in (AscType.MORPH_ANIM, AscType.DYNAMIC_ANIM, AscType.DYNAMIC_MESH):
                        self.model_type = AscType.STATIC_MESH
                    self.objects_stats.append(desc)
                return
            elif "*NODE_NAME" in line:
                desc.object_name = line.split('"')[1]
                desc.object_type = TNameAnalyzer(desc.object_name).type

                for statistic in self.objects_stats:
                    if desc.object_name == statistic.object_name:
                        if desc.object_type == ObjectType.BONE:
                            skip_obj = True

                if desc.object_type == ObjectType.BONE:
                    if desc.object_name.upper() == "BIP01" and self.model_type not in (
                        AscType.MORPH_ANIM,
                        AscType.DYNAMIC_ANIM,
                        AscType.MORPH_MESH,
                    ):
                        self.model_type = AscType.DYNAMIC_MESH
                    self.num_bones += 1
                elif desc.object_type == ObjectType.SLOT:
                    self.num_slots += 1
                elif desc.object_type == ObjectType.MESH:
                    self.num_meshes += 1
                elif desc.object_type == ObjectType.DUMMY: #TODO: There are interesting things behind this...
                    skip_obj = True
                else:
                    skip_obj = True
            elif "*NODE_PARENT" in line:
                parent_name = line.split('"')[1]
                parent_found = any(saved_obj_desc.object_name == parent_name for saved_obj_desc in self.objects_stats)
                if not parent_found:
                    parent_name = ""
                desc.parent_name = parent_name
            elif "*NODE_TM" in line:
                self.read_object_transform(desc)
            elif "*MESH_ANIMATION " in line:
                self.read_morphmesh_animation(desc)
            elif "*MESH " in line:
                self.read_mesh(desc)
            elif "*MESH_SOFTSKIN " in line:
                self.model_type = AscType.DYNAMIC_MESH
                self.read_mesh(desc)
            elif "*MATERIAL_REF" in line:
                desc.material_reference = int(line.split()[1])
            elif "*TM_ANIMATION" in line:
                self.read_animation_transform(desc)

    def read_softskin_data(self):
        self.model_type = AscType.DYNAMIC_MESH
        object_name = self.data.pop()
        obj_index = next((i for i, desc in enumerate(self.objects_stats) if desc.object_name == object_name), None)
        if obj_index:
            desc = self.objects_stats[obj_index]
            num_verts = int(self.data.pop())
            desc.softskin.SetNumVerts(num_verts)
            for i in range(num_verts):
                line = self.data.pop()
                if line.startswith("}"):
                    return
                words = line.split('"')
                num_weights = int(words.pop(0))
                skin_vert = desc.softskin.verts[i]
                skin_vert.SetNumWeights(num_weights)
                assert num_weights == len(words) // 2, "Softskin weightdata is invalid!"  # fmt: skip
                for j in range(num_weights):
                    g = j * 2
                    skin_vert.bones[j] = words[g]  # node_name
                    skin_vert.weights[j] = float(words[g + 1])

    def read_morphmesh_animation(self, desc: TObjectDesc):
        self.model_type = AscType.MORPH_ANIM
        morph_track = desc.morph_track
        num_samples = self.time_transform.max_frame_in_file - self.time_transform.min_frame_in_file + 1
        morph_track.SetNumSamples(num_samples)
        for i in range(num_samples):
            while len(self.data) > 0:
                line = self.data.pop()
                if line.startswith("}"):
                    break
                elif "*MESH " in line:
                    while len(self.data) > 0:
                        line = self.data.pop()
                        if line.startswith("}"):
                            break
                        elif "*MESH_VERTEX_LIST {" in line:
                            verts = list()
                            while len(self.data) > 0:
                                line = self.data.pop()
                                if line.startswith("}"):
                                    break
                                words = line.split()
                                x, y, z = float(words[2]), float(words[3]), float(words[4])  # location
                                verts.append(Vector((x, y, z)))
                            morph_track.sample_verts[i] = verts

    def read_animation_transform(self, desc: TObjectDesc):
        self.model_type = AscType.DYNAMIC_ANIM
        pos_track = desc.position_track
        rot_track = desc.rotation_track
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                return
            elif "*CONTROL_POS_TRACK {" in line:
                while len(self.data) > 0:
                    line = self.data.pop()
                    if line.startswith("}"):
                        break
                    words = line.split()

                    assert "*CONTROL_POS_SAMPLE" in words.pop(0) and len(words) == 4, "Animation position track is invalid!"  # fmt: skip

                    # words[0] is tickrate, unused
                    x, y, z = float(words[1]), float(words[2]), float(words[3])  # location
                    pos_track.sample_positions.append(Vector((x, y, z)))
            elif "*CONTROL_ROT_TRACK {" in line:
                while len(self.data) > 0:
                    line = self.data.pop()
                    if line.startswith("}"):
                        break
                    words = line.split()
                    assert "*CONTROL_ROT_SAMPLE" in words.pop(0) and len(words) == 5, "Animation rotation track is incomplete!"  # fmt: skip
                    # words[0] is tickrate, unused
                    x, y, z, angle = float(words[1]), float(words[2]), float(words[3]), float(words[4])  # axis + angle
                    rot_track.sample_axes.append(Vector((x, y, z)))
                    rot_track.sample_angles.append(angle)

    def read_object_transform(self, desc: TObjectDesc):
        transform = Matrix()
        axis = None
        angle = None
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                # TODO: Seems like for most of the asc models, transform rows are not set properly at the moment, scale part is borked.
                # Partial fix - For now let's just use axis, angle + translation part to rebuild the transform for all objects
                if axis and angle:
                    rotation = Matrix.Rotation(angle, 4, axis)
                    translation = get_translation_part(transform)
                    transform = rotation @ translation_matrix(translation)

                desc.transform = transform
                return
            elif "*TM_ROW0" in line:
                set_row0(transform, Vector((float(x) for x in line.split()[1:])))
            elif "*TM_ROW1" in line:
                set_row1(transform, Vector((float(x) for x in line.split()[1:])))
            elif "*TM_ROW2" in line:
                set_row2(transform, Vector((float(x) for x in line.split()[1:])))
            elif "*TM_ROW3" in line:
                set_translation_part(transform, Vector((float(x) for x in line.split()[1:])))
            elif "*TM_ROTAXIS" in line:
                axis = Vector((float(x) for x in line.split()[1:]))
            elif "*TM_ROTANGLE" in line:
                angle = float(line.split()[1])

    def read_mesh(self, desc: TObjectDesc):
        mesh_desc = desc.mesh_description
        while len(self.data) > 0:
            line = self.data.pop()
            if line.startswith("}"):
                return
            elif "*MESH_NUMVERTEX" in line:
                mesh_desc.num_vertex = int(line.split()[1])
            elif "*MESH_NUMTVERTEX" in line:
                mesh_desc.num_tvertex = int(line.split()[1])
            elif "*MESH_NUMFACES" in line:
                mesh_desc.num_faces = int(line.split()[1])
            elif "*MESH_NUMTVFACES" in line:
                mesh_desc.num_tvfaces = int(line.split()[1])
            elif "*MESH_VERTEX_LIST" in line:
                matrix_world_to_local = desc.transform.inverted_safe()
                for x in range(mesh_desc.num_vertex):
                    words = self.data.pop().split()
                    x, y, z = float(words[2]), float(words[3]), float(words[4])
                    mesh_desc.verts.append(Vector((x, y, z)) @ matrix_world_to_local)
                self.data.pop()
            elif "*MESH_TVERTLIST" in line:
                for x in range(mesh_desc.num_tvertex):
                    words = self.data.pop().split()
                    u, v = float(words[2]), float(words[3])
                    mesh_desc.tverts.append(Vector((u, v)))
                self.data.pop()
            elif "*MESH_FACE_LIST" in line:
                for x in range(mesh_desc.num_faces):
                    words = self.data.pop().split()
                    mesh_desc.faces.append([int(words[3]), int(words[5]), int(words[7])])
                    mesh_desc.edge_vis.append([bool(words[9]), bool(words[11]), bool(words[13])])
                    # !!! sometimes there's a skipped data in *MESH_SMOOTHING (len(words) == 17, not 18), so let's pick the last one by reverse index
                    mesh_desc.face_material_ids.append(int(words[-1]))
                self.data.pop()
            elif "*MESH_TFACELIST" in line:
                for x in range(mesh_desc.num_tvfaces):
                    words = self.data.pop().split()
                    x, y, z = int(words[2]), int(words[3]), int(words[4])
                    mesh_desc.tvfaces.append((x, y, z))
                self.data.pop()

    # ------------------------------------------------------------------------------------------
    # --------------------------------------Model Creation--------------------------------------
    # ------------------------------------------------------------------------------------------

    # -----------------------------------------Helpers------------------------------------------

    def find_object(self, name, with_prefix=True):
        try:
            return self.imported_objects[name]
        except KeyError:
            if with_prefix:
                return find_object_by_name(self.model_prefix + name)
            else:
                return find_object_by_name(name)

    def is_hierarchy_animated(self, start_obj=None):
        for child in get_children(start_obj):
            if has_transform_animation(child) or has_vertex_animation(child):
                yield True
            yield from self.is_hierarchy_animated(child)
        yield False

    def link_imported_model_to_object(self, new_parent_name):
        new_parent = find_object_by_name(new_parent_name)
        for obj in self.imported_objects.values():
            if obj and get_parent(obj) is None:
                set_parent(obj, new_parent)
                set_transform(obj, get_transform(new_parent) @ get_transform(obj))

    def clear_animation_data(self):
        for obj in bpy.data.objects:
            if obj and obj.name.startswith(self.model_prefix):
                # morphmesh
                if isinstance(obj.data, bpy.types.Mesh):
                    if (
                        obj.data
                        and obj.data.shape_keys
                        and obj.data.shape_keys.animation_data
                        and obj.data.shape_keys.animation_data.action
                    ):
                        bpy.data.actions.remove(obj.data.shape_keys.animation_data.action)
                # softskin
                elif obj.animation_data and obj.animation_data.action:
                    bpy.data.actions.remove(obj.animation_data.action)

    def clear_meshes(self):
        for obj in bpy.data.objects:
            if obj and obj.name.startswith(self.model_prefix) and "ZM_" in obj.name.upper():
                bpy.data.objects.remove(obj)

    def setup_mesh(self, prefixed_name, desc: TObjectDesc):
        obj = new_mesh_object(prefixed_name)
        if desc.parent_name:
            parent_obj = self.find_object(desc.parent_name)
            if parent_obj:
                set_parent(obj, parent_obj)

        transform = desc.transform.copy()  # DO NOT modify the desc.transform!
        set_translation_part(transform, get_translation_part(transform) * self.scale)
        set_transform(obj, transform)
        return obj

    def calculate_bone_bbox(self, desc: TObjectDesc, connected_bones=True) -> Tuple[Vector]:
        def max_dist_to_child(desc: TObjectDesc, direction: Vector) -> float:
            for child_desc in self.objects_stats:
                if child_desc.object_type == ObjectType.BONE and child_desc.parent_name == desc.object_name:
                    child_pos = get_translation_part(child_desc.transform)
                    parent_pos = get_translation_part(desc.transform)
                    return direction.dot(child_pos - parent_pos) / direction.length
            return 0

        default_size = 10
        bbox_min_point = Vector((-default_size / 2, -default_size / 2, -default_size / 2))
        bbox_max_point = Vector((default_size / 2, default_size / 2, default_size / 2))
        if connected_bones:
            transform = desc.transform.copy()
            dir_x = get_row0(transform)
            dir_y = get_row1(transform)
            dir_z = get_row2(transform)

            along_x = max_dist_to_child(desc, dir_x)
            along_y = max_dist_to_child(desc, dir_y)
            along_z = max_dist_to_child(desc, dir_z)
            opposite_y = max_dist_to_child(desc, -dir_y)
            opposite_z = max_dist_to_child(desc, -dir_z)

            # Ensure minimum dimensions
            along_x = max(along_x, default_size)
            along_y = max(along_y, default_size / 2)
            along_z = max(along_z, default_size / 2)
            opposite_y = max(opposite_y, default_size / 2)
            opposite_z = max(opposite_z, default_size / 2)

            bbox_min_point = Vector((0, -opposite_y, -opposite_z))
            bbox_max_point = Vector((along_x, along_y, along_z))
        return bbox_min_point * self.scale, bbox_max_point * self.scale

    # ------------------------------------------Object------------------------------------------

    def create_slots(self, sample_meshes_directory=None):
        if self.model_type in (AscType.DYNAMIC_MESH, AscType.STATIC_MESH):
            if sample_meshes_directory is not None:
                sample_meshes = set(Path(sample_meshes_directory).glob("*.3DS"))
            else:
                sample_meshes = None

            for desc in self.objects_stats:
                obj_name = desc.object_name
                prefixed_name = f"{self.model_prefix}{obj_name}"
                obj = None
                if desc.object_type == ObjectType.SLOT:
                    obj = self.setup_mesh(prefixed_name, desc)

                if sample_meshes:
                    file_path = None

                    for file in sample_meshes:
                        if file.stem.upper() in obj_name.upper():
                            file_path = file
                            break

                    if file_path and file_path.exists():
                        loader_3ds = Krx3dsImp(
                            filename=str(file_path), selected_slot_or_bone=prefixed_name, scale=self.scale
                        )
                        if loader_3ds:
                            for obj_3ds in loader_3ds.imported_objects:
                                name = obj_3ds.name.removeprefix(self.model_prefix)
                                self.imported_objects[name] = obj_3ds
                elif obj:
                    mesh = MeshData(obj, tool="ASC")
                    a = float(4) * self.scale
                    mesh.verts = [[0, 0, a * 1.5], [a, 0, 0], [0, a, 0], [-a, 0, 0], [0, -a, 0], [0, 0, -a]]
                    mesh.faces = [
                        [0, 1, 2],
                        [0, 2, 3],
                        [0, 3, 4],
                        [0, 4, 1],
                        [5, 2, 1],
                        [5, 3, 2],
                        [5, 4, 3],
                        [5, 1, 4],
                    ]
                    mesh.update()
                    self.imported_objects[obj_name] = obj

    def create_bones(self, bone_style, connected_bone: bool = True):
        if self.model_type in (AscType.STATIC_MESH, AscType.DYNAMIC_MESH):
            for desc in self.objects_stats:
                if desc.object_type == ObjectType.BONE:
                    armature = get_armature(self.model_prefix, bone_style) # TODO: Refactored on sceneanalyzer branch
                    bbox_min, bbox_max = self.calculate_bone_bbox(desc, connected_bone)
                    create_bone(
                        desc.object_name,
                        desc.parent_name,
                        armature_obj=armature,
                        bound_box_min=bbox_min,
                        bound_box_max=bbox_max,
                    )
                    transform = desc.transform.copy()  # DO NOT modify the desc.transform!
                    set_translation_part(transform, get_translation_part(transform) * self.scale)
                    set_transform((armature, desc.object_name), transform)
                    self.imported_objects[desc.object_name] = (armature, desc.object_name)# extended_object bullshit

    def create_meshes(self):
        materials = list()
        for material_desc_array in self.materials:
            material_array = list()
            for mat_desc in material_desc_array:
                if not any(mat_desc):
                    continue
                name, diffuse, bitmap, map_reflect = mat_desc
                material = new_material(name)
                material.diffuse_color = diffuse
                material["map_reflect"] = map_reflect
                link_texture_to_material(material, bitmap, import_file=self.filename, color_adjustment=self.color_adjustment)
                material_array.append(material)
            materials.append(material_array)

        for desc in self.objects_stats:
            if desc.object_type == ObjectType.MESH:
                new_obj_name = self.model_prefix + desc.object_name

                obj = self.setup_mesh(new_obj_name, desc)
                mesh_desc = desc.mesh_description
                mesh = MeshData(obj, tool="ASC")

                mesh.faces = list()
                mat_ref = desc.material_reference
                created_materials_len = len(materials)

                for face, face_material_id in zip(mesh_desc.faces, mesh_desc.face_material_ids):
                    sub_material = None
                    mesh.faces.append(face)

                    if 0 <= mat_ref < created_materials_len:
                        sub_materials = materials[mat_ref]
                        material_index = face_material_id
                        num_sub_materials = len(sub_materials)
                        if num_sub_materials > 0:
                            if material_index < 0:
                                material_index = 0

                            while material_index >= num_sub_materials:
                                material_index -= num_sub_materials
                            sub_material = sub_materials[material_index]
                    mesh.face_materials.append(sub_material)

                mesh.verts = [vert * self.scale for vert in mesh_desc.verts]
                mesh.tverts = mesh_desc.tverts
                mesh.tvfaces = mesh_desc.tvfaces
                mesh.update()
                self.imported_objects[desc.object_name] = obj

                # This makes scale uniform
                # I don't think this workaround is needed anymore due to using native math.
                deselect_all()
                obj.select_set(True)
                bpy.ops.object.transforms_to_deltas(mode="SCALE", reset_values=True)
                obj.select_set(False)

    def insert_softskin(self):
        for desc in self.objects_stats:
            if desc.object_type == ObjectType.MESH and desc.softskin.verts:
                obj = self.find_object(desc.object_name)

                all_bones = set(
                    self.find_object(skin_vert.bones[k])
                    for skin_vert in desc.softskin.verts
                    for k in range(len(skin_vert.weights))
                    if skin_vert.bones[k] and self.find_object(skin_vert.bones[k])
                )

                if not all_bones:
                    return

                skin_data = SkinData(obj)
                skin_data.add_bones(all_bones)

                for i, skin_vert in enumerate(desc.softskin.verts):
                    bones = []
                    weights = []
                    total_weight = sum(skin_vert.weights)
                    for j, weight in enumerate(skin_vert.weights):
                        bone = self.find_object(skin_vert.bones[j])
                        if not bone:
                            continue

                        if bone not in bones and weight != 0:
                            bones.append(bone)
                            weights.append(weight)

                    if total_weight != 0:  # normalize the ratio to flat 1.0
                        weights = [w / total_weight for w in weights]

                    skin_data.set_vert_weights(i, bones, weights)

    # ---------------------------------------Animation------------------------------------------
    def apply_anim_range(self):
        bpy.context.scene.render.fps = self.framerate
        start_frame = self.time_transform.start_frame_in_scene
        end_frame = self.time_transform.end_frame_in_scene
        if next(self.is_hierarchy_animated()):
            start_frame = max(start_frame, bpy.context.scene.frame_start)
            end_frame = min(end_frame, bpy.context.scene.frame_end)
        bpy.context.scene.frame_start = start_frame
        bpy.context.scene.frame_end = end_frame

    def transform_time(self, frame_in_file):
        return frame_in_file - self.time_transform.start_frame_in_file + self.time_transform.start_frame_in_scene

    def apply_single_transform_animation(self):
        for i, desc in enumerate(self.objects_stats):
            if not self.animated_hierarchy[i]:
                continue

            obj = self.find_object(desc.object_name)
            if not obj:
                continue

            root_parent = get_parent(obj)
            ancestor = root_parent
            while ancestor:
                name_analyzer = TNameAnalyzer(get_object_name(ancestor))
                if name_analyzer.type != ObjectType.MESH:
                    ancestor_transform = get_transform(ancestor)
                    break
                ancestor = get_parent(ancestor)

            transform = self.dynamic_anim_transforms[i]
            parent_transform = Matrix()
            for j, anim_transform in enumerate(self.dynamic_anim_transforms[:i]):
                # Always compare against hierarchy that's loaded in-scene, not some bullshit from the asc animation!
                # Following the game behaviour!
                if self.objects_stats[j].object_name == get_object_name(root_parent).removeprefix(self.model_prefix):
                    parent_transform = anim_transform
                    break

            final_transform = transform @ parent_transform.inverted_safe() @ ancestor_transform

            transform_delta = final_transform - get_transform(obj)
            delta0 = get_row0(transform_delta).length
            delta1 = get_row1(transform_delta).length
            delta2 = get_row2(transform_delta).length
            delta3 = get_translation_part(transform_delta).length

            threshold = 0.001 * self.scale
            if any(delta > threshold for delta in (delta0, delta1, delta2, delta3)):
                animate_transform(obj, final_transform)

    def calc_animation_transforms(self, frame_offset):
        if len(self.dynamic_anim_transforms) != len(self.objects_stats):
            self.dynamic_anim_transforms = [obj_desc.transform for obj_desc in self.objects_stats]

        for i, obj_desc in enumerate(self.objects_stats):
            transform = obj_desc.transform.copy()
            set_translation_part(transform, get_translation_part(transform) * self.scale)

            pos_track = obj_desc.position_track
            rot_track = obj_desc.rotation_track

            if len(pos_track.sample_positions) != 0 or len(rot_track.sample_axes) != 0:
                translation = (
                    pos_track.sample_positions[frame_offset] * self.scale
                    if len(pos_track.sample_positions) != 0
                    else get_translation_part(transform)
                )
                axis = rot_track.sample_axes[frame_offset] if len(rot_track.sample_axes) != 0 else None
                angle = rot_track.sample_angles[frame_offset] if len(rot_track.sample_angles) != 0 else None

                rotation = (
                    mathutils.Quaternion(axis, angle)
                    if axis is not None and angle is not None
                    else transform.decompose()[1]
                )
                transform = rotation.to_matrix().to_4x4() @ translation_matrix(translation)
            else:
                parent_name = obj_desc.parent_name
                if parent_name:
                    for j, parent_transform in enumerate(self.dynamic_anim_transforms[:i]):
                        if self.objects_stats[j].object_name == parent_name:
                            start_parent_transform = self.objects_stats[j].transform
                            set_translation_part(
                                start_parent_transform, get_translation_part(start_parent_transform) * self.scale
                            )
                            transform = transform @ start_parent_transform.inverted_safe() @ parent_transform
                            break

            self.dynamic_anim_transforms[i] = transform

    def find_animated_objects(self):
        animated_map = {}

        for desc in self.objects_stats:
            if desc.object_type in (ObjectType.BONE, ObjectType.SLOT):
                animated_map[desc.object_name] = (
                    len(desc.position_track.sample_positions) != 0 or len(desc.rotation_track.sample_axes) != 0
                )

        self.animated_hierarchy = [animated_map.get(desc.object_name, False) for desc in self.objects_stats]

    def apply_morphmesh_animation(self):
        if self.model_type == AscType.MORPH_ANIM:
            self.apply_anim_range()
            start = self.time_transform.start_frame_in_file
            end = self.time_transform.end_frame_in_file
            points = None
            for file_frame in range(start, end + 1):
                bpy.context.scene.frame_set(self.transform_time(file_frame))
                for desc in self.objects_stats:
                    try: # Skip if there's no morph_track data
                        points = desc.morph_track.sample_verts[file_frame - self.time_transform.min_frame_in_file]
                    except IndexError:
                        continue

                    obj = self.find_object(self.model_prefix, with_prefix=False) # NOT REALLY A MODEL PREFIX, BUT COMPATIBLE MORPHMESH NAME, WILL BE CHANGED LATER WHEN SCENE ANALYZER GETS REWRITTEN
                    if not obj:
                        continue

                    mat_world_to_local = desc.transform.inverted_safe()
                    if points:
                        for j, location in enumerate(points):
                            points[j] = (location @ mat_world_to_local) * self.scale
                        animate_vertices(obj, points)

            bpy.context.scene.frame_set(self.time_transform.start_frame_in_scene)

    def apply_transform_animation(self):
        if self.model_type == AscType.DYNAMIC_ANIM:
            self.apply_anim_range()
            self.find_animated_objects()
            start = self.time_transform.start_frame_in_file
            end = self.time_transform.end_frame_in_file
            for file_frame in range(start, end + 1):
                bpy.context.scene.frame_set(self.transform_time(file_frame))
                self.calc_animation_transforms(file_frame - self.time_transform.min_frame_in_file)
                self.apply_single_transform_animation()

            bpy.context.scene.frame_set(self.time_transform.start_frame_in_scene)

    # --------------------------------------Post Creation---------------------------------------

    def adjust_softskin_transforms(self):
        deselect_all()
        # Shoun:
        # This should be rewritten in near future where cars fly, cancer was cured, and Gothic 3 Remake was released...
        # Anyway, (I think) Kerrax initially wanted to gather every transform before inserting TO-BE-IMPORTED mesh
        # and then reapply it, but this only works when your armature is set at pos 0,0,0 (in object mode) and pose is compatible with replaced/inserted mesh.
        # Feature TODO: Pose Import
        for desc in self.objects_stats:
            if desc.object_type == ObjectType.MESH:  # Fix meshes
                obj = self.find_object(desc.object_name)
                if obj:
                    transform = get_transform(obj)
                    parent = get_parent(obj)
                    parent_transform = get_transform(parent)
                    set_translation_part(transform, get_translation_part(parent_transform))
                    set_transform(obj, transform)
                    if is_valid_tuple_instance(parent):
                        parent_armature, _ = parent
                        in_scene_scale_coef = parent_armature.get("scale_coefficient", 0.01)  # 0.01 as fallback
                    else:
                        in_scene_scale_coef = parent.get("scale_coefficient", 0.01)

                    obj.scale *= in_scene_scale_coef / self.scale
                    obj.select_set(True)
            if desc.object_name.upper() == "BIP01":  # We need to pinpoint the armature
                obj = self.find_object(desc.object_name)
                if is_valid_tuple_instance(obj):
                    armature, _ = obj
                    armature.scale *= 100 * self.scale  # resize to scale of RECENT import, will affect ALL children
                    armature.select_set(True)

        # This makes scale uniform
        # I don't think this workaround is needed anymore due to using native math.
        bpy.ops.object.transforms_to_deltas(mode="SCALE", reset_values=True)
        deselect_all()

    def setup_scene_visibility(self, slot_transparency=1):
        for obj in self.imported_objects.values():
            if not obj:
                continue
            type = TNameAnalyzer(get_object_name(obj)).type
            if type == ObjectType.BONE:  # Bones
                show(obj, True)
                set_renderable(obj, False)
                set_box_mode(obj, False)
            # Slots and dummies - IMPORTANT: Dummies are created at a runtime in 3ds importer
            elif type in (ObjectType.SLOT, ObjectType.DUMMY):
                show(obj, True)
                set_renderable(obj, False)
                set_box_mode(obj, False)

                obj.data.materials.clear()
                material = new_material(f"{self.model_prefix}ZS_SLOT")
                obj.data.materials.append(material)

                if slot_transparency:
                    material.roughness = 1
                    material.diffuse_color[3] = slot_transparency
                    material.use_nodes = True
                    node = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
                    node.inputs["Alpha"].default_value = slot_transparency
                    material.blend_method = "BLEND"
            elif type == ObjectType.MESH:  # Meshes
                show(obj, True)
                set_renderable(obj, True)
                set_box_mode(obj, False)

    def save_metadata(self):
        for obj in self.imported_objects.values():
            name = get_object_name(obj)
            if "BIP01" in name.upper() and is_valid_tuple_instance(obj):  # Pinpoint the armature
                armature, _ = obj
                armature["scale_coefficient"] = self.scale
                break


def load_asc(
    asc,
    scene_mode,
    selected_slot,
    selected_bone,
    bone_style,
    slot_transparency,
    connect_bones,
    use_sample_dir,
    sample_meshes_directory,
    start_frame_in_file,
    end_frame_in_file,
    start_frame_in_scene,
    end_frame_in_scene,
):
    if scene_mode == SceneMode.REPLACE:
        reset_scene()

    # TODO: Create ALL objects on scene first, then parent them...
    # STATIC: Bones > Meshes > Slots
    # DYNAMIC: Bones > Slots > Meshes
    # ^^^And this limitation is due to antifeature of merging softskins...

    if asc.model_type in (AscType.MORPH_MESH, AscType.STATIC_MESH):
        asc.create_bones(bone_style, connected_bone=connect_bones)
        asc.create_meshes()

        if use_sample_dir:
            asc.create_slots(sample_meshes_directory=sample_meshes_directory)
        else:
            asc.create_slots()
    elif asc.model_type == AscType.DYNAMIC_MESH:
        if scene_mode not in (SceneMode.MERGE_SOFTSKIN_MESH, SceneMode.REPLACE_SOFTSKIN_MESH):
            asc.create_bones(bone_style, connected_bone=connect_bones)

            if use_sample_dir:
                asc.create_slots(sample_meshes_directory=sample_meshes_directory)
            else:
                asc.create_slots()

        if scene_mode == SceneMode.MERGE_SOFTSKIN_MESH:
            asc.clear_meshes()

        asc.create_meshes()
        asc.insert_softskin()
    elif asc.model_type in (AscType.DYNAMIC_ANIM, AscType.MORPH_ANIM):
        if scene_mode == SceneMode.REPLACE_ANIM:
            asc.clear_animation_data()

        frame_offset = 0
        if start_frame_in_file is not None and start_frame_in_file < 0:
            frame_offset = 1000

        # all of those might be 0, check against None!
        if start_frame_in_file is not None:
            asc.time_transform.start_frame_in_file = start_frame_in_file
        if end_frame_in_file is not None:
            asc.time_transform.end_frame_in_file = end_frame_in_file
        if start_frame_in_scene is not None:
            asc.time_transform.start_frame_in_scene = start_frame_in_scene + frame_offset
        if end_frame_in_scene is not None:
            asc.time_transform.end_frame_in_scene = end_frame_in_scene + frame_offset

        asc.apply_morphmesh_animation()
        asc.apply_transform_animation()

    if scene_mode == SceneMode.LINK_SLOT_TO_OBJECT:
        asc.link_imported_model_to_object(selected_slot)
    elif scene_mode == SceneMode.LINK_BONE_TO_OBJECT:
        asc.link_imported_model_to_object(selected_bone)

    if scene_mode in (SceneMode.MERGE_SOFTSKIN_MESH, SceneMode.REPLACE_SOFTSKIN_MESH):
        asc.adjust_softskin_transforms()

    asc.setup_scene_visibility(slot_transparency=slot_transparency)
    # save metadata as custom properties into the scene
    asc.save_metadata()


# The interactive importer lives in operators.py (BatAscImpGUI).

DEFAULT_SAMPLE_MESH_DIR = str(PLUGIN_ROOT / "_Samples" / "slot_meshes")

# For calling outside KrxImpExp module
# This is barebones, you have to gather data from sceneAnalyzer to parse this yourself
# It's being done this way to give scripters flexibility (Because if you're doing it from this side, then you know the deal.)
def BatAscImp(
    filename: str,
    sample_meshes_directory: str = None,
    parser_handle: ASCParser = None,
    scene_mode: int = None,
    model_prefix: str = None,
    scale: float = 0.01,
    connect_bones: bool = True,
    bone_style: str = "STICK",
    slot_transparency: float = 1,
    selected_slot: str = None,
    selected_bone: str = None,
    start_frame_in_file: float = None,
    end_frame_in_file: float = None,
    start_frame_in_scene: float = None,
    end_frame_in_scene: float = None,
):
    if not parser_handle:
        asc = ASCParser(filename=filename)
    else:
        asc = parser_handle

    asc.scale = scale
    asc.model_prefix = model_prefix

    if sample_meshes_directory is True:
        sample_meshes_directory = DEFAULT_SAMPLE_MESH_DIR
        use_sample_meshes = True
    elif isinstance(sample_meshes_directory, str):
        use_sample_meshes = True
    else:
        use_sample_meshes = False

    load_asc(
        asc,
        scene_mode,
        selected_slot,
        selected_bone,
        bone_style,
        slot_transparency,
        connect_bones,
        use_sample_meshes,
        sample_meshes_directory,
        start_frame_in_file,
        end_frame_in_file,
        start_frame_in_scene,
        end_frame_in_scene,
    )
