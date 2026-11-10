"""Compiler: build new .car (CoreUI asset catalog) BOM containers.

The writer mirrors real Xcode-produced files (sample G1).  Block layout is
the one used here (nothing in the format requires Apple's exact order):

    block 1     CARHEADER            ('RATC', 436 bytes)      var CARHEADER
    next        rendition key blocks (per RENDITIONS leaf entry)
    next        rendition CSI blocks ('ISTC' + TLVs + 'MLEC' payload)
    next        RENDITIONS leaf path node, then the 'tree' header   var RENDITIONS
    next        facet name key blocks, then FacetToken value blocks
    next        FACETKEYS leaf path node, then its 'tree' header    var FACETKEYS
    [optional]  APPEARANCEKEYS tree blocks (absent in G1/G2)        var APPEARANCEKEYS
    next        KEYFORMAT            ('tmfk')                    var KEYFORMAT
    next        EXTENDED_METADATA    ('META')                    var EXTENDED_METADATA
    last        BITMAPKEYS tree + leaf + value block               var BITMAPKEYS

Notes validated against the Rust reference crate and the ground truth
catalogs (G1 = Xcode 27.2 plugin assets, G2 = IconFoundation Assets.car):

    * APPEARANCEKEYS is optional: the crate maps its absence to an empty
      appearance map, and neither G1 nor G2 contains it.  The writer skips
      it unless appearance entries are added via :meth:`add_appearance`.
      Appearance payloads are 2-byte LE bitmasks (light/any b"\x00\x00",
      dark b"\x01\x00"); rendition-key attribute 7 (ThemeAppearance —
      confirmed 7, not the dbg.re article's 6, from live WatchSimulator
      catalogs) selects the variant.
    * BITMAPKEYS is present in both ground-truth files.  Its leaf entry's
      key "block id" dangles (Xcode points it at a sparse identifier value,
      not an allocated block); we reproduce that pattern and copy G1's
      64-byte bitmap-key value record verbatim.  The Rust crate never
      reads BITMAPKEYS.
    * RENDITIONS and FACETKEYS leaf entries must be ordered by raw key
      bytes (unsigned byte order) exactly like real trees.
    * Rendition image payloads are emitted as 'MLEC' with payload version
      0 (single chunk, no part headers) and compression code 0 (raw) or
      2 (gzip); the Rust crate decodes both.
"""

from __future__ import annotations

import dataclasses
import struct
from dataclasses import dataclass, field

from . import blocks, structs as S
from .bom import BOMWriter, tree_header
from .blocks import (CarHeader, CSIHeader, FacetToken, Flags, KeyFormat,
                     Metric, Slice, TlvBlendModeOpacity, TlvEXIFOrientation,
                     TlvBytesPerRow, TlvMetrics, TlvSlices)


# --------------------------------------------------------------------------
# Builder input model


@dataclass
class ImageRendition:
    """One flat image rendition (pixel data + compression) to compile."""

    name: str
    pixels: bytes              # width*height*4 bytes, memory order B,G,R,A
    width: int
    height: int
    scale_factor: int = 200    # CSI scale: 100/@1x, 200/@2x
    key_scale: int = 2         # rendition-key Scale attribute (@2x -> 2)
    appearance: int = 0        # rendition-key Appearance (0 = any/light, 1 = dark)
    compression: int = S.COMP_UNCOMPRESSED  # 0 = raw, 2 = gzip
    encoding: bytes = b"BGRA"
    color_model: int = S.COLORMODEL_RGB
    layout: int = S.LAYOUT_ONE_PART_FIXED_SIZE
    exif_orientation: int = 1  # 1 = Normal (as the G2 icon renditions)
    identifier: int = 0x989A   # shared facet / rendition Identifier
    element: int = 85
    part: int = 220
    deployment_target: int = 5
    modification_time: int = 0
    flags: Flags = field(default_factory=Flags)
    uti: bytes | None = None   # optional UTI TLV (absent in G1/G2 samples)


