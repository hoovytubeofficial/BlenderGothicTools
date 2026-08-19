# numconv.py: simple utilities to convert numbers.
# --------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly Baranov
# License: GPL
# --------------------------------------------------------------------------------------------------

from .log import klog as print  # route console output through the add-on's log tag
def truncate(num, n=6):
    integer = int(num * (10**n)) / (10**n)
    return float(integer)

def float_to_string(f):
    return str(truncate(float(f)))

def string_to_int(string: str):
    if not string:
        return None

    try:
        val = int(string)
    except ValueError as err:
        print("Error", __name__, f"string_to_int(string={string})", err, sep=" --> ")
        val = None

    if val is not None:
        return val
    
    print("Trying string > float > int...")
    val = string_to_float(string)
    if val is not None:
        return int(val)
    return None

def string_to_float(string: str):
    if not string:
        return None

    try:
        val = float(string)
    except ValueError as err:
        print("Error", __name__, f"string_to_float(string={string})", err, sep=" --> ")
        val = None

    if val is not None:
        return val

    print("Trying string > int > float...")
    val = string_to_int(string)
    if val is not None:
        return float(val)
    return None