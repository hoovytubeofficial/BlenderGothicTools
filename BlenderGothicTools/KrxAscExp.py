from dataclasses import dataclass
from typing import List

import bpy
from mathutils import Matrix

from .file import TFile, delete_file
from .helpers import (
    SLOTS_DATACLASS,
    TSceneAnalyzer,
    ObjectType,
    SkinData,
    TNameAnalyzer,
    TPosTrack,
    TRotTrack,
    TTimeTransform,
)
from .material import get_texture_name, check_diffuse_color_adjustment
from .math_utils import get_row0, get_row1, get_row2, get_translation_part, translation_matrix
from .mesh import MeshData, is_mesh_object
from .numconv import float_to_string
from .object import find_object_by_name, get_object_name, get_transform
from .scene import get_scene_filename
from .types import is_skin_object
from .numconv import truncate

@dataclass(**SLOTS_DATACLASS)
class ASCObject:
    short_name: str
    parent_short_name: str
    type_id: str
    select_flag: bool
    pos_track: TPosTrack
    rot_track: TRotTrack


class TASCFileSaver:
    __slots__ = (
        "__file",
        "__model_prefix",
        "__model_type",
        "__objects",
        "__export_animation",
        "__scale_coef",
        "__time_transform",
        "__exported_materials",
        "__current_obj",
        "__current_obj_name",
        "__current_obj_short_name",
        "__skinned_objects",
    )

    def __init__(self):
        self.__file = TFile()
        self.__model_prefix = ""
        self.__model_type = 0
        self.__objects: List[ASCObject] = []
        self.__export_animation = False
        self.__scale_coef = 1
        self.__time_transform = TTimeTransform()
        self.__exported_materials: List[bpy.types.Material] = []
        self.__current_obj = None
        self.__current_obj_name = ""
        self.__current_obj_short_name = ""
        self.__skinned_objects = []

    def __write_line(self, line):
        self.__file.WriteLine(line)

    def __write_asc_version(self):
        self.__write_line("*3DSMAX_ASCIIEXPORT\t110")

    def __write_comment(self):
        self.__write_line('*COMMENT "[ Kerrax ASCII Model Exporter ] - Feb 25 2013"')

    def __write_scene_info(self):
        self.__write_line("*SCENE {")
        first_frame = 0
        last_frame = 100
        frame_speed = 25
        ticks_per_frame = 192
        if self.__export_animation:
            first_frame = self.__time_transform.start_frame_in_file
            last_frame = self.__time_transform.end_frame_in_file
            frame_speed = bpy.context.scene.render.fps
            ticks_per_frame = 4800 // bpy.context.scene.render.fps

        self.__write_line(f'\t*SCENE_FILENAME "{get_scene_filename()}"')
        self.__write_line(f"\t*SCENE_FIRSTFRAME {first_frame}")
        self.__write_line(f"\t*SCENE_LASTFRAME {last_frame}")
        self.__write_line(f"\t*SCENE_FRAMESPEED {frame_speed}")
        self.__write_line(f"\t*SCENE_TICKSPERFRAME {ticks_per_frame}")
        self.__write_line("}")

    def __enumerate_materials(self):
        for obj in self.__objects:
            if not obj.select_flag:
                continue

            self.__current_obj_short_name = obj.short_name
            self.__current_obj_name = f"{self.__model_prefix}{self.__current_obj_short_name}"
            self.__current_obj = find_object_by_name(self.__current_obj_name)

            if not self.__current_obj:
                continue

            name_analyzer = TNameAnalyzer(self.__current_obj_name)

            if name_analyzer.type != ObjectType.MESH:
                continue

            mesh = MeshData(self.__current_obj)
            mesh.prepare_export()
            for material in mesh.face_materials:
                if material:
                    found = False
                    for exported_material in self.__exported_materials:  # ????????????????
                        if exported_material == material:  # ????????????????
                            found = True  # ????????????????
                            break

                    if not found:
                        self.__exported_materials.append(material)

    def __write_material_list(self):
        self.__enumerate_materials()
        total_count = len(self.__exported_materials)

        if total_count == 0:
            return

        self.__write_line("*MATERIAL_LIST {")
        self.__write_line("\t*MATERIAL_COUNT 1")
        self.__write_line("\t*MATERIAL 0 {")
        # *MATERIAL_CLASS and *MATERIAL_NAME "MultiMtl #0" literally mean nothing. It's here still due to compatibility
        # This is unused in zengin, but due to how this shit was written in 2009 we have to write it to file
        # so that 2.7X crybabies and 3dsmax users could import those models...
        self.__write_line('\t\t*MATERIAL_NAME "MultiMtl #0"')
        self.__write_line('\t\t*MATERIAL_CLASS "Multi/Sub-Object"')
        self.__write_line(f"\t\t*NUMSUBMTLS {total_count}")
        for i in range(total_count):
            if i < len(self.__exported_materials):
                material = self.__exported_materials[i]
                material_name = material.name
                r, g, b, _ = check_diffuse_color_adjustment(material)
                diffuse_map = get_texture_name(material)

            self.__write_line(f"\t\t*SUBMATERIAL {i}" + " {")
            self.__write_line(f'\t\t\t*MATERIAL_NAME "{material_name}"')
            self.__write_line('\t\t\t*MATERIAL_CLASS "Standard"')
            self.__write_line(f"\t\t\t*MATERIAL_DIFFUSE {truncate(r, n=3)} {truncate(g, n=3)} {truncate(b, n=3)}")

            if diffuse_map:
                self.__write_line("\t\t\t*MAP_DIFFUSE {")
                self.__write_line(f'\t\t\t\t*BITMAP "{diffuse_map}"')
                # TODO: add support for MAP_REFLECT - essentially controls reflective/shiny surfaces, tied to zEnvMappingEnabled (Gothic.ini) and zCMaterial::m_bEnvironmentalMappingStrength (Gothic API used with union)
                # self.__write_line(f'\t\t\t\t*MAP_REFLECT 0')
                self.__write_line("\t\t\t}")

            self.__write_line("\t\t}")

        self.__write_line("\t}")
        self.__write_line("}")

    @staticmethod
    def __point3_to_string(point3):
        return f"{float_to_string(point3.x)}\t{float_to_string(point3.y)}\t{float_to_string(point3.z)}"

    def __write_node_transform(self, transform: Matrix):
        translation = get_translation_part(transform)
        axis, angle = transform.to_quaternion().to_axis_angle()
        matrix = transform.to_quaternion().to_matrix().to_4x4() @ translation_matrix(translation)
        self.__write_line("\t*NODE_TM {")
        self.__write_line(f'\t\t*NODE_NAME "{self.__current_obj_short_name}"')
        self.__write_line(f"\t\t*TM_ROW0 {self.__point3_to_string(get_row0(matrix))}")
        self.__write_line(f"\t\t*TM_ROW1 {self.__point3_to_string(get_row1(matrix))}")
        self.__write_line(f"\t\t*TM_ROW2 {self.__point3_to_string(get_row2(matrix))}")
        self.__write_line(f"\t\t*TM_ROW3 {self.__point3_to_string(get_translation_part(matrix) * self.__scale_coef)}")
        self.__write_line(f"\t\t*TM_POS {self.__point3_to_string(translation * self.__scale_coef)}")
        self.__write_line(f"\t\t*TM_ROTAXIS {self.__point3_to_string(axis)}")
        self.__write_line(f"\t\t*TM_ROTANGLE {float_to_string(angle)}")
        self.__write_line("\t\t*TM_SCALE 1\t1\t1")
        self.__write_line("\t}")

    def __write_material_ref(self, material_ref):
        if material_ref != -1:
            self.__write_line(f"\t*MATERIAL_REF {material_ref}")

    def __transform_time(self, frame_in_scene):
        start_in_scene = self.__time_transform.start_frame_in_scene
        start_in_file = self.__time_transform.start_frame_in_file
        frame_in_file = frame_in_scene - start_in_scene + start_in_file
        return frame_in_file

    def __write_mesh(self):
        mesh = MeshData(self.__current_obj)
        mesh.prepare_export()
        if is_skin_object(self.__current_obj) and self.__model_type == (8 + 16):
            self.__write_line("\t*MESH_SOFTSKIN {")
            self.__skinned_objects.append(self.__current_obj)
        else:
            self.__write_line("\t*MESH {")

        self.__write_line(f"\t\t*MESH_NUMVERTEX {len(mesh.verts)}")
        self.__write_line(f"\t\t*MESH_NUMFACES {len(mesh.faces)}")

        self.__write_line("\t\t*MESH_VERTEX_LIST {")

        transform = get_transform(self.__current_obj)
        if self.__model_type == (1 + 2):
            transform = transform @ get_transform(self.__current_obj).inverted_safe()

        for i, vert in enumerate(mesh.verts):
            vert = (vert @ transform) * self.__scale_coef
            self.__write_line(f"\t\t\t*MESH_VERTEX {i}\t{self.__point3_to_string(vert)}")

        self.__write_line("\t\t}")

        determinant = transform.determinant()

        self.__write_line("\t\t*MESH_FACE_LIST {")

        for i, (face, material) in enumerate(zip(mesh.faces, mesh.face_materials)):
            vert_a, vert_b, vert_c = face

            edge = 1

            if determinant < 0:
                vert_a, vert_c = vert_c, vert_a

            material_index = 0

            if material:
                for j, exported_material in enumerate(self.__exported_materials):
                    if exported_material == material:
                        material_index = j
                        break

            line = (
                f"\t\t\t*MESH_FACE    {i}:   "
                f" A:    {vert_a}"
                f" B:    {vert_b}"
                f" C:    {vert_c} "
                f"AB:    {edge} "
                f"BC:    {edge} "
                f"CA:    {edge}"
                f"\t *MESH_SMOOTHING 0 "  # Checked the engine around, turns out this thing is unused
                f"\t*MESH_MTLID {material_index}"
            )
            self.__write_line(line)

        self.__write_line("\t\t}")

        num_t_verts = len(mesh.tverts)
        if num_t_verts and not (self.__model_type == (1 + 2) and self.__export_animation):
            self.__write_line(f"\t\t*MESH_NUMTVERTEX {num_t_verts}")
            self.__write_line("\t\t*MESH_TVERTLIST {")
            for i, t_vert in enumerate(mesh.tverts):
                u, v = t_vert
                line = f"\t\t\t*MESH_TVERT {i}" f"\t{float_to_string(u)}" f"\t{float_to_string(v)}" f"\t0"
                self.__write_line(line)

            self.__write_line("\t\t}")

        num_tv_faces = len(mesh.faces)
        if num_tv_faces and not (self.__model_type == (1 + 2) and self.__export_animation):
            self.__write_line(f"\t\t*MESH_NUMTVFACES {num_tv_faces}")
            self.__write_line("\t\t*MESH_TFACELIST {")
            for i, tvface in enumerate(mesh.tvfaces):
                t_vert_a, t_vert_b, t_vert_c = tvface

                if determinant < 0:
                    t_vert_a, t_vert_c = t_vert_c, t_vert_a

                line = f"\t\t\t*MESH_TFACE {i}" f"\t{t_vert_a}" f"\t{t_vert_b}" f"\t{t_vert_c}"
                self.__write_line(line)

            self.__write_line("\t\t}")

        self.__write_line("\t}")

    def __write_transform_animation(self, obj_index):
        if self.__model_type != (8 + 16) or not self.__export_animation or not self.__objects[obj_index].select_flag:
            return

        self.__current_obj_short_name = self.__objects[obj_index].short_name

        self.__write_line("\t*TM_ANIMATION {")
        self.__write_line(f'\t\t*NODE_NAME "{self.__current_obj_short_name}"')
        self.__write_line("\t\t*CONTROL_POS_TRACK {")

        start_in_scene = self.__time_transform.start_frame_in_scene
        end_in_scene = self.__time_transform.end_frame_in_scene
        for frame_in_scene in range(start_in_scene, end_in_scene + 1):
            frame_in_file = self.__transform_time(frame_in_scene)
            pos = (
                self.__objects[obj_index].pos_track.sample_positions[frame_in_scene - start_in_scene]
                * self.__scale_coef
            )
            line = (
                f"\t\t\t*CONTROL_POS_SAMPLE {frame_in_file * (4800 // bpy.context.scene.render.fps)}" f"\t{self.__point3_to_string(pos)}"
            )
            self.__write_line(line)

        self.__write_line("\t\t}")
        self.__write_line("\t\t*CONTROL_ROT_TRACK {")
        for frame_in_scene in range(start_in_scene, end_in_scene + 1):
            frame_in_file = self.__transform_time(frame_in_scene)
            axis = self.__objects[obj_index].rot_track.sample_axes[frame_in_scene - start_in_scene]
            angle = self.__objects[obj_index].rot_track.sample_angles[frame_in_scene - start_in_scene]
            line = (
                f"\t\t\t*CONTROL_ROT_SAMPLE {frame_in_file * (4800 // bpy.context.scene.render.fps)}"
                f"\t{self.__point3_to_string(axis)}"
                f"\t{float_to_string(angle)}"
            )
            self.__write_line(line)

        self.__write_line("\t\t}")
        self.__write_line("\t}")

    def __write_geometry_object(self, obj_index):
        self.__current_obj_short_name = self.__objects[obj_index].short_name
        self.__current_obj_name = f"{self.__model_prefix}{self.__current_obj_short_name}"
        self.__current_obj = find_object_by_name(self.__current_obj_name)
        if not self.__current_obj:
            return

        obj_type = self.__objects[obj_index].type_id
        if obj_type == 3:
            self.__write_line("*GEOMOBJECT {")
        else:
            self.__write_line("*HELPEROBJECT {")

        self.__write_line(f'\t*NODE_NAME "{self.__current_obj_short_name}"')

        parent_name = self.__objects[obj_index].parent_short_name
        if parent_name:
            self.__write_line(f'\t*NODE_PARENT "{parent_name}"')

        transform = get_transform(self.__current_obj)
        if self.__model_type == (1 + 2):
            transform = Matrix()

        self.__write_node_transform(transform)

        if obj_type == 3 and is_mesh_object(self.__current_obj):
            self.__write_material_ref(0)
            if self.__model_type == (1 + 2) and self.__export_animation:
                self.__write_line("\t*MESH_ANIMATION {")
                old_cur_frame = bpy.context.scene.frame_current

                start_in_scene = self.__time_transform.start_frame_in_scene
                end_in_scene = self.__time_transform.end_frame_in_scene
                for frame_in_scene in range(start_in_scene, end_in_scene + 1):
                    bpy.context.scene.frame_set(frame_in_scene)
                    self.__write_mesh()

                bpy.context.scene.frame_set(old_cur_frame)
                self.__write_line("\t}")
            else:
                self.__write_mesh()

        self.__write_transform_animation(obj_index)
        self.__write_line("}")

    def __write_geom_objects(self):
        for i, obj in enumerate(self.__objects):
            if obj.select_flag or self.__export_animation and obj.type_id in (1, 2):
                self.__write_geometry_object(i)

    def __write_skin_weights(self):
        for skinned_object in self.__skinned_objects:
            self.__current_obj = skinned_object
            self.__current_obj_name = get_object_name(self.__current_obj)
            name_analyzer = TNameAnalyzer(self.__current_obj_name)
            self.__current_obj_short_name = name_analyzer.short_name
            skin_data = SkinData(self.__current_obj)
            num_verts = skin_data.get_num_verts()

            self.__write_line("*MESH_SOFTSKINVERTS {")
            self.__write_line(self.__current_obj_short_name)
            self.__write_line(str(num_verts))
            for i in range(num_verts):
                num_weights = skin_data.get_vert_num_weights(i)
                line = str(num_weights)
                bones = []
                weights = []
                total_weight = 0

                for j in range(num_weights):
                    weight = skin_data.get_vert_weight(i, j)
                    bone = skin_data.get_vert_weight_bone(i, j)
                    bone_name = get_object_name(bone)
                    name_analyzer = TNameAnalyzer(bone_name)

                    weights.append(weight)
                    bones.append(name_analyzer.short_name)

                    total_weight = total_weight + weight

                for j in range(num_weights):
                    if total_weight == 0:
                        line += f'\t"{bones[j]}"\t{float_to_string(weights[j])}'
                    else:
                        weights[j] /= total_weight
                        line += f'\t"{bones[j]}"\t{float_to_string(weights[j])}'
                self.__write_line(line)

            self.__write_line("}")

    def __retrieve_transform_animation_from_scene(self):
        if self.__model_type != (8 + 16) or not self.__export_animation:
            return

        num_samples = self.__time_transform.end_frame_in_file - self.__time_transform.start_frame_in_file + 1
        for obj in self.__objects:
            if obj.select_flag:
                obj.pos_track.SetNumSamples(num_samples)
                obj.rot_track.SetNumSamples(num_samples)

        old_current_frame = bpy.context.scene.frame_current
        start_in_scene = self.__time_transform.start_frame_in_scene
        end_in_scene = self.__time_transform.end_frame_in_scene
        for frame_in_scene in range(start_in_scene, end_in_scene + 1):
            bpy.context.scene.frame_set(frame_in_scene)
            frame_offset = frame_in_scene - start_in_scene
            for i, obj in enumerate(self.__objects):
                if not obj.select_flag:
                    continue

                self.__current_obj_short_name = obj.short_name
                self.__current_obj_name = f"{self.__model_prefix}{self.__current_obj_short_name}"
                self.__current_obj = find_object_by_name(self.__current_obj_name)

                if not self.__current_obj:
                    continue

                transform = get_transform(self.__current_obj)
                pos = get_translation_part(transform)
                axis, angle = transform.to_quaternion().to_axis_angle()
                self.__objects[i].pos_track.sample_positions[frame_offset] = pos
                self.__objects[i].rot_track.sample_axes[frame_offset] = axis
                self.__objects[i].rot_track.sample_angles[frame_offset] = angle

        bpy.context.scene.frame_set(old_current_frame)

    def WriteASCFile(
        self, filename, model_hierarchy, selected_obj_short_names, export_animation, time_transform, space_transform
    ):
        self.__init__()

        self.__model_prefix = model_hierarchy.model_prefix
        self.__model_type = model_hierarchy.model_type
        obj_short_names = model_hierarchy.objects
        parent_short_names = model_hierarchy.object_parents
        obj_types = model_hierarchy.object_types

        self.__objects = [
            ASCObject(short_name, parent_short_names[i], obj_types[i], False, TPosTrack(), TRotTrack())
            for i, short_name in enumerate(obj_short_names)
        ]

        for obj in self.__objects:
            if obj.short_name in selected_obj_short_names:
                obj.select_flag = True

        self.__export_animation = export_animation
        self.__scale_coef = space_transform
        self.__time_transform = time_transform
        try:
            self.__file.Open(filename, "wt")
            self.__write_asc_version()
            self.__write_comment()
            self.__write_scene_info()
            self.__write_material_list()
            self.__retrieve_transform_animation_from_scene()
            self.__write_geom_objects()
            self.__write_skin_weights()
            self.__file.Close()

        except RuntimeError as err:
            self.__file.Close()
            delete_file(filename)
            raise err

