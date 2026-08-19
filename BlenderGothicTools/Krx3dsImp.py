from dataclasses import dataclass
from typing import Dict, List

import bpy.types
from mathutils import Matrix

from .file import TFile
from .helpers import SLOTS_DATACLASS
from .material import link_texture_to_material, new_material
from .mesh import MeshData, new_mesh_object
from .object import (
    delete_object,
    find_object_by_name,
    get_parent,
    get_transform,
    is_valid_tuple_instance,
    set_parent,
    set_transform,
)


@dataclass(**SLOTS_DATACLASS)
class Chunk3DSReader:
    position: int = 0
    size: int = 0
    id: int = 0
    data_size: int = 0
    file: TFile = None

    def __enter__(self):
        self.position = self.file.GetPos()
        self.id = self.file.ReadUnsignedShort()
        self.size = self.file.ReadUnsignedLong()
        self.data_size = 0
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.file.SetPos(self.position + self.size)

    @property
    def subchunk_size(self):
        return self.size - self.data_size - 6


class T3DSFileLoader:
    __slots__ = (
        "__scale",
        "__file",
        "__filename",
        "_remove_sectored_materials",
        "color_adjustment",
        "__imported_materials",
        "imported_objects",
        "mesh_data",
    )

    def __iter__(self):
        yield "scale_coefficient", self.__scale

    def __init__(
        self, filename, scale, remove_sectored_materials=False, color_adjustment=None
    ):
        self.__file = TFile()
        self.__imported_materials: Dict[str, bpy.types.Material] = {}
        self.imported_objects: List[bpy.types.Object] = []
        self.mesh_data: MeshData = None
        self.__filename = filename
        self.__scale = scale
        self._remove_sectored_materials = remove_sectored_materials
        self.color_adjustment = color_adjustment
        try:
            self.__file.Open(filename, "rb")
            with Chunk3DSReader(file=self.__file) as chunk:
                if chunk.id == 0x4D4D:
                    self.__read_main_chunk(chunk)
                else:
                    raise RuntimeError(
                        f"{__name__}: File is not a 3d studio mesh.\n"
                        f'File name: "{self.__file.GetPath()}".'
                    )

            self.__file.Close()

        except RuntimeError as err:
            self.__file.Close()
            raise err

    def __read_color(self, parent_chunk: Chunk3DSReader):
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0x0010:
                    r = self.__file.ReadFloat()
                    g = self.__file.ReadFloat()
                    b = self.__file.ReadFloat()
                elif chunk.id == 0x0011:
                    r = float(self.__file.ReadUnsignedChar()) / 255
                    g = float(self.__file.ReadUnsignedChar()) / 255
                    b = float(self.__file.ReadUnsignedChar()) / 255

                r = min(1, max(0, r))  # 0..1
                g = min(1, max(0, g))  # 0..1
                b = min(1, max(0, b))  # 0..1
                return r, g, b, 1

    def __read_map(self, parent_chunk: Chunk3DSReader):
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0xA300:
                    return self.__file.ReadString()  # mapname
        return ""

    def __read_material_block(self, parent_chunk: Chunk3DSReader):
        material = None
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0xA000:
                    material_name = self.__file.ReadString().upper()
                    material = new_material(material_name)
                    self.__imported_materials[material_name] = material
                elif chunk.id == 0xA020:
                    material.diffuse_color = self.__read_color(chunk)
                elif chunk.id == 0xA200:
                    link_texture_to_material(
                        material,
                        self.__read_map(chunk),
                        import_file=self.__filename,
                        color_adjustment=self.color_adjustment,
                    )

    def __read_vertices_list(self):
        num_verts = self.__file.ReadUnsignedShort()

        self.mesh_data.verts = []
        for i in range(num_verts):
            x = self.__file.ReadFloat() * self.__scale
            y = self.__file.ReadFloat() * self.__scale
            z = self.__file.ReadFloat() * self.__scale

            self.mesh_data.verts.append([x, y, z])

    def __create_tv_faces(self):
        if not self.mesh_data.tverts:
            return

        self.mesh_data.tvfaces = [(*face,) for face in self.mesh_data.faces]

    def __read_faces_material_list(self):
        material_name = self.__file.ReadString()
        try:
            material = self.__imported_materials[material_name.upper()]
        except KeyError:
            material = None
        num_entries = self.__file.ReadUnsignedShort()
        num_faces = len(self.mesh_data.faces)
        for i in range(num_entries):
            face_index = self.__file.ReadUnsignedShort()
            if face_index >= num_faces:
                raise RuntimeError(
                    f"{__name__}: "
                    f"Face index is out of range while reading faces material list.\n"
                    f"Face index: {face_index} (Allowable range: 0..{num_faces - 1}).\n"
                    f'File name: "{self.__file.GetPath()}".'
                )
            if material is not None:
                self.mesh_data.face_materials[face_index] = material

    def __read_faces_description(self, parent_chunk: Chunk3DSReader):
        num_faces = self.__file.ReadUnsignedShort()

        self.mesh_data.faces = []
        num_verts = len(self.mesh_data.verts)
        for i in range(num_faces):
            v0 = self.__file.ReadUnsignedShort()
            v1 = self.__file.ReadUnsignedShort()
            v2 = self.__file.ReadUnsignedShort()
            _ = self.__file.ReadUnsignedShort()  # flags
            verr = max(v0, v1, v2)
            if verr >= num_verts:
                raise RuntimeError(
                    f"{__name__}: "
                    f"Vertex index is out of range while reading faces description.\n"
                    f"Vertex index: {verr} (Allowable range: 0..{num_verts - 1}).\n"
                    f'File name: "{self.__file.GetPath()}".'
                )

            self.mesh_data.faces.append([v0, v1, v2])
            self.mesh_data.face_materials.append(None)
            # _ = (flags & 0x01) != 0  # vis_ca
            # _ = (flags & 0x02) != 0  # vis_bc
            # _ = (flags & 0x04) != 0  # vis_ab

        self.__create_tv_faces()

        parent_chunk.data_size = 2 + 8 * num_faces
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0x4130:
                    self.__read_faces_material_list()

    def __read_mapping_coords(self):
        num_t_verts = self.__file.ReadUnsignedShort()

        self.mesh_data.tverts = []
        for i in range(num_t_verts):
            u = self.__file.ReadFloat()
            v = self.__file.ReadFloat()
            self.mesh_data.tverts.append([u, v])

    def __read_mesh(self, parent_chunk: Chunk3DSReader, name):
        obj = new_mesh_object(name)
        set_transform(obj, Matrix())
        self.mesh_data = MeshData(obj)
        self.imported_objects.append(obj)

        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0x4110:
                    self.__read_vertices_list()
                elif chunk.id == 0x4140:
                    self.__read_mapping_coords()
                elif chunk.id == 0x4120:
                    self.__read_faces_description(chunk)

        self.mesh_data.update(remove_sectored_materials=self._remove_sectored_materials)

    def __read_object_block(self, parent_chunk: Chunk3DSReader):
        name = self.__file.ReadString()

        parent_chunk.data_size = len(name) + 1
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0x4100:
                    self.__read_mesh(chunk, name)

    def __read_3d_editor(self, parent_chunk: Chunk3DSReader):
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0xAFFF:
                    self.__read_material_block(chunk)
                elif chunk.id == 0x4000:
                    self.__read_object_block(chunk)

    def __read_main_chunk(self, parent_chunk: Chunk3DSReader):
        size_of_chunks = parent_chunk.subchunk_size
        while size_of_chunks > 0:
            with Chunk3DSReader(file=self.__file) as chunk:
                size_of_chunks = size_of_chunks - chunk.size
                if chunk.id == 0x3D3D:
                    self.__read_3d_editor(chunk)

    def ReplaceObjectWithLoaded(self, target_name):
        if len(self.imported_objects) == 0:
            return
        replacement_object = self.imported_objects[0]
        if not replacement_object:
            return

        old_object = find_object_by_name(target_name)  # We'll be replacing this one....
        if old_object is None:
            raise RuntimeError(
                f"{__name__}: Fatal Error, Object {target_name} was removed before replacement!"
            )
        valid_parent = get_parent(old_object)
        transform = get_transform(old_object)

        if not is_valid_tuple_instance(old_object):
            old_name = old_object.name
            old_mesh_name = old_object.data.name

            old_object.name = "zNukedMesh"  #
            old_object.data.name = "zNukedMesh"  #

            if old_object.animation_data:  # TODO: Test more!
                if old_object.animation_data.action:
                    replacement_object.animation_data = old_object.animation_data
                    replacement_object.animation_data.action = (
                        old_object.animation_data.action
                    )

            replacement_object.name = old_name
            replacement_object.data.name = old_mesh_name
            set_transform(replacement_object, get_transform(old_object))

            if valid_parent:
                set_parent(replacement_object, valid_parent)

            # If there are any other objects included in this 3DS files
            # insert them into scene with replacement_object being the parent.
            for imported_object in self.imported_objects[1:]:
                set_parent(imported_object, replacement_object)
                set_transform(imported_object, transform)

            delete_object(old_object)

        else:  # Or linking to bone - For ascs, this actually should be moved from here due to convoluted flow
            armature, _ = old_object
            prefix = "" if armature.name == "Armature" else armature.name  # hate this.
            inserted_obj_name = replacement_object.name.upper()
            if "BIP01" in inserted_obj_name:
                inserted_obj_name = inserted_obj_name.strip("BIP01")

            assembled_name = f"{prefix}{inserted_obj_name}"
            # This will work only on specific sample meshes! Default package has been updated with codename2.0 accordingly
            if inserted_obj_name.upper() == "HEAD":
                assembled_name = f"{prefix}DUMMY_{inserted_obj_name}"

            replacement_object.name = assembled_name
            replacement_object.data.name = assembled_name

            set_parent(replacement_object, old_object)
            set_transform(
                replacement_object, transform @ get_transform(replacement_object)
            )


# The interactive importer lives in operators.py (Krx3dsImpGUI).


# For calling outside KrxImpExp module
def Krx3dsImp(
    filename: str,
    scale: float = 0.01,
    selected_slot_or_bone: str = None,
    remove_sectored_materials: bool = True,
    color_adjustment: float = None,
):
    # Note: Do not purge orphans inside this importer, other importer depends on it!
    loader = T3DSFileLoader(
        filename,
        scale,
        remove_sectored_materials=remove_sectored_materials,
        color_adjustment=color_adjustment,
    )
    if selected_slot_or_bone:
        loader.ReplaceObjectWithLoaded(selected_slot_or_bone)
    return loader
