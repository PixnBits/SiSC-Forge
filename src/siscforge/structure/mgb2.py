"""MgB₂ structure prototype (Phase 1 golden EPW system).

AlB₂-type hexagonal structure (P6/mmm). Experimental lattice constants.
MgB₂ is a classic **two-gap** superconductor; SiSC-Forge's screening EPW path
uses an **isotropic** average (λ, ω_log → Allen–Dynes / isotropic Eliashberg).
Anisotropic / multi-band Eliashberg is out of scope for Phase 1 screening.
"""

from __future__ import annotations

from typing import Any

from pymatgen.core import Lattice, Structure

# Experimental lattice constants (Å) for bulk MgB₂ (literature AlB₂-type)
MGB2_A_ANG: float = 3.086
MGB2_C_ANG: float = 3.524

# Approximate experimental density (g/cm³) for sanity checks
MGB2_DENSITY_G_CM3: float = 2.57


def build_mgb2(
    a: float = MGB2_A_ANG,
    c: float = MGB2_C_ANG,
) -> Structure:
    """Return a conventional 3-atom hexagonal MgB₂ cell.

    Mg at (0,0,0); B at (1/3, 2/3, 1/2) and (2/3, 1/3, 1/2).
    """
    lattice = Lattice.hexagonal(float(a), float(c))
    species = ["Mg", "B", "B"]
    frac = [
        [0.0, 0.0, 0.0],
        [1.0 / 3.0, 2.0 / 3.0, 0.5],
        [2.0 / 3.0, 1.0 / 3.0, 0.5],
    ]
    return Structure(lattice, species, frac)


def mgb2_metadata() -> dict[str, Any]:
    """Static metadata for campaigns / golden docs / feasibility."""
    return {
        "formula": "MgB2",
        "material_family": "mgb2_boride",
        "spacegroup": "P6/mmm",
        "a_ang": MGB2_A_ANG,
        "c_ang": MGB2_C_ANG,
        "kind": "binary",
        "conventional_lattice_a": MGB2_A_ANG,
        "pairing": "conventional_two_gap",
        "epw_tc_model": "isotropic_average",
        "notes": (
            "AlB2-type bulk MgB2 for Phase 1 EPW golden path. "
            "Two-gap character is averaged isotropically in screening EPW "
            "(no anisotropic Eliashberg)."
        ),
    }
