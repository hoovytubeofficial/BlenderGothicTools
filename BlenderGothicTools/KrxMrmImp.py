from .log import klog as print  # route console output through the add-on's log tag
import struct

from mathutils import Matrix

from .file import TFile
from .helpers import TZENArchive
from .material import link_texture_to_material, new_material
from .mesh import MeshData, new_mesh_object


def _map_positions(mesh, positions):
    """Blender-space position -> the Blender vertices sitting there.

    Building the mesh splits faces, which reorders the vertex array, so a file index is
    NOT a Blender index. A position shared by several split copies maps to all of them,
    which is what keeps seams from tearing: a weight (or a morph offset) has to reach
    every copy of the vertex it belongs to."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    tree = KDTree(len(mesh.vertices))
    for index, vertex in enumerate(mesh.vertices):
        tree.insert(vertex.co, index)
    tree.balance()

    mapping = []
    for position in positions:
        target = Vector(position)
        hits = [index for _co, index, _distance in tree.find_range(target, 1.0e-4)]
        if not hits:
            _co, index, _distance = tree.find(target)
            hits = [index]
        mapping.append(hits)
    return mapping


def _map_file_vertices(mesh, base_positions, scale):
    """file vertex index -> the Blender vertices sitting at that position.

    Building the mesh splits faces, which reorders the vertex array, so a file index
    is NOT a Blender index - applying morph offsets by index scrambles the shape keys.
    The morph base positions ARE the neutral mesh, so they can be matched back by
    position; a position shared by several split vertices maps to all of them, which
    is what keeps the seams from tearing."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    tree = KDTree(len(mesh.vertices))
    for index, vertex in enumerate(mesh.vertices):
        tree.insert(vertex.co, index)
    tree.balance()

    mapping = []
    matched = set()
    for x, y, z in base_positions:
        target = Vector((x * scale, z * scale, y * scale))  # file (x, y, z) -> (x, z, y)
        hits = [index for _co, index, _distance in tree.find_range(target, 1.0e-4)]
        if not hits:
            _co, index, _distance = tree.find(target)
            hits = [index]
        mapping.append(hits)
        matched.update(hits)

    return mapping, matched


def _build_morph_shape_keys(obj, base_positions, animations, scale):
    """Turn decoded MorphMesh animation data into Blender shape keys.

    Samples are OFFSETS from the base vertex positions (verified: the S_NEUTRAL
    animation is all zeros). File axes are swizzled the same way the mesh reader
    does it: file (x, y, z) -> Blender (x, z, y)."""
    if obj is None or obj.data is None or not animations:
        return 0

    mesh = obj.data
    vertex_count = len(mesh.vertices)
    if not base_positions:
        print("morph data skipped - the file carries no base positions", level="WARN")
        return 0

    mapping, matched = _map_file_vertices(mesh, base_positions, scale)
    if len(matched) < vertex_count:
        print(
            f"morph mapping reached {len(matched)} of {vertex_count} mesh vertices - "
            f"some shape keys may be incomplete",
            level="WARN",
        )

    if mesh.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)

    created = 0
    for animation in animations:
        indices = animation["vertices"]
        samples = animation["samples"]
        frame_count = max(1, animation["frame_count"])

        for frame in range(frame_count):
            name = animation["name"] if frame_count == 1 else f"{animation['name']}_{frame:03d}"
            key_block = obj.shape_key_add(name=name, from_mix=False)
            offset = frame * len(indices)
            for i, file_index in enumerate(indices):
                if file_index >= len(mapping):
                    continue
                dx, dy, dz = samples[offset + i]
                for vertex_index in mapping[file_index]:
                    base = key_block.data[vertex_index].co
                    key_block.data[vertex_index].co = (
                        base.x + dx * scale,
                        base.y + dz * scale,
                        base.z + dy * scale,
                    )
            key_block.value = 0.0
            created += 1

    from . import log

    for animation in animations:
        log.debug(f"morph '{animation['name']}': {animation['frame_count']} frame(s), "
                  f"{len(animation['vertices'])} vertices, layer {animation['layer']}, "
                  f"speed {animation['speed']}")
    print(f"created {created} morph shape key(s) from {len(animations)} animation(s), "
          f"{len(matched)}/{vertex_count} vertices mapped by position")
    return created


