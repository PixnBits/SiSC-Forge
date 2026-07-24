"""Transition-metal nitride structure builders (rocksalt binaries + simple ternaries)."""

from __future__ import annotations

from typing import Any

import numpy as np
from pymatgen.core import Lattice, Species, Structure

# Experimental / literature rocksalt lattice constants (Å), conventional cubic cell.
ROCKSALT_LATTICE_CONSTANTS: dict[str, float] = {
    "Nb": 4.392,
    "Ti": 4.242,
    "Zr": 4.577,
    "Hf": 4.525,
    "V": 4.139,
    "Ta": 4.330,
    "Cr": 4.140,
    "Mo": 4.250,
    "W": 4.220,
    "Sc": 4.440,
    "Y": 4.877,
}

DEFAULT_BINARY_METALS: tuple[str, ...] = ("Nb", "Ti", "Zr", "Hf")


def rocksalt_lattice_constant(*metals: str, fractions: list[float] | None = None) -> float:
    """Return rocksalt *a* (Å), using Vegard's law for multi-metal alloys."""
    if not metals:
        raise ValueError("At least one metal is required")
    if fractions is None:
        fractions = [1.0 / len(metals)] * len(metals)
    if len(fractions) != len(metals):
        raise ValueError("fractions must match metals")
    total = sum(fractions)
    if total <= 0:
        raise ValueError("fractions must sum to a positive value")
    a = 0.0
    for m, f in zip(metals, fractions, strict=True):
        key = m if m in ROCKSALT_LATTICE_CONSTANTS else m.capitalize()
        if key not in ROCKSALT_LATTICE_CONSTANTS:
            raise KeyError(
                f"No rocksalt lattice constant for metal {m!r}. "
                f"Known: {sorted(ROCKSALT_LATTICE_CONSTANTS)}"
            )
        a += (f / total) * ROCKSALT_LATTICE_CONSTANTS[key]
    return float(a)


