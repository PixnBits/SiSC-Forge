"""Infinite-layer nickelate prototypes and oxygen-vacancy screening variants (P3.5).

Builds small, documented RNiO₂-family cells (R = Nd, Pr, La) plus a **curated**
set of oxygen-vacancy / apical-O patterns. This is a first-class structure-
generation option for campaigns — not a defect-formation-energy or grand-
canonical thermodynamics workflow.

Golden reference
----------------
NdNiO₂ infinite-layer (P4/mmm), matching the lattice and origin used by the
existing ``examples/ndnio2_*`` mock CIFs (a = 3.92 Å, c = 3.31 Å).

Default screening patterns (intentionally small)
------------------------------------------------
1. ``stoichiometric`` — primitive infinite-layer RNiO₂.
2. ``inplane_vacancy`` — ordered **single** in-plane O vacancy in a 2×2×1
   supercell (R₄Ni₄O₇). All in-plane O sites are symmetrically equivalent
   in the parent P4/mmm cell, so only one representative is emitted.
3. ``apical_o`` — parent-like RNiO₃ with one apical oxygen per formula
   (idealized P4/mmm perovskite). The IL → perovskite reduction path.

Optional named pattern (off by default): ``apical_half`` (R₂Ni₂O₅ in 1×1×2).

Idealized approximations
------------------------
RNiO₃ precursors are typically orthorhombic (Pbnm) with octahedral rotations;
the P4/mmm cell is a common high-symmetry **screening** approximation.
The ordered 2×2×1 single in-plane vacancy is the symmetry-unique minimal
representative — real reduction pathways can be disordered, clustered,
residual-apical, or larger-period. Do not treat these cells as a defect
ensemble or a thermodynamics workflow.

Out of scope
------------
Combinatorial vacancy enumerations, bilayer nickelates, cuprates, real
defect thermodynamics, mixed AL pools (P3.6).
"""

from __future__ import annotations

from typing import Any

from pymatgen.core import Lattice, Structure

# Screening lattice constants (Å). Workstation prototypes, not refined fits.
# Primary experimental anchors (IL, tetragonal a / c):
#   Nd — Hayward et al., J. Am. Chem. Soc. 121, 8843 (1999);
#        Li et al., Nature 572, 624 (2019) and subsequent film XRD
#        (a ≈ 3.92 Å, c ≈ 3.28–3.37 Å). Golden CIF uses 3.92 / 3.31.
#   Pr — film/bulk reports in the same family (a ≈ 3.96 Å, c ≈ 3.31 Å).
#   La — Hayward, Wilson et al., J. Am. Chem. Soc. 125, 12768 (2003)
#        (a ≈ 3.96 Å, c ≈ 3.37 Å).
INFINITE_LAYER_LATTICE: dict[str, tuple[float, float]] = {
    "Nd": (3.92, 3.31),
    "Pr": (3.96, 3.31),
    "La": (3.96, 3.37),
}

# Idealized tetragonal / pseudocubic perovskite (apical-O filled) *a* = *c*.
# Screening values near the common pseudocubic RNiO3 constants
# (~3.81–3.84 Å); real precursors are typically Pbnm with rotations.
PEROVSKITE_LATTICE: dict[str, float] = {
    "Nd": 3.81,
    "Pr": 3.82,
    "La": 3.84,
}

SUPPORTED_RARE_EARTHS: tuple[str, ...] = ("Nd", "Pr", "La")
DEFAULT_RARE_EARTHS: tuple[str, ...] = ("Nd",)

PATTERN_STOICHIOMETRIC = "stoichiometric"
PATTERN_INPLANE_VACANCY = "inplane_vacancy"
PATTERN_APICAL_O = "apical_o"
PATTERN_APICAL_HALF = "apical_half"

DEFAULT_PATTERNS: tuple[str, ...] = (
    PATTERN_STOICHIOMETRIC,
    PATTERN_INPLANE_VACANCY,
    PATTERN_APICAL_O,
)

