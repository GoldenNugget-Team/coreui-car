# coreui-car

Pure-Python (stdlib-only) **reader and writer for Apple CoreUI compiled asset
catalogs** — `Assets.car` / `*.car` files. Written for Linux, no PyObjC / macOS
tooling anywhere in the pipeline.

```
coreui_car/
├── bom.py         BOM container: BOMStore (read), BOMWriter (write), B+ trees
├── car.py         high-level Car model (header, META, KEYFORMAT, facets, renditions)
├── blocks.py      block codecs: CARHEADER, EXTENDED_METADATA, KEYFORMAT,
│                  APPEARANCEKEYS, FACETKEYS, CSI rendition blocks + payloads
│                  (MLEC / KCBC, SISM, RLOC, DWAR), TLV builders
├── structs.py     constants: attribute tokens, compression codes, TLV tags
├── decode.py      payload decompression (0 raw, 1 PackBits, 2 gzip, 4 LZFSE)
└── lzfse.py       LZFSE decompression via the Rust sidecar below
tools/build_car.py  CLI: craft a .car from pixels/parameters
lzfse-sidecar/      tiny Rust binary (lzfse_rust) for LZFSE streams
tests/test_car.py   round-trip tests against real Xcode-produced catalogs
```

## Why

`.car` is the BOM + CoreUI container `actool` compiles asset catalogs into.
Both the BOM block store and the CSI rendition format are undocumented; this
project ports them in Python from two sources (see *Format*):

* the **skytoup/car-parser** Rust crate (MIT) — parsing semantics, tree walks,
  chunk/compression model. Where the crate was lenient, the byte-level layout
  was pinned against real Xcode catalogs;
* the **dbg.re "CAR file format"** article — BOM streaming compression and the
  MLEC/CELM/KCBC theme-chunk blocks.

## Status

Verified end-to-end on-device: a catalog **written entirely by this library**
(`coreui_car` `writer` + `bom`) was accepted by iOS CoreUI (iPhone, iOS 27.2)
and rendered a real icon on the springboard, including the dark variant.
App-icon MLEC payloads use the exact Xcode shape — version 3 (light) / 1
(dark), compression 2 (gzip members), `KCBC` chunks of 341 pixel rows
+ tail, `BGRA` encoding.

Lessons baked in that cost real device debugging time:

* rendition ** Identifier attribute must be < 0x8000** — a value ≥ 0x8000 is
  negative when read as signed `int16` and the icon silently falls back to a
  placeholder;
* the `MultisizeImage` holder rendition's CSI `scale_factor` is `0`
  (only the key attribute `Scale` is meaningful);
* appearance names in APPEARANCEKEYS are `UIAppearanceAny` / `UIAppearanceDark`
  / `ISAppearanceTintable` (not —`UIAppearanceTintable`);
* MLEC v0 payloads are ignored for version-1/v3 consumers, only
  v1/v3 with `KCBC` part headers are accepted in practice.

## Usage

As a sibling dependency (no packaging needed):

```python
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__) / ".." / "coreui-car"))

from coreui_car import car, blocks, writer

catalog = car.load("Assets.car")
print(catalog.header, catalog.facet_names(), len(catalog.renditions))
```

Building a flat icon catalog from scratch:

```
python3 tools/build_car.py --output AppIcon.car
```

## Tests

The tests round-trip **real Apple-produced catalogs** (macOS SDK fixtures):

* **G1**: `IDEOSXSupportCore.ideplugin` Resources `Assets.car` (17 renditions, cst2 originally)
* **G2**: `IconFoundation.framework` `Assets.car` (136 renditions, LZFSE/Deepmap2)

Defaults expect those files at the `swift-sdks` artifact-bundle path; both can
be overridden via `G1` / `G2` environment variables. `CAR_CLI` optionally
cross-checks against the Rust crate's `car-cli` (default:
`/tmp/opencode/car-parser/target/release/car-cli`).

```
python3 tests/test_car.py            # 5/5 gates: parse, round-trip, build, gzip
```

For the LZFSE gate the sidecar must be built once:

```
(cargo build --release --offline in lzfse-sidecar/)
```

## License

GPL-3.0-or-later. Format knowledge derived from the MIT-licensed
*car-parser* crate and the dbg.re article; all code here is original.
