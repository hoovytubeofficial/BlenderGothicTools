from enum import IntFlag
from typing import List

import bpy

from .file import TFile
from .helpers import TZENArchive
from .material import link_texture_to_material, new_material
from .mesh import MeshData, new_mesh_object
from .scene import end_editmode, reset_scene, start_editmode


class MSHVersion(IntFlag):
    G1 = 0x0009
    G2 = 0x0109


class MSHChunkType(IntFlag):
    HEADER = 0xB000
    MATERIALS = 0xB020
    VERTICES = 0xB030
    UVS = 0xB040
    FACES = 0xB050
    END = 0xB060


class zenginWorldLoader:
    __slots__ = (
        "__scale_coef",
        "__file",
        "__filename",
        "__msh_version",
        "__imported_materials",
        "__current_object",
        "__current_name_in_file",
        "__current_mesh",
        "__current_name_in_scene",
    )

    def __init__(self):
        self.__scale_coef = 1.0
        self.__file = TFile()
        self.__filename = ""
        self.__msh_version = 0
        self.__imported_materials = []
        self.__current_object = None
        self.__current_name_in_file = ""
        self.__current_mesh: MeshData = None
        self.__current_name_in_scene = ""

    def __iter__(self):
        yield "scale_coefficient", self.__scale_coef

    def __read_msh_version(self):
        self.__msh_version = self.__file.ReadUnsignedShort()
        if self.__msh_version not in (MSHVersion.G1, MSHVersion.G2):
            raise RuntimeError(
                f"{__name__}: "
                f"Msh version is not supported.\n"
                f"Msh version: 0x{self.__msh_version:X}.\n"
                f'File name: "{self.__file.GetPath()}".'
            )

    def __read_materials(self, color_adjustment: float):
        zen_archive = TZENArchive()
        zen_archive.ReadHeader(self.__file)
        num_materials = self.__file.ReadUnsignedLong()
        for _ in range(num_materials):
            zen_archive.ReadString(self.__file)
            pos = self.__file.GetPos()
            zen_chunk = zen_archive.ReadChunkStart(self.__file)
            if zen_chunk.class_name != "zCMaterial":
                raise RuntimeError(
                    f"{__name__}: "
                    f'A chunk of class "zCMaterial" expected here.\n'
                    f'Position: "0x"{pos:X}.\n'
                    f'File name: "{self.__file.GetPath()}".'
                )

            name = self.__file.ReadString()
            material_stats = self.__file.ReadData("5Bf", 9)
            blue = material_stats[1] / 255.0
            green = material_stats[2] / 255.0
            red = material_stats[3] / 255.0
            texture = self.__file.ReadString()
            zen_archive.ReadChunkEnd(self.__file, zen_chunk)

            material = new_material(name)
            self.__imported_materials.append(material)
            material.diffuse_color = (red, green, blue, 1)
            link_texture_to_material(
                material,
                texture,
                import_file=self.__filename,
                color_adjustment=color_adjustment,
            )

    def __create_object(self):
        self.__current_name_in_file = self.__file.GetName()
        self.__current_object = new_mesh_object(self.__current_name_in_file)
        self.__current_mesh = MeshData(self.__current_object)

    def __read_vertices(self):
        num_verts = self.__file.ReadUnsignedLong()
        self.__current_mesh.verts = []
        pattern = "3f" * num_verts
        verts_pos = self.__file.ReadData(pattern, num_verts * 12)
        for i in range(0, len(verts_pos), 3):
            x, z, y = verts_pos[i : i + 3]
            x *= self.__scale_coef
            z *= self.__scale_coef
            y *= self.__scale_coef
            self.__current_mesh.verts.append([x, y, z])

    def __read_uv_mapping(self):
        num_t_verts = self.__file.ReadUnsignedLong()
        self.__current_mesh.tverts = []
        pattern = "6f" * num_t_verts
        size = num_t_verts * 24
        # worth mentioning is that im actually reading color_static_light as float here, as its unused
        verts_uv = self.__file.ReadData(pattern, size)
        for i in range(0, len(verts_uv), 6):
            u, v, _, _, _, _ = verts_uv[i : i + 6]
            self.__current_mesh.tverts.append([u, -v])

    def __read_faces(self):
        self.__current_mesh.faces = []
        self.__current_mesh.tvfaces = []
        num_verts = len(self.__current_mesh.verts)
        num_t_verts = len(self.__current_mesh.tverts)
        num_materials = len(self.__imported_materials)
        num_faces = self.__file.ReadUnsignedLong()
        for _ in range(num_faces):
            vert_index_0 = 0
            t_vert_index_0 = 0
            vert_index_prev = 0
            t_vert_index_prev = 0

            if self.__msh_version == MSHVersion.G1:
                face_data = self.__file.ReadData(f"2H4fHHB", 25)
            elif self.__msh_version == MSHVersion.G2:
                face_data = self.__file.ReadData(f"2H4fBHB", 24)

            material_id = face_data[0]
            if material_id >= num_materials:
                raise RuntimeError(
                    f"{__name__}: "
                    f"Material ID is out of range while reading chunk 0xB050.\n"
                    f"Material ID: {material_id} (Allowable range: 0..{num_materials - 1}).\n"
                    f'File name: "{self.__file.GetPath()}".'
                )

            material = self.__imported_materials[material_id]
            num_verts_in_face = face_data[8]
            if self.__msh_version == MSHVersion.G1:
                vert_data = self.__file.ReadData(
                    f"HL" * num_verts_in_face, 6 * num_verts_in_face
                )
            if self.__msh_version == MSHVersion.G2:
                vert_data = self.__file.ReadData(
                    f"LL" * num_verts_in_face, 8 * num_verts_in_face
                )

            vert_data = [vert_data[x : x + 2] for x in range(0, len(vert_data), 2)]

            for j in range(num_verts_in_face):
                vert_idx = vert_data[j][0]
                t_vert_idx = vert_data[j][1]

                if vert_idx >= num_verts:
                    raise RuntimeError(
                        f"{__name__}: "
                        f"Vertex index is out of range while reading chunk 0xB050.\n"
                        f"Vertex index: {vert_idx} (Allowable range: 0..{num_verts - 1}).\n"
                        f'File name: "{self.__file.GetPath()}".'
                    )

                if t_vert_idx >= num_t_verts:
                    raise RuntimeError(
                        f"{__name__}: "
                        f"Texture vertex index is out of range while reading chunk 0xB050.\n"
                        f"Texture vertex index: {t_vert_idx} (Allowable range: 0..{num_t_verts - 1}).\n"
                        f'File name: "{self.__file.GetPath()}".'
                    )

                if j == 0:
                    vert_index_0 = vert_idx
                    t_vert_index_0 = t_vert_idx

                if j >= 2:
                    self.__current_mesh.faces.append(
                        [vert_index_0, vert_index_prev, vert_idx]
                    )
                    self.__current_mesh.tvfaces.append(
                        (t_vert_index_0, t_vert_index_prev, t_vert_idx)
                    )
                    self.__current_mesh.face_materials.append(material)

                vert_index_prev = vert_idx
                t_vert_index_prev = t_vert_idx

    def ReadMSHFile(
        self,
        filename,
        scale,
        remove_sectored_materials=False,
        color_adjustment: float = None,
    ):
        try:
            if not self.__file.IsOpened():
                self.__init__()
                self.__filename = filename
                self.__scale_coef = scale
                self.__file.Open(filename, "rb")

            file_beginning = True
            while not self.__file.Eof():
                chunk_type = self.__file.ReadUnsignedShort()
                if file_beginning and chunk_type != MSHChunkType.HEADER:
                    raise RuntimeError(
                        f'{__name__}: File is not a compiled mesh.\nFile name: "{self.__file.GetPath()}".'
                    )

                file_beginning = False
                size = self.__file.ReadUnsignedLong()
                chunk_pos = self.__file.GetPos()
                if chunk_type == MSHChunkType.HEADER:
                    self.__read_msh_version()
                elif chunk_type == MSHChunkType.MATERIALS:
                    self.__read_materials(color_adjustment)
                elif chunk_type == MSHChunkType.VERTICES:
                    self.__create_object()
                    self.__read_vertices()
                elif chunk_type == MSHChunkType.UVS:
                    self.__read_uv_mapping()
                elif chunk_type == MSHChunkType.FACES:
                    self.__read_faces()
                elif chunk_type == MSHChunkType.END:
                    self.__current_mesh.update(
                        remove_sectored_materials=remove_sectored_materials
                    )
                    break

                self.__file.SetPos(chunk_pos + size)

            self.__file.Close()
        except RuntimeError as err:
            self.__file.Close()
            raise err

    def ReadZENFile(
        self,
        filename,
        scale,
        remove_sectored_materials=True,
        color_adjustment: float = None,
        import_vobs: bool = True,
    ):
        """Read a ZenGin archive: its compiled world mesh, and its VOB tree if that is
        all it has.

        Not every .zen is a level. The small ones in _work/Data/Worlds are PREFABS - a
        torch plus its flame plus its light, saved as a VOB tree with no world mesh at
        all - so a missing "MeshAndBsp" is a normal file, not a broken one."""
        try:
            if not self.__file.IsOpened():
                self.__init__()
                self.__filename = filename
                self.__scale_coef = scale
                self.__file.Open(filename, "rb")

            zen_archive = TZENArchive()
            zen_archive.ReadHeader(self.__file)
            zen_chunk = zen_archive.ReadChunkStart(self.__file)
            zen_chunk2 = zen_archive.ReadChunkStart(self.__file)

            if zen_chunk.class_name != "oCWorld:zCWorld":
                raise RuntimeError(
                    f'{__name__}: A chunk of class "oCWorld:zCWorld" wasn\'t found.\nFile name: "{self.__file.GetPath()}".'
                )
            if zen_chunk2.name != "MeshAndBsp":
                # A VOB-tree-only .zen (a prefab). Nothing is wrong with the file - it
                # simply has no level geometry, so read its props, lights and effects.
                self.__file.Close()
                if not import_vobs:
                    raise RuntimeError(
                        f'{__name__}: This archive has no world mesh (chunk "MeshAndBsp"), '
                        f'only a VOB tree.\nFile name: "{filename}".'
                    )
                from . import zen_vobs

                if not zen_vobs.is_ascii_archive(filename):
                    raise RuntimeError(
                        f'{__name__}: This archive has no world mesh, and its VOB tree is '
                        f'not ASCII - that needs the binary archive reader.\n'
                        f'File name: "{filename}".'
                    )
                zen_vobs.import_vobs(filename, scale=scale)
                return

            self.__file.SkipByOffset(
                8
            )  # L byte mesh_and_bsp_ver and L mesh_and_bsp_size

            self.ReadMSHFile(
                filename,
                scale,
                remove_sectored_materials=remove_sectored_materials,
                color_adjustment=color_adjustment,
            )

            self.__file.Close()

        except RuntimeError as err:
            self.__file.Close()
            raise err


# The interactive importer lives in operators.py (KrxMshImpGUI).


# For calling outside KrxImpExp module
def KrxMshImp(
    filename: str,
    scale: float = 0.01,
    remove_sectored_materials: bool = True,
    color_adjustment: bool = False,
):
    zenginWorldLoader().ReadMSHFile(
        filename, scale, remove_sectored_materials, color_adjustment=color_adjustment
    )