_PATTERN_ALIASES: dict[str, str] = {
    "stoichiometric": PATTERN_STOICHIOMETRIC,
    "infinite_layer": PATTERN_STOICHIOMETRIC,
    "il": PATTERN_STOICHIOMETRIC,
    "rnio2": PATTERN_STOICHIOMETRIC,
    "inplane_vacancy": PATTERN_INPLANE_VACANCY,
    "single_vacancy": PATTERN_INPLANE_VACANCY,
    "o_vacancy": PATTERN_INPLANE_VACANCY,
    "ordered_vacancy": PATTERN_INPLANE_VACANCY,
    "apical_o": PATTERN_APICAL_O,
    "apical": PATTERN_APICAL_O,
    "perovskite": PATTERN_APICAL_O,
    "rnio3": PATTERN_APICAL_O,
    "apical_half": PATTERN_APICAL_HALF,
    "r2ni2o5": PATTERN_APICAL_HALF,
}

_KNOWN_PATTERNS = (
    PATTERN_STOICHIOMETRIC,
    PATTERN_INPLANE_VACANCY,
    PATTERN_APICAL_O,
    PATTERN_APICAL_HALF,
)


def normalize_rare_earth(symbol: str) -> str:
    """Return a supported rare-earth symbol (Nd / Pr / La)."""
    key = (symbol or "").strip()
    key = key[:1].upper() + key[1:].lower() if key else key
    if key not in INFINITE_LAYER_LATTICE:
        raise ValueError(
            f"Unsupported rare earth {symbol!r} for infinite-layer nickelates. "
            f"Supported: {list(SUPPORTED_RARE_EARTHS)}"
        )
    return key


def normalize_pattern(name: str) -> str:
    """Map a user/alias pattern name onto a canonical pattern id."""
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in _PATTERN_ALIASES:
        raise ValueError(
            f"Unknown nickelate vacancy pattern {name!r}. "
            f"Known: {list(_PATTERN_ALIASES)}"
        )
    return _PATTERN_ALIASES[key]


def resolve_patterns(patterns: list[str] | None, *, max_patterns: int = 8) -> list[str]:
    """Canonical, de-duplicated pattern list capped at *max_patterns*."""
    raw = list(patterns) if patterns else list(DEFAULT_PATTERNS)
    seen: set[str] = set()
    out: list[str] = []
    for name in raw:
        canon = normalize_pattern(name)
        if canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
        if len(out) >= int(max_patterns):
            break
    if not out:
        raise ValueError("nickelate_patterns resolved to an empty list")
    return out


def structure_key(
    rare_earth: str,
    pattern: str,
    supercell: tuple[int, int, int] | None = None,
) -> str:
    """Stable identity for symmetry-reduced (R, pattern, supercell) triples."""
    sc = supercell or (1, 1, 1)
    if pattern in {PATTERN_STOICHIOMETRIC, PATTERN_APICAL_O}:
        sc = (1, 1, 1)
    if pattern == PATTERN_APICAL_HALF:
        sc = (1, 1, 2)
    return f"nickelate:{rare_earth}:{pattern}:{sc[0]}x{sc[1]}x{sc[2]}"


def build_infinite_layer(
    rare_earth: str = "Nd",
    *,
    a: float | None = None,
    c: float | None = None,
) -> Structure:
    """Build the 4-atom infinite-layer RNiO₂ cell (P4/mmm).

    Origin matches the golden ``examples/ndnio2_*`` CIF:

    * R  at (0, 0, 1/2)
    * Ni at (1/2, 1/2, 0)
    * O  at (1/2, 0, 0) and (0, 1/2, 0)  — in-plane, square-planar NiO₂
    """
    r = normalize_rare_earth(rare_earth)
    a0, c0 = INFINITE_LAYER_LATTICE[r]
    a_use = float(a0 if a is None else a)
    c_use = float(c0 if c is None else c)
    return Structure.from_spacegroup(
        "P4/mmm",
        Lattice.tetragonal(a_use, c_use),
        [r, "Ni", "O"],
        [[0.0, 0.0, 0.5], [0.5, 0.5, 0.0], [0.5, 0.0, 0.0]],
    )


