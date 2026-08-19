# mesh.py: mesh utilities.
# --------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix, Shoun, Kamil "HRY" Krzyśków
# License: GPL
# --------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
from enum import IntEnum
from typing import Dict, List, Optional, Set


import bpy, bmesh, math
from mathutils import Vector

from .material import new_material
from .object import deselect_all
from .scene import end_editmode, start_editmode
from .types import Face, TVertex, TVFace, Vertex


def new_mesh_object(name):
    """Creates a new object linked to mesh"""
    mesh = bpy.data.meshes.new(name)
    mesh_obj = bpy.data.objects.new(name, object_data=mesh)
    mesh_obj.rotation_mode = "QUATERNION"
    bpy.context.collection.objects.link(mesh_obj)
    return mesh_obj


def is_mesh_object(obj):
    """Checks if this object is linked to a mesh"""
    return isinstance(obj, bpy.types.Object) and obj.type == "MESH"


class MeshData:
    """Mesh class which is styled in 3dsmax"""

    __slots__ = ("_obj", "_mesh", "verts", "_faces", "_face_materials", "tool", "tverts", "tvfaces")

    def __init__(self, obj, tool: Optional[str] = None):
        if not is_mesh_object(obj):
            raise TypeError(f"{__name__}: MeshData.__init__: error with argument 2, 'obj' not a mesh_object")

        self.tool = tool

        if not obj.modifiers and not obj.data.shape_keys:
            self._mesh: bpy.types.Mesh = obj.data
        else:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            self._mesh: bpy.types.Mesh = obj.evaluated_get(depsgraph).to_mesh()

        self._obj: bpy.types.Object = obj
        self._face_materials: List[Optional[bpy.types.Material]] = []
        self.verts: List[Vertex] = []
        self.faces: List[Face] = []
        self.tverts: List[TVertex] = []
        self.tvfaces: List[TVFace] = []

    def prepare_export_old(self):  # Returns conversion status
        self.verts: List[Vertex] = [v.co for v in self._mesh.vertices]

        if not self._mesh.uv_layers.active:
            return

        self.tverts: List[Vector] = [uvl.uv for uvl in self._mesh.uv_layers.active.data]

        for polygon in self._mesh.polygons:
            mat_index: int = polygon.material_index

            material: Optional[bpy.types.Material] = None

            if 0 <= mat_index < len(self._obj.material_slots):
                material = self._obj.material_slots[mat_index].material

            for i in range(2, polygon.loop_total):
                ta = polygon.loop_start
                tb = polygon.loop_start + i - 1
                tc = polygon.loop_start + i
                va = self._mesh.loops[ta].vertex_index
                vb = self._mesh.loops[tb].vertex_index
                vc = self._mesh.loops[tc].vertex_index
                self.tvfaces.append((ta, tb, tc))
                self._faces.append([va, vb, vc])
                self._face_materials.append(material)

        if self.tool == "3DS_EXPORT":
            can_be_saved = True
            if len(self.faces) > 65535 or len(self.verts) > 65535:
                can_be_saved = False
            else:
                converted_verts, converted_uv_verts, converted_faces = self.convert_to_3ds_faces()
                self.verts = converted_verts
                self._faces = converted_faces
                self.tverts = converted_uv_verts

                if len(self.verts) > 65535:
                    can_be_saved = False
            if not can_be_saved:
                print(
                    f"Invalid mesh conversion to 3DS for {self._obj.name} - Verts: {len(self._mesh.vertices)} Tris: {len(self.faces)} Verts in file: {len(self.verts)}"
                )
            return can_be_saved
        return True

    def convert_to_3ds_faces(self):
        """Since 3ds files only support one pair of uv coordinates for each vertex, face uv coordinates
        need to be converted to vertex uv coordinates. That means that vertices need to be duplicated when
        there are multiple uv coordinates per vertex."""

        if not self.tverts:
            return self.verts, [], self.faces

        converted_verts = []
        converted_uv_verts = []
        converted_faces = []
        dupl = []

        for _ in self.verts:
            converted_verts.append(Vector((0, 0, 0)))
            converted_uv_verts.append(Vector((0, 0)))
            dupl.append([])

        num_verts = len(self.verts)
        for face, tv_face in zip(self.faces, self.tvfaces):
            for j in range(3):
                v_index = face[j]
                vert = self.verts[v_index]
                tvert = self.tverts[tv_face[j]]

                d = dupl[v_index]

                if len(d) == 0:
                    converted_verts[v_index] = vert
                    converted_uv_verts[v_index] = tvert
                    d.append(v_index)
                else:
                    found_match = False
                    for du in d:
                        uv1 = converted_uv_verts[du]
                        if uv1[UVIndexes.U] == tvert[UVIndexes.U] and uv1[UVIndexes.V] == tvert[UVIndexes.V]:
                            face[j] = du
                            found_match = True
                            break

                    if not found_match:
                        face[j] = num_verts
                        d.append(num_verts)
                        num_verts = num_verts + 1
                        converted_verts.append(vert)
                        converted_uv_verts.append(tvert)

                dupl[v_index] = d
            converted_faces.append(face)

        return converted_verts, converted_uv_verts, converted_faces

    def prepare_export(self):
        # Experimental bmesh-based exporting
        # Slower, but accurate mesh triangulation
        # Field-tested through several production:tm: meshes
        # Works fine, although this makes exporters incompatible with older krximpexp
        # Expect different results across older versions.
        obj = self._obj
        mesh = self._mesh
        tri_count = sum(len(polygon.vertices) - 2 for polygon in mesh.polygons)
        bm_obj: bmesh.types.BMesh = bmesh.new()

        if len(mesh.polygons) != tri_count:
            # Important! Do not triangulate mesh in the same "run" as gathering the mesh data!
            # This will result in garbled mess as the mesh after triangulation is in invalid state!
            print(obj.type, obj.name, "Mesh has quads and ngons, triangulating")
            temp_mesh = bpy.data.meshes.new(obj.name)
            bm_obj.from_mesh(mesh)
            bmesh.ops.triangulate(bm_obj, faces=bm_obj.faces, quad_method="BEAUTY", ngon_method="BEAUTY")
            bm_obj.to_mesh(temp_mesh)
            bm_obj.free()
            bm_obj: bmesh.types.BMesh = bmesh.new()
        else:
            temp_mesh = mesh

        bm_obj.from_mesh(temp_mesh)
        bm_obj.verts.ensure_lookup_table()
        bm_obj.edges.ensure_lookup_table()
        bm_obj.faces.ensure_lookup_table()

        self.verts: List[Vertex] = [v.co.copy() for v in bm_obj.verts]
        for face in bm_obj.faces:
            mat_index: int = face.material_index
            material: Optional[bpy.types.Material] = None
            if 0 <= mat_index < len(obj.material_slots):
                material = obj.material_slots[mat_index].material
                if not material: # inject material to mesh with empty material slot
                    material = new_material(f"ZM_MATERIAL_{mat_index}")
                    obj.material_slots[mat_index].material = material
            else:  # inject material to mesh without slots
                material = new_material("ZM_EMPTY_MATERIAL")
                obj.data.materials.append(material)
            self._faces.append([v.index for v in face.verts])
            self.tvfaces.append([l.index for l in face.loops])
            self._face_materials.append(material)
            # Not sure if this is entirely valid
            if bm_obj.loops.layers.uv.active:
                for loop in face.loops:
                    self.tverts.append(loop[bm_obj.loops.layers.uv.active].uv.copy())
        bm_obj.free()

        if self.tool == "3DS_EXPORT":
            can_be_saved = True
            if len(self.faces) > 65535 or len(self.verts) > 65535:
                can_be_saved = False
            else:
                converted_verts, converted_uv_verts, converted_faces = self.convert_to_3ds_faces()
                self.verts = converted_verts
                self._faces = converted_faces
                self.tverts = converted_uv_verts

                if len(self.verts) > 65535:
                    can_be_saved = False
            if not can_be_saved:
                print(
                    f"Invalid mesh conversion to 3DS for {self._obj.name} - Verts: {len(self._mesh.vertices)} Tris: {len(self.faces)} Verts in file: {len(self.verts)}"
                )
            return can_be_saved
        return True

    @property
    def faces(self) -> List[Face]:
        return self._faces

    @faces.setter
    def faces(self, value: List[Face]):
        self._faces = value
        self._face_materials = [None for _ in value]

    @property
    def face_materials(self) -> List[Optional[bpy.types.Material]]:
        return self._face_materials

    def update(self, remove_sectored_materials=False):
        """Update mesh after it has been changed"""

        obj_mesh_id: str = f"{self._obj.type} {self._mesh.name}"
        def report(*msg, end="\n"):
            if "ZS_" not in obj_mesh_id.upper():
                print(*msg, end=end)
        # Initialise the mesh's vertices and faces
        # the "Mesh.from_pydata" function works only on clean meshes
        if len(self._mesh.vertices) != 0 or len(self._mesh.polygons) != 0:
            return


        report(f"{obj_mesh_id}: Generating from pydata {len(self.verts)} verts, {len(self._faces)} faces...")
        self._mesh.from_pydata(vertices=self.verts, edges=[], faces=self._faces)
        report(f"{obj_mesh_id}: Generating from pydata done!")
        report(f"{obj_mesh_id}: Applying materials and UVs to {len(self._mesh.polygons)} faces...")

        self._mesh.update_tag()
        deselect_all()
        portal_markers = ("PI:", "P:", "PN:")
        special_faces: Set[bmesh.types.BMFace] = set()
        invalid_faces: Set[bmesh.types.BMFace] = set()
        material_lookup: Dict[str, int] = dict()
        if remove_sectored_materials:
            portal_names: Set[str] = set()  # clean portal names without P:, PI:, PN: and underscores

        mesh_materials = sorted(set(self._face_materials), key=lambda material: material.name if material is not None else '')

        # Keyed on the UPPER-CASE name, so every read below must upper-case too. Gothic
        # material names arrive upper-cased already, which hid a mismatch here for years:
        # a material with no name in the file becomes Blender's own "Material.001", whose
        # case does not survive the round trip (GROUND_SLOT.MDL and TREASURE_ADDON_01.MDL
        # both crashed on exactly that).
        for i, material in enumerate(mesh_materials):
            if material:
                material_name = material.name.upper()
                material_lookup[material_name] = i
                self._mesh.materials.append(material)
                if material_name.startswith(portal_markers) and remove_sectored_materials:
                    portal_names.update(material_name.removeprefix("P:").removeprefix("PI:").removeprefix("PN:").split("_"))

        if remove_sectored_materials:
            if "" in portal_names:
                portal_names.remove("")  # Product of split

        bm_obj: bmesh.types.BMesh = bmesh.new()
        bm_obj.from_mesh(self._mesh)
        bm_obj.verts.ensure_lookup_table()
        bm_obj.edges.ensure_lookup_table()
        bm_obj.faces.ensure_lookup_table()

        # Apply materials, UVs and remove invalid faces and split special faces
        for face in bm_obj.faces:
            if len(set(face.verts)) != 3:
                invalid_faces.add(face)
                # report("HRY DEBUG invalid special_faces index", face.index, "-", *[v.index for v in face.verts])

            material = self._face_materials[face.index]
            if material is not None:
                material_name = material.name.upper()
                if material_name.startswith("S:") and remove_sectored_materials:
                    for portal_name in portal_names:
                        if portal_name in material_name:
                            target_name = material_name.removeprefix(f"S:{portal_name}_")
                            if target_name.startswith("S:"):
                                # Sometimes there are portals like ST and STHAUS that will still execute the TRY-BLOCK if this check isn't in place
                                # TRY-BLOCK-PRINT -  portal_name:ST material.name:S:STHAUS_OW_OLDCAMP_WALL_02 target_name:S:STHAUS_OW_OLDCAMP_WALL_02
                                continue
                            try:
                                face.material_index = material_lookup[target_name]
                            except KeyError:
                                old_name = material_name
                                target_material = bpy.data.materials.get(target_name)
                                if target_material is None:
                                    material.name = target_name
                                else:
                                    self._obj.material_slots[material_lookup[old_name]].material = target_material

                                material_lookup[target_name] = material_lookup[old_name]
                                face.material_index = material_lookup[old_name]
                else:
                    face.material_index = material_lookup[material_name]

                if not self.tool == "ASC":
                    if material.blend_method != "OPAQUE" or material.name.startswith(portal_markers):
                        special_faces.add(face)

            # uvs
            if not self.tvfaces:
                continue

            uv_layer = bm_obj.loops.layers.uv.active or bm_obj.loops.layers.uv.new()
            for vert_idx, loop in enumerate(face.loops):
                if loop.index == -1:
                    continue

                x,y = self.tverts[self.tvfaces[face.index][vert_idx]]
                if math.isnan(x) or math.isnan(y):
                    continue

                loop[uv_layer].uv = self.tverts[self.tvfaces[face.index][vert_idx]]

        if self._mesh.materials:
            report(f"{obj_mesh_id}: Applied {len(self._mesh.materials)} materials to the world mesh...")

        special_faces = special_faces - invalid_faces

        # TODO: Pinpoint the exact issue with invalid faces and fix that...
        if invalid_faces:
            from .impexp import OPERATOR  # import done here to get the current operator reference

            OPERATOR.report(type={"WARNING"}, message="Warning Check System Console")
            report("\033[33m", end="")  # ANSI Yellow
            report(f"[WARNING] This {obj_mesh_id} seems to have: {len(invalid_faces)} invalid faces")
            report("Be extremely careful operating on this mesh, since on newer blender version it can lead to a crash.")
            report("\033[39m", end="")  # ANSI Normal
            bmesh.ops.delete(bm_obj, geom=list(invalid_faces), context="FACES_KEEP_BOUNDARY")

        # Any geometry modification should be done AFTER UV calculations, expect garbled mess otherwise!!!
        # Finally, disconnect special from main mesh
        if special_faces:
            report(f"{obj_mesh_id}: Splitting {len(special_faces)} special faces from main mesh...")
            bmesh.ops.split(bm_obj, geom=list(special_faces), use_only_faces=True)

        bm_obj.to_mesh(self._mesh)
        bm_obj.free()
        report(
            f"{obj_mesh_id}: Updated {len(self._mesh.vertices)} vertices,",
            f"{len(self._mesh.edges)} edges, {len(self._mesh.polygons)} faces",
        )

        start_editmode(self._obj)  # Setting valid context
        end_editmode()  # Setting valid context
        self._obj.select_set(True)
        if remove_sectored_materials:
            bpy.ops.object.material_slot_remove_unused()
        # Return the list of all the objects which were changed

        # Blender 4.0...
        bpy.ops.object.shade_flat()
        return [self._obj]


# Mapping coordinates (uv)


class UVIndexes(IntEnum):
    """Semantic values for UV vertices"""

    U = 0
    V = 1
