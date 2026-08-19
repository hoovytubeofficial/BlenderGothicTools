# material.py: material utilities.
# ------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly Baranov
# License: GPL
# ------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
from operator import itemgetter
from pathlib import Path

import bpy
from bpy_extras.node_shader_utils import PrincipledBSDFWrapper
from mathutils import Color
from .preferences import KrxImpExpPreferences, get_master_folders

loaded_texture_paths = {}

IMPORT_SETTINGS = {
    "brute_search": True,
    "metallic": 0.0,
    "roughness": 1.0,
    "ior": 1.45,
}
"""Per-import material settings, set by the import operators before the loaders run."""


def _compiled_logical_name(path: Path) -> str:
    """'HUM_HEAD_V14_C0-C.TEX' -> 'HUM_HEAD_V14_C0.TGA' (the name the meshes reference)."""
    stem = path.stem.upper()
    if stem.endswith("-C"):
        stem = stem[:-2]
    return f"{stem}.TGA"


def _build_texture_index():
    """Fill loaded_texture_paths from the preference directories (shallow) and,
    when brute search is enabled, from the master game folders (recursive).
    Source .tga files win; compiled .TEX files fill the gaps (converted on load)."""

    loaded_texture_paths.clear()

    addon_entry = bpy.context.preferences.addons.get(__package__)
    texture_dirs = addon_entry.preferences.texture_directories if addon_entry else ()

    print(f"building texture index (preference directories: {len(texture_dirs)})")

    for texture_dir in reversed(texture_dirs):
        for path in Path(texture_dir.name).glob("*.tga"):
            loaded_texture_paths[path.name.upper()] = str(path)

    if IMPORT_SETTINGS["brute_search"]:
        masters = get_master_folders()
        compiled = []
        for master in masters:
            # .TGA sources always win: they are indexed first and .TEX only fills gaps
            for path in Path(master).rglob("*.tga"):
                loaded_texture_paths.setdefault(path.name.upper(), str(path))
            compiled.extend(Path(master).rglob("*.[Tt][Ee][Xx]"))

        tga_count = len(loaded_texture_paths)
        for path in compiled:
            loaded_texture_paths.setdefault(_compiled_logical_name(path), str(path))
            loaded_texture_paths.setdefault(path.name.upper(), str(path))
        gap_filled = len(loaded_texture_paths) - tga_count

        print(f"texture index: {tga_count} .tga source(s) from {len(masters)} master folder(s), "
              f"{len(compiled)} compiled .TEX scanned, {gap_filled} name(s) served by .TEX only")

    print("texture index ready -", len(loaded_texture_paths), "entries")

    # sentinel so an empty result doesn't trigger a rebuild for every texture
    loaded_texture_paths.setdefault("__BUILT__", "")


def load_image(image_name, import_path):
    """Loads image with searching next to the imported file, then in the preference
    texture directories, then (brute search) recursively in the master game folders."""

    if not image_name:
        return None

    path_exists = False
    if import_path:
        file = Path(import_path).parent.joinpath(image_name)
        path_exists = file.exists()
        file_path = str(file)

    if not path_exists:
        if len(loaded_texture_paths) == 0:
            _build_texture_index()

        loaded_path = loaded_texture_paths.get(image_name.upper())
        if loaded_path:
            file_path = loaded_path
            path_exists = True

    if path_exists and file_path.lower().endswith(".tex"):
        # compiled zTEX: convert to DDS once, then load the conversion
        from .tex_convert import cached_dds

        converted = cached_dds(file_path)
        if converted:
            file_path = converted
        else:
            path_exists = False

    if path_exists:
        image = bpy.data.images.load(file_path, check_existing=True)
        image.source = "FILE"
        image.filepath = file_path
    else:
        image = bpy.data.images.get(image_name)
        image = image if image else bpy.data.images.new(image_name, 4, 4)
        image.source = "FILE"
        image.filepath = image_name
    return image


