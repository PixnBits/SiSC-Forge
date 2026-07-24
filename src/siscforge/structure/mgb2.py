"""MgB₂ structure prototype (Phase 1 skeleton for golden-system EPW).

AlB₂-type hexagonal structure (P6/mmm). Full EPW golden recovery is a
follow-up; this module provides a reproducible bulk cell for campaigns.
"""

from __future__ import annotations

from typing import Any

from pymatgen.core import Lattice, Structure

# Experimental lattice constants (Å) for bulk MgB₂
MGB2_A_ANG: float = 3.086
MGB2_C_ANG: float = 3.524


def build_mgb2(
    a: float = MGB2_A_ANG,
    c: float = MGB2_C_ANG,
) -> Structure:
    """Return a conventional 3-atom hexagonal MgB₂ cell.

    Mg at (0,0,0); B at (1/3, 2/3, 1/2) and (2/3, 1/3, 1/2).
    """
    # Hexagonal lattice matrix (pymatgen Lattice.hexagonal)
    lattice = Lattice.hexagonal(a, c)
    species = ["Mg", "B", "B"]
    frac = [
        [0.0, 0.0, 0.0],
        [1.0 / 3.0, 2.0 / 3.0, 0.5],
        [2.0 / 3.0, 1.0 / 3.0, 0.5],
    ]
    return Structure(lattice, species, frac)


def mgb2_metadata() -> dict[str, Any]:
    """Static metadata for campaigns / golden docs."""
    return {
        "formula": "MgB2",
        "material_family": "mgb2_boride",
        "spacegroup": "P6/mmm",
        "a_ang": MGB2_A_ANG,
        "c_ang": MGB2_C_ANG,
        "notes": "AlB2-type prototype for Phase 1 EPW skeleton",
    }
