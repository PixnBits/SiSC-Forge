"""Heavily boron-doped silicon prototype structures."""

from __future__ import annotations

from typing import Any

import numpy as np
from pymatgen.core import Lattice, Species, Structure

# Diamond cubic Si lattice constant (Å).
SI_DIAMOND_A: float = 5.4307


def build_diamond_si(a: float = SI_DIAMOND_A) -> Structure:
    """Conventional 8-atom diamond cubic silicon cell."""
    lattice = Lattice.cubic(a)
    # Diamond FCC + basis (0,0,0) and (1/4,1/4,1/4)
    coords = [
        [0.0, 0.0, 0.0],
        [0.25, 0.25, 0.25],
        [0.5, 0.5, 0.0],
        [0.75, 0.75, 0.25],
        [0.5, 0.0, 0.5],
        [0.75, 0.25, 0.75],
        [0.0, 0.5, 0.5],
        [0.25, 0.75, 0.75],
    ]
    return Structure(lattice, ["Si"] * 8, coords)


def build_b_doped_si(
    b_fraction: float,
    *,
    supercell: tuple[int, int, int] = (2, 2, 2),
    seed: int = 42,
) -> Structure:
    """Build a Si supercell with a target B atomic fraction.

    Substitutes the nearest integer number of Si sites with B. For very small
    fractions the supercell is expanded if needed so at least one B is present
    when ``b_fraction > 0``.
    """
    if not 0.0 <= b_fraction < 1.0:
        raise ValueError(f"b_fraction must be in [0, 1), got {b_fraction}")

    base = build_diamond_si()
    structure = base * supercell
    n_sites = len(structure)
    n_b = int(round(b_fraction * n_sites))
    if b_fraction > 0 and n_b == 0:
        n_b = 1
    n_b = min(n_b, n_sites)

    if n_b == 0:
        return structure

    rng = np.random.default_rng(seed)
    chosen = rng.choice(n_sites, size=n_b, replace=False)
    for idx in chosen:
        structure.replace(int(idx), Species("B"))
    return structure.get_sorted_structure()


def enumerate_b_doped_si(
    concentrations: list[float],
    *,
    supercell: tuple[int, int, int] = (2, 2, 2),
    seed: int = 42,
) -> list[tuple[Structure, dict[str, Any]]]:
    """Enumerate B:Si prototypes for each concentration."""
    results: list[tuple[Structure, dict[str, Any]]] = []
    for c in concentrations:
        s = build_b_doped_si(c, supercell=supercell, seed=seed)
        actual = s.composition["B"] / s.composition.num_atoms if "B" in s.composition else 0.0
        results.append(
            (
                s,
                {
                    "formula": s.composition.reduced_formula,
                    "kind": "b_doped_si",
                    "b_fraction_target": float(c),
                    "b_fraction_actual": float(actual),
                    "supercell": list(supercell),
                },
            )
        )
    return results
