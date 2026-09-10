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


def build_rocksalt_conventional(metal: str, a: float | None = None) -> Structure:
    """Build the conventional cubic rocksalt cell (8 atoms: 4 M + 4 N).

    *a* is the experimental FCC lattice constant (Å), e.g. 4.392 for NbN.
    """
    metal = metal if metal in ROCKSALT_LATTICE_CONSTANTS else metal.capitalize()
    a = a if a is not None else ROCKSALT_LATTICE_CONSTANTS[metal]
    # Fm-3m: M at 4a, N at 4b — yields correct density (~8.4 g/cm³ for NbN).
    return Structure.from_spacegroup(
        "Fm-3m",
        Lattice.cubic(float(a)),
        [metal, "N"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )


def build_rocksalt_primitive(metal: str, a: float | None = None) -> Structure:
    """Build a 2-atom primitive rocksalt cell (metal + N).

    Important: the conventional cubic lattice constant *a* (e.g. 4.392 Å for NbN)
    must **not** be used as a simple-cubic 2-atom cell — that under-densifies by 4×
    (~2.1 vs ~8.4 g/cm³) and produces soft/imaginary phonons and broken EPW.

    Uses the exact primitive FCC lattice (no spglib float noise). Near-zero
    components like ``1e-16`` from analyzer-derived cells break QE/EPW PAW
    symmetry (``d_matrix … not orthogonal``).
    """
    import numpy as np

    metal = metal if metal in ROCKSALT_LATTICE_CONSTANTS else metal.capitalize()
    a_conv = float(a if a is not None else ROCKSALT_LATTICE_CONSTANTS[metal])
    half = a_conv / 2.0
    # Standard primitive FCC vectors for conventional cubic *a*
    matrix = np.array(
        [
            [0.0, half, half],
            [half, 0.0, half],
            [half, half, 0.0],
        ],
        dtype=float,
    )
    return Structure(
        Lattice(matrix),
        [metal, "N"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )


def build_binary_nitride(
    metal: str,
    a: float | None = None,
    *,
    conventional: bool = False,
) -> Structure:
    """Return a rocksalt MN structure for *metal*.

    Parameters
    ----------
    conventional:
        If True, return the 8-atom cubic cell (lattice *a* = experimental FCC
        constant). Default False → 2-atom primitive cell (cheaper DFPT/EPW).
    """
    if conventional:
        return build_rocksalt_conventional(metal, a=a)
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
        mcap = m.capitalize()
        s = build_binary_nitride(mcap)
        results.append(
            (
                s,
                {
                    "formula": f"{mcap}N",
                    "kind": "binary",
                    "metals": [mcap],
                    "x": 1.0,
                    "conventional_lattice_a": rocksalt_lattice_constant(mcap),
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
    except ValueError as exc:
        raise ValueError(f"Cannot parse formula {formula!r}") from exc

    elements = {el.symbol: comp[el] for el in comp.elements}
    if "N" not in elements:
        raise ValueError(f"Nitride formula must contain N: {formula!r}")

    metals = [sym for sym in elements if sym != "N"]
    if len(metals) == 1:
        s = build_binary_nitride(metals[0])
        meta: dict[str, Any] = {
            "formula": formula,
            "kind": "binary",
            "metals": metals,
            "x": 1.0,
        }
        try:
            meta["conventional_lattice_a"] = rocksalt_lattice_constant(metals[0])
        except KeyError:
            pass
        return s, meta
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


# ---------------------------------------------------------------------------
# Ordered N-vacancy rocksalt supercells (screening — not defect thermodynamics)
# ---------------------------------------------------------------------------
#
# Hypothesis (unverified): ideal stoichiometric δ-NbN is often reported as
# harmonically soft; experimental superconducting NbN is typically N-deficient.
# Ordered N vacancies in a small rocksalt supercell *may* heal optical soft
# modes on Nb mixes — treat that as a screening hypothesis to check with DFPT,
# not as an established result. Do not claim healing from structure generation
# alone. Mirrors the P3.5 nickelate O-vacancy style: curated, symmetry-reduced
# representatives — not a combinatorial vacancy engine.

PATTERN_STOICHIOMETRIC_SC = "stoichiometric_sc"
PATTERN_N_VAC_1 = "n_vac_1"
PATTERN_N_VAC_2 = "n_vac_2"

DEFAULT_NVAC_METALS: tuple[str, ...] = ("Nb", "Zr")
DEFAULT_NVAC_COUNTS: tuple[int, ...] = (1, 2)
DEFAULT_NVAC_SUPERCELL: tuple[int, int, int] = (2, 2, 2)

_NVAC_PATTERN_ALIASES: dict[str, str] = {
    "stoichiometric_sc": PATTERN_STOICHIOMETRIC_SC,
    "stoichiometric": PATTERN_STOICHIOMETRIC_SC,
    "parent": PATTERN_STOICHIOMETRIC_SC,
    "n_vac_1": PATTERN_N_VAC_1,
    "nvac_1": PATTERN_N_VAC_1,
    "single_vacancy": PATTERN_N_VAC_1,
    "n_vacancy": PATTERN_N_VAC_1,
    "n_vac_2": PATTERN_N_VAC_2,
    "nvac_2": PATTERN_N_VAC_2,
    "double_vacancy": PATTERN_N_VAC_2,
}


def normalize_nvac_metal(symbol: str) -> str:
    """Return a rocksalt nitride metal with a known lattice constant."""
    key = (symbol or "").strip()
    key = key[:1].upper() + key[1:].lower() if key else key
    if key not in ROCKSALT_LATTICE_CONSTANTS:
        raise ValueError(
            f"Unsupported metal {symbol!r} for nitride N-vacancy cells. "
            f"Known: {sorted(ROCKSALT_LATTICE_CONSTANTS)}"
        )
    return key


def nvac_count_to_pattern(n_vacancies: int) -> str:
    """Map a vacancy count onto a canonical pattern id."""
    n = int(n_vacancies)
    if n < 0:
        raise ValueError(f"n_vacancies must be ≥ 0, got {n}")
    if n == 0:
        return PATTERN_STOICHIOMETRIC_SC
    if n == 1:
        return PATTERN_N_VAC_1
    if n == 2:
        return PATTERN_N_VAC_2
    return f"n_vac_{n}"


def normalize_nvac_pattern(name: str) -> str:
    """Map a user/alias pattern name onto a canonical N-vacancy pattern id."""
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in _NVAC_PATTERN_ALIASES:
        return _NVAC_PATTERN_ALIASES[key]
    if key.startswith("n_vac_"):
        try:
            return nvac_count_to_pattern(int(key.split("_")[-1]))
        except ValueError as exc:
            raise ValueError(f"Unknown nitride N-vacancy pattern {name!r}") from exc
    raise ValueError(
        f"Unknown nitride N-vacancy pattern {name!r}. "
        f"Known: {list(_NVAC_PATTERN_ALIASES)}"
    )


def nvac_pattern_to_count(pattern: str) -> int:
    """Vacancy count encoded by a canonical pattern id."""
    pat = normalize_nvac_pattern(pattern)
    if pat == PATTERN_STOICHIOMETRIC_SC:
        return 0
    if pat.startswith("n_vac_"):
        return int(pat.split("_")[-1])
    raise ValueError(f"Unhandled N-vacancy pattern {pat!r}")


def nvac_structure_key(
    metal: str,
    n_vacancies: int,
    supercell: tuple[int, int, int] = DEFAULT_NVAC_SUPERCELL,
) -> str:
    """Stable identity for (metal, vacancy count, supercell) triples."""
    sc = (int(supercell[0]), int(supercell[1]), int(supercell[2]))
    return f"nitride_nvac:{metal}:{nvac_count_to_pattern(n_vacancies)}:{sc[0]}x{sc[1]}x{sc[2]}"


def _nitrogen_site_indices(structure: Structure) -> list[int]:
    return [i for i, site in enumerate(structure) if site.specie.symbol == "N"]


def _pick_unique_site(structure: Structure, indices: list[int]) -> int:
    """Deterministic representative: lowest rounded (x, y, z), then index."""
    if not indices:
        raise ValueError("no candidate sites to pick")

    def _key(i: int) -> tuple[float, float, float, int]:
        x, y, z = (round(float(v) % 1.0, 6) for v in structure[i].frac_coords)
        return (x, y, z, i)

    return min(indices, key=_key)


def _ordered_vacancy_indices(
    structure: Structure,
    n_vacancies: int,
    nitrogen_indices: list[int] | None = None,
) -> list[int]:
    """Symmetry-reduced ordered N sites to remove (not a combinatorial search).

    * One vacancy: all N sites are equivalent in parent rocksalt → one rep.
    * Two+ vacancies: greedily add the site that maximises the minimum
      Cartesian distance to already chosen vacancies (deterministic tie-break).
    """
    n_vac = int(n_vacancies)
    if n_vac < 0:
        raise ValueError(f"n_vacancies must be ≥ 0, got {n_vac}")
    n_idx = list(nitrogen_indices) if nitrogen_indices is not None else _nitrogen_site_indices(structure)
    if n_vac > len(n_idx):
        raise ValueError(
            f"Cannot remove {n_vac} N sites from a cell with only {len(n_idx)} N atoms"
        )
    if n_vac == 0:
        return []
    chosen = [_pick_unique_site(structure, n_idx)]
    remaining = [i for i in n_idx if i not in chosen]
    while len(chosen) < n_vac:
        def _score(i: int) -> tuple[float, float, float, float, int]:
            # Maximise min distance to chosen; then prefer low frac coords.
            dists = [float(structure[i].distance(structure[j])) for j in chosen]
            min_d = min(dists) if dists else 0.0
            x, y, z = (round(float(v) % 1.0, 6) for v in structure[i].frac_coords)
            return (-min_d, x, y, z, i)

        nxt = min(remaining, key=_score)
        chosen.append(nxt)
        remaining.remove(nxt)
    return chosen


def build_n_vacancy_rocksalt(
    metal: str,
    n_vacancies: int = 1,
    *,
    supercell: tuple[int, int, int] = DEFAULT_NVAC_SUPERCELL,
    a: float | None = None,
) -> Structure:
    """Ordered N-deficient rocksalt MN₁₋δ from a primitive-cell supercell.

    Expands the 2-atom primitive rocksalt cell by *supercell*, then removes
    *n_vacancies* nitrogen sites with a deterministic ordered pattern.
    Default ``(2, 2, 2)`` → 8 formula units (16 sites before vacancy).

    Parameters
    ----------
    metal:
        Transition-metal species (Nb, Zr, …).
    n_vacancies:
        Number of N atoms to remove (0 = stoichiometric supercell control).
    supercell:
        Expansion of the **primitive** rocksalt cell (not the conventional 8-atom).
    """
    m = normalize_nvac_metal(metal)
    sc = (int(supercell[0]), int(supercell[1]), int(supercell[2]))
    if any(n < 1 for n in sc):
        raise ValueError(f"supercell components must be ≥ 1, got {sc!r}")
    base = build_rocksalt_primitive(m, a=a)
    structure = base * sc
    remove = _ordered_vacancy_indices(structure, int(n_vacancies))
    if remove:
        structure.remove_sites(remove)
    return structure.get_sorted_structure()


def build_nitride_nvac_pattern(
    metal: str,
    pattern: str | int = PATTERN_N_VAC_1,
    *,
    supercell: tuple[int, int, int] = DEFAULT_NVAC_SUPERCELL,
) -> tuple[Structure, dict[str, Any]]:
    """Build one (structure, metadata) pair for an N-vacancy screening pattern.

    *pattern* may be a canonical / alias name or an integer vacancy count.
    """
    m = normalize_nvac_metal(metal)
    sc = (int(supercell[0]), int(supercell[1]), int(supercell[2]))
    if isinstance(pattern, int):
        n_vac = int(pattern)
        pat = nvac_count_to_pattern(n_vac)
    else:
        pat = normalize_nvac_pattern(str(pattern))
        n_vac = nvac_pattern_to_count(pat)

    parent = build_rocksalt_primitive(m) * sc
    n_parent = int(parent.composition["N"])
    s = build_n_vacancy_rocksalt(m, n_vacancies=n_vac, supercell=sc)
    n_n = int(s.composition["N"])
    n_m = int(s.composition[m])
    delta = float(n_vac) / float(n_parent) if n_parent else 0.0
    # Hypothesis note kept in metadata for operators / exports — not a claim.
    hypothesis = (
        "Hypothesis only: N deficiency may heal optical soft modes seen on "
        "ideal stoichiometric δ-NbN / Nb-rich mixes. Verify with DFPT; do not "
        "overclaim from enumeration."
    )
    meta: dict[str, Any] = {
        "formula": s.composition.reduced_formula,
        "kind": "n_vacancy" if n_vac else "binary_supercell",
        "material_family": "tm_nitride",
        "prototype": "rocksalt",
        "metals": [m],
        "metal": m,
        "vacancy_pattern": pat,
        "pattern_class": "nitrogen_vacancy" if n_vac else "stoichiometric",
        "n_vacancies": n_vac,
        "n_nitrogen": n_n,
        "n_nitrogen_parent": n_parent,
        "n_metal": n_m,
        "vacancy_fraction": delta,
        "delta": delta,
        "supercell": list(sc),
        "structure_key": nvac_structure_key(m, n_vac, sc),
        "conventional_lattice_a": rocksalt_lattice_constant(m),
        "screening_only": True,
        "hypothesis_soft_mode_healing": hypothesis,
        "notes": (
            f"Ordered {n_vac} N vacancy(ies) in a {sc[0]}x{sc[1]}x{sc[2]} "
            f"primitive rocksalt supercell (MN₁₋δ, δ={delta:g}). "
            "Curated screening cell — not defect formation energies. "
            + hypothesis
        ),
    }
    return s, meta


def structure_from_nitride_nvac_metadata(
    *,
    metal: str | None = None,
    formula: str | None = None,
    metadata: dict[str, Any] | None = None,
    supercell: tuple[int, int, int] = DEFAULT_NVAC_SUPERCELL,
) -> tuple[Structure, dict[str, Any]]:
    """Rebuild an N-vacancy screening cell from metadata / shortlist fields."""
    meta_in = dict(metadata or {})
    m = meta_in.get("metal") or metal
    if not m and formula:
        from pymatgen.core import Composition

        comp = Composition(formula)
        metals = [el.symbol for el in comp.elements if el.symbol != "N"]
        if len(metals) != 1:
            raise ValueError(
                f"N-vacancy rebuild needs exactly one metal in formula/metadata; "
                f"got {formula!r}"
            )
        m = metals[0]
    if not m:
        raise ValueError("N-vacancy rebuild requires metal or formula")
    if meta_in.get("vacancy_pattern"):
        pattern: str | int = str(meta_in["vacancy_pattern"])
    elif meta_in.get("n_vacancies") is not None:
        pattern = int(meta_in["n_vacancies"])
    else:
        pattern = PATTERN_N_VAC_1
    sc = meta_in.get("supercell") or list(supercell)
    sc_t = (int(sc[0]), int(sc[1]), int(sc[2]))
    return build_nitride_nvac_pattern(str(m), pattern, supercell=sc_t)


def enumerate_n_vacancy_nitrides(
    *,
    metals: list[str] | None = None,
    n_vacancies: list[int] | None = None,
    patterns: list[str] | None = None,
    supercell: tuple[int, int, int] = DEFAULT_NVAC_SUPERCELL,
    include_stoichiometric: bool = False,
    seed: int = 42,  # noqa: ARG001 — ordered patterns only
) -> list[tuple[Structure, dict[str, Any]]]:
    """Enumerate a tiny curated set of ordered N-vacancy rocksalt cells.

    Default: Nb + Zr × {1, 2} vacancies in a 2×2×2 primitive supercell
    (δ = 1/8 and 2/8). Optionally include the stoichiometric supercell
    control (``include_stoichiometric`` or an explicit 0 / stoichiometric
    pattern). *seed* is accepted for API parity and unused.
    """
    del seed
    metals_use = [normalize_nvac_metal(m) for m in (metals or list(DEFAULT_NVAC_METALS))]
    sc = (int(supercell[0]), int(supercell[1]), int(supercell[2]))

    counts: list[int] = []
    if patterns:
        for name in patterns:
            counts.append(nvac_pattern_to_count(name))
    elif n_vacancies is not None:
        counts = [int(n) for n in n_vacancies]
    else:
        counts = list(DEFAULT_NVAC_COUNTS)
    if include_stoichiometric and 0 not in counts:
        counts = [0, *counts]

    # De-dupe while preserving order
    seen_c: set[int] = set()
    counts_u: list[int] = []
    for c in counts:
        if c in seen_c:
            continue
        seen_c.add(c)
        counts_u.append(c)

    results: list[tuple[Structure, dict[str, Any]]] = []
    seen_keys: set[str] = set()
    for m in metals_use:
        for n_vac in counts_u:
            structure, meta = build_nitride_nvac_pattern(m, n_vac, supercell=sc)
            key = str(meta["structure_key"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append((structure, meta))
    return results
