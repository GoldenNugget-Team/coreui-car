"""CoreUI .car reader/writer package.

Public API:
    carlib.load(path)            -> Car
    carlib.get_rust_car_cli()    -> path string usable as x
    carlib.writer.build_catalog(...) -> bytes of a new .car file
"""

from .car import CSIItem, Car, FacetToken, load
from .bom import BOMStore

__all__ = ["Car", "CSIItem", "FacetToken", "BOMStore", "load"]
