# types.py: Typing and typechecking utilities.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix, Shoun, Kamil "HRY" Krzyśków
# License: GPL
# -------------------------------------------------------------------------------------------------------

from typing import List, Tuple

import bpy

# Helper types
EdgeVertexType = List[bool]

# Mesh types
Vertex = List[float]
TVertex = List[float]
Face = List[int]
TVFace = Tuple[int, int, int]


def is_skin_object(obj):
    if isinstance(obj, bpy.types.Object):
        return obj.find_armature() is not None
    return False


def is_valid_tuple_instance(extended_obj) -> bool:
    """Check if the extended object is a valid tuple"""
    return (
        isinstance(extended_obj, tuple)
        and len(extended_obj) == 2
        and isinstance(extended_obj[0], bpy.types.Object)
        and isinstance(extended_obj[1], str)
    )
