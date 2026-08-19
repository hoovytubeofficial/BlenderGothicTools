from dataclasses import dataclass
from typing import List

import bpy.types

from .file import TFile, delete_file
from .helpers import TSceneAnalyzer, SLOTS_DATACLASS
from .material import MatLibParser, get_texture_name, check_diffuse_color_adjustment
from .mesh import MeshData, UVIndexes, is_mesh_object
from .object import get_children, get_object_name, get_transform
from .numconv import truncate


###################
# Data Structures #
###################
# Taken from: https://projects.blender.org/blender/blender-addons/src/branch/main/io_scene_3ds/export_3ds.py
# Some of the chunks that we will export
# >----- Primary Chunk, at the beginning of each file
PRIMARY = 0x4D4D

# >----- Main Chunks
VERSION = 0x0002  # This gives the version of the .3ds file

# >----- sub defines of OBJECTINFO
OBJECTINFO = 0x3D3D  # Main mesh object chunk before material and object information
MESHVERSION = 0x3D3E  # This gives the version of the mesh
MATERIAL = 0xAFFF  # This stored the texture info
OBJECT = 0x4000  # This stores the faces, vertices, etc...

# >------ sub defines of MATERIAL
MATNAME = 0xA000  # This holds the material name
MATDIFFUSE = 0xA020  # This holds the color of the object/material

# >------ sub defines of MAT_MAP
MAT_DIFFUSEMAP = 0xA200  # This is a header for a new diffuse texture
MAT_MAP_FILE = 0xA300  # This holds the file name of a texture

RGB1 = 0x0011  # RGB int Color1
MASTERSCALE = 0x0100  # Master scale factor

# >------ sub defines of OBJECT
OBJECT_MESH = 0x4100  # This lets us know that we are reading a new object

# >------ sub defines of OBJECT_MESH
OBJECT_VERTICES = 0x4110  # The objects vertices
OBJECT_FACES = 0x4120  # The objects faces
OBJECT_MATERIAL = 0x4130  # This is found if the object has a material, either texture map or color
OBJECT_UV = 0x4140  # The UV texture coordinates