def build_apical_oxygen(
    rare_earth: str = "Nd",
    *,
    a: float | None = None,
    c: float | None = None,
) -> Structure:
    """Build idealized P4/mmm RNiO₃ (one apical O per formula, perovskite-like).

    Apical oxygen sits at (1/2, 1/2, 1/2) relative to the IL origin — the
    vacant apical site of infinite-layer RNiO₂. Lattice defaults to the
    documented pseudocubic perovskite *a*.
    """
    r = normalize_rare_earth(rare_earth)
    a_use = float(PEROVSKITE_LATTICE[r] if a is None else a)
    c_use = float(a_use if c is None else c)
    return Structure.from_spacegroup(
        "P4/mmm",
        Lattice.tetragonal(a_use, c_use),
        [r, "Ni", "O", "O"],
        [
            [0.0, 0.0, 0.5],
            [0.5, 0.5, 0.0],
            [0.5, 0.0, 0.0],
            [0.5, 0.5, 0.5],
        ],
    )


def _inplane_oxygen_indices(structure: Structure, *, z_tol: float = 0.15) -> list[int]:
    """Oxygen sites in the NiO₂ plane (fractional *z* near 0 or 1)."""
    out: list[int] = []
    for i, site in enumerate(structure):
        if site.specie.symbol != "O":
            continue
        z = float(site.frac_coords[2]) % 1.0
        if z < z_tol or z > (1.0 - z_tol):
            out.append(i)
    return out


def _pick_unique_site(structure: Structure, indices: list[int]) -> int:
    """Deterministic representative: lowest rounded (x, y, z), then index."""
    if not indices:
        raise ValueError("no candidate sites to pick")

    def _key(i: int) -> tuple[float, float, float, int]:
        x, y, z = (round(float(v) % 1.0, 6) for v in structure[i].frac_coords)
        return (x, y, z, i)

    return min(indices, key=_key)


def build_inplane_vacancy(
    rare_earth: str = "Nd",
    *,
    supercell: tuple[int, int, int] = (2, 2, 1),
) -> Structure:
    """Ordered single in-plane O vacancy in an IL supercell (symmetry-unique).

    Parent P4/mmm in-plane oxygens are equivalent; only one representative
    is generated (no combinatorial explosion).
    """
    if any(int(n) < 1 for n in supercell):
        raise ValueError(f"supercell components must be ≥ 1, got {supercell!r}")
    base = build_infinite_layer(rare_earth)
    structure = base * tuple(int(n) for n in supercell)
    inplane = _inplane_oxygen_indices(structure)
    if not inplane:
        raise ValueError("no in-plane oxygen sites found in infinite-layer supercell")
    remove_i = _pick_unique_site(structure, inplane)
    structure.remove_sites([remove_i])
    return structure.get_sorted_structure()


def build_apical_half(rare_earth: str = "Nd") -> Structure:
    """Ordered half-apical occupancy: R₂Ni₂O₅ in a 1×1×2 IL stack.

    One apical oxygen between two IL formula units. Optional screening
    pattern — not in the default set.
    """
    r = normalize_rare_earth(rare_earth)
    a, c = INFINITE_LAYER_LATTICE[r]
    base = build_infinite_layer(r, a=a, c=c)
    structure = base * (1, 1, 2)
    # Apical site between the two NiO₂ planes, at z ≈ 0.25 of the doubled cell
    # (Ni of the first layer is at z=0; second layer Ni at z=0.5).
    structure.append("O", [0.5, 0.5, 0.25])
    return structure.get_sorted_structure()


