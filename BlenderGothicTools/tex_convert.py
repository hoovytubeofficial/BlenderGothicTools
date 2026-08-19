# tex_convert.py: zTEX (.TEX) -> DDS conversion.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# Gothic compiles every texture to zTEX. The container is trivial once you know it:
#
#   36-byte header: "ZTEX" | version u32 | format u32 | width u32 | height u32
#                   mipmaps u32 | refWidth u32 | refHeight u32 | averageColor u32
#   payload:        the mipmap chain stored SMALLEST FIRST, so the full-size level is last.
#
# For the DXT formats (which is what the game ships) the payload is raw DXT blocks, so a
# converted file is just "DDS header + the last mip level" - no decoding required, and
# Blender loads DDS natively. Verified against HUM_HEAD_V14_C0-C.TEX (DXT1 256x128, 5 mips).
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import struct
from pathlib import Path
from typing import Optional

from .system import CACHE_DIR as _CACHE_ROOT

ZTEX_SIGNATURE = b"ZTEX"
ZTEX_HEADER_SIZE = 36

# zTEX_FORMAT enum
(
    FMT_B8G8R8A8, FMT_R8G8B8A8, FMT_A8B8G8R8, FMT_A8R8G8B8,
    FMT_B8G8R8, FMT_R8G8B8, FMT_A4R4G4B4, FMT_A1R5G5B5,
    FMT_R5G6B5, FMT_P8, FMT_DXT1, FMT_DXT2, FMT_DXT3, FMT_DXT4, FMT_DXT5,
) = range(15)

_FOURCC = {
    FMT_DXT1: b"DXT1",
    FMT_DXT2: b"DXT2",
    FMT_DXT3: b"DXT3",
    FMT_DXT4: b"DXT4",
    FMT_DXT5: b"DXT5",
}

_BLOCK_BYTES = {FMT_DXT1: 8, FMT_DXT2: 16, FMT_DXT3: 16, FMT_DXT4: 16, FMT_DXT5: 16}

# uncompressed formats we can pass through: (bits per pixel, R, G, B, A masks)
_UNCOMPRESSED = {
    FMT_B8G8R8A8: (32, 0x0000FF00, 0x00FF0000, 0xFF000000, 0x000000FF),
    FMT_A8R8G8B8: (32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
    FMT_R8G8B8A8: (32, 0xFF000000, 0x00FF0000, 0x0000FF00, 0x000000FF),
    FMT_A8B8G8R8: (32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000),
    FMT_R5G6B5: (16, 0xF800, 0x07E0, 0x001F, 0x0000),
    FMT_A1R5G5B5: (16, 0x7C00, 0x03E0, 0x001F, 0x8000),
    FMT_A4R4G4B4: (16, 0x0F00, 0x00F0, 0x000F, 0xF000),
}

CACHE_DIR = _CACHE_ROOT / "textures"


class TexError(Exception):
    """Raised when a .TEX file cannot be converted"""


def read_header(path) -> dict:
    with open(path, "rb") as handle:
        raw = handle.read(ZTEX_HEADER_SIZE)
    if len(raw) < ZTEX_HEADER_SIZE or raw[:4] != ZTEX_SIGNATURE:
        raise TexError(f"Not a zTEX file: {path}")
    version, fmt, width, height, mipmaps, ref_width, ref_height, average = struct.unpack_from("<8I", raw, 4)
    return {
        "version": version,
        "format": fmt,
        "width": width,
        "height": height,
        "mipmaps": mipmaps,
        "ref_width": ref_width,
        "ref_height": ref_height,
        "average_color": average,
    }


def _level_size(fmt: int, width: int, height: int) -> int:
    if fmt in _BLOCK_BYTES:
        blocks_x = max(1, (width + 3) // 4)
        blocks_y = max(1, (height + 3) // 4)
        return blocks_x * blocks_y * _BLOCK_BYTES[fmt]
    if fmt in _UNCOMPRESSED:
        return width * height * (_UNCOMPRESSED[fmt][0] // 8)
    raise TexError(f"Unsupported zTEX format {fmt}")


def _dds_header(fmt: int, width: int, height: int, payload_size: int) -> bytes:
    DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PIXELFORMAT = 0x1, 0x2, 0x4, 0x1000
    DDSD_LINEARSIZE, DDSD_PITCH = 0x80000, 0x8
    DDPF_ALPHAPIXELS, DDPF_FOURCC, DDPF_RGB = 0x1, 0x4, 0x40
    DDSCAPS_TEXTURE = 0x1000

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT

    if fmt in _FOURCC:
        flags |= DDSD_LINEARSIZE
        pitch_or_linear = payload_size
        pf = struct.pack("<8I", 32, DDPF_FOURCC, struct.unpack("<I", _FOURCC[fmt])[0], 0, 0, 0, 0, 0)
    else:
        bpp, r_mask, g_mask, b_mask, a_mask = _UNCOMPRESSED[fmt]
        flags |= DDSD_PITCH
        pitch_or_linear = width * bpp // 8
        pf_flags = DDPF_RGB | (DDPF_ALPHAPIXELS if a_mask else 0)
        pf = struct.pack("<8I", 32, pf_flags, 0, bpp, r_mask, g_mask, b_mask, a_mask)

    header = struct.pack(
        "<7I", 124, flags, height, width, pitch_or_linear, 0, 1  # depth 0, 1 mip level
    )
    header += b"\x00" * 44  # dwReserved1[11]
    header += pf
    header += struct.pack("<5I", DDSCAPS_TEXTURE, 0, 0, 0, 0)
    return b"DDS " + header


def tex_to_dds(tex_path, dds_path=None) -> str:
    """Convert a zTEX file to a DDS holding its full-size mip level. Returns the DDS path."""
    tex_path = Path(tex_path)
    info = read_header(tex_path)
    fmt, width, height = info["format"], info["width"], info["height"]

    if fmt not in _FOURCC and fmt not in _UNCOMPRESSED:
        raise TexError(f"Unsupported zTEX format {fmt} in {tex_path.name}")

    payload = tex_path.read_bytes()[ZTEX_HEADER_SIZE:]
    top_size = _level_size(fmt, width, height)
    if top_size > len(payload):
        raise TexError(f"{tex_path.name}: truncated payload ({len(payload)} < {top_size})")

    # mip chain is stored smallest-first, so the full-size level is at the end
    top_level = payload[len(payload) - top_size:]

    if dds_path is None:
        dds_path = CACHE_DIR / (tex_path.stem + ".dds")
    dds_path = Path(dds_path)
    dds_path.parent.mkdir(parents=True, exist_ok=True)
    dds_path.write_bytes(_dds_header(fmt, width, height, top_size) + top_level)
    return str(dds_path)


def cached_dds(tex_path) -> Optional[str]:
    """DDS path for a .TEX, converting only when missing or out of date."""
    tex_path = Path(tex_path)
    dds_path = CACHE_DIR / (tex_path.stem + ".dds")
    try:
        if dds_path.exists() and dds_path.stat().st_mtime >= tex_path.stat().st_mtime:
            return str(dds_path)
        return tex_to_dds(tex_path, dds_path)
    except (TexError, OSError) as err:
        print(f".TEX conversion failed - {err}")
        return None


def clear_cache():
    if not CACHE_DIR.is_dir():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.dds"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