def default_icon_tlvs(image: ImageRendition) -> list:
    """TLV profile mirroring the icon renditions in G2."""
    point_size = image.width * 100 // image.scale_factor
    unit = max(0, point_size // 128)
    tlvs = [
        TlvSlices([Slice(0, 0, 0, 0)]),
        TlvMetrics(Metric(0, 0), Metric(0, 0), Metric(unit, unit)),
        TlvBlendModeOpacity(0, 1.0),
        TlvEXIFOrientation(image.exif_orientation),
        TlvBytesPerRow(image.width * 4),
    ]
    if image.uti is not None:
        tlvs.append(blocks.TlvUTI(image.uti))
    return tlvs


def default_key_values(image: ImageRendition, keyformat: KeyFormat) -> list:
    """Rendition-key slot values for the flat-icon KeyFormat token set."""
    mapping = {
        1: image.element,
        2: image.part,
        7: image.appearance,  # ThemeAppearance (0 = light, 1 = dark)
        17: image.identifier,
        9: 0,    # dimension2
        10: 0,   # state
        14: 0,   # presentation state
        12: image.key_scale,
        19: 0,   # previous state
        18: 0,   # previous value
        25: image.deployment_target,
        26: 0,   # glyph weight
        27: 0,   # glyph size
    }
    return [mapping.get(token, 0) for token in keyformat.tokens]


@dataclass
class Appearance:
    """Optional appearance-variant entry backing the APPEARANCEKEYS tree.

    Ground truth (WatchSimulator IDEInterfaceBuilder catalogs): the payload
    is a 2-byte LE bitmask — light/any = b"\\x00\\x00", dark = b"\\x01\\x00" —
    and rendition-key appearance values match these bits directly.
    """

    name: str
    appearance_bits: bytes = b"\x00\x00"


# --------------------------------------------------------------------------
# The compiler


class CatalogBuilder:
    """Assemble a complete .car from header/meta/key-format plus renditions."""

    def __init__(self, header: CarHeader, extended_metadata: blocks.ExtendedMetadata,
                 key_format: KeyFormat, bitmapkeys_value: bytes | None = None):
        self.header = header
        self.extended_metadata = extended_metadata
        self.key_format = key_format
        self.entries: list[tuple[str, FacetToken, ImageRendition, list, list]] = []
        self.raw_renditions: list[tuple[bytes, bytes]] | None = None
        self.raw_facets: list[tuple[bytes, bytes]] | None = None
        self.bitmapkeys_key_id: int | None = None
        self.appearances: list[Appearance] = []
        self.bitmapkeys_value = (bitmapkeys_value if bitmapkeys_value is not None
                                 else G1_BITMAPKEYS_VALUE)

    # -- inputs ---------------------------------------------------------------

    def add_image(self, image: ImageRendition, facet_name: str | None = None) -> None:
        """Register one facet + rendition using the default icon profile."""
        facet_name = facet_name or image.name
        token = FacetToken(0, 0, [(1, image.element), (2, image.part),
                                  (17, image.identifier)])
        tlvs = default_icon_tlvs(image)
        key_values = default_key_values(image, self.key_format)
        self.entries.append((facet_name, token, image, key_values, tlvs))

    def add_raw_rendition(self, key_bytes: bytes, csi_block: bytes) -> None:
        """Pass-through mode: reuse (rendition key, CSI block) verbatim, as
        captured from an existing catalog (used by the round-trip tests)."""
        self.raw_renditions = (self.raw_renditions or []) + [(key_bytes, csi_block)]

    def add_raw_facet(self, name: str, token_bytes: bytes) -> None:
        """Pass-through mode for an existing facet (serialized FacetToken)."""
        self.raw_facets = (self.raw_facets or []) + [(name.encode("utf-8"),
                                                     token_bytes)]

    def add_appearance(self, appearance: Appearance) -> None:
        self.appearances.append(appearance)

    # -- serialization ------------------------------------------------------------

    def build(self) -> bytes:
        """Serialize everything; rendition_count is always recomputed."""
        writer = BOMWriter()

        def emit(payload: bytes, var: str | None = None) -> int:
            block_id = writer.add_block(payload)
            if var is not None:
                writer.add_var(var, block_id)
            return block_id

        rend_entries = self.raw_renditions
        if rend_entries is None:
            rend_entries = []  # (key bytes, csi bytes) in file order
            for _facet_name, _token, image, key_values, tlvs in self.entries:
                rend_entries.append((blocks.encode_key_values(key_values),
                                     self._csi_block(image, tlvs)))
            # Real trees order leaf entries by raw key bytes (unsigned order).
            rend_entries.sort(key=lambda entry: entry[0])
        n_rend = len(rend_entries)

        header = dataclasses.replace(self.header,
                                     rendition_count=n_rend)
        emit(blocks.build_car_header(header), S.VAR_CAR_HEADER)

        key_ids, csi_ids = [], []
        for key, _value in rend_entries:
            key_ids.append(writer.add_block(key))
        for _key, value in rend_entries:
            csi_ids.append(writer.add_block(value))

        # Renditions leaf page, mirroring real Xcode pages byte-for-byte:
        # node header + pointer pairs, a u32 gap, then one inline copy of
        # each key (LE u16 attribute values, exactly the key-block bytes),
        # zero fill up to blockSize (4096), and a trailing reserve of
        # count * inline-key-size zero bytes.  Total page length is
        # 4096 + count * key_size (verified against G1/G2/Watch catalogs).
        key_size = 2 * len(self.key_format.tokens)
        page = struct.pack(">HHII", 1, n_rend, 0, 0)
        for i in range(n_rend):
            page += struct.pack(">II", csi_ids[i], key_ids[i])
        page += struct.pack(">I", 0)                       # gap
        for key, _value in rend_entries:
            page += key                                    # inline key copy
        if len(page) < 4096:
            page += b"\x00" * (4096 - len(page))
        page += b"\x00" * (key_size * n_rend)              # reserve
        rend_leaf_id = writer.add_block(page)
        emit(tree_header(first_path_block=rend_leaf_id, path_count=n_rend,
                         block_size=4096, flags_byte=0,
                         inline_keys=key_size), S.VAR_RENDITIONS)

        # ---- facets: name keys + tokens, then leaf + tree ---------------------
        facet_entries = self.raw_facets
        if facet_entries is None:
            # One facet leaf per unique name (several renditions share a
            # facet, e.g. the size variants of a single asset).
            facet_map: dict[str, FacetToken] = {}
            for name, tok, _img, _kv, _tl in self.entries:
                facet_map.setdefault(name, tok)
            facet_entries = sorted(((name.encode("utf-8"),
                                     blocks.build_facet_token(tok))
                                    for name, tok in facet_map.items()),
                                   key=lambda e: e[0])
        else:
            facet_entries = sorted(facet_entries, key=lambda e: e[0])
        n_facets = len(facet_entries)

        facet_name_ids, facet_token_ids = [], []
        for name, _token in facet_entries:
            facet_name_ids.append(writer.add_block(name))
        for _name, token in facet_entries:
            facet_token_ids.append(writer.add_block(token))

        leaf = struct.pack(">HHII", 1, n_facets, 0, 0)
        for i in range(n_facets):
            leaf += struct.pack(">II", facet_token_ids[i], facet_name_ids[i])
        if len(leaf) < 4096:
            leaf += b"\x00" * (4096 - len(leaf))           # pad to blockSize
        facet_leaf_id = writer.add_block(leaf)
        emit(tree_header(first_path_block=facet_leaf_id, path_count=n_facets,
                         block_size=4096, flags_byte=0,
                         inline_keys=-1), S.VAR_FACET_KEYS)

        # ---- optional appearance entries ---------------------------------------
        if self.appearances:
            appearance_entries = sorted(((a.name.encode("utf-8"), a.appearance_bits)
                                         for a in self.appearances),
                                        key=lambda e: e[0])
            n_appear = len(appearance_entries)
            name_ids, value_ids = [], []
            for name, _bits in appearance_entries:
                name_ids.append(writer.add_block(name))
            for _name, bits in appearance_entries:
                value_ids.append(writer.add_block(bits))
            leaf = struct.pack(">HHII", 1, n_appear, 0, 0)
            for i in range(n_appear):
                leaf += struct.pack(">II", value_ids[i], name_ids[i])
            if len(leaf) < 4096:
                leaf += b"\x00" * (4096 - len(leaf))
            appearance_leaf_id = writer.add_block(leaf)
            emit(tree_header(first_path_block=appearance_leaf_id,
                             path_count=n_appear, block_size=4096,
                             flags_byte=0, inline_keys=-1),
                 S.VAR_APPEARANCE_KEYS)

        # ---- keyformat / metadata -------------------------------------------------
        emit(blocks.build_key_format(self.key_format), S.VAR_KEY_FORMAT)
        emit(blocks.build_extended_metadata(self.extended_metadata),
             S.VAR_EXTENDED_METADATA)

        # ---- bitmapkeys ------------------------------------------------------------
        if self.bitmapkeys_value is not None:
            if self.bitmapkeys_key_id is not None:
                ident = self.bitmapkeys_key_id
            elif self.entries:
                ident = self.entries[0][2].identifier
            else:
                ident = 0x989A
            value_id = writer.add_block(self.bitmapkeys_value)
            leaf = struct.pack(">HHII", 1, 1, 0, 0)
            # Reproduce Apple's dangling key id pattern: the sparse catalog
            # identifier, not an allocated block id.
            leaf += struct.pack(">II", value_id, ident)
            if len(leaf) < 1024:
                leaf += b"\x00" * (1024 - len(leaf))       # blockSize 0x400
            bitmapkeys_leaf_id = writer.add_block(leaf)
            emit(tree_header(first_path_block=bitmapkeys_leaf_id, path_count=1,
                             block_size=1024, flags_byte=1, inline_keys=0),
                 S.VAR_BITMAP_KEYS)

        return writer.build()

    # -- helpers ------------------------------------------------------------------

    def _csi_block(self, image: ImageRendition, tlvs: list) -> bytes:
        csi = CSIHeader(
            version=1,
            flags=image.flags,
            width=image.width,
            height=image.height,
            scale_factor=image.scale_factor,
            encoding=image.encoding,
            color_model=image.color_model,
            modification_time=image.modification_time,
            layout_type=image.layout,
            name=image.name,
            tlv_length=0,
            bitmap_count=1,
            _bitmap_zero=0,
            rendition_length=0,
            tlvs=tlvs,
            payload=None,
        )
        # Compression is encoded as a version-0 single-chunk MLEC payload.
        if image.compression == S.COMP_UNCOMPRESSED:
            payload_data = image.pixels
        elif image.compression == S.COMP_GZIP:
            from .decode import gzip_compress

            payload_data = gzip_compress(image.pixels)
        else:
            raise ValueError(
                f"writer supports compression 0 (raw) or 2 (gzip), "
                f"got {image.compression}")
        csi.payload = blocks.build_theme_payload(image.compression, payload_data)
        return blocks.build_csi_block(csi)


# --------------------------------------------------------------------------
# Constants

# G1's 64-byte BITMAPKEYS value record, copied verbatim as sixteen LE
# u32s (semantics undocumented; the Rust crate never reads this var).
G1_BITMAPKEYS_VALUE = struct.pack(
    "<16I",
    1, 0, 52, 12,
    0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF,
    7, 1, 1, 6, 1,
    0xFFFFFFFF, 32, 530, 14,
)
assert len(G1_BITMAPKEYS_VALUE) == 64


@dataclass
class BuildOptions:
    """Header/metadata overrides; defaults reproduce G1's values."""

    coreui_version: int = 1011
    storage_version: int = 17
    storage_timestamp: int = 0
    schema_version: int = 2
    color_space: int = S.COLORSPACE_SRGB
    key_semantics: int = 1
    main_version_string: str = blocks.G1_MAIN_VERSION
    version_string: str = blocks.G1_VERSION_STRING
    authoring_tool: str = blocks.G1_AUTHORING_TOOL
    thinning_args: str = ""
    deployment_platform_version: str = "26.0"
    deployment_platform: str = "macosx"
    uuid: bytes = b"\x00" * 16


def builder_from_options(options: BuildOptions | None = None,
                         key_tokens: list[int] | None = None) -> CatalogBuilder:
    """Convenience factory preloading G1-sample-like header/metadata.

    ``key_tokens`` optionally overrides the rendition-key attribute token
    list (e.g. prepend 7, ThemeAppearance, for appearance-variant catalogs).
    """
    opts = options or BuildOptions()
    header = CarHeader(
        coreui_version=opts.coreui_version,
        storage_version=opts.storage_version,
        storage_timestamp=opts.storage_timestamp,
        schema_version=opts.schema_version,
        color_space=opts.color_space,
        key_semantics=opts.key_semantics,
        main_version_string=opts.main_version_string,
        version_string=opts.version_string,
        uuid=opts.uuid,
    )
    extended = blocks.ExtendedMetadata(
        thinning_args=opts.thinning_args,
        deployment_platform_version=opts.deployment_platform_version,
        deployment_platform=opts.deployment_platform,
        authoring_tool=opts.authoring_tool,
    )
    key_format = KeyFormat.from_tokens(key_tokens or blocks.DEFAULT_KEY_TOKENS)
    return CatalogBuilder(header, extended, key_format)
