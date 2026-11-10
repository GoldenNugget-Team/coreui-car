#!/usr/bin/env python3
"""Verification suite for the coreui-car CoreUI asset-catalog library.

Run from anywhere:

    python3 tests/test_car.py

Covers the three gates agreed for the port:

  1. Parser summaries for the two ground-truth catalogs
       * G1: Xcode 27.2 IDESXSupportCore plugin Assets.car (small, Deepmap2)
       * G2: IconFoundation framework Assets.car   (large, LZFSE + Deepmap2)
  2. G1 -> rebuild -> re-parse -> ``Car.to_dict()`` structural equality
     (verifies the BOM writer serializes the same tree structure Apple does;
     rendition keys / CSI blocks / facet tokens are reused verbatim).
  3. Writer gate: ``build_car.py`` compiles a fresh flat AppIcon catalog that
     the Rust reference crate's ``car-cli info`` parses (facet AppIcon, one
     rendition, ARGB 120x120 @2x, uncompressed), and our own reader plus
     pixel decode agree.

Environment knobs:
    CAR_CLI      path to car-cli (default inside /tmp/opencode/car-parser)
    G1 / G2      ground-truth catalog paths (see DEFAULTS below)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)           # project root -> coreui_car
sys.path.insert(0, os.path.join(_ROOT, "tools"))   # for `build_car`

from coreui_car import blocks, structs as S  # noqa: E402
from coreui_car import car as carlib  # noqa: E402
from coreui_car.writer import CatalogBuilder  # noqa: E402

CAR_CLI = os.environ.get(
    "CAR_CLI", "/tmp/opencode/car-parser/target/release/car-cli")

PLAIN_RED = "#cc0000"  # build_car.py default color for the gate catalog

G1 = os.environ.get(
    "G1",
    "/home/awesomenull/.swiftpm/swift-sdks/darwin.artifactbundle/Xcode.app"
    "/Contents/Developer/Platforms/MacOSX.platform/Developer/Library/Xcode"
    "/PrivatePlugIns/IDEOSXSupportCore.ideplugin/Contents/Resources"
    "/Assets.car")
G2 = os.environ.get(
    "G2",
    "/home/awesomenull/.swiftpm/swift-sdks/darwin.artifactbundle/Xcode.app"
    "/Contents/Developer/Platforms/MacOSX.platform/System/AssetRuntime"
    "/System/Library/PrivateFrameworks/IconFoundation.framework/Versions"
    "/A/Resources/Assets.car")

_TMP = "/tmp/opencode"


# -- helpers ----------------------------------------------------------------

def parse_json_documents(text: str) -> list:
    """car-cli writes pretty-printed JSON docs; tolerate concatenation."""
    decoder = json.JSONDecoder()
    docs, pos = [], 0
    while pos < len(text):
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos >= len(text):
            break
        obj, pos = decoder.raw_decode(text, pos)
        docs.append(obj)
    return docs


def renditions_by_compression(c: "carlib.Car") -> dict:
    counts: dict = {}
    for item in c.renditions:
        try:
            payload = blocks.parse_payload(item.csi.payload)
        except Exception:
            counts["non-theme"] = counts.get("non-theme", 0) + 1
            continue
        if isinstance(payload, blocks.PayloadThemeCBCK):
            counts[payload.compression] = counts.get(payload.compression, 0) + 1
        else:
            counts["non-image"] = counts.get("non-image", 0) + 1
    return counts


# -- 1) ground-truth summaries ------------------------------------------------

def test_g1_summary() -> None:
    c = carlib.load(G1)
    assert list(c.store.vars) == ["CARHEADER", "RENDITIONS", "FACETKEYS",
                                  "KEYFORMAT", "EXTENDED_METADATA",
                                  "BITMAPKEYS"]
    assert c.header.coreui_version == 1011
    assert c.header.storage_version == 17
    assert c.header.schema_version == 2
    assert c.header.color_space == S.COLORSPACE_SRGB
    assert c.key_format.tokens == [1, 2, 17, 9, 10, 14, 12, 19, 18, 25, 26, 27]
    assert len(c.store.slots) == 256
    assert len(c.renditions) == 17
    assert c.renditions_path_count == 17
    assert len(c.facet_keys) == 1
    assert c.facet_names() == ["macOS.download"]
    assert c.facetkeys_path_count == len(c.facet_keys)
    assert c.appearance_keys == {}
    assert len(c.bitmapkeys) == 1
    layouts = {1004: 2, 1017: 3, 1003: 12}
    assert carlib.Car.rendition_names_histogram(c) == layouts
    assert renditions_by_compression(c) == {11: 2, "non-image": 15}
    # G1's two raster renditions use compression 11 (Deepmap2), which the
    # Python port does not decode; assert their payload shapes instead.
    for item in c.renditions:
        payload = blocks.parse_payload(item.csi.payload)
        if isinstance(payload, blocks.PayloadThemeCBCK):
            assert payload.compression == 11
            assert len(payload.chunks) >= 1


def test_g2_summary() -> None:
    c = carlib.load(G2)
    assert c.header.coreui_version == 1011
    assert c.header.storage_version == 17
    assert c.header.schema_version == 2
    assert c.key_format.tokens == [1, 2, 17, 8, 9, 10, 14, 12, 24, 19, 18]
    assert len(c.store.slots) == 384
    assert len(c.renditions) == 136
    assert c.renditions_path_count == 136
    assert len(c.facet_keys) == 33
    assert c.facetkeys_path_count == 33
    assert len(c.bitmapkeys) == 33
    assert c.appearance_keys == {}
    assert carlib.Car.rendition_names_histogram(c) == {1004: 12, 1003: 68,
                                                      12: 50, 1010: 6}
    assert renditions_by_compression(c) == {4: 35, 11: 27, "non-image": 74}
    # decode every LZFSE (compression 4) rendition to its exact byte count
    lzfse = [it for it in c.renditions
             if isinstance(blocks.parse_payload(it.csi.payload),
                           blocks.PayloadThemeCBCK)
             and blocks.parse_payload(it.csi.payload).compression == 4]
    assert len(lzfse) == 35
    for item in lzfse:
        rows = c.raw_pixel_rows(item)
        bpp = S.ENCODING_BPP[S.ENCODING_BY_TAG[bytes(item.csi.encoding)]]
        assert len(rows) == item.csi.height, item.csi.name
        # Tight rows of width*bpp (stride padding is stripped).
        assert all(len(row) == item.csi.width * bpp for row in rows), item.csi.name
    # GA8 (2 bpp) icon renditions decode to the exact expected raster size.
    ga8 = [it for it in lzfse if bytes(it.csi.encoding) == b" 8AG"]
    assert len(ga8) == 5


# -- 2) G1 structural round trip ------------------------------------------------

def test_g1_roundtrip() -> None:
    c = carlib.load(G1)
    reference = c.to_dict()

    builder = CatalogBuilder(
        c.header, c.extended_metadata, c.key_format,
        bitmapkeys_value=next(iter(c.bitmapkeys.values()), None))
    if c.bitmapkeys:
        # Reproduce Apple's dangling key id in the BITMAPKEYS leaf.
        _pc, ids = c.store.tree_entry_ids(c.store.vars["BITMAPKEYS"])
        builder.bitmapkeys_key_id = ids[0][1]
    for name, token in sorted(c.facet_keys.items()):
        builder.add_raw_facet(name, blocks.build_facet_token(token))
    for key, value in c.store.tree_entries(c.store.vars["RENDITIONS"])[1]:
        builder.add_raw_rendition(key, value)

    out_path = os.path.join(_TMP, "G1rebuilt.car")
    with open(out_path, "wb") as fh:
        writer_bytes = builder.build()
        fh.write(writer_bytes)

    reparsed = carlib.load(out_path)
    assert reparsed.to_dict() == reference, "G1 round-trip diverged"
    # Container-level structure must match as well.
    assert list(reparsed.store.vars) == list(c.store.vars)
    assert len(reparsed.store.slots) == 256
    assert reparsed.renditions_path_count == c.renditions_path_count
    # Dangling BITMAPKEYS key id must reproduce exactly (G1: 0x989A).
    _pc, rebuilt_ids = reparsed.store.tree_entry_ids(
        reparsed.store.vars["BITMAPKEYS"])
    assert rebuilt_ids[0][1] == 39066, rebuilt_ids


# -- 3) writer gate -------------------------------------------------------------

def test_build_appicon() -> None:
    out_path = os.path.join(_TMP, "AppIcon.car")
    from build_car import main as build_main
    assert build_main(["--output", out_path,
                       "--color", PLAIN_RED,
                       "--width", "120", "--height", "120",
                       "--scale", "2"]) == 0

    # Gate A: the Rust reference crate parses it.
    assert os.path.exists(CAR_CLI), f"car-cli missing: {CAR_CLI}"
    proc = subprocess.run([CAR_CLI, "info", out_path],
                          capture_output=True, text=True, check=True)
    docs = parse_json_documents(proc.stdout)
    docs = [d for d in docs if isinstance(d, list)]
    assert docs, proc.stdout
    header, rendition = docs[-1]
    assert rendition["Name"] == "AppIcon"
    assert rendition["RenditionName"] == "AppIcon60x60@2x.png"
    assert rendition["AssetType"] == "Image"
    assert rendition["Layout"] == "OnePartFixedSize"
    assert rendition["Encoding"] == "ARGB"
    assert rendition["ColorModel"] == "RGB"
    assert rendition["Compression"] == "Uncompressed"
    assert rendition["Width"] == 120
    assert rendition["Height"] == 120
    assert rendition["Scale"] == 2
    assert rendition["AttributeScale"] == 2
    assert header["CoreUIVersion"] == 1011
    assert header["SchemaVersion"] == 2
    assert header["RenditionCount"] == 1
    assert header["ColorSpace"] == "srgb"

    # Gate B: our own reader agrees and decodes the pixels.
    c = carlib.load(out_path)
    d = c.to_dict()
    assert list(c.store.vars)[:1] == ["CARHEADER"]
    assert [it.csi.name for it in c.renditions] == ["AppIcon60x60@2x.png"]
    assert c.facet_names() == ["AppIcon"]
    assert c.renditions_path_count == 1
    assert d["key_format"]["tokens"] == [1, 2, 17, 9, 10, 14, 12, 19, 18,
                                         25, 26, 27]
    item = c.renditions[0]
    assert (item.csi.width, item.csi.height, item.csi.scale_factor) == \
        (120, 120, 200)
    assert item.identifier == 0x989A
    assert [(t, v) for t, v in zip(c.key_format.tokens, item.key)][0:3] == \
        [(1, 85), (2, 220), (17, 0x989A)]  # Element, Part, Identifier
    stride, data = c.decoded_image(item)
    assert len(data) == 120 * 120 * 4  # uncompressed BGRA raster
    assert stride == 120 * 4
    rows = c.raw_pixel_rows(item)
    assert len(rows) == 120
    # #cc0000 (red) as BGRA memory order: B,G,R,A = 00,00,cc,ff
    expected_px = bytes.fromhex("0000ccff")
    assert rows[0][:4] == expected_px
    assert rows[-1][-4:] == expected_px
    # The facet's rendition lookup works through the Identifier attribute.
    token = c.facet("AppIcon")
    assert token is not None
    assert len(c.renditions_for_facet(token)) == 1
    # The rebuilt file's header timestamps/count fields match the inputs.
    assert c.header.rendition_count == 1
    assert c.extended_metadata.deployment_platform == "macosx"


def test_gzip_variant_roundtrip() -> None:
    """The writer's compression=2 path must produce a crate-decodable file."""
    out_path = os.path.join(_TMP, "AppIconGz.car")
    from build_car import main as build_main
    assert build_main(["--output", out_path, "--color", "3366cc",
                       "--compression", "gzip"]) == 0
    proc = subprocess.run([CAR_CLI, "info", out_path],
                          capture_output=True, text=True, check=True)
    # The Rust crate reports compression code 2 (gzip) as "Zip".
    docs = [d for d in parse_json_documents(proc.stdout) if isinstance(d, list)]
    assert docs[-1][1]["Compression"] == "Zip", docs
    c = carlib.load(out_path)
    _stride, data = c.decoded_image(c.renditions[0])
    assert len(data) == 120 * 120 * 4


TESTS = [
    test_g1_summary,
    test_g2_summary,
    test_g1_roundtrip,
    test_build_appicon,
    test_gzip_variant_roundtrip,
]


def main() -> int:
    failures = 0
    for test in TESTS:
        name = test.__name__
        try:
            test()
        except Exception as exc:  # surface first failure detail
            failures += 1
            print(f"FAIL {name}: {exc!r}")
            import traceback
            traceback.print_exc()
        else:
            print(f"PASS {name}")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