class TMRMFileLoader:
    __slots__ = (
        "__scale_coef",
        "__file",
        "__filename",
        "__imported_materials",
        "__imp_objects",
        "__cur_obj",
        "__cur_name_in_file",
        "__cur_mesh",
        "__mrm_version",
        "__data_pos",
        "__data_size",
        "__num_sub_meshes",
        "__pos_sub_meshes",
        "__pos_verts",
        "__num_verts",
        "__pos_materials",
    )

    def __init__(self):
        self.__scale_coef = 1.0
        self.__file = TFile()
        self.__filename = ""
        self.__imported_materials = []
        self.__imp_objects = []
        self.__cur_obj = None
        self.__cur_name_in_file = ""
        self.__cur_mesh: MeshData = None
        self.__mrm_version = 0
        self.__data_pos = 0
        self.__data_size = 0
        self.__num_sub_meshes = 0
        self.__pos_sub_meshes = 0
        self.__pos_verts = 0
        self.__num_verts = 0
        self.__pos_materials = 0

    def __iter__(self):
        yield "scale_coefficient", self.__scale_coef

    def __create_object(self):
        self.__cur_name_in_file = self.__file.GetName()
        self.__cur_obj = new_mesh_object(self.__cur_name_in_file)
        self.__cur_mesh = MeshData(self.__cur_obj)
        self.__imp_objects.append(self.__cur_obj)

    def __read_vertices(self):
        self.__cur_mesh.verts = []
        self.__file.SetPos(self.__pos_verts)
        pattern = "3f" * self.__num_verts
        verts_pos = self.__file.ReadData(pattern, self.__num_verts * 12)
        for i in range(0, len(verts_pos), 3):
            x, z, y = verts_pos[i : i + 3]
            x *= self.__scale_coef
            z *= self.__scale_coef
            y *= self.__scale_coef
            self.__cur_mesh.verts.append([x, y, z])

    def __read_uv_mapping(self):
        self.__cur_mesh.tverts = []

        for i in range(self.__num_sub_meshes):
            self.__file.SetPos(self.__pos_sub_meshes + i * 80 + 8)
            submesh_stats = self.__file.ReadData("ll", 8)
            pos_wdg_sub_mesh = submesh_stats[0] + self.__data_pos
            num_wdg_sub_mesh = submesh_stats[1]
            for j in range(num_wdg_sub_mesh):
                self.__file.SetPos(pos_wdg_sub_mesh + j * 24 + 12)
                u, v = self.__file.ReadData("ff", 8)
                self.__cur_mesh.tverts.append([u, -v])

    def __read_faces(self):
        self.__cur_mesh.faces = []
        self.__cur_mesh.tvfaces = []
        t_vert_base = 0
        num_verts = len(self.__cur_mesh.verts)
        num_t_verts = len(self.__cur_mesh.tverts)

        for i in range(self.__num_sub_meshes):
            self.__file.SetPos(self.__pos_sub_meshes + i * 80 + 0)
            face_data = self.__file.ReadData("4l", 16)

            pos_tri_sub_mesh = face_data[0] + self.__data_pos
            num_tri_sub_mesh = face_data[1]
            pos_wdg_sub_mesh = face_data[2] + self.__data_pos
            num_wdg_sub_mesh = face_data[3]

            for j in range(num_tri_sub_mesh):
                self.__file.SetPos(pos_tri_sub_mesh + j * 6 + 0)
                wdg_idx_0 = self.__file.ReadUnsignedShort()
                self.__file.SetPos(pos_wdg_sub_mesh + wdg_idx_0 * 24 + 20)
                vert_idx_0 = self.__file.ReadUnsignedShort()
                t_vert_idx_0 = wdg_idx_0 + t_vert_base

                self.__file.SetPos(pos_tri_sub_mesh + j * 6 + 2)
                wdg_idx_1 = self.__file.ReadUnsignedShort()
                self.__file.SetPos(pos_wdg_sub_mesh + wdg_idx_1 * 24 + 20)
                vert_idx_1 = self.__file.ReadUnsignedShort()
                t_vert_idx_1 = wdg_idx_1 + t_vert_base

                self.__file.SetPos(pos_tri_sub_mesh + j * 6 + 4)
                wdg_idx_2 = self.__file.ReadUnsignedShort()
                self.__file.SetPos(pos_wdg_sub_mesh + wdg_idx_2 * 24 + 20)
                vert_idx_2 = self.__file.ReadUnsignedShort()
                t_vert_idx_2 = wdg_idx_2 + t_vert_base

                vert_err_idx = None
                if vert_idx_0 >= num_verts:
                    vert_err_idx = vert_idx_0

                if vert_idx_1 >= num_verts and not vert_err_idx:
                    vert_err_idx = vert_idx_1

                if vert_idx_2 >= num_verts and not vert_err_idx:
                    vert_err_idx = vert_idx_2

                if vert_err_idx:
                    raise RuntimeError(
                        f"{__name__}: "
                        f"Vertex index is out of range while reading multi-resolution mesh.\n"
                        f"Vertex index: {vert_err_idx} "
                        f"(Allowable range: 0..{num_verts - 1}).\n"
                        f'File name: "{self.__file.GetPath()}".'
                    )

                t_vert_err_idx = None
                if t_vert_idx_0 >= num_t_verts:
                    t_vert_err_idx = t_vert_idx_0

                if t_vert_idx_1 >= num_t_verts:
                    t_vert_err_idx = t_vert_idx_1

                if t_vert_idx_2 >= num_t_verts:
                    t_vert_err_idx = t_vert_idx_2

                if t_vert_err_idx:
                    raise RuntimeError(
                        f"{__name__}: "
                        f"Texture vertex index is out of range while reading multi-resolution mesh.\n"
                        f"Texture vertex index: {t_vert_err_idx} "
                        f"(Allowable range: 0..{num_t_verts - 1}).\n"
                        f'File name: "{self.__file.GetPath()}".'
                    )

                self.__cur_mesh.faces.append([vert_idx_0, vert_idx_1, vert_idx_2])
                self.__cur_mesh.tvfaces.append(
                    (t_vert_idx_0, t_vert_idx_1, t_vert_idx_2)
                )
                self.__cur_mesh.face_materials.append(self.__imported_materials[i])

            t_vert_base += num_wdg_sub_mesh

    def __read_materials(self, color_adjustment=None):
        self.__file.SetPos(self.__pos_materials)
        zen_archive = TZENArchive()
        zen_archive.ReadHeader(self.__file)
        for i in range(self.__num_sub_meshes):
            zen_archive.ReadString(self.__file)  # name
            pos = self.__file.GetPos()
            zen_chunk = zen_archive.ReadChunkStart(self.__file)
            if zen_chunk.class_name != "zCMaterial":
                raise RuntimeError(
                    f"{__name__}: "
                    f'A chunk of class "zCMaterial" expected here.\n'
                    f'Position: {"0x"+hex(pos)[2:].upper()}.\n'
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

    def __read_mrm_data(self, color_adjustment=None):
        self.__mrm_version = self.__file.ReadUnsignedShort()
        self.__data_size = self.__file.ReadUnsignedLong()
        self.__data_pos = self.__file.GetPos()
        self.__file.SetPos(self.__data_pos + self.__data_size)
        self.__num_sub_meshes = self.__file.ReadUnsignedChar()

        self.__pos_verts = self.__file.ReadUnsignedLong() + self.__data_pos
        self.__num_verts = self.__file.ReadUnsignedLong()
        self.__file.SkipByOffset(8)
        # _ = self.__file.ReadUnsignedLong() + self.__data_pos  # pos_t_axes
        # _ = self.__file.ReadUnsignedLong()  # num_t_axes
        self.__pos_sub_meshes = self.__file.GetPos()
        self.__pos_materials = self.__pos_sub_meshes + 80 * self.__num_sub_meshes

        self.__read_materials(color_adjustment)
        self.__create_object()
        self.__read_vertices()
        self.__read_uv_mapping()
        self.__read_faces()

    def __read_morph_animations(self):
        """Chunk 0xE030: the morph animation table.

        uint16 count, then per animation:
          name (LF-terminated) | 20 bytes of blend/duration/layer/speed | uint8 flags
          uint32 vertex_count  | uint32 frame_count
          uint32 * vertex_count   (indices into the mesh's vertices)
          float3 * vertex_count * frame_count   (per-frame OFFSETS for those vertices)
        """
        animations = []
        count = self.__file.ReadUnsignedShort()
        for _ in range(count):
            name = self.__file.ReadString(terminator=0x0A)
            blend_in, blend_out, duration = self.__file.ReadData("3f", 12)
            layer = self.__file.ReadData("i", 4)[0]
            speed = self.__file.ReadData("f", 4)[0]
            flags = self.__file.ReadUnsignedChar()
            vertex_count = self.__file.ReadUnsignedLong()
            frame_count = self.__file.ReadUnsignedLong()

            indices = list(self.__file.ReadData(f"{vertex_count}I", vertex_count * 4))
            total = vertex_count * frame_count
            flat = self.__file.ReadData(f"{total * 3}f", total * 12) if total else ()
            samples = [tuple(flat[i * 3: i * 3 + 3]) for i in range(total)]

            animations.append({
                "name": name.strip() or f"Morph{len(animations)}",
                "layer": layer,
                "blend_in": blend_in,
                "blend_out": blend_out,
                "duration": duration,
                "speed": speed,
                "flags": flags,
                "frame_count": frame_count,
                "vertices": indices,
                "samples": samples,
            })
        return animations

    def __read_softskin_weights(self, num_verts: int):
        """Chunk 0xB1FF inside an .mdm: the skinning table of one soft-skin mesh.

        uint32 weight-buffer size, then PER VERTEX of the mesh:
          uint32 weight_count, then that many of
            float weight | float3 position relative to the node | uint8 node index
        followed by the wedge-normal list (uint32 count, then count * 16 bytes; Gothic 2
        stores none) and the node list (uint16 count, then count * int32).

        The node index is a DIRECT, ZERO-BASED index into the .MDH node list - verified
        on HUM_BODY_NAKED0, whose highest vertex weights to BIP01 HEAD and lowest to
        BIP01 R FOOT, and on ORC_BODYWARRIOR, whose shoulder pads weight to the
        clavicles. One-based reads put the pads on ZS_RIGHTHAND.

        The per-weight POSITION is kept, not skipped: it is where the vertex sits in that
        bone's own space, and summing those over a vertex's weights is what actually
        produces the bind pose (see KrxManImp.skin_softskin_meshes). The engine skins from
        exactly these numbers."""
        buffer_size = self.__file.ReadUnsignedLong()
        buffer_end = self.__file.GetPos() + buffer_size

        per_vertex = []
        for _ in range(num_verts):
            count = self.__file.ReadUnsignedLong()
            entries = []
            for _ in range(count):
                weight = self.__file.ReadData("f", 4)[0]
                position = self.__file.ReadData("3f", 12)   # relative to the node
                entries.append((weight, position, self.__file.ReadUnsignedChar()))
            per_vertex.append(entries)

        self.__file.SetPos(buffer_end)
        normal_count = self.__file.ReadUnsignedLong()
        self.__file.SkipByOffset(normal_count * 16)
        node_count = self.__file.ReadUnsignedShort()
        nodes = list(self.__file.ReadData(f"{node_count}i", node_count * 4)) if node_count else []
        return per_vertex, nodes

    def ReadMDMFile(
        self,
        filename,
        space_transform,
        remove_sectored_materials=False,
        color_adjustment=None,
        allow_empty=False,
    ):
        """Read a compiled model mesh (.mdm): the skinned body of a character or monster.

        An .mdm holds two kinds of mesh, and 77 of the 173 retail files have both:

          ATTACHED (rigid) - 0xD020 lists uint16 count and that many LF-terminated BONE
            NAMES, then one 0xB100 zCProgMeshProto + empty 0xB1FF per name. These are
            parts bolted to a single bone: an orc's shoulder plates, a helmet.
          SOFT-SKIN (weighted) - 0xE100 version | 0xB100 zCProgMeshProto
            | 0xB1FF skinning table | 0xE110 end.

        The SAME 0xB1FF id terminates a rigid mesh (size 0) and carries the weight table
        of a skinned one, so only a mesh inside an 0xE100..0xE110 pair may be read as
        weights - otherwise the 77 files with attachments run off the end of the file.

        Around them sit 0xD000 version | 0xD010 the source .ASC path | 0xD030 the
        SKELETON CHECKSUM, and the file ends with 0xD040. That checksum is the same one
        the .MDH hierarchy chunk ends with, so a body mesh finds its own skeleton with no
        naming convention involved - it matches for every retail .mdm.

        The zCProgMeshProto positions are only a rough stand-in for a soft-skin mesh: 94
        of the 119 retail bodies that can be checked disagree with their own weight table,
        the dragons by 85 cm. They are close enough to build the mesh from and to match
        vertices back by position, and skin_softskin_meshes() then moves every vertex to
        where the weights actually put it.

        The same reader takes a .mdl, which is a skeleton section (0xD100 hierarchy,
        0xD110 its source path, 0xD120 end) with exactly this file glued on after it - the
        hierarchy chunks are skipped here and read by KrxManImp.read_mdh instead. Two
        retail .mdl files are skeleton-only, hence `allow_empty`.

        Returns {"objects", "attachments", "checksum", "source", "weights"}: weights maps
        a soft-skin object to [(blender vertex indices, [(weight, node index), ...]), ...]
        and attachments is [(object, bone name)]."""
        self.__init__()
        self.__filename = filename
        self.__scale_coef = space_transform

        checksum = 0
        source = ""
        pending = None
        collected = []
        attached = []
        attachment_bones = []
        in_softskin = False
        try:
            self.__file.Open(filename, "rb")
            file_beginning = True
            while not self.__file.Eof():
                chunk_type = self.__file.ReadUnsignedShort()
                if file_beginning and chunk_type not in (0xD000, 0xD100):
                    raise RuntimeError(
                        f'{__name__}: File is not a model mesh.\n'
                        f'File name: "{self.__file.GetPath()}".'
                    )
                file_beginning = False
                chunk_size = self.__file.ReadUnsignedLong()
                chunk_pos = self.__file.GetPos()

                if chunk_type == 0xD010:              # timestamps, then the source path
                    self.__file.SkipByOffset(16)
                    source = self.__file.ReadString(terminator=0x0A).strip()
                elif chunk_type == 0xD020:            # the bones the rigid parts hang on
                    count = self.__file.ReadUnsignedShort()
                    attachment_bones = [
                        self.__file.ReadString(terminator=0x0A).strip() for _ in range(count)
                    ]
                elif chunk_type == 0xD030:            # skeleton checksum
                    checksum = self.__file.ReadUnsignedLong()
                elif chunk_type == 0xE100:
                    in_softskin = True
                elif chunk_type == 0xE110:
                    in_softskin = False
                elif chunk_type == 0xB100:
                    self.__read_mrm_data(color_adjustment)
                    pending = (self.__cur_obj, self.__cur_mesh, list(self.__cur_mesh.verts))
                elif chunk_type == 0xB1FF and pending is not None:
                    if in_softskin:
                        weights, _nodes = self.__read_softskin_weights(len(pending[2]))
                        collected.append((*pending, weights))
                    else:
                        index = len(attached)
                        bone = attachment_bones[index] if index < len(attachment_bones) else ""
                        attached.append((*pending, bone))
                    pending = None

                self.__file.SetPos(chunk_pos + chunk_size)
            self.__file.Close()
        except RuntimeError as err:
            self.__file.Close()
            raise err

        if not collected and not attached and not allow_empty:
            raise RuntimeError(
                f'{__name__}: No mesh in model mesh.\nFile name: "{filename}".'
            )

        objects = []
        weights_by_object = {}
        for obj, mesh_data, file_positions, weights in collected:
            mesh_data.update(remove_sectored_materials=remove_sectored_materials)
            mapping = _map_positions(obj.data, file_positions)
            weights_by_object[obj] = [
                (mapping[index], entries) for index, entries in enumerate(weights)
            ]
            objects.append(obj)

        attachments = []
        for obj, mesh_data, _file_positions, bone in attached:
            mesh_data.update(remove_sectored_materials=remove_sectored_materials)
            attachments.append((obj, bone))
            objects.append(obj)

        print(f"model mesh: {len(collected)} soft-skin + {len(attachments)} attached "
              f"mesh(es), skeleton checksum 0x{checksum:08X}"
              + (f", source {source}" if source else ""))
        if not objects:
            print("this model carries a skeleton and no geometry at all")
        return {
            "objects": objects,
            "attachments": attachments,
            "checksum": checksum,
            "source": source,
            "weights": weights_by_object,
        }

    def ReadMMBFile(
        self,
        filename,
        space_transform,
        remove_sectored_materials=False,
        color_adjustment=None,
        import_morphs=False,
    ):
        """Read a compiled MorphMesh binary (.mmb): heads, bows, flags.

        Chunk layout: 0xE000 (start) | 0xE020 header (version + source name) |
        0xB100 embedded zCProgMeshProto - the same structure as an .mrm |
        0xB1FF the morph base positions (float3 per vertex) |
        0xE010 source file list | 0xE030 the morph animation table.

        With `import_morphs` the animations become shape keys (S_ANGRY, VISEME_000, ...)."""
        self.__init__()
        self.__filename = filename
        self.__scale_coef = space_transform
        morph_name = ""
        mesh_found = False
        base_positions = []
        animations = []
        try:
            self.__file.Open(filename, "rb")
            file_beginning = True
            while not self.__file.Eof():
                chunk_type = self.__file.ReadUnsignedShort()
                if file_beginning and chunk_type not in (0xE000, 0xE010, 0xE020):
                    raise RuntimeError(
                        f'{__name__}: File is not a MorphMesh binary.\nFile name: "{self.__file.GetPath()}".'
                    )

                file_beginning = False
                chunk_size = self.__file.ReadUnsignedLong()
                chunk_pos = self.__file.GetPos()

                if chunk_type == 0xE020:  # morph mesh header: version + source name
                    _version = self.__file.ReadUnsignedLong()
                    morph_name = self.__file.ReadString(terminator=0x0A)
                elif chunk_type == 0xB100:  # embedded zCProgMeshProto
                    self.__read_mrm_data(color_adjustment)
                    mesh_found = True
                    if not import_morphs:
                        break
                elif chunk_type == 0xB1FF and import_morphs and mesh_found:
                    num_positions = chunk_size // 12
                    flat = self.__file.ReadData(f"{num_positions * 3}f", num_positions * 12)
                    base_positions = [tuple(flat[i * 3: i * 3 + 3]) for i in range(num_positions)]
                elif chunk_type == 0xE030 and import_morphs:
                    animations = self.__read_morph_animations()
                    break

                self.__file.SetPos(chunk_pos + chunk_size)

            self.__file.Close()

            if not mesh_found:
                raise RuntimeError(
                    f'{__name__}: No mesh chunk found in MorphMesh binary.\nFile name: "{self.__file.GetPath()}".'
                )

            self.__cur_mesh.update(remove_sectored_materials=remove_sectored_materials)
            if morph_name and self.__cur_obj:
                self.__cur_obj["krx_morph_source"] = morph_name

            if import_morphs and animations:
                _build_morph_shape_keys(
                    self.__cur_obj, base_positions, animations, self.__scale_coef
                )

        except RuntimeError as err:
            self.__file.Close()
            raise err

        return self.__cur_obj

    def ReadMRMFile(
        self,
        filename,
        space_transform,
        remove_sectored_materials=False,
        color_adjustment=None,
    ):
        self.__init__()
        self.__filename = filename
        self.__scale_coef = space_transform
        try:
            self.__file.Open(filename, "rb")
            file_beginning = True
            while not self.__file.Eof():
                chunk_type = self.__file.ReadUnsignedShort()
                if file_beginning and chunk_type != 0xB100:
                    raise RuntimeError(
                        f'{__name__}: File is not a multi-resolution mesh.\nFile name: "{self.__file.GetPath()}".'
                    )

                file_beginning = False
                file_size = self.__file.ReadUnsignedLong()
                chunk_pos = self.__file.GetPos()
                if chunk_type == 0xB100:
                    self.__read_mrm_data(color_adjustment)
                elif chunk_type == 0xB1FF:
                    break

                self.__file.SetPos(chunk_pos + file_size)

            self.__file.Close()
            self.__cur_mesh.update(remove_sectored_materials=remove_sectored_materials)

        except RuntimeError as err:
            self.__file.Close()
            raise err


# The interactive importer lives in operators.py (KrxMrmImpGUI).


# For calling outside KrxImpExp module
def KrxMrmImp(
    filename: str,
    scale: float = 0.01,
    remove_sectored_materials: bool = True,
    color_adjustment: float = None
):
    TMRMFileLoader().ReadMRMFile(
        filename, scale, remove_sectored_materials, color_adjustment=color_adjustment
    )


# For calling outside KrxImpExp module
def KrxMdlImp(
    filename: str,
    scale: float = 0.01,
    remove_sectored_materials: bool = True,
    color_adjustment: float = None,
    armature_obj=None,
    skin: bool = True,
    yaw_180: bool = True,
):
    """Import a compiled model (.mdl): a skeleton and its skinned body in one file.

    A .mdl is a .mdh hierarchy section followed by a whole .mdm, so it is the only Gothic
    model file that needs nothing beside it - the skeleton comes out of the same file
    rather than being hunted down by checksum. Two retail .mdl files (FIREPLACE_GROUND_USE
    and WASH_SLOT, the interaction slots) are skeleton and no mesh at all; those import as
    a bare armature."""
    return KrxMdmImp(
        filename, scale, remove_sectored_materials, color_adjustment=color_adjustment,
        armature_obj=armature_obj, hierarchy_path=filename, skin=skin, yaw_180=yaw_180,
        allow_empty=True,
    )


# For calling outside KrxImpExp module
def KrxMdmImp(
    filename: str,
    scale: float = 0.01,
    remove_sectored_materials: bool = True,
    color_adjustment: float = None,
    armature_obj=None,
    hierarchy_path: str = None,
    skeleton: str = None,
    skin: bool = True,
    yaw_180: bool = True,
    allow_empty: bool = False,
):
    """Import a compiled model mesh (.mdm) and skin it to its own skeleton.

    With `skin` the .MDH named by the file's skeleton checksum is located, an armature is
    built from it (or `armature_obj` is reused), every vertex gets its groups from the
    soft-skin table, and an Armature modifier is added.

    `yaw_180` turns the model to face the way an .ASC-imported one does - see
    KrxManImp.GOTHIC_YAW. A compiled .mdm is in the skeleton's own space, so without it a
    monster body would face the opposite way from every ASC armor in the scene. The turn
    lives in the rest matrices, so it reaches the mesh through them: every vertex that can
    be positioned from the skeleton already comes out turned, and only what is left over -
    a model with no skeleton to be found - needs it applied by hand."""
    from . import KrxManImp as skeleton_module

    previous_yaw = skeleton_module.GOTHIC_YAW
    if not yaw_180:
        skeleton_module.GOTHIC_YAW = Matrix.Identity(4)
    try:
        result = TMRMFileLoader().ReadMDMFile(
            filename, scale, remove_sectored_materials, color_adjustment=color_adjustment,
            allow_empty=allow_empty,
        )
        if skin:
            result.update(skeleton_module.skin_softskin_meshes(
                result, scale=scale, armature_obj=armature_obj,
                hierarchy_path=hierarchy_path, mesh_path=filename, skeleton=skeleton,
            ))

        if yaw_180:
            positioned = result.get("positioned") or set()
            for obj in result["objects"]:
                if obj not in positioned:
                    obj.data.transform(previous_yaw)
    finally:
        skeleton_module.GOTHIC_YAW = previous_yaw
    return result


# For calling outside KrxImpExp module
def KrxMmbImp(
    filename: str,
    scale: float = 0.01,
    remove_sectored_materials: bool = True,
    color_adjustment: float = None,
    import_morphs: bool = False,
):
    return TMRMFileLoader().ReadMMBFile(
        filename, scale, remove_sectored_materials,
        color_adjustment=color_adjustment, import_morphs=import_morphs,
    )
