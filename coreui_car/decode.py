"""Image payload decompression for CSI renditions.

Supported fully in Python (stdlib only):
    0 raw / Uncompressed
    1 PackBits (Apple RLE)
    2 Zip     (gzip - members are decoded via stdlib zlib; multi-member and
               plain zlib streams are both accepted)

Compression 4 (LZFSE) shells out to the tiny Rust sidecar in
``tools/car/lzfse-sidecar`` built from the ``lzfse_rust`` crate (see
:mod:`carlib.lzfse`).  LZVN / HEVC / Deepmap2 payloads are not decoded here.

Byte semantics ported from ``car/src/decode.rs``: chunks are decompressed
independently and concatenated in order.
"""
from __future__ import annotations

import gzip
import zlib

from . import structs as S
from .lzfse import lzfse_decompress


def decompress_chunk(compression: int, data: bytes) -> bytes:
    if compression == S.COMP_UNCOMPRESSED:
        return bytes(data)
    if compression == S.COMP_PACKBITS:
        return unpack_packbits(data)
    if compression == S.COMP_GZIP:
        return gzip_decompress(data)
    if compression == S.COMP_LZFSE:
        return lzfse_decompress(data)
    raise S.UnsupportedCompression(
        f"compression code {compression} "
        f"({[v for k, v in S.COMPRESSION_LABEL.items() if k == compression]} "
        f"is not implemented in this Python port)")


def decompress_csiblock(cbck) -> bytes:
    """Decode a ParsedPayloadThemeCBCK payload into raw encoder bytes."""
    out = bytearray()
    for _part_header, raw in cbck.chunks:
        out += decompress_chunk(cbck.compression, raw)
    return bytes(out)


def gzip_compress(data: bytes, mtime: int = 0) -> bytes:
    """Single-member gzip compression (the writer path for compression 2)."""
    return gzip.compress(data, mtime=mtime)


def gzip_decompress(data: bytes) -> bytes:
    """Multi-member gzip decompression (the crate uses MultiGzDecoder)."""
    if not data:
        return b""
    out = bytearray()
    if data[:2] == b"\x78":
        try:
            out += zlib.decompress(data)
            return bytes(out)
        except zlib.error:
            pass
    remaining = data
    while remaining:
        dec = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        # Feed everything at once; decompressobj keeps leftover input after
        # each member ends, which lets us loop over subsequent members.
        out += dec.decompress(remaining)
        out += dec.flush()
        remaining = dec.unused_data
        if dec.eof and not remaining:
            break
        if not dec.eof:
            raise ValueError("truncated gzip member in Zip payload")
    return bytes(out)


def unpack_packbits(data: bytes) -> bytes:
    """Apple PackBits decoder.

    Control byte: ``-128`` is a no-op, ``n >= 0`` copies the next ``n + 1``
    literal bytes, ``-127 <= n < 0`` repeats the next byte ``1 - n`` times.
    """
    out = bytearray()
    p = 0
    n = len(data)
    while p < n:
        ctrl = data[p]
        p += 1
        ctrl = struct_unpack_int8(ctrl)
        if ctrl == -128:
            continue
        if ctrl >= 0:
            count = ctrl + 1
            out += data[p:p + count]
            p += count
        else:
            count = 1 - ctrl
            out += data[p:p + 1] * count
            p += 1
    return bytes(out)


def struct_unpack_int8(b: int) -> int:
    return b - 0x100 if b > 127 else b


def raw_row_bytes(decoded: bytes, width: int, height: int, encoding: bytes,
                   bytes_per_row=None) -> list[bytes]:
    """Split a decoded raster into tight rows, stripping stride padding.

    ``encoding`` is the raw 4-byte CSI tag (e.g. b'BGRA').  Iff there is a
    known bytes-per-row TLV larger than the tight row size, padding is
    stripped (mirrors the crate's LZVN stride handling)."""
    bpp = S.ENCODING_BPP.get(S.ENCODING_BY_TAG.get(bytes(encoding)))
    if bpp is None:
        raise S.UnsupportedCompression(f"non-raster encoding {encoding!r} has no row layout")
    tight = width * bpp
    stride = tight
    if bytes_per_row is not None and bytes_per_row >= tight:
        stride = bytes_per_row
    rows = []
    for y in range(height):
        base = stride * y
        rows.append(decoded[base:base + tight])
    return rows


def bgra_to_rgba(rows: list[bytes]) -> bytes:
    """Convert BGRA rows to RGBA (handy for image inspection, no deps)."""
    out = bytearray()
    for row in rows:
        for i in range(0, len(row), 4):
            b, g, r, a = row[i:i + 4]
            out += bytes((r, g, b, a))
    return bytes(out)


def gray_to_rgba8(rows: list[bytes]) -> bytes:
    """Expand 8-bit grayscale rows to RGBA."""
    out = bytearray()
    for row in rows:
        for v in row:
            out += bytes((v, v, v, 0xFF))
    return bytes(out)