def _il_meta(rare_earth: str, pattern: str, **extra: Any) -> dict[str, Any]:
    a, c = INFINITE_LAYER_LATTICE[rare_earth]
    sc = extra.get("supercell", [1, 1, 1])
    sc_t = (int(sc[0]), int(sc[1]), int(sc[2])) if sc else (1, 1, 1)
    kind = {
        PATTERN_STOICHIOMETRIC: "infinite_layer",
        PATTERN_INPLANE_VACANCY: "oxygen_vacancy",
        PATTERN_APICAL_O: "apical_oxygen",
        PATTERN_APICAL_HALF: "apical_oxygen",
    }.get(pattern, "nickelate")
    pattern_class = {
        PATTERN_STOICHIOMETRIC: "stoichiometric",
        PATTERN_INPLANE_VACANCY: "oxygen_vacancy",
        PATTERN_APICAL_O: "apical_addition",
        PATTERN_APICAL_HALF: "apical_addition",
    }.get(pattern, "other")
    prototype = "perovskite_like" if pattern == PATTERN_APICAL_O else "infinite_layer"
    notes = {
        PATTERN_STOICHIOMETRIC: (
            "Stoichiometric infinite-layer RNiO2 (P4/mmm). Screening prototype."
        ),
        PATTERN_INPLANE_VACANCY: (
            "Ordered single in-plane O vacancy in an IL supercell "
            "(symmetry-unique representative). Screening enumeration — "
            "not a defect formation energy."
        ),
        PATTERN_APICAL_O: (
            "Idealized P4/mmm RNiO3 with apical oxygen *added* relative to "
            "the IL parent (n_oxygen_il_parent=2). Perovskite-like reduction "
            "parent — not a Pbnm experimental precursor."
        ),
        PATTERN_APICAL_HALF: (
            "Ordered half-apical occupancy (R2Ni2O5, 1x1x2): one apical O "
            "added to a doubled IL stack. Screening only."
        ),
    }
    meta: dict[str, Any] = {
        "material_family": "nickelate",
        "prototype": prototype,
        "kind": kind,
        "pattern_class": pattern_class,
        "rare_earth": rare_earth,
        "vacancy_pattern": pattern,
        "structure_key": structure_key(rare_earth, pattern, sc_t),
        "conventional_lattice_a": float(a),
        "a_ang": float(a),
        "c_ang": float(c),
        "screening_only": True,
        "notes": notes.get(pattern, "Nickelate screening cell."),
    }
    meta.update(extra)
    return meta


def build_nickelate_pattern(
    rare_earth: str = "Nd",
    pattern: str = PATTERN_STOICHIOMETRIC,
    *,
    supercell: tuple[int, int, int] = (2, 2, 1),
) -> tuple[Structure, dict[str, Any]]:
    """Build one (structure, metadata) pair for *pattern*."""
    r = normalize_rare_earth(rare_earth)
    pat = normalize_pattern(pattern)
    if pat == PATTERN_STOICHIOMETRIC:
        s = build_infinite_layer(r)
        n_o = int(s.composition["O"])
        meta = _il_meta(
            r,
            pat,
            formula=s.composition.reduced_formula,
            supercell=[1, 1, 1],
            n_oxygen=n_o,
            n_oxygen_parent=n_o,
            n_oxygen_il_parent=n_o,
            vacancy_fraction=0.0,
        )
        return s, meta
    if pat == PATTERN_APICAL_O:
        s = build_apical_oxygen(r)
        n_o = int(s.composition["O"])
        meta = _il_meta(
            r,
            pat,
            formula=s.composition.reduced_formula,
            supercell=[1, 1, 1],
            n_oxygen=n_o,
            n_oxygen_parent=2,
            n_oxygen_il_parent=2,
            vacancy_fraction=0.0,
            apical_added=1,
            conventional_lattice_a=float(PEROVSKITE_LATTICE[r]),
            a_ang=float(PEROVSKITE_LATTICE[r]),
            c_ang=float(PEROVSKITE_LATTICE[r]),
        )
        return s, meta
    if pat == PATTERN_APICAL_HALF:
        s = build_apical_half(r)
        n_o = int(s.composition["O"])
        meta = _il_meta(
            r,
            pat,
            formula=s.composition.reduced_formula,
            supercell=[1, 1, 2],
            n_oxygen=n_o,
            n_oxygen_parent=4,
            n_oxygen_il_parent=4,
            vacancy_fraction=0.0,
            apical_added=1,
        )
        return s, meta
    if pat == PATTERN_INPLANE_VACANCY:
        sc = (int(supercell[0]), int(supercell[1]), int(supercell[2]))
        parent = build_infinite_layer(r) * sc
        n_parent = int(parent.composition["O"])
        s = build_inplane_vacancy(r, supercell=sc)
        n_o = int(s.composition["O"])
        meta = _il_meta(
            r,
            pat,
            formula=s.composition.reduced_formula,
            supercell=list(sc),
            n_oxygen=n_o,
            n_oxygen_parent=n_parent,
            n_oxygen_il_parent=n_parent,
            vacancy_fraction=float(n_parent - n_o) / float(n_parent),
        )
        return s, meta
    raise ValueError(f"Unhandled nickelate pattern {pat!r}")


