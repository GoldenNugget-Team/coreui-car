"""LZFSE decompression via a tiny Rust sidecar binary.

The pure-Python port deliberately stops short of entropy-coded LZFSE
('bvn$' blocks with FSE state machines); the format's compressed blocks are
large (the lzfse_rust crate is ~2.5k lines of Rust).  This module shells out
to the sidecar built from the official ``lzfse_rust`` crate.

Build once (cargo must be available):

    (cd tools/car/lzfse-sidecar && cargo build --release --offline)

The binary is located in this order:
    1. $CAR_LZFSE_SIDECAR (explicit override)
    2. <package root>/lzfse-sidecar/target/release/lzfse-sidecar
    3. `lzfse-sidecar` on $PATH

Protocol: stdin carries the compressed bytes; stdout receives the exact
decompressed byte stream (exit code 0).  Anything else is an error.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SIDECAR_REL = Path("lzfse-sidecar") / "target" / "release" / "lzfse-sidecar"


class LzfseError(Exception):
    pass


def sidecar_path() -> Path:
    override = os.environ.get("CAR_LZFSE_SIDECAR")
    if override:
        return Path(override)
    local = PACKAGE_ROOT / SIDECAR_REL
    if local.is_file() and os.access(local, os.X_OK):
        return local
    found = shutil.which("lzfse-sidecar")
    if found:
        return Path(found)
    raise LzfseError(
        "LZFSE sidecar not found. Build it once with:\n"
        "  (cd tools/car/lzfse-sidecar && cargo build --release --offline)\n"
        "or set $CAR_LZFSE_SIDECAR to a prebuilt binary.")


def lzfse_decompress(data: bytes) -> bytes:
    if not data:
        return b""
    path = sidecar_path()
    proc = subprocess.run([str(path)], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise LzfseError(
            f"lzfse sidecar failed (exit {proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def is_available() -> bool:
    try:
        sidecar_path()
        return True
    except LzfseError:
        return False


def main_cli() -> int:  # pragma: no cover
    """Installed by the sidecar build as a fallback Python entrypoint."""
    if not is_available():
        print(__doc__, file=sys.stderr)
        return 1
    data = sys.stdin.buffer.read()
    try:
        sys.stdout.buffer.write(lzfse_decompress(data))
        return 0
    except LzfseError as err:
        print(str(err), file=sys.stderr)
        return 1