class Chunk3DSWriter:
    __slots__ = ("__chunk_pos", "_file")

    def __init__(self, file, chunk_id):
        self._file: TFile = file
        self.__chunk_pos = self._file.GetPos()
        self._file.WriteUnsignedShort(chunk_id)
        self._file.WriteUnsignedLong(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        chunk_end_pos = self._file.GetPos()
        chunk_size = chunk_end_pos - self.__chunk_pos
        self._file.SetPos(self.__chunk_pos + 2)
        self._file.WriteUnsignedLong(chunk_size)
        self._file.SetPos(chunk_end_pos)


@dataclass(**SLOTS_DATACLASS)
class ProcessedObject:
    name: str
    obj: bpy.types.Object
    mesh_data: MeshData

    def __iter__(self):
        return iter((self.name, self.obj, self.mesh_data))


class T3DSFileSaver:
    __slots__ = (
        "__file",
        "__scale_coef",
        "matlib",
        "__use_local_cs",
        "__exported_objects",
        "__exported_materials_for_each_obj",
        "_total_material_list",
        "_current_data",
        "__german_char_exception",
    )

    def __init__(
        self,
        filename,
        selected_objects: list,
        use_local_cs,
        scale,
        object_data: tuple,
        matlib_filepath,
        matlib_autonaming=False,
        german_char_exception=False,
    ):
        processed_objects, processed_materials = object_data

        self.__use_local_cs = use_local_cs
        self.__scale_coef = scale
        self.__german_char_exception = german_char_exception

        self.matlib = MatLibParser(matlib_filepath, autonaming=matlib_autonaming)
        self.__file = TFile()

        self._current_data = dict()

        self.__exported_objects: List[ProcessedObject] = list()
        self.__exported_materials_for_each_obj: List[List[bpy.types.Material]] = list()

        total_materials = set()
        for idx, (name, obj, mesh_data) in enumerate(processed_objects):
            try:
                hit = selected_objects.index(name)
            except ValueError:
                hit = None

            if hit is not None:
                self.__exported_objects.append(processed_objects[idx])
                self.__exported_materials_for_each_obj.append(processed_materials[idx])
                total_materials.update(processed_materials[idx])

        self._total_material_list: List[bpy.types.Material] = sorted(
            total_materials, key=lambda material: material.name
        )

        try:
            self.__file.Open(filename, "wb")
            self.__write_main_chunk()
            self.__file.Close()

        except RuntimeError as err:
            self.__file.Close()
            delete_file(filename)
            raise err

    def __calculate_material_name(self, material):
        # This function processes material names to be used right before writing to binary
        # There are several injections to the material names based on matlib parsing, renaming texture names as materials
        # and exception to a peculiar bug regarding Gothic 1 meshes with german characters.

        matlib_materials = self.matlib.materials
        material_name = material.name
        if material_name.startswith("P:"):
            if self.__german_char_exception:
                material_name = material_name.replace("Ü", "ü").replace("Ö", "ö")
            return material_name

        tex_file_name = get_texture_name(material)
        if not tex_file_name:
            return material_name

        if matlib_materials:
            # TODO simplify!
            index_tex = -1
            for i in range(len(matlib_materials)):
                if tex_file_name.upper() == matlib_materials[i]["texture"].upper():
                    index_tex = i
                    break

            if index_tex != -1:
                index_tex_and_mat = -1
                for i in range(len(matlib_materials)):
                    if material_name.upper() == matlib_materials[i]["name"].upper():
                        if tex_file_name.upper() == matlib_materials[i]["texture"].upper():
                            index_tex_and_mat = i
                            break

                if index_tex_and_mat != -1:
                    return material_name
                else:
                    return matlib_materials[index_tex]["name"]

        if self.matlib.autonames:
            material_name = tex_file_name.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]

        if self.__german_char_exception:
            material_name = material_name.replace("Ü", "ü").replace("Ö", "ö")
        return material_name

    def __write_3ds_version(self, version_3ds):
        with Chunk3DSWriter(self.__file, VERSION):
            self.__file.WriteUnsignedLong(version_3ds)

    def __write_mesh_version(self, mesh_version):
        with Chunk3DSWriter(self.__file, MESHVERSION):
            self.__file.WriteUnsignedLong(mesh_version)

    def __write_color(self, color_chunk_id, diffuse):
        r, g, b, _ = diffuse
        with Chunk3DSWriter(self.__file, color_chunk_id):
            with Chunk3DSWriter(self.__file, RGB1):
                self.__file.WriteUnsignedChar(int(truncate(r, n=3) * 255))
                self.__file.WriteUnsignedChar(int(truncate(g, n=3) * 255))
                self.__file.WriteUnsignedChar(int(truncate(b, n=3) * 255))

    def __write_map(self, map_chunk_id, map_name):
        if not map_name:
            return

        with Chunk3DSWriter(self.__file, map_chunk_id):
            with Chunk3DSWriter(self.__file, MAT_MAP_FILE):
                self.__file.WriteString(map_name)

    def __write_material_block(self, material_name="", diffuse_color=(0, 0, 0, 1), diffuse_map_filename=""):
        with Chunk3DSWriter(self.__file, MATERIAL):
            with Chunk3DSWriter(self.__file, MATNAME):
                self.__file.WriteString(material_name)
            self.__write_color(MATDIFFUSE, diffuse_color)
            self.__write_map(MAT_DIFFUSEMAP, diffuse_map_filename)

    def __write_one_unit(self, one_unit):
        with Chunk3DSWriter(self.__file, MASTERSCALE):
            self.__file.WriteFloat(one_unit)

    def __write_vertices_list(self):
        with Chunk3DSWriter(self.__file, OBJECT_VERTICES):
            mesh_data: MeshData = self._current_data["mesh_data"]
            self.__file.WriteUnsignedShort(len(mesh_data.verts))
            for vert in mesh_data.verts:
                vert = vert @ self._current_data["transform"]

                self.__file.WriteFloat(vert.x * self.__scale_coef)
                self.__file.WriteFloat(vert.y * self.__scale_coef)
                self.__file.WriteFloat(vert.z * self.__scale_coef)

    def __write_faces_material_list(self):
        for material in self._current_data["materials"]:
            with Chunk3DSWriter(self.__file, OBJECT_MATERIAL):
                # Write true material name that's preprocessed to our needs, check __calculate_material_name for more info
                self.__file.WriteString(self.__calculate_material_name(material))

                if material:
                    entries = self._current_data["faces_index_pairs_lookup"][material.name]
                else:
                    entries = []

                self.__file.WriteUnsignedShort(len(entries))
                for entry in entries:
                    self.__file.WriteUnsignedShort(entry)

    def __write_mapping_coords(self):
        tverts = self._current_data["mesh_data"].tverts
        if not tverts:
            return

        with Chunk3DSWriter(self.__file, OBJECT_UV):
            self.__file.WriteUnsignedShort(len(tverts))
            for uv_vert in tverts:
                self.__file.WriteFloat(uv_vert[UVIndexes.U])
                self.__file.WriteFloat(uv_vert[UVIndexes.V])

    def __write_faces_description(self):
        with Chunk3DSWriter(self.__file, OBJECT_FACES):
            mesh_data: MeshData = self._current_data["mesh_data"]
            self.__file.WriteUnsignedShort(len(mesh_data.faces))
            for face in mesh_data.faces:
                v0 = face[0]
                v1 = face[1]
                v2 = face[2]
                vis_ab = True
                vis_bc = True
                vis_ca = True

                if self._current_data["transform_determinant"] < 0:
                    v0, v2 = v2, v0
                    vis_ab, vis_bc = vis_bc, vis_ab

                flags = 0
                if vis_ca:
                    flags = flags | 0x01

                if vis_bc:
                    flags = flags | 0x02

                if vis_ab:
                    flags = flags | 0x04

                self.__file.WriteUnsignedShort(v0)
                self.__file.WriteUnsignedShort(v1)
                self.__file.WriteUnsignedShort(v2)
                self.__file.WriteUnsignedShort(flags)

            self.__write_faces_material_list()

    def __write_mesh(self):
        with Chunk3DSWriter(self.__file, OBJECT_MESH):
            self.__write_vertices_list()
            self.__write_mapping_coords()
            self.__write_faces_description()

    def __write_object_block(self, index):
        faces_index_pairs_lookup = dict()
        for face_idx, material in enumerate(self.__exported_objects[index].mesh_data.face_materials):
            face_mat_name = material.name

            if face_mat_name not in faces_index_pairs_lookup:
                faces_index_pairs_lookup[face_mat_name] = list()

            faces_index_pairs_lookup[face_mat_name].append(face_idx)

        transform = get_transform(self.__exported_objects[index].mesh_data._obj)
        if self.__use_local_cs:
            transform = transform @ transform.inverted_safe()

        self._current_data = {
            "mesh_data": self.__exported_objects[index].mesh_data,
            "materials": self.__exported_materials_for_each_obj[index],
            "transform": transform,
            "transform_determinant": transform.determinant(),
            "faces_index_pairs_lookup": faces_index_pairs_lookup,
        }

        with Chunk3DSWriter(self.__file, OBJECT):
            self.__file.WriteString(self.__exported_objects[index].obj.name)
            self.__write_mesh()

    def __write_3d_editor(self):
        with Chunk3DSWriter(self.__file, OBJECTINFO):
            self.__write_mesh_version(3)
            for material in self._total_material_list:
                if material:
                    self.__write_material_block(
                        self.__calculate_material_name(material),
                        check_diffuse_color_adjustment(material),
                        get_texture_name(material),
                    )
            self.__write_one_unit(1)
            for i in range(len(self.__exported_objects)):
                self.__write_object_block(i)

    def __write_main_chunk(self):
        with Chunk3DSWriter(self.__file, PRIMARY):
            self.__write_3ds_version(3)
            self.__write_3d_editor()


