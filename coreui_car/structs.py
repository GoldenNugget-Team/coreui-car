"""Constants and enums for the CoreUI .car format.

Ported one-to-one from the Rust reference crate's ``car/src/model/mod.rs``
and ``car/src/model/rendition.rs`` (deku definitions).  Numeric ids are the
little-endian u32/u16 values stored inside CAR blocks; magic tags are raw
4-byte sequences as they appear in the file.
"""
from __future__ import annotations

import enum

# --------------------------------------------------------------------------
# BOM variable names (trees and blocks)
VAR_RENDITIONS = "RENDITIONS"
VAR_FACET_KEYS = "FACETKEYS"
VAR_CAR_HEADER = "CARHEADER"
VAR_KEY_FORMAT = "KEYFORMAT"
VAR_APPEARANCE_KEYS = "APPEARANCEKEYS"
VAR_EXTENDED_METADATA = "EXTENDED_METADATA"
VAR_BITMAP_KEYS = "BITMAPKEYS"

# --------------------------------------------------------------------------
# Rendition key attribute ids (u16, little-endian in key streams).
# Format: (attr tag -> name).  Renamed "Element/Part/..." exactly as the
# crate's AttributeType enum.
TAG_NAMES = {
    0: "ThemeLook",
    1: "Element",
    2: "Part",
    3: "Size",
    4: "Direction",
    5: "Placeholder",
    6: "Value",
    7: "ThemeAppearance",
    8: "Dimension1",
    9: "Dimension2",
    10: "State",
    11: "Layer",
    12: "Scale",
    13: "Localization",
    14: "PresentationState",
    15: "Idiom",
    16: "Subtype",
    17: "Identifier",
    18: "PreviousValue",
    19: "PreviousState",
    20: "HorizontalSizeClass",
    21: "VerticalSizeClass",
    22: "MemoryLevelClass",
    23: "GraphicsFeatureSetClass",
    24: "DisplayGamut",
    25: "DeploymentTarget",
    26: "GlyphWeight",
    27: "GlyphSize",
}
NAMES_TAGS = {v: k for k, v in TAG_NAMES.items()}


class AttributeType(enum.IntEnum):
    """Rendition-key attribute tags; unknown tags are preserved as ints."""

    ThemeLook = 0
    Element = 1
    Part = 2
    Size = 3
    Direction = 4
    Placeholder = 5
    Value = 6
    ThemeAppearance = 7
    Dimension1 = 8
    Dimension2 = 9
    State = 10
    Layer = 11
    Scale = 12
    Localization = 13
    PresentationState = 14
    Idiom = 15
    Subtype = 16
    Identifier = 17
    PreviousValue = 18
    PreviousState = 19
    HorizontalSizeClass = 20
    VerticalSizeClass = 21
    MemoryLevelClass = 22
    GraphicsFeatureSetClass = 23
    DisplayGamut = 24
    DeploymentTarget = 25
    GlyphWeight = 26
    GlyphSize = 27

    @classmethod
    def label(self, tag: int) -> str:
        try:
            return AttributeType(tag).name
        except ValueError:
            return f"Unknown({tag})"


# Attribute used as the facet/token identifier (points at rendition key slot)
ATTR_IDENTIFIER = AttributeType.Identifier.value


# --------------------------------------------------------------------------
# Rendition layout types (u32 LE inside the CSI header)
class LayoutType(enum.IntEnum):
    Gradient = 6
    Effect = 7
    Vector = 9
    OnePartFixedSize = 10
    OnePartTile = 11
    OnePartScale = 12
    ThreePartHorizontalTile = 20
    ThreePartHorizontalScale = 21
    ThreePartHorizontalUniform = 22
    ThreePartVerticalTile = 23
    ThreePartVerticalScale = 24
    ThreePartVerticalUniform = 25
    NinePartTile = 30
    NinePartScale = 31
    NinePartHorizontalUniformVerticalScale = 32
    NinePartHorizontalScaleVerticalUniform = 33
    NinePartEdgesOnly = 34
    SixPart = 40
    AnimationFilmstrip = 50
    Data = 1000
    ExternalLink = 1001
    LayerStack = 1002
    InternalReference = 1003
    PackedImage = 1004
    NameList = 1005
    UnknownAddObject = 1006
    Texture = 1007
    TextureImage = 1008
    Color = 1009
    MultisizeImage = 1010
    LayerReference = 1012
    ContentRendition = 1013
    RecognitionObject = 1014

    @classmethod
    def label(self, tag: int) -> str:
        try:
            return LayoutType(tag).name
        except ValueError:
            return f"Unknown({tag})"


LAYOUT_INTERNAL_REFERENCE = LayoutType.InternalReference.value
# Flat single-image layouts written by the writer:
LAYOUT_ONE_PART_FIXED_SIZE = LayoutType.OnePartFixedSize.value


# --------------------------------------------------------------------------
# Pixel encodings: 4 raw bytes inside the CSI header.  The file order is the
# Apple tag itself (e.g. b'BGRA').  The crate's historical names follow.
class Encoding(enum.Enum):
    NONE = b"\x00\x00\x00\x00"
    ARGB = b"BGRA"    # 8bpc RGBA memory order B,G,R,A
    DATA = b"ATAD"
    GRAY = b"YARG"
    JPEG = b"GEPJ"
    PDF = b" FDP"
    WEBP = b"PBEW"
    ARGB16 = b"WBGR"
    GA16 = b"61AG"
    GA8 = b" 8AG"
    RGB5 = b"5BGR"
    SVGV = b" GVS"
    HEIF = b"FIEH"

    @property
    def name_tag(self) -> bytes:
        return self.value


