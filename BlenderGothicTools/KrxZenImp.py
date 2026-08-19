from .KrxMshImp import zenginWorldLoader

# The interactive importer lives in operators.py (KrxZenImpGUI); this module keeps
# the scripting API only.


# For calling outside KrxImpExp module
def KrxZenImp(
    filename: str,
    scale: float = 0.01,
    remove_sectored_materials: bool = True,
    color_adjustment: float = None,
):
    zenginWorldLoader().ReadZENFile(
        filename,
        scale,
        remove_sectored_materials=remove_sectored_materials,
        color_adjustment=color_adjustment,
    )
