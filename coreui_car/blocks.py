"""Block-level codecs: CARHEADER, EXTENDED_METADATA, KEYFORMAT, facet
tokens, CSI rendition headers with their TLVs, and rendition payloads.

Semantics ported exactly from the Rust reference crate
(``car/src/model/mod.rs`` + ``car/src/model/rendition.rs``).  All CAR block
numeric fields are LITTLE-endian; only the outer BOM container is big-endian
(see bom.py)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from . import structs as S

G1_MAIN_VERSION = "@(#)PROGRAM:CoreUI  PROJECT:CoreUI-1011.3 [LAR]"
G1_VERSION_STRING = "Xcode 27.2 (27B49) via AssetCatalogAgent-AssetRuntime"
G1_AUTHORING_TOOL = ("@(#)PROGRAM:CoreThemeDefinition  PROJECT:CoreThemeDefinition-664 "
                     "[LAR] [IIO-2851.1.5][LAR] AppleJPEG[1][32]  CMPhoto[1][222]] [IR-120(sw)]")


class ParseError(Exception):
    pass


# ==========================================================================
# CARHEADER ('RATC')

@dataclass
class CarHeader:
    coreui_version: int = 1011
    storage_version: int = 17
    storage_timestamp: int = 0
    rendition_count: int = 0
    main_version_string: str = G1_MAIN_VERSION
    version_string: str = G1_VERSION_STRING
    uuid: bytes = b"\x00" * 16
    associated_checksum: int = 0
    schema_version: int = 2
    color_space: int = S.COLORSPACE_SRGB
    key_semantics: int = 1

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "CarHeader":
        return cls(**data)


def parse_car_header(data: bytes) -> CarHeader:
    if data[:4] != S.CAR_HEADER_MAGIC:
        raise ParseError(f"CARHEADER magic mismatch: {data[:4]!r}")
    if len(data) < S.CAR_HEADER_SIZE:
        raise ParseError(f"CARHEADER too short: {len(data)}")
    (coreui, storage, ts, count) = struct.unpack_from("<4I", data, 4)
    main = data[20:148].split(b"\x00")[0].decode("utf-8")
    version = data[148:404].split(b"\x00")[0].decode("utf-8")
    uuid = data[404:420]
    checksum, schema, color_space, key_semantics = struct.unpack_from("<4I", data, 420)
    return CarHeader(coreui, storage, ts, count, main, version, uuid,
                     checksum, schema, color_space, key_semantics)


def build_car_header(header: CarHeader) -> bytes:
    out = bytearray(S.CAR_HEADER_SIZE)
    out[0:4] = S.CAR_HEADER_MAGIC
    struct.pack_into("<4I", out, 4, header.coreui_version, header.storage_version,
                     header.storage_timestamp, header.rendition_count)
    _put_fixed_str(out, 20, 128, header.main_version_string)
    _put_fixed_str(out, 148, 256, header.version_string)
    if len(header.uuid) != 16:
        raise ValueError("uuid must be 16 bytes")
    out[404:420] = header.uuid
    struct.pack_into("<4I", out, 420, header.associated_checksum,
                     header.schema_version, header.color_space, header.key_semantics)
    return bytes(out)


def _put_fixed_str(buf: bytearray, offset: int, size: int, text: str) -> None:
    raw = text.encode("utf-8")
    if len(raw) >= size:
        raise ValueError(f"string too long for {size}-byte field: {len(raw)}")
    buf[offset:offset + size] = raw + b"\x00" * (size - len(raw))


# ==========================================================================
# EXTENDED_METADATA ('META')

@dataclass
class ExtendedMetadata:
    thinning_args: str = ""
    deployment_platform_version: str = ""
    deployment_platform: str = ""
    authoring_tool: str = G1_AUTHORING_TOOL

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "ExtendedMetadata":
        return cls(**data)


def parse_extended_metadata(data: bytes) -> ExtendedMetadata:
    if data[:4] != S.EXTENDED_METADATA_MAGIC:
        raise ParseError(f"EXTENDED_METADATA magic mismatch: {data[:4]!r}")
    if len(data) < S.EXTENDED_METADATA_SIZE:
        raise ParseError(f"EXTENDED_METADATA too short: {len(data)}")
    strings = [data[4 + 256 * i: 4 + 256 * (i + 1)].split(b"\x00")[0].decode("utf-8")
               for i in range(4)]
    return ExtendedMetadata(*strings)


def build_extended_metadata(meta: ExtendedMetadata) -> bytes:
    out = bytearray(S.EXTENDED_METADATA_SIZE)
    out[0:4] = S.EXTENDED_METADATA_MAGIC
    for i, text in enumerate((meta.thinning_args, meta.deployment_platform_version,
                              meta.deployment_platform, meta.authoring_tool)):
        _put_fixed_str(out, 4 + 256 * i, 256, text)
    return bytes(out)


# ==========================================================================
# KEYFORMAT ('tmfk')

@dataclass
class KeyFormat:
    version: int = 0
    tokens: list = field(default_factory=list)  # attribute tags in key order

    @staticmethod
    def from_tokens(tokens: list) -> "KeyFormat":
        return KeyFormat(0, list(tokens))


# Xcode 27's default flat-icon key layout (same as the G1 sample catalog):
DEFAULT_KEY_TOKENS = [1, 2, 17, 9, 10, 14, 12, 19, 18, 25, 26, 27]


def parse_key_format(data: bytes) -> KeyFormat:
    if data[:4] != S.KEY_FORMAT_MAGIC:
        raise ParseError(f"KEYFORMAT magic mismatch: {data[:4]!r}")
    version, count = struct.unpack_from("<2I", data, 4)
    tokens = []
    p = 12
    for _ in range(count):
        (tag,) = struct.unpack_from("<H", data, p)
        p += 4  # attr u16 + 2 reserved bytes
        tokens.append(tag)
    return KeyFormat(version, tokens)


def build_key_format(kf: KeyFormat) -> bytes:
    out = bytearray(12 + 4 * len(kf.tokens))
    out[0:4] = S.KEY_FORMAT_MAGIC
    struct.pack_into("<2I", out, 4, kf.version, len(kf.tokens))
    p = 12
    for tag in kf.tokens:
        struct.pack_into("<HH", out, p, tag, 0)
        p += 4
    return bytes(out)


def encode_key_values(values: list) -> bytes:
    """A RENDITIONS tree key: the u16 attribute values in KeyFormat order."""
    return b"".join(struct.pack("<H", v & 0xFFFF) for v in values)


# ==========================================================================
# Facet keys (FACETKEYS tree values: KeyToken)

@dataclass
class FacetToken:
    x: int = 0
    y: int = 0
    attrs: list = field(default_factory=list)  # (attr_tag, value) pairs

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "attrs": [list(a) for a in self.attrs]}


def parse_facet_token(data: bytes) -> FacetToken:
    x, y, count = struct.unpack_from("<3H", data, 0)
    attrs = []
    p = 6
    for _ in range(count):
        tag, value = struct.unpack_from("<2H", data, p)
        p += 4
        attrs.append((tag, value))
    return FacetToken(x, y, attrs)


def build_facet_token(token: FacetToken) -> bytes:
    out = bytearray(6 + 4 * len(token.attrs))
    struct.pack_into("<3H", out, 0, token.x & 0xFFFF, token.y & 0xFFFF, len(token.attrs))
    p = 6
    for tag, value in token.attrs:
        struct.pack_into("<2H", out, p, tag & 0xFFFF, value & 0xFFFF)
        p += 4
    return bytes(out)


# ==========================================================================
# CSI headers and TLVs

@dataclass
class Flags:
    """CSI flag bitfield (32-bit LE, lsb-first, mirroring the crate)."""

    is_header_flagged_fpo: bool = False
    is_excluded_from_contrast_filter: bool = False
    is_vector_based: bool = False
    is_opaque: bool = False
    bitmap_encoding: int = 0
    opt_out_of_thinning: bool = False
    is_flippable: bool = False
    is_tintable: bool = False
    preserved_vector_representation: bool = False

    def to_u32(self) -> int:
        value = 0
        value |= int(self.is_header_flagged_fpo) << 0
        value |= int(self.is_excluded_from_contrast_filter) << 1
        value |= int(self.is_vector_based) << 2
        value |= int(self.is_opaque) << 3
        value |= (self.bitmap_encoding & 0xF) << 4
        value |= int(self.opt_out_of_thinning) << 8
        value |= int(self.is_flippable) << 9
        value |= int(self.is_tintable) << 10
        value |= int(self.preserved_vector_representation) << 11
        return value

    @classmethod
    def from_u32(cls, raw: int) -> "Flags":
        return cls(
            is_header_flagged_fpo=bool(raw & (1 << 0)),
            is_excluded_from_contrast_filter=bool(raw & (1 << 1)),
            is_vector_based=bool(raw & (1 << 2)),
            is_opaque=bool(raw & (1 << 3)),
            bitmap_encoding=(raw >> 4) & 0xF,
            opt_out_of_thinning=bool(raw & (1 << 8)),
            is_flippable=bool(raw & (1 << 9)),
            is_tintable=bool(raw & (1 << 10)),
            preserved_vector_representation=bool(raw & (1 << 11)),
        )


@dataclass
class Slice:
    x: int
    y: int
    width: int
    height: int


@dataclass
class Metric:
    width: int
    height: int


@dataclass
class TlvSlices:
    slices: list


@dataclass
class TlvMetrics:
    top_right_inset: Metric
    bottom_left_inset: Metric
    image_size: Metric


@dataclass
class TlvBlendModeOpacity:
    blend_mode: int
    opacity: float


@dataclass
class TlvUTI:
    string: bytes  # NUL-terminated not included


@dataclass
class TlvEXIFOrientation:
    orientation: int  # 0 None, 1 Normal, 2 Mirrored, ...
    _tag = S.TLV_TAG_EXIF_ORIENTATION


@dataclass
class TlvBytesPerRow:
    bytes_per_row: int


@dataclass
class TlvReference:
    x: int
    y: int
    width: int
    height: int
    layout: int
    keys: bytes


@dataclass
class TlvUnknown:
    tag: int
    data: bytes


@dataclass
class CSIHeader:
    version: int
    flags: Flags
    width: int
    height: int
    scale_factor: int  # 100 = @1x, 200 = @2x, 300 = @3x
    encoding: bytes    # 4 raw bytes, e.g. b'BGRA'
    color_model: int
    modification_time: int
    layout_type: int
    name: str
    tlv_length: int
    bitmap_count: int
    _bitmap_zero: int
    rendition_length: int
    tlvs: list
    payload: bytes  # raw rendition payload (tag + body), b"" if none

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "flags": {"raw": self.flags.to_u32()},
            "width": self.width,
            "height": self.height,
            "scale_factor": self.scale_factor,
            "encoding": self.encoding.decode("latin-1"),
            "color_model": self.color_model,
            "modification_time": self.modification_time,
            "layout_type": self.layout_type,
            "name": self.name,
            "tlvs": [tlv_to_dict(tlv) for tlv in self.tlvs],
            "payload": payload_to_dict(self.payload),
        }


def tlv_to_dict(tlv: Any) -> dict:
    if isinstance(tlv, TlvUnknown):
        return {"tag": tlv.tag, "data_hex": tlv.data.hex()}
    result = {"tag": tlv._tag if hasattr(tlv, "_tag") else 0}
    return {**result, **tlv.__dict__}


def parse_csi(data: bytes) -> CSIHeader:
    if len(data) < S.CSI_FIXED_HEADER_SIZE:
        raise ParseError(f"CSI block too short: {len(data)}")
    if data[:4] != S.CSI_MAGIC:
        raise ParseError(f"CSI magic mismatch: {data[:4]!r}")
    version = struct.unpack_from("<I", data, 4)[0]
    flags = Flags.from_u32(struct.unpack_from("<I", data, 8)[0])
    width, height, scale = struct.unpack_from("<3I", data, 12)
    encoding = data[24:28]
    color_model, modification_time, layout = struct.unpack_from("<3I", data, 28)
    name = data[40:168].split(b"\x00")[0].decode("utf-8")
    tlv_length, bitmap_count, zero, rendition_length = struct.unpack_from("<4I", data, 168)
    tlvs = parse_tlvs(data[184:184 + tlv_length])
    payload = data[184 + tlv_length: 184 + tlv_length + rendition_length]
    return CSIHeader(version, flags, width, height, scale, encoding, color_model,
                     modification_time, layout, name, tlv_length, bitmap_count,
                     zero, rendition_length, tlvs, payload)


def parse_tlvs(data: bytes) -> list:
    tlvs = []
    p = 0
    total = len(data)
    while p + 8 <= total:
        tag, length = struct.unpack_from("<2I", data, p)
        p += 8
        body = data[p:p + length]
        p += length
        if tag == S.TLV_TAG_SLICES:
            (count,) = struct.unpack_from("<I", body, 0)
            slices = [Slice(*struct.unpack_from("<4I", body, 4 + 16 * i))
                      for i in range(count)]
            tlvs.append(TlvSlices(slices))
        elif tag == S.TLV_TAG_METRICS:
            (count,) = struct.unpack_from("<I", body, 0)
            top, bottom, size = [Metric(*struct.unpack_from("<2I", body, 4 + 8 * i))
                                 for i in range(3)]
            tlvs.append(TlvMetrics(top, bottom, size))
        elif tag == S.TLV_TAG_BLEND_MODE_OPACITY:
            blend_mode, opacity = struct.unpack_from("<If", body, 0)
            tlvs.append(TlvBlendModeOpacity(blend_mode, opacity))
        elif tag == S.TLV_TAG_UTI:
            string_len, _padding = struct.unpack_from("<2I", body, 0)
            tlvs.append(TlvUTI(bytes(body[8:8 + string_len])))
        elif tag == S.TLV_TAG_EXIF_ORIENTATION:
            (orientation,) = struct.unpack_from("<I", body, 0)
            tlvs.append(TlvEXIFOrientation(orientation))
        elif tag == S.TLV_TAG_BYTES_PER_ROW:
            (bytes_per_row,) = struct.unpack_from("<I", body, 0)
            tlvs.append(TlvBytesPerRow(bytes_per_row))
        elif tag == S.TLV_TAG_REFERENCE:
            magic = body[0:4]
            _padding, x, y, width, height = struct.unpack_from("<5I", body, 4)
            (layout,) = struct.unpack_from("<H", body, 24)
            (key_length,) = struct.unpack_from("<I", body, 28)
            tlvs.append(TlvReference(x, y, width, height, layout,
                                     bytes(body[32:32 + key_length])))
        else:
            tlvs.append(TlvUnknown(tag, bytes(body)))
    return tlvs


def tlv_bytes(tlv: Any) -> bytes:
    """Serialize one TLV to its tag + length-prefixed body form."""
    if isinstance(tlv, TlvSlices):
        body = struct.pack("<I", len(tlv.slices))
        for s in tlv.slices:
            body += struct.pack("<4I", s.x, s.y, s.width, s.height)
        return struct.pack("<2I", S.TLV_TAG_SLICES, len(body)) + body
    if isinstance(tlv, TlvMetrics):
        body = struct.pack("<I", 1)
        for m in (tlv.top_right_inset, tlv.bottom_left_inset, tlv.image_size):
            body += struct.pack("<2I", m.width, m.height)
        return struct.pack("<2I", S.TLV_TAG_METRICS, len(body)) + body
    if isinstance(tlv, TlvBlendModeOpacity):
        body = struct.pack("<If", tlv.blend_mode, tlv.opacity)
        return struct.pack("<2I", S.TLV_TAG_BLEND_MODE_OPACITY, len(body)) + body
    if isinstance(tlv, TlvUTI):
        body = struct.pack("<2I", len(tlv.string), 0) + tlv.string
        return struct.pack("<2I", S.TLV_TAG_UTI, len(body)) + body
    if isinstance(tlv, TlvEXIFOrientation):
        body = struct.pack("<I", tlv.orientation)
        return struct.pack("<2I", S.TLV_TAG_EXIF_ORIENTATION, len(body)) + body
    if isinstance(tlv, TlvBytesPerRow):
        body = struct.pack("<I", tlv.bytes_per_row)
        return struct.pack("<2I", S.TLV_TAG_BYTES_PER_ROW, len(body)) + body
    if isinstance(tlv, TlvReference):
        body = struct.pack("<4s5I", b"INLK", 0, tlv.x, tlv.y, tlv.width, tlv.height)
        body += struct.pack("<H", tlv.layout)
        body += struct.pack("<I", len(tlv.keys)) + tlv.keys
        return struct.pack("<2I", S.TLV_TAG_REFERENCE, len(body)) + body
    return struct.pack("<2I", tlv.tag, len(tlv.data)) + tlv.data


def build_csi_block(header: CSIHeader) -> bytes:
    tlv_blob = b"".join(tlv_bytes(tlv) for tlv in header.tlvs)
    rendition_blob = header.payload if header.payload is not None else b""

    out = bytearray(S.CSI_FIXED_HEADER_SIZE)
    out[0:4] = S.CSI_MAGIC
    struct.pack_into("<I", out, 4, header.version)
    struct.pack_into("<I", out, 8, header.flags.to_u32())
    struct.pack_into("<3I", out, 12, header.width, header.height, header.scale_factor)
    out[24:28] = header.encoding
    struct.pack_into("<3I", out, 28, header.color_model, header.modification_time,
                     header.layout_type)
    _put_fixed_str(out, 40, 128, header.name)
    struct.pack_into("<4I", out, 168, len(tlv_blob), header.bitmap_count, 0,
                     len(rendition_blob))
    return bytes(out) + tlv_blob + rendition_blob


# ==========================================================================
# Rendition payloads

@dataclass
class PayloadThemeCBCK:
    """'MLEC' compressed-image payload (chunked)."""

    version: int                  # 0/2: single chunk; 1/3: chunk part headers
    compression: int
    chunks: list = field(default_factory=list)

    @property
    def raw_datas(self):
        chunk_list = self.chunks
        for part_header, raw in chunk_list:
            yield raw

    @property
    def part_heights(self):
        return [part_header[3] if part_header is not None else None
                for part_header, _raw in self.chunks]

    def to_dict(self) -> dict:
        return {
            "tag": S.PAYLOAD_TAG_THEME_COMPRESSED.decode("latin-1"),
            "version": self.version,
            "compression": self.compression,
            "chunk_count": len(self.chunks),
            "chunks": [{"height": h, "size": len(raw)}
                       for h, (_header, raw) in zip(self.part_heights, self.chunks)],
        }


@dataclass
class PayloadColor:
    version: int
    color_space: int
    components: list

    def to_dict(self) -> dict:
        return {"tag": S.PAYLOAD_TAG_COLOR.decode("latin-1"), "version": self.version,
                "color_space": self.color_space,
                "components": list(self.components)}


@dataclass
class PayloadRawData:
    version: int
    data: bytes

    def to_dict(self) -> dict:
        return {"tag": S.PAYLOAD_TAG_RAW_DATA.decode("latin-1"), "version": self.version,
                "size": len(self.data)}


@dataclass
class PayloadMultisizeImageSet:
    version: int
    entries: list  # (width, height, index, idiom)

    def to_dict(self) -> dict:
        return {"tag": S.PAYLOAD_TAG_MULTISIZE.decode("latin-1"), "version": self.version,
                "entries": [list(e) for e in self.entries]}


@dataclass
class PayloadUnknown:
    tag: bytes
    version: int
    data: bytes

    def to_dict(self) -> dict:
        return {"tag": self.tag.decode("latin-1"), "version": self.version,
                "size": len(self.data)}


def parse_payload(payload: bytes):
    if not payload:
        return None
    if len(payload) < 8:
        raise ParseError(f"rendition payload too short: {len(payload)}")
    tag = bytes(payload[:4])
    if tag == S.PAYLOAD_TAG_THEME_COMPRESSED:
        version, compression, raw_len = struct.unpack_from("<3I", payload, 4)
        chunk_list = []
        p = 16
        if version in (0, 2):
            chunk_list.append((None, bytes(payload[p:p + raw_len])))
        elif version in (1, 3):
            for _ in range(raw_len):
                part_tag, arg2, arg3, height, chunk_len = struct.unpack_from("<5I", payload, p)
                p += 20
                chunk_list.append(((part_tag, arg2, arg3, height), bytes(payload[p:p + chunk_len])))
                p += chunk_len
        else:
            raise ParseError(f"unsupported MLEC version {version}")
        return PayloadThemeCBCK(version, compression, chunk_list)
    if tag == S.PAYLOAD_TAG_COLOR:
        version, color_space, count = struct.unpack_from("<3I", payload, 4)
        p = 16
        components = list(struct.unpack_from("<%dd" % count, payload, p))
        return PayloadColor(version, color_space, components)
    if tag == S.PAYLOAD_TAG_RAW_DATA:
        version, raw_len = struct.unpack_from("<2I", payload, 4)
        return PayloadRawData(version, bytes(payload[12:12 + raw_len]))
    if tag == S.PAYLOAD_TAG_MULTISIZE:
        version, count = struct.unpack_from("<2I", payload, 4)
        entries = [struct.unpack_from("<2I2H", payload, 12 + 12 * i) for i in range(count)]
        return PayloadMultisizeImageSet(version, entries)
    version, raw_len = struct.unpack_from("<2I", payload, 4)
    return PayloadUnknown(tag, version, bytes(payload[12:12 + raw_len]))


def payload_to_dict(payload: bytes):
    parsed = parse_payload(payload)
    return {} if parsed is None else parsed.to_dict()


def build_theme_payload(compression: int, data: bytes, version: int = 0) -> bytes:
    """MLEC payload with a single chunk (no part headers) - version 0/2."""
    out = bytearray(16)
    out[0:4] = S.PAYLOAD_TAG_THEME_COMPRESSED
    struct.pack_into("<3I", out, 4, version, compression, len(data))
    return bytes(out + data)


def build_theme_payload_multi(compression: int, chunks, version: int = 1,
                              tag: bytes = S.PAYLOAD_TAG_CHUNK_HEADER) -> bytes:
    """MLEC v1/v3 payload: a counted list of ``tag`` (KCBC) part headers.

    Part-header shape (little-endian, byte-parallel to the ground truth):
        tag[4]  arg2 u32  arg3 u32  height u32  compressed_len u32
    followed by the compressed bytes of that chunk."""
    out = bytearray(16)
    out[0:4] = S.PAYLOAD_TAG_THEME_COMPRESSED
    struct.pack_into("<3I", out, 4, version, compression, len(chunks))
    for part in chunks:
        if part[0] == S.PAYLOAD_TAG_CHUNK_HEADER:
            part_tag, height, data = part
        else:
            part_tag, height, data = tag, part[0], part[1]
        out += part_tag
        out += struct.pack("<4I", 0, 0, height, len(data))
        out += data
    return bytes(out)


def build_raw_data_payload(data: bytes, version: int = 1) -> bytes:
    return b"".join([S.PAYLOAD_TAG_RAW_DATA, struct.pack("<2I", version, len(data)), data])
