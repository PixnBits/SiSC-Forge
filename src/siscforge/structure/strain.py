"""Epitaxial strain helpers for Si(001) / Si(111) and free biaxial strain."""

from __future__ import annotations

import math
import re
from typing import Literal

import numpy as np
from pymatgen.core import Lattice, Structure

# Cubic silicon lattice constant (Å), room-temperature experimental.
SI_LATTICE_CONSTANT: float = 5.4307

SubstrateOrientation = Literal["001", "111"]
EpitaxyMatch = Literal["cube_on_cube", "45deg"]


def parse_substrate(substrate: str) -> tuple[str, SubstrateOrientation]:
    """Parse labels like ``Si(001)``, ``Si(111)``, ``si-001`` into (material, hkl)."""
    text = substrate.strip()
    m = re.match(
        r"^(?P<mat>[A-Za-z]+)\s*[\(\-_]?\s*(?P<hkl>001|111)\s*\)?\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        raise ValueError(
            f"Unsupported substrate label {substrate!r}; expected e.g. 'Si(001)' or 'Si(111)'"
        )
    mat = m.group("mat").capitalize()
    hkl: SubstrateOrientation = m.group("hkl")  # type: ignore[assignment]
    if mat != "Si":
        raise ValueError(f"Only Si substrates are supported in Phase 0/2, got {mat!r}")
    return mat, hkl


def substrate_in_plane_spacing(substrate: str) -> float:
    """Effective cubic-matching in-plane lattice target (Å) for coherent epitaxy.

    - Si(001): match film *a* to *a*_Si (cube-on-cube reference)
    - Si(111): match film *a* to *a*_Si / √2  (common effective spacing)
    """
    _, hkl = parse_substrate(substrate)
    if hkl == "001":
        return SI_LATTICE_CONSTANT
    return SI_LATTICE_CONSTANT / np.sqrt(2.0)


def effective_film_spacing(
    film_a: float,
    match: EpitaxyMatch = "cube_on_cube",
) -> float:
    """In-plane film period (Å) used for mismatch vs substrate.

    - ``cube_on_cube``: conventional cubic *a*
    - ``45deg``: diagonal match *a*√2 (rocksalt [110] // Si [100] style)
    """
    if film_a <= 0:
        raise ValueError("film_a must be positive")
    if match == "45deg":
        return float(film_a) * math.sqrt(2.0)
    return float(film_a)


def lattice_mismatch_percent(
    film_in_plane_a: float,
    substrate: str = "Si(001)",
    *,
    match: EpitaxyMatch = "cube_on_cube",
    substrate_a: float | None = None,
) -> float:
    """Misfit (%) = 100 * (a_sub - a_film_eff) / a_film_eff.

    Positive ⇒ substrate larger than effective film spacing (tensile if matched).

    Parameters
    ----------
    match:
        ``cube_on_cube`` or ``45deg`` (diagonal film period).
    substrate_a:
        Override substrate spacing (e.g. buffer lattice when stacking).
    """
    a_sub = float(substrate_a) if substrate_a is not None else substrate_in_plane_spacing(
        substrate
    )
    a_film_eff = effective_film_spacing(film_in_plane_a, match)
    if a_film_eff <= 0:
        raise ValueError("effective film spacing must be positive")
    return 100.0 * (a_sub - a_film_eff) / a_film_eff


def voigt_biaxial(eps_ip: float, eps_zz: float) -> tuple[float, float, float, float, float, float]:
    """Voigt strain tuple (εxx, εyy, εzz, εyz, εxz, εxy)."""
    return (float(eps_ip), float(eps_ip), float(eps_zz), 0.0, 0.0, 0.0)


def apply_biaxial_strain(
    structure: Structure,
    eps_ip: float,
    *,
    poisson_ratio: float = 0.25,
    relax_out_of_plane: bool = True,
) -> tuple[Structure, tuple[float, float, float, float, float, float]]:
    """Apply isotropic biaxial in-plane strain to a structure.

    The *a* and *b* lattice lengths are scaled by ``(1 + eps_ip)``. When
    ``relax_out_of_plane`` is True, *c* is scaled by the continuum estimate
    ``1 - 2 ν ε / (1 - ν)`` for isotropic materials under biaxial strain.
    Fractional coordinates are preserved (affine lattice deformation).

    Returns
    -------
    strained, strain_tensor
        Strained structure and Voigt strain tensor.
    """
    if poisson_ratio < 0 or poisson_ratio >= 1:
        raise ValueError("poisson_ratio must be in [0, 1)")

    scale_ip = 1.0 + eps_ip
    if relax_out_of_plane:
        # ε_zz = -2 ν / (1 - ν) * ε_xx  for equal biaxial strain
        eps_zz = -2.0 * poisson_ratio / (1.0 - poisson_ratio) * eps_ip
        scale_c = 1.0 + eps_zz
    else:
        eps_zz = 0.0
        scale_c = 1.0

    old = structure.lattice
    matrix = old.matrix.copy()
    matrix[0, :] *= scale_ip
    matrix[1, :] *= scale_ip
    matrix[2, :] *= scale_c
    new_lattice = Lattice(matrix)
    strained = Structure(
        new_lattice,
        structure.species,
        structure.frac_coords,
        site_properties=structure.site_properties,
    )
    return strained, voigt_biaxial(eps_ip, eps_zz)


def apply_epitaxial_strain(
    structure: Structure,
    substrate: str = "Si(001)",
    *,
    in_plane_strain: float | None = None,
    poisson_ratio: float = 0.25,
    relax_out_of_plane: bool = True,
    match_substrate: bool = False,
) -> tuple[Structure, tuple[float, float, float, float, float, float], float]:
    """Return a strained copy of *structure* for epitaxial series / substrate match.

    Parameters
    ----------
    in_plane_strain:
        If given, apply this biaxial strain relative to the input structure.
    match_substrate:
        If True (and ``in_plane_strain`` is None), strain the film so its
        in-plane lattice constant matches the substrate target spacing.

    Returns
    -------
    strained, strain_tensor, eps_ip
    """
    parse_substrate(substrate)  # validate early

    if in_plane_strain is not None:
        eps = float(in_plane_strain)
    elif match_substrate:
        a_film = float(structure.lattice.a)
        a_sub = substrate_in_plane_spacing(substrate)
        eps = (a_sub - a_film) / a_film
    else:
        eps = 0.0

    strained, tensor = apply_biaxial_strain(
        structure,
        eps,
        poisson_ratio=poisson_ratio,
        relax_out_of_plane=relax_out_of_plane,
    )
    return strained, tensor, eps
