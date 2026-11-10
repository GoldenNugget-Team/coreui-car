#!/usr/bin/env python3
"""Build a fresh .car asset catalog containing a flat AppIcon rendition.

Example (default): solid 120x120 @2x ARGB PNG-style AppIcon.

    python3 tools/build_car.py --output AppIcon.car

Options let you tweak size, scale, colors, compression and facet naming.
The output is verified in tests/test_car.py against the Rust reference
crate's car-cli (`cargo run -p car-cli -- info AppIcon.car`).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coreui_car import blocks, structs as S  # noqa: E402
from coreui_car.writer import BuildOptions, ImageRendition, builder_from_options  # noqa: E402


def parse_hex_color(text: str) -> str:
    text = text.strip().lstrip("#")
    expected = {3: 3, 6: 6, 8: 8}
    length = len(text)
    if length not in expected:
        raise ValueError(f"hex color must have 3, 6 or 8 digits: {text!r}")
    if length == 3:
        text = "".join(ch * 2 for ch in text) + "ff"
    elif length == 6:
        text += "ff"
    return text


def make_pixels(brick: str, width: int, height: int, gradient: bool) -> bytes:
    """BGRA pixel buffer, memory order B,G,R,A."""
    rgba = parse_hex_color(brick)
    r, g, b, a = (int(rgba[i:i + 2], 16) for i in (0, 2, 4, 6))
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            index = (y * width + x) * 4
            rgb_r, rgb_g, rgb_b = r, g, b
            if gradient:
                rgb_r = min(255, r + x * 64 // max(1, width))
                rgb_g = min(255, g + (y * 64) // max(1, height))
            # BGRA memory order
            pixels[index] = rgb_b
            pixels[index + 1] = rgb_g
            pixels[index + 2] = rgb_r
            pixels[index + 3] = a
    return bytes(pixels)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="AppIcon.car",
                        help="output .car file path (default AppIcon.car)")
    parser.add_argument("--width", type=int, default=120,
                        help="pixel width (default 120 = 60pt @2x)")
    parser.add_argument("--height", type=int, default=120,
                        help="pixel height (default 120)")
    parser.add_argument("--scale", type=int, default=2, choices=(1, 2, 3),
                        help="scale multiplier (default 2)")
    parser.add_argument("--factor", "--name", type=str, default="AppIcon60x60@2x.png",
                        help="rendition name (default AppIcon60x60@2x.png)")
    parser.add_argument("--facet", default="AppIcon",
                        help="facet name (default AppIcon)")
    parser.add_argument("--color", default="#3366cc",
                        help="solid fill color as hex #RRGGBB[AA] (default #3366cc)")
    parser.add_argument("--gradient", action="store_true",
                        help="add a subtle horizontal gradient for visibility")
    parser.add_argument("--compression", choices=("raw", "gzip"), default="raw",
                        help="payload compression (default raw)")
    parser.add_argument("--scale-attr", type=int, default=None,
                        help="override the rendition-key Scale attribute "
                             "(default = --scale)")
    args = parser.parse_args(argv)

    compression = (S.COMP_GZIP if args.compression == "gzip"
                   else S.COMP_UNCOMPRESSED)
    scale_factor = args.scale * 100
    key_scale = args.scale if args.scale_attr is None else args.scale_attr

    image = ImageRendition(
        name=args.factor,
        pixels=make_pixels(args.color, args.width, args.height, args.gradient),
        width=args.width,
        height=args.height,
        scale_factor=scale_factor,
        key_scale=key_scale,
        compression=compression,
        layout=S.LAYOUT_ONE_PART_FIXED_SIZE,
    )
    builder = builder_from_options(BuildOptions())
    builder.add_image(image, facet_name=args.facet)
    data = builder.build()

    with open(args.output, "wb") as fh:
        fh.write(data)
    print(f"wrote {args.output}: {len(data)} bytes, "
          f"facet {args.facet!r}, rendition {image.name!r} "
          f"({image.width}x{image.height}@{key_scale}x, "
          f"{'gzip' if compression == S.COMP_GZIP else 'raw'} ARGB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