def _rare_earth_from_formula(formula: str) -> str:
    from pymatgen.core import Composition

    try:
        comp = Composition(formula)
    except ValueError as exc:
        raise ValueError(f"Cannot parse nickelate formula {formula!r}") from exc
    symbols = {el.symbol for el in comp.elements}
    rare = [s for s in SUPPORTED_RARE_EARTHS if s in symbols]
    if len(rare) != 1 or "Ni" not in symbols:
        raise ValueError(
            f"Nickelate formula must contain Ni and exactly one of "
            f"{list(SUPPORTED_RARE_EARTHS)}; got {formula!r}"
        )
    return rare[0]


def infer_pattern_from_formula(formula: str) -> str:
    """Best-effort pattern from a compact formula (NdNiO2 / NdNiO3 / Nd4Ni4O7)."""
    from pymatgen.core import Composition

    comp = Composition(formula)
    n_ni = float(comp["Ni"]) if "Ni" in comp else 0.0
    n_o = float(comp["O"]) if "O" in comp else 0.0
    if n_ni <= 0:
        raise ValueError(f"Nickelate formula must contain Ni: {formula!r}")
    ratio = n_o / n_ni
    if abs(ratio - 2.0) < 1e-6:
        return PATTERN_STOICHIOMETRIC
    if abs(ratio - 3.0) < 1e-6:
        return PATTERN_APICAL_O
    if abs(ratio - 2.5) < 1e-6:
        return PATTERN_APICAL_HALF
    if abs(ratio - 1.75) < 1e-6:
        return PATTERN_INPLANE_VACANCY
    raise ValueError(
        f"Cannot infer a screening vacancy pattern from {formula!r} "
        f"(O/Ni = {ratio:g}). Use nickelate_patterns instead."
    )


def structure_from_nickelate_formula(
    formula: str,
    *,
    metadata: dict[str, Any] | None = None,
    supercell: tuple[int, int, int] = (2, 2, 1),
) -> tuple[Structure, dict[str, Any]]:
    """Rebuild a screening nickelate from a formula / shortlist metadata."""
    meta_in = dict(metadata or {})
    rare = meta_in.get("rare_earth") or _rare_earth_from_formula(formula)
    if meta_in.get("vacancy_pattern"):
        pattern = str(meta_in["vacancy_pattern"])
    else:
        pattern = infer_pattern_from_formula(formula)
    sc = meta_in.get("supercell") or list(supercell)
    sc_t = (int(sc[0]), int(sc[1]), int(sc[2]))
    return build_nickelate_pattern(str(rare), pattern, supercell=sc_t)


def enumerate_nickelates(
    *,
    rare_earths: list[str] | None = None,
    patterns: list[str] | None = None,
    formulas: list[str] | None = None,
    max_patterns: int = 8,
    supercell: tuple[int, int, int] = (2, 2, 1),
    seed: int = 42,  # noqa: ARG001 — reserved; patterns are ordered, not random
) -> list[tuple[Structure, dict[str, Any]]]:
    """Enumerate IL + curated O-vacancy / apical-O cells.

    When *formulas* is given, each formula is rebuilt (pattern inferred or
    taken from a compact O/Ni ratio). Otherwise a small (R × pattern) grid
    is emitted. *seed* is accepted for API parity with nitride enumeration
    and is unused (patterns are ordered / symmetry-reduced).
    """
    del seed  # ordered patterns only
    results: list[tuple[Structure, dict[str, Any]]] = []
    seen_keys: set[str] = set()

    def _add(structure: Structure, meta: dict[str, Any]) -> None:
        key = str(meta.get("structure_key") or "")
        if key and key in seen_keys:
            return
        if key:
            seen_keys.add(key)
        results.append((structure, meta))

    if formulas:
        for formula in formulas:
            structure, meta = structure_from_nickelate_formula(
                formula, supercell=supercell
            )
            _add(structure, meta)
        return results

    rares = [normalize_rare_earth(r) for r in (rare_earths or list(DEFAULT_RARE_EARTHS))]
    pats = resolve_patterns(patterns, max_patterns=max_patterns)
    for r in rares:
        for pat in pats:
            structure, meta = build_nickelate_pattern(r, pat, supercell=supercell)
            _add(structure, meta)
    return results
