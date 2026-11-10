#!/usr/bin/env python3
"""BOM (Bill of Materials) container layer for Apple .car CoreUI asset catalogs.

Implements the BOMStore container exactly as the Rust reference crate
(``/tmp/opencode/car-parser/bom``) models it, plus a small writer used by
``carlib.writer`` to assemble new .car files:

Container header (32 bytes, all fields BIG-endian):
    'BOMStore'(8 bytes magic) + u32 version + u32 block_count +
    u32 index_offset + u32 index_length + u32 vars_offset + u32 vars_length

Block index, at ``index_offset`` (BIG-endian):
    u32 slot_count, then slot_count u32 pairs (offset, length).  Slot 0 is
    the null block (offset=0, length=0); block i lives in slot i.  Apple
    files carry trailing zero padding after the last full slot (20 bytes
    in the Xcode 27 samples); it is copied on write and ignored on read.

Variable (name) table at ``vars_offset`` (BIG-endian):
    u32 var_count, then per var: u32 block_index + u8 name_length + name
    bytes (no padding or terminator).

Tree header block (BIG-endian):
    'tree'(4) + u32 version(=1) + u32 first_path_block +
    u32 block_size(=4096) + u32 full_path_count + u8 zero byte.

Path (B+ tree node) block (BIG-endian):
    u16 is_leaf + u16 entry_count + u32 forward + u32 backward, then
    entry_count u32 pairs (value_block, key_block).  Leaf entries reference
    key/value *blocks* (not inline bytes); internal entries reference the
    child path block through the value field.  Leaf chains link via
    ``forward``.

Ground-truth observations from real Xcode-built Assets.car files:
    - blocks are 16-byte aligned; data starts after a 512-byte region that
      holds only the container header;
    - EOF layout: blocks..., vars table, small gap, block index + pad;
    - vars hold CARHEADER/RENDITIONS/FACETKEYS/KEYFORMAT/EXTENDED_METADATA/
      BITMAPKEYS; APPEARANCEKEYS is optional and absent unless the catalog
      has appearance variants.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

BLOCK_ALIGN = 16  # blocks in Apple .car files are 16-byte aligned
FIRST_DATA_OFFSET = 512  # header then zero padding, exactly like real files
MIN_INDEX_SLOTS = 256  # slot count Xcode uses for small catalogs
INDEX_TRAILING_PAD = 20  # zero bytes after the last index slot in real files


class BOMError(Exception):
    """Raised for malformed BOM containers."""


@dataclass
class PathNode:
    """One B+ tree node (leaf path list or internal dispatch node)."""

    is_leaf: bool = False
    forward: int = 0
    backward: int = 0
    entries: list = field(default_factory=list)  # leaf: (value_id, key_id); internal: (child_id, key_id)

    @classmethod
    def from_bytes(cls, data: bytes, block_id: int = 0) -> "PathNode":
        if len(data) < 12:
            raise BOMError(f"path node block {block_id} too short ({len(data)} bytes)")
        is_leaf, count, forward, backward = struct.unpack_from(">HHII", data, 0)
        entries = []
        p = 12
        for _ in range(count):
            value_id, key_id = struct.unpack_from(">II", data, p)
            p += 8
            entries.append((value_id, key_id))
        return cls(is_leaf=bool(is_leaf), forward=forward, backward=backward,
                   entries=entries)

    def to_bytes(self) -> bytes:
        out = struct.pack(">HHII", int(self.is_leaf), len(self.entries),
                          self.forward, self.backward)
        for value_id, key_id in self.entries:
            out += struct.pack(">II", value_id, key_id)
        return out


def tree_header(first_path_block: int, path_count: int,
                count: int | None = None, *, block_size: int = 4096,
                flags_byte: int = 0, inline_keys: int = 0) -> bytes:
    """Serialize a 29-byte 'tree' header block pointing at the first
    path-block, byte-parallel to the real Xcode-produced files.

    ``count`` (the full-path count) defaults to ``path_count``; Xcode writes
    the same value in both slots.  ``inline_keys`` is the on-disk size of
    one inline rendition key (u16 array) copied into the leaf page tail:
    2 * keyformat-token-count for RENDITIONS trees, -1 (0xffffffff) when a
    tree keeps keys in blocks only (FACETKEYS / APPEARANCEKEYS), 0 for
    BITMAPKEYS.  ``flags_byte`` is 0 except BITMAPKEYS, which carries 1.
    """
    if count is None:
        count = path_count
    x = inline_keys & 0xFFFFFFFF
    return (b"tree"
            + struct.pack(">IIII", 1, first_path_block, block_size, count)
            + struct.pack(">BII", flags_byte, x, 0))


class BOMStore:
    """Read-only view over a parsed .car BOM container."""

    MAGIC = b"BOMStore"

    def __init__(self, data: bytes):
        self.data = data
        if data[:8] != self.MAGIC:
            raise BOMError(f"not a BOM file: {data[:8]!r}")
        (self.version, self.block_count, self.index_offset, self.index_length,
         self.vars_offset, self.vars_length) = struct.unpack(">6I", data[8:32])

        self.slots: list[tuple[int, int]] = []
        p = self.index_offset
        (slot_count,) = struct.unpack_from(">I", data, p)
        p += 4
        for _ in range(slot_count):
            self.slots.append(struct.unpack_from(">II", data, p))
            p += 8

        self.vars: dict[str, int] = {}
        p = self.vars_offset
        (var_count,) = struct.unpack_from(">I", data, p)
        p += 4
        for _ in range(var_count):
            (block_id,) = struct.unpack_from(">I", data, p)
            name_len = data[p + 4]
            name = data[p + 5:p + 5 + name_len].decode("utf-8")
            p += 5 + name_len
            self.vars[name] = block_id

    @classmethod
    def load(cls, path: str) -> "BOMStore":
        with open(path, "rb") as fh:
            return cls(fh.read())

    # -- block access -----------------------------------------------------

    def has_block(self, block_id: int) -> bool:
        return 0 < block_id < len(self.slots)

    def block_byte_range(self, block_id: int) -> tuple[int, int]:
        if not 0 <= block_id < len(self.slots):
            raise KeyError(f"block {block_id} outside index (0..{len(self.slots) - 1})")
        return self.slots[block_id]

    def block(self, block_id: int) -> bytes:
        """Block contents; unknown/missing ids (Apple emits dangling ids in
        the BITMAPKEYS tree) yield b"" instead of raising."""
        if block_id == 0 or not self.has_block(block_id):
            return b""
        offset, length = self.block_byte_range(block_id)
        if length == 0:
            return b""
        return self.data[offset:offset + length]

    def block_tag(self, block_id: int) -> str:
        value = self.block(block_id)
        return value[:4].decode("latin-1") if len(value) >= 4 else ""

    # -- tree walking -------------------------------------------------------

    def tree_entries(self, block_id: int) -> tuple[int, list[tuple[bytes, bytes]]]:
        """Walk the B+ tree whose header block is ``block_id``.

        Mirrors the Rust crate's walk: descend internal nodes to their first
        child, then chain through leaf ``forward`` links.  Returns
        ``(full_path_count, entries)`` with entries as
        ``(key_bytes, value_bytes)`` in leaf order.
        """
        data = self.block(block_id)
        if data[:4] != b"tree":
            raise BOMError(f"block {block_id} is not a tree header: {data[:4]!r}")
        _version, first, _block_size, path_count = struct.unpack_from(">4I", data, 4)

        entries: list[tuple[bytes, bytes]] = []
        node = first
        hops = 0
        while node:
            hops += 1
            if hops > 10_000:
                raise BOMError("tree walk exceeded 10k nodes (cycle?)")
            path = PathNode.from_bytes(self.block(node), node)
            if path.is_leaf:
                for value_id, key_id in path.entries:
                    entries.append((self.block(key_id), self.block(value_id)))
                node = path.forward if path.forward and path.forward != node else 0
            else:
                if not path.entries:
                    break
                node = path.entries[0][0]
        return path_count, entries

    def tree_entry_ids(self, block_id: int) -> tuple[int, list[tuple[int, int]]]:
        """Like :meth:`tree_entries` but returns raw (value_block, key_block) ids."""
        data = self.block(block_id)
        if data[:4] != b"tree":
            raise BOMError(f"block {block_id} is not a tree header: {data[:4]!r}")
        _version, first, _block_size, path_count = struct.unpack_from(">4I", data, 4)
        node = first
        out: list[tuple[int, int]] = []
        while node:
            path = PathNode.from_bytes(self.block(node), node)
            if path.is_leaf:
                out.extend(path.entries)
                node = path.forward if path.forward and path.forward != node else 0
            else:
                if not path.entries:
                    break
                node = path.entries[0][0]
        return path_count, out


class BOMWriter:
    """Assembles a BOM container in the layout Xcode-produced .car uses.

    Usage: ``add_block`` in creation order (block 1 is first), optionally
    ``add_var`` pairs, then ``build``.  Blocks are 16-byte aligned after the
    512-byte header region; the vars table and index are appended at EOF,
    mirroring real files.
    """

    def __init__(self):
        self._blocks: list[bytes] = []  # payload data for blocks 1..n
        self._vars: list[tuple[str, int]] = []

    def add_block(self, data: bytes) -> int:
        """Register a block; returns its block id (>= 1)."""
        if not data:
            raise ValueError("empty block")
        self._blocks.append(data)
        return len(self._blocks)

    def add_var(self, name: str, block_id: int) -> None:
        self._vars.append((name, block_id))

    def build(self) -> bytes:
        if not self._blocks:
            raise ValueError("container has no blocks")

        # vars table: u32 count + per var (u32 block id BE, u8 len, name)
        var_blob = bytearray(struct.pack(">I", len(self._vars)))
        for name, block_id in self._vars:
            encoded = name.encode("utf-8")
            if len(encoded) > 255:
                raise ValueError(f"variable name too long: {name!r}")
            var_blob += struct.pack(">IB", block_id, len(encoded)) + encoded
        var_len = len(var_blob)

        index_slots = max(MIN_INDEX_SLOTS, len(self._blocks) + 1)
        index_len = 4 + 8 * index_slots + INDEX_TRAILING_PAD

        cursor = FIRST_DATA_OFFSET
        block_offsets = []
        for payload in self._blocks:
            cursor = (cursor + BLOCK_ALIGN - 1) & ~(BLOCK_ALIGN - 1)
            block_offsets.append(cursor)
            cursor += len(payload)

        vars_offset = cursor
        cursor += var_len
        index_offset = cursor
        cursor += index_len

        out = bytearray(cursor)
        header = struct.pack(">8s6I", b"BOMStore", 1, len(self._blocks) + 1,
                             index_offset, index_len, vars_offset, var_len)
        out[0:32] = header
        block_id = 1
        for payload, off in zip(self._blocks, block_offsets):
            out[off:off + len(payload)] = payload
            block_id += 1
        out[vars_offset:vars_offset + var_len] = var_blob

        index = bytearray(index_len)
        struct.pack_into(">I", index, 0, index_slots)
        for i, off in enumerate(block_offsets, start=1):
            struct.pack_into(">II", index, 4 + 8 * i, off, len(self._blocks[i - 1]))
        out[index_offset:index_offset + index_len] = index
        return bytes(out)


def parse_car_stage_one(path: str) -> BOMStore:
    """Load a file, used by ad-hoc inspection / tests."""
    return BOMStore.load(path)