def new_material(name):
    """
    Gathers material from bpy.data.materials and returns if it finds similar one by name, else creates new one.
    Counterintuitive to the python's design, but the .get method for bpy.data.materials is faster than finding by try...except
    """
    material_name = name.upper()[:63] # limit of material name, blender has cutting the string right...
    material = bpy.data.materials.get(material_name)
    return material if material else bpy.data.materials.new(material_name)


def game_ready_material_name(image_name: str) -> str:
    """'HUM_HEAD_V14_C0-C.DDS' / 'Hum_Head_V14_C0.tga' -> 'Hum_Head_V14_C0'.

    Gothic material names are the artist's Max slot names and mean nothing downstream;
    the texture file is what every other tool keys on."""
    import os

    stem = os.path.splitext(image_name)[0]
    if stem.upper().endswith("-C"):        # the compiled zTEX suffix
        stem = stem[:-2]
    return stem.strip().strip("._-")


def rename_materials_to_texture(objects) -> int:
    """Rename every material slot after its own texture file, merging duplicates.

    Two meshes that use the same texture end up sharing ONE material instead of
    collecting a .001 suffix each, which is what makes the result game-ready."""
    renamed = 0
    for obj in objects:
        if getattr(obj, "type", None) != "MESH" or obj.data is None:
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            texture = get_texture_name(material)
            if not texture:
                continue
            wanted = game_ready_material_name(texture)
            if not wanted or material.name == wanted:
                continue
            existing = bpy.data.materials.get(wanted)
            if existing is not None and existing is not material:
                slot.material = existing      # same texture, one material
            else:
                material.name = wanted
            renamed += 1
    return renamed


def image_has_alpha(img):
    b = 32 if img.is_float else 8
    return img.depth == 2 * b or img.depth == 4 * b  # Grayscale+Alpha  # RGB+Alpha


def get_texture_name(material):
    """
    Returns filename string of a texture linked to material.
    Returns an empty string if there is no diffuse map.
    """

    if not material.node_tree:
        return ""

    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE":
            if not node.image:
                continue
            return node.image.name
    return ""