def process_objects(obj, processed_objects: list, processed_materials: list):
    if obj is None:
        for child in get_children(obj):
            process_objects(child, processed_objects, processed_materials)
        return

    if is_mesh_object(obj):
        meshdata = MeshData(obj, "3DS_EXPORT")
        meshdata.prepare_export()

        processed_objects.append(ProcessedObject(get_object_name(obj), obj, meshdata))
        # Important note! obj.data.materials couldn't be used here, because there could be material assigned to material slots, but not to the faces themselfs!
        processed_materials.append(sorted(set(meshdata.face_materials), key=lambda material: material.name))

    for child in get_children(obj):
        process_objects(child, processed_objects, processed_materials)

# The interactive exporter lives in operators.py (Krx3dsExpGUI).


# For calling outside KrxImpExp module
def Krx3dsExp(
    filename: str,
    selected_objects_param: list = None,
    use_local_cs: bool = True,
    scale: float = 100,
    matlib_file_path: str = None,
    matlib_autorenaming: bool = False,
    german_char_exception: bool = False,
):
    sceneAnalyzer = TSceneAnalyzer()
    selected_objects = sceneAnalyzer.selected_meshes_by_type
    objects = sceneAnalyzer.scene_meshes_by_type
    if len(selected_objects) == 0:
        selected_objects = objects

    processed_objects = list()
    processed_materials = list()  # processed_objects index == processed_materials index!!!
    # TODO: multithread? i think it might help with perfo, but im not sure the if types would be compatible
    process_objects(None, processed_objects, processed_materials)

    if selected_objects_param:
        selected_objects = selected_objects_param

    T3DSFileSaver(
        filename,
        selected_objects,
        use_local_cs,
        scale,
        (processed_objects, processed_materials),
        matlib_file_path,
        matlib_autonaming=matlib_autorenaming,
        german_char_exception=german_char_exception,
    )
    return None
