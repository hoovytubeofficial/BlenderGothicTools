# math_utils.py: provides helper math related functions.
# --------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly "Kerrax" Baranov, Patrix. Shoun, HRY
# License: GPL
# --------------------------------------------------------------------------------------------------

from mathutils import Matrix, Vector

# ----------------------------------
# Matrix operations               #
# ----------------------------------


def get_row0(matrix: Matrix) -> Vector:
    """
    Returns the zeroth row as three-components vector.
    The zeroth row represents the unit vector along the first axis of a local coordinate system
    projected on the three axes of the world coordinate system.
    """
    return matrix[0].copy()


def get_row1(matrix: Matrix) -> Vector:
    """
    Returns the first row as three-components vector.
    The first row represents the unit vector along the second axis of a local coordinate system
    projected on the three axes of the world coordinate system.
    """
    return matrix[1].copy()


def get_row2(matrix: Matrix) -> Vector:
    """
    Returns the second row as three-components vector.
    The second row represents the unit vector along the third axis of a local coordinate system
    projected on the three axes of the world coordinate system.
    """
    return matrix[2].copy()


def get_translation_part(matrix: Matrix) -> Vector:
    """
    Returns the third row as three-components vector.
    The third row represents the origin of a local coordinate system
    in the world coordinate system.
    """
    return matrix[3].copy()


def set_row0(matrix: Matrix, row0: Vector) -> None:
    # Blender 5.x mathutils requires the assigned sequence to match the slice size,
    # while the get_* helpers may hand back 4-component rows - slice to 3.
    matrix[0][:3] = row0[:3]


def set_row1(matrix: Matrix, row1: Vector) -> None:
    matrix[1][:3] = row1[:3]


def set_row2(matrix: Matrix, row2: Vector) -> None:
    matrix[2][:3] = row2[:3]


def set_translation_part(matrix: Matrix, row3: Vector) -> None:
    matrix[3][:3] = row3[:3]


def translation_matrix(vector: Vector) -> Matrix:
    """Create transformation matrix for translation by translation vector"""
    return Matrix(([1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [vector.x, vector.y, vector.z, 1]))