# The interactive exporter lives in operators.py (KrxAscExpGUI).


# For calling outside KrxImpExp module
def KrxAscExp(
    filename_param,
    selected_prefix_param: str = None,
    start_frame_in_scene: int = None,
    end_frame_in_scene: int = None,
    start_frame_in_file: int = None,
    end_frame_in_file: int = None,
    selected_objects_param: list = None,
    scale: float = 100,
    *,
    export_anim: bool = False,
):
    saver = TASCFileSaver()
    sceneAnalyzer = TSceneAnalyzer()
    selected_prefix = None
    selected_objects = None
    selected_prefixes = sceneAnalyzer.selected_prefixes

    if (len(selected_prefixes)) == 0:
        selected_prefixes = sceneAnalyzer.scene_prefixes

    if (len(selected_prefixes)) != 0:
        selected_prefix = selected_prefixes[0]
        selected_objects = sceneAnalyzer.get_model_hierarchy_by_prefix(selected_prefix).objects

    time_transform = TTimeTransform()
    time_transform.min_frame_in_scene = bpy.context.scene.frame_start
    time_transform.max_frame_in_scene = bpy.context.scene.frame_end
    time_transform.min_frame_in_file = -32768
    time_transform.max_frame_in_file = 32767

    if not start_frame_in_scene:
        start_frame_in_scene = bpy.context.scene.frame_start
    if not end_frame_in_scene:
        end_frame_in_scene = bpy.context.scene.frame_end
    if not start_frame_in_file:
        start_frame_in_file = bpy.context.scene.frame_start
    if not end_frame_in_file:
        end_frame_in_file = bpy.context.scene.frame_end

    time_transform.start_frame_in_scene = start_frame_in_scene
    time_transform.end_frame_in_scene = end_frame_in_scene
    time_transform.start_frame_in_file = start_frame_in_file
    time_transform.end_frame_in_file = end_frame_in_file

    if selected_prefix_param:
        selected_prefix = selected_prefix_param

    if selected_objects_param:
        selected_objects = selected_objects_param

    model_hierarchy = sceneAnalyzer.get_model_hierarchy_by_prefix(selected_prefix)
    saver.WriteASCFile(filename_param, model_hierarchy, selected_objects, export_anim, time_transform, scale)
