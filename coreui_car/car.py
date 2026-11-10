"""High-level .car (CoreUI asset catalog) reader.

Parses a .car into Python structures mirroring the Rust reference crate:

    Car
    ├─ header                 CARHEADER ('RATC')
    ├─ extended_metadata      EXTENDED_METADATA ('META')
    ├─ key_format             KEYFORMAT ('tmfk')
    ├─ appearance_keys        APPEARANCEKEYS tree (optional; usually absent)
    ├─ facet_keys             FACETKEYS tree: name -> FacetToken
    └─ renditions: list[CSIItem]  (RENDITIONS tree entries) each with:
         ├─ key              attribute values (u16, KeyFormat order)
         ├─ attrs            [(AttributeType tag, value), ...]
         └─ csi              CSIHeader ('ISTC') with flags, TLVs, payload

Image payloads ('MLEC') decode to raw encoder bytes via carlib.decode for
compression codes 0/1/2/4 (4 needs the Rust sidecar, see carlib/lzfse.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct

from . import blocks, structs as S
from .bom import BOMStore
from .blocks import FacetToken  # re-export


@dataclass
class CSIItem:
    key: tuple  # attribute values in KeyFormat token order
    tags: tuple  # attribute tags, i.e. the catalog KeyFormat token list
    csi: blocks.CSIHeader

    # -- conveniences --------------------------------------------------------

    @property
    def identifier(self):
        tag = S.ATTR_IDENTIFIER
        if tag in self.tags:
            return self.key[self.tags.index(tag)]
        return None

    def attribute(self, tag: int):
        if tag in self.tags:
            return self.key[self.tags.index(tag)]
        return None

    @property
    def name(self) -> str:
        return self.csi.name

    @property
    def width(self) -> int:
        return self.csi.width

    @property
    def height(self) -> int:
        return self.csi.height

    @property
    def scale_factor(self) -> int:
        return self.csi.scale_factor

    @property
    def layout(self) -> int:
        return self.csi.layout_type

    @property
    def encoding(self) -> bytes:
        return self.csi.encoding

    def to_dict(self) -> dict:
        return {
            "key": list(self.key),
            "csi": self.csi.to_dict(),
        }


@dataclass
class AppearanceEntry:
    name: str
    appearance_bits: bytes  # APPEARANCEKEYS value block (opaque)

    def to_dict(self) -> dict:
        return {"name": self.name, "appearance_bits_hex": self.appearance_bits.hex()}


@dataclass
class Car:
    """The parsed asset catalog."""

    header: blocks.CarHeader
    extended_metadata: blocks.ExtendedMetadata
    key_format: blocks.KeyFormat
    facet_keys: dict = field(default_factory=dict)      # name -> FacetToken
    renditions: list = field(default_factory=list)      # list[CSIItem]
    appearance_keys: dict = field(default_factory=dict)  # name -> AppearanceEntry
    bitmapkeys: dict = field(default_factory=dict)      # raw (key(bytes), value(bytes))
    store: BOMStore | None = None
    renditions_path_count: int = 0
    facetkeys_path_count: int = 0

    # -- lookups -------------------------------------------------------------

    def facet(self, name: str) -> FacetToken | None:
        return self.facet_keys.get(name)

    def facet_names(self):
        return sorted(self.facet_keys)

    def renditions_for_facet(self, token: FacetToken):
        """By the CRC-style Identifier attribute matching, i.e. the crate's
        facet-identifier lookup: all renditions whose Identifier attr equals
        the token's Identifier attr."""
        ident = None
        for tag, value in token.attrs:
            if tag == S.ATTR_IDENTIFIER:
                ident = value
                break
        return [it for it in self.renditions if it.identifier == ident]

    def renditions_for_name(self, name: str):
        token = self.facet_keys.get(name)
        return [] if token is None else self.renditions_for_facet(token)

    def rendition_names_histogram(self) -> dict:
        hist = {}
        for it in self.renditions:
            hist[it.csi.layout_type] = hist.get(it.csi.layout_type, 0) + 1
        return hist

    # -- image data ------------------------------------------------------------

    def decoded_image(self, item: CSIItem) -> tuple[None, bytes] | tuple[bytes, bytes]:
        """Returns (bytes_per_row_stride_or_None, decoded_encoder_bytes).

        Only MLEC payloads are decoded; color/raw/multisize payloads are
        not rasters."""
        from .decode import decompress_csiblock

        payload = blocks.parse_payload(item.csi.payload)
        if isinstance(payload, blocks.PayloadThemeCBCK):
            stride = None
            for tlv in item.csi.tlvs:
                if isinstance(tlv, blocks.TlvBytesPerRow):
                    stride = tlv.bytes_per_row
            return stride, decompress_csiblock(payload)
        return None, b""

    def raw_pixel_rows(self, item=None):
        if item is None:
            return []
        _stride, data = self.decoded_image(item)
        from .decode import raw_row_bytes

        bpr = None
        for tlv in item.csi.tlvs:
            if isinstance(tlv, blocks.TlvBytesPerRow):
                bpr = tlv.bytes_per_row
        return raw_row_bytes(data, item.csi.width, item.csi.height,
                             item.csi.encoding, bpr)

    # -- summary ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "header": self.header.to_dict(),
            "extended_metadata": self.extended_metadata.to_dict(),
            "key_format": {"version": self.key_format.version, "tokens": self.key_format.tokens},
            "appearance_keys": {name: entry.to_dict()
                                for name, entry in self.appearance_keys.items()},
            "facet_keys": {name: token.to_dict() for name, token in self.facet_keys.items()},
            "renditions": [it.to_dict() for it in self.renditions],
            "bitmapkeys": {key.hex(): value.hex()
                           for key, value in self.bitmapkeys.items()},
            "renditions_path_count": self.renditions_path_count,
            "facetkeys_path_count": self.facetkeys_path_count,
            "appearance_keys_count": len(self.appearance_keys),
        }

    # -- construction ---------------------------------------------------------------

    @classmethod
    def from_bom_store(cls, store: BOMStore) -> "Car":
        def var_block(name, magic, what) -> bytes:
            if name not in store.vars:
                raise ValueError(f"var {name} missing from BOM store")
            data = store.block(store.vars[name])
            if data[:4] != magic:
                raise ValueError(f"{what} magic mismatch: {data[:4]!r}")
            return data

        key_format = blocks.parse_key_format(
            var_block(S.VAR_KEY_FORMAT, S.KEY_FORMAT_MAGIC, "KEYFORMAT"))
        header = blocks.parse_car_header(
            var_block(S.VAR_CAR_HEADER, S.CAR_HEADER_MAGIC, "CARHEADER"))
        extended_metadata = blocks.parse_extended_metadata(
            var_block(S.VAR_EXTENDED_METADATA, S.EXTENDED_METADATA_MAGIC,
                      "EXTENDED_METADATA"))

        # APPEARANCEKEYS is optional (crate special-cases the missing var).
        appearance: dict[str, AppearanceEntry] = {}
        if S.VAR_APPEARANCE_KEYS in store.vars:
            _pc, entries = store.tree_entries(store.vars[S.VAR_APPEARANCE_KEYS])
            for key, value in entries:
                appearance[key.decode("utf-8")] = AppearanceEntry(
                    key.decode("utf-8"), value)

        facets: dict[str, FacetToken] = {}
        pc_fk, fk_entries = store.tree_entries(store.vars[S.VAR_FACET_KEYS])
        for key, value in fk_entries:
            facets[key.decode("utf-8")] = blocks.parse_facet_token(value)

        renditions: list[CSIItem] = []
        pc_rd, rd_entries = store.tree_entries(store.vars[S.VAR_RENDITIONS])
        for key, value in rd_entries:
            count = len(key_format.tokens)
            if len(key) < 2 * count:
                raise ValueError(f"rendition key too short: {len(key)} bytes")
            values = struct.unpack_from("<%dH" % count, key, 0)
            csi = blocks.parse_csi(value)
            renditions.append(CSIItem(values, tuple(key_format.tokens), csi))

        bitmapkeys: dict[bytes, bytes] = {}
        if S.VAR_BITMAP_KEYS in store.vars:
            _pc, bk_entries = store.tree_entry_ids(store.vars[S.VAR_BITMAP_KEYS])
            for value_id, key_id in bk_entries:
                key = store.block(key_id) if store.has_block(key_id) else (
                    key_id.to_bytes(4, "little"))
                value = store.block(value_id) if store.has_block(value_id) else None
                if value is not None:
                    bitmapkeys[key] = value

        return cls(header, extended_metadata, key_format, facets, renditions,
                   appearance, bitmapkeys, store, pc_rd, pc_fk)


def load(path: str) -> Car:
    """Parse a .car file into a :class:`Car`."""
    return Car.from_bom_store(BOMStore.load(path))