ENCODING_BY_TAG = {tag: enc for enc, tag in [(e, e.value) for e in Encoding]}


def encoding_label(raw: bytes) -> str:
    enc = ENCODING_BY_TAG.get(bytes(raw))
    if enc is None:
        return f"Unknown({raw.hex()})"
    if enc is Encoding.ARGB:
        return "ARGB"
    if enc is Encoding.DATA:
        return "Data"
    if enc is Encoding.NONE:
        return "None"
    if enc is Encoding.GRAY:
        return "GRAY"
    names = {Encoding.JPEG: "JPEG", Encoding.PDF: "PDF", Encoding.WEBP: "WebP",
             Encoding.ARGB16: "ARGB16", Encoding.GA16: "GA16", Encoding.GA8: "GA8",
             Encoding.RGB5: "RGB5", Encoding.SVGV: "SVG", Encoding.HEIF: "HEIF"}
    return names[enc]


# Bytes per pixel for raster encodings (mirrors the crate's
# encoding_bytes_per_pixel).
ENCODING_BPP = {
    Encoding.ARGB: 4,
    Encoding.GRAY: 1,
    Encoding.GA8: 2,
    Encoding.ARGB16: 8,
    Encoding.GA16: 4,
    Encoding.RGB5: 2,
}


# --------------------------------------------------------------------------
# Color spaces (u32 LE)
COLORSPACE_LABEL = {
    1: "srgb", 2: "gray_gamma_2_2", 3: "display_p3", 4: "extended_range_srgb",
    5: "extended_linear_srgb", 6: "extended_gray", 257: "system_srgb",
}
COLORSPACE_SRGB = 1
COLORSPACE_DISPLAY_P3 = 3

# Color models (u32 LE)
COLORMODEL_LABEL = {0: "None", 1: "RGB", 2: "Monochrome", 3: "RGB0", 4: "RGBP3"}
COLORMODEL_RGB = 1


# --------------------------------------------------------------------------
# Compression types (u32 LE in 'MLEC' payloads)
COMP_UNCOMPRESSED = 0
COMP_PACKBITS = 1
COMP_GZIP = 2
COMP_LZVN = 3
COMP_LZFSE = 4
COMP_JPEG_LZFSE = 5
COMP_BLURRED = 6
COMP_ASTC = 7
COMP_PALETTE_IMG = 8
COMP_HEVC = 9
COMP_DEEPMAP_LZFSE = 10
COMP_DEEPMAP2 = 11

COMPRESSION_LABEL = {
    COMP_UNCOMPRESSED: "Uncompressed",
    COMP_PACKBITS: "Rle",
    COMP_GZIP: "Zip",
    COMP_LZVN: "Lzvn",
    COMP_LZFSE: "Lzfse",
    COMP_JPEG_LZFSE: "JpegLzfse",
    COMP_BLURRED: "Blurred",
    COMP_ASTC: "Astc",
    COMP_PALETTE_IMG: "PaletteImg",
    COMP_HEVC: "HEVC",
    COMP_DEEPMAP_LZFSE: "DeepmapLzfse",
    COMP_DEEPMAP2: "Deepmap2",
}


class UnsupportedCompression(Exception):
    """Raised for payload compression modes this library cannot decode."""


# --------------------------------------------------------------------------
# Rendition payload magic tags (4 raw bytes at payload start)
PAYLOAD_TAG_COLOR = b"RLOC"      # color (RenditionColor)
PAYLOAD_TAG_RAW_DATA = b"DWAR"   # opaque data blob
PAYLOAD_TAG_THEME_COMPRESSED = b"MLEC"  # compressed image/theme chunks (CBCK)
PAYLOAD_TAG_MULTISIZE = b"SISM"  # multisize image set
PAYLOAD_TAG_CHUNK_HEADER = b"KCBC"  # MLEC v1/v3 chunk part-header magic


# --------------------------------------------------------------------------
# CSI TLV tags (u32 LE, prefixing each TLV)
TLV_TAG_SLICES = 1001
TLV_TAG_METRICS = 1003
TLV_TAG_BLEND_MODE_OPACITY = 1004
TLV_TAG_UTI = 1005
TLV_TAG_EXIF_ORIENTATION = 1006
TLV_TAG_BYTES_PER_ROW = 1007
TLV_TAG_REFERENCE = 1010

# CSI rendition magic is literally 'ISTC' in the byte stream.
CSI_MAGIC = b"ISTC"
CSI_FIXED_HEADER_SIZE = 184

# Car header magic ('RATC') and size
CAR_HEADER_MAGIC = b"RATC"
CAR_HEADER_SIZE = 436

# Extended metadata magic and size
EXTENDED_METADATA_MAGIC = b"META"
EXTENDED_METADATA_SIZE = 4 + 4 * 256

# KeyFormat magic
KEY_FORMAT_MAGIC = b"tmfk"