def build_rocksalt_primitive(metal: str, a: float | None = None) -> Structure:
    """Build a 2-atom rocksalt primitive cell (metal + N).

    Uses the conventional cubic rocksalt lattice with basis:
    metal at (0,0,0), N at (0.5,0.5,0.5).
    """
    metal = metal if metal in ROCKSALT_LATTICE_CONSTANTS else metal.capitalize()
    a = a if a is not None else ROCKSALT_LATTICE_CONSTANTS[metal]
    lattice = Lattice.cubic(a)
    return Structure(
        lattice,
        [metal, "N"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )


def build_binary_nitride(metal: str, a: float | None = None) -> Structure:
    """Return a conventional rocksalt MN structure for *metal*."""
    return build_rocksalt_primitive(metal, a=a)


def _metal_site_indices(structure: Structure) -> list[int]:
    return [i for i, site in enumerate(structure) if site.specie.symbol != "N"]


def build_ternary_nitride(
    metal_a: str,
    metal_b: str,
    x: float,
    *,
    supercell: tuple[int, int, int] = (2, 2, 1),
    seed: int = 42,
    ordered: bool = True,
) -> Structure:
    """Build AₓB₁₋ₓN rocksalt structure via ordered or random substitution.

    Parameters
    ----------
    metal_a, metal_b:
        Metal species; *x* is the atomic fraction of *metal_a* on the metal sublattice.
    supercell:
        Expansion of the conventional 2-atom rocksalt cell before substitution.
    seed:
        RNG seed for random substitution (ignored when ``ordered=True`` and
        stoichiometry permits a simple ordered pattern).
    ordered:
        Prefer a deterministic ordered occupation when possible.
    """
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"x must be in [0, 1], got {x}")

    metal_a = metal_a.capitalize()
    metal_b = metal_b.capitalize()
    a = rocksalt_lattice_constant(metal_a, metal_b, fractions=[x, 1.0 - x])
    base = build_rocksalt_primitive(metal_a, a=a)
    structure = base * supercell

    metal_idx = _metal_site_indices(structure)
    n_metal = len(metal_idx)
    n_a = int(round(x * n_metal))
    n_a = max(0, min(n_metal, n_a))

    if n_a == 0:
        species_plan = [metal_b] * n_metal
    elif n_a == n_metal:
        species_plan = [metal_a] * n_metal
    elif ordered:
        # Checkerboard-like: fill first n_a sites in a strided pattern for spread.
        species_plan = [metal_b] * n_metal
        step = max(1, n_metal // n_a)
        placed = 0
        for start in range(step):
            for j in range(start, n_metal, step):
                if placed >= n_a:
                    break
                species_plan[j] = metal_a
                placed += 1
            if placed >= n_a:
                break
        # Top up if striding undershot.
        for j in range(n_metal):
            if placed >= n_a:
                break
            if species_plan[j] != metal_a:
                species_plan[j] = metal_a
                placed += 1
    else:
        rng = np.random.default_rng(seed)
        chosen = set(rng.choice(n_metal, size=n_a, replace=False).tolist())
        species_plan = [metal_a if i in chosen else metal_b for i in range(n_metal)]

    for local_i, site_i in enumerate(metal_idx):
        structure.replace(site_i, Species(species_plan[local_i]))

    structure = structure.get_sorted_structure()
    return structure


def formula_from_structure(structure: Structure) -> str:
    """Pretty reduced formula string."""
    return structure.composition.reduced_formula


def composition_fractions(structure: Structure) -> dict[str, float]:
    """Element → atomic fraction (sums to 1)."""
    comp = structure.composition
    total = comp.num_atoms
    return {el.symbol: float(comp[el] / total) for el in comp.elements}


def enumerate_nitrides(
    *,
    metals: list[str] | None = None,
    ternary_metals: list[str] | None = None,
    x_values: list[float] | None = None,
    formulas: list[str] | None = None,
    supercell: tuple[int, int, int] = (2, 2, 1),
    seed: int = 42,
) -> list[tuple[Structure, dict[str, Any]]]:
    """Enumerate bulk nitride structures with metadata dicts.

    Returns list of ``(structure, meta)`` where *meta* includes formula tags.
    """
    results: list[tuple[Structure, dict[str, Any]]] = []

    if formulas:
        for formula in formulas:
            structure, meta = _structure_from_formula(
                formula, supercell=supercell, seed=seed
            )
            results.append((structure, meta))
        return results

    bin_metals = metals if metals else list(DEFAULT_BINARY_METALS)
    # Binaries
    for m in bin_metals:
        s = build_binary_nitride(m)
        results.append(
            (
                s,
                {
                    "formula": f"{m.capitalize()}N",
                    "kind": "binary",
                    "metals": [m.capitalize()],
                    "x": 1.0,
                },
            )
        )

    # Ternaries
    t_metals = ternary_metals or []
    xs = x_values or []
    if len(t_metals) == 2 and xs:
        a, b = t_metals[0].capitalize(), t_metals[1].capitalize()
        for x in xs:
            # Skip pure end-members already covered as binaries when they are
            # in bin_metals; still emit if not.
            if x <= 0.0:
                if a not in {m.capitalize() for m in bin_metals}:
                    s = build_binary_nitride(b)
                    results.append(
                        (s, {"formula": f"{b}N", "kind": "binary", "metals": [b], "x": 0.0})
                    )
                continue
            if x >= 1.0:
                if b not in {m.capitalize() for m in bin_metals} and a not in {
                    m.capitalize() for m in bin_metals
                }:
                    s = build_binary_nitride(a)
                    results.append(
                        (s, {"formula": f"{a}N", "kind": "binary", "metals": [a], "x": 1.0})
                    )
                elif a not in {m.capitalize() for m in bin_metals}:
                    s = build_binary_nitride(a)
                    results.append(
                        (s, {"formula": f"{a}N", "kind": "binary", "metals": [a], "x": 1.0})
                    )
                continue
            s = build_ternary_nitride(
                a, b, x, supercell=supercell, seed=seed, ordered=True
            )
            results.append(
                (
                    s,
                    {
                        "formula": f"{a}{x:g}{b}{1 - x:g}N",
                        "kind": "ternary",
                        "metals": [a, b],
                        "x": float(x),
                        "supercell": list(supercell),
                    },
                )
            )

    return results


def _structure_from_formula(
    formula: str,
    *,
    supercell: tuple[int, int, int],
    seed: int,
) -> tuple[Structure, dict[str, Any]]:
    """Best-effort parse of simple nitride formulas into structures."""
    from pymatgen.core import Composition

    # Normalize common compact forms
    formula = formula.strip()
    try:
        comp = Composition(formula)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot parse formula {formula!r}") from exc

    elements = {el.symbol: comp[el] for el in comp.elements}
    if "N" not in elements:
        raise ValueError(f"Nitride formula must contain N: {formula!r}")

    metals = [sym for sym in elements if sym != "N"]
    if len(metals) == 1:
        s = build_binary_nitride(metals[0])
        return s, {
            "formula": formula,
            "kind": "binary",
            "metals": metals,
            "x": 1.0,
        }
    if len(metals) == 2:
        m_a, m_b = metals[0], metals[1]
        n_a, n_b = elements[m_a], elements[m_b]
        x = float(n_a / (n_a + n_b))
        s = build_ternary_nitride(
            m_a, m_b, x, supercell=supercell, seed=seed, ordered=True
        )
        return s, {
            "formula": formula,
            "kind": "ternary",
            "metals": [m_a, m_b],
            "x": x,
            "supercell": list(supercell),
        }
    raise ValueError(f"Only binary/ternary nitrides supported, got {formula!r}")