def link_texture_to_material(material, image_name, import_file, color_adjustment: float = None):
    """
    Links image texture to material.
    Uses proper node linking to avoid redundant duplicates of images and fake users.
    """
    if color_adjustment is not None and color_adjustment != 0:
        # Check function check_diffuse_color_adjustment() description for more info.
        r, g, b, a = material.diffuse_color
        color = Color((r, g, b))
        material["original_saturation"] = color.s
        # increase HSV Saturation by percentage defined with color_adjustment value
        color.s *= 1 + (color_adjustment * 0.01)
        material.diffuse_color = color.r, color.g, color.b, a
    else:
        material["original_saturation"] = None

    image = load_image(image_name, import_file)
    material.use_backface_culling = True
    material_wrapper = PrincipledBSDFWrapper(material, is_readonly=False)
    material_wrapper.specular = 0

    metallic = IMPORT_SETTINGS["metallic"]
    roughness = IMPORT_SETTINGS["roughness"]
    material.roughness = roughness  # viewport display
    material.metallic = metallic  # viewport display

    principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is not None:
        principled.inputs["Metallic"].default_value = metallic
        principled.inputs["Roughness"].default_value = roughness
        if "IOR" in principled.inputs:
            principled.inputs["IOR"].default_value = IMPORT_SETTINGS["ior"]

    if image:
        node_tree = material.node_tree
        # new_material() hands back an existing material when the name matches, so a
        # second import of the same mesh must NOT stack another image node on top.
        existing = [node for node in node_tree.nodes if node.type == "TEX_IMAGE"]
        for spare in existing[1:]:
            node_tree.nodes.remove(spare)
        image_node = existing[0] if existing else node_tree.nodes.new("ShaderNodeTexImage")
        image_node.image = image
        image_node.location = (-500, 0)
        target_link = next(n for n in node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        node_tree.links.new(image_node.outputs["Color"], target_link.inputs["Base Color"])
        if image_has_alpha(image):
            node_tree.links.new(image_node.outputs["Alpha"], target_link.inputs["Alpha"])
            material.show_transparent_back = False
            material.blend_method = "CLIP"
            if hasattr(material, "shadow_method"):
                material.shadow_method = "CLIP"
            material["biplanar"] = True

def check_diffuse_color_adjustment(material: bpy.types.Material) -> tuple:
    """
    So, the thing with material colors imported from files is that, those are dull, it's kind of difficult to
    distinguish between which is which at first glance in viewport. This function brings similar functionality as
    Gothic_MaT_Blender but puts twist on it - this color change will work only in Blender, in viewport.
    When exported, it'll try to restore the original saturation that was imported from file.
    This effect is achieved with Blender's Custom Properties.
    """
    r, g, b, a = material.diffuse_color
    saturation = material.get("original_saturation", None)
    if saturation is None:
        return r, g, b, a

    color = Color((r, g, b))
    color.s = saturation
    return color.r, color.g, color.b, a

class MatLibParser:
    __slots__ = ("__tmaterials", "autonames")

    def __init__(self, filepath, autonaming=False):
        self.__tmaterials = list()
        self.autonames = autonaming
        self.load_material_filter(filepath)

    @property
    def materials(self) -> list:
        if self.__tmaterials:
            if len(self.__tmaterials) > 0:
                return self.__tmaterials
        return None

    def name(self, index) -> dict:
        return self.materials[index]["name"]

    def texture(self, index) -> dict:
        return self.materials[index]["texture"]

    def material(self, index) -> dict:
        return self.materials[index]

    def set_material(self, index, value):
        if self.__tmaterials:
            if len(self.__tmaterials) > 0:
                self.__tmaterials[index] = value

    def __pml_parse(self, pml_file_path):
        try:
            with open(pml_file_path, "rt", encoding="Windows-1250") as file_handle:
                for line in file_handle:
                    if "[% zCMaterial" in line:
                        tmaterial = dict()
                    data = line.rstrip("\r\n").lstrip("\t").split("=")
                    if len(data) == 2:
                        key = data[0]
                        value_type = data[1].split(":")[0]
                        value = data[1].split(":")[1]
                        if value_type.find("enum") != -1 or value_type.find("int") != -1:
                            value = int(value)
                        elif value_type.find("float") != -1:
                            value = float(value)
                        elif value_type.find("bool") != -1:
                            value = bool(value)
                        elif value_type.find("string") != -1:
                            value = str(value)
                        elif value_type.find("color") != -1:
                            value = value.split()
                            value = [int(val) for val in value]
                        elif value_type.find("rawFloat") != -1:
                            value = value.split()
                            value = [float(val) for val in value]
                        tmaterial[key] = value

                    if line.find("[]") != -1:
                        self.__tmaterials.append(tmaterial)
        # EnvironmentError = parent of IOError, OSError *and* WindowsError where available
        except (EnvironmentError, AttributeError, ValueError) as e:
            self.__tmaterials = list()
            self.autonames = False

    def load_material_filter(self, mat_lib_ini_path: str):
        if mat_lib_ini_path:
            if len(self.__tmaterials) > 0:
                self.__tmaterials = list()
                self.autonames = False
            try:
                with open(mat_lib_ini_path, "rt", encoding="Windows-1250") as file_handle:
                    self.__tmaterials = []
                    for line in file_handle:
                        line = line.rstrip("\r\n")
                        pos = line.find("=")
                        if pos != -1:
                            pml_file_path = Path(mat_lib_ini_path).parent / f"{line[:pos]}.pml"
                            self.__pml_parse(pml_file_path)

                    self.__tmaterials.sort(key=itemgetter("name"))
            # parent of IOError, OSError *and* WindowsError where available
            except (EnvironmentError, AttributeError) as e:
                self.__tmaterials = list()
                self.autonames = False
