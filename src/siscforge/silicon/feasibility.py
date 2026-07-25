"""First-version Silicon Feasibility scorer (transparent heuristics).

Every component of :class:`~siscforge.models.results.SiFeasibilityComponents`
is always populated. Scores are 0–100 (higher = more Si-process friendly).
This is intentionally simple and fully documented — not a calibrated process model.
"""

from __future__ import annotations

import math
from typing import Any

from siscforge.models.candidate import StructureCandidate
from siscforge.models.results import SiFeasibilityComponents, SiFeasibilityScore
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    lattice_mismatch_percent,
    parse_substrate,
    substrate_in_plane_spacing,
)

SCORER_VERSION = "0.1"

# Default weights for the composite total (must sum to 1.0).
COMPONENT_WEIGHTS: dict[str, float] = {
    "lattice_mismatch": 0.35,
    "thermal_budget": 0.20,
    "chemical_compatibility": 0.20,
    "buffer_availability": 0.10,
    "process_maturity": 0.15,
}

# Heuristic process temperatures (°C) by family — lower is easier on CMOS backend.
_FAMILY_PROCESS_TEMP_C: dict[str, float] = {
    "tm_nitride": 600.0,  # reactive sputtering / PLD typical range
    "b_doped_si": 900.0,  # dopant activation / epi
    "mgb2_boride": 750.0,
    "nickelate": 600.0,  # soft chemistry / PLD + topotactic reduction
    "cuprate": 800.0,
    "other": 700.0,
}

# Rule-of-thumb chemical compatibility with Si (0–100).
_FAMILY_CHEMICAL: dict[str, float] = {
    "tm_nitride": 80.0,  # TiN/NbN widely used; nitrogen chemistry manageable
    "b_doped_si": 95.0,  # native to Si
    "mgb2_boride": 55.0,  # B diffusion / high reactivity
    "nickelate": 40.0,  # oxygen control, Ni silicide risk
    "cuprate": 35.0,  # oxygen, Ba/Cu silicide, interdiffusion
    "other": 50.0,
}

# Process maturity / industrial readiness (0–100).
_FAMILY_MATURITY: dict[str, float] = {
    "tm_nitride": 90.0,  # NbN, TiN, NbTiN foundry-adjacent
    "b_doped_si": 85.0,
    "mgb2_boride": 50.0,
    "nickelate": 25.0,
    "cuprate": 30.0,
    "other": 40.0,
}

# Suggested buffer stacks (names only — no full library yet).
_FAMILY_BUFFERS: dict[str, list[str]] = {
    "tm_nitride": ["direct_Si", "TiN_seed", "AlN"],
    "b_doped_si": ["direct_Si"],
    "mgb2_boride": ["TiB2", "SiC", "BN"],
    "nickelate": ["STO", "YSZ/CeO2", "SrTiO3"],
    "cuprate": ["YSZ/CeO2", "MgO", "STO"],
    "other": ["TBD"],
}


def _clamp(score: float) -> float:
    return float(max(0.0, min(100.0, score)))


def _mismatch_score_from_percent(mismatch_pct: float) -> float:
    """Map |misfit|% → 0–100 score.

    ~0% → 100, ~2% → ~75, ~5% → ~45, ≥15% → near 0.
    """
    mag = abs(mismatch_pct)
    # Smooth exponential decay with 4% characteristic scale.
    return _clamp(100.0 * float(math.exp(-mag / 4.0)))


def _thermal_score(process_temp_c: float, *, cmos_limit_c: float = 450.0) -> float:
    """Higher score when process temperature is closer to / below CMOS backend limit.

    Frontend / dedicated epi flows may allow higher T; we still penalize gently.
    """
    if process_temp_c <= cmos_limit_c:
        return 95.0
    # Linear penalty above limit; 0 at ~1200 °C.
    overshoot = process_temp_c - cmos_limit_c
    return _clamp(95.0 - overshoot * 0.12)


def _film_in_plane_a(candidate: StructureCandidate) -> float | None:
    """Best-effort *conventional* in-plane lattice constant for epitaxy metrics.

    Primitive rocksalt cells have a ≈ a_conv/√2; for cube-on-cube Si mismatch we
    prefer the conventional FCC *a* (stored in metadata or looked up).
    """
    meta = candidate.metadata or {}
    for key in ("conventional_lattice_a", "rocksalt_a", "a_conventional"):
        if key in meta and meta[key] is not None:
            return float(meta[key])

    # Unstrained tm nitrides: use tabulated conventional a when available
    if (
        candidate.material_family == "tm_nitride"
        and (candidate.in_plane_strain is None or abs(float(candidate.in_plane_strain)) < 1e-12)
    ):
        from siscforge.structure.nitrides import ROCKSALT_LATTICE_CONSTANTS

        metals = meta.get("metals") or []
        if len(metals) == 1 and metals[0] in ROCKSALT_LATTICE_CONSTANTS:
            return float(ROCKSALT_LATTICE_CONSTANTS[metals[0]])
        # Formula like NbN
        formula = (candidate.formula or "").replace(" ", "")
        for m, a in ROCKSALT_LATTICE_CONSTANTS.items():
            if formula in {f"{m}N", f"N{m}"}:
                return float(a)

    if candidate.lattice_abc is not None:
        return float(candidate.lattice_abc[0])
    return None


def _raw_mismatch_percent(candidate: StructureCandidate) -> float | None:
    """Compute lattice mismatch % vs substrate, if possible."""
    substrate = candidate.substrate or "Si(001)"
    try:
        parse_substrate(substrate)
    except ValueError:
        return None

    a_film = _film_in_plane_a(candidate)
    if a_film is None:
        return None

    # If the film was already strained, lattice_abc is the strained metric.
    # Report residual mismatch of current in-plane a vs substrate target.
    return lattice_mismatch_percent(a_film, substrate)


def score_si_feasibility(
    candidate: StructureCandidate,
    *,
    weights: dict[str, float] | None = None,
    cmos_limit_c: float = 450.0,
) -> SiFeasibilityScore:
    """Compute a transparent Silicon Feasibility Score for *candidate*.

    All component fields are always filled. Missing structural data falls back
    to family-level heuristics so scoring never crashes on partial candidates.
    """
    w = dict(COMPONENT_WEIGHTS)
    if weights:
        w.update(weights)
    # Renormalize
    w_sum = sum(w.values()) or 1.0
    w = {k: v / w_sum for k, v in w.items()}

    family = candidate.material_family
    notes: list[str] = []

    # --- lattice mismatch ---
    mismatch_pct = _raw_mismatch_percent(candidate)
    if mismatch_pct is None:
        # Fall back: if in_plane_strain is set, treat |ε|*100 as effective misfit
        # relative to an already-matched reference (screening series).
        if candidate.in_plane_strain is not None:
            mismatch_pct = abs(candidate.in_plane_strain) * 100.0
            notes.append("mismatch from |in_plane_strain| (no lattice_abc vs substrate)")
        else:
            mismatch_pct = 5.0  # neutral-ish default
            notes.append("mismatch defaulted (no lattice data)")
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
    else:
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        # Rocksalt nitrides on Si often use 45° variants; note large cube-on-cube mismatch.
        if family == "tm_nitride" and abs(mismatch_pct) > 10:
            notes.append(
                "large cube-on-cube mismatch vs Si; consider 45° epitaxy or buffers"
            )

    # --- thermal ---
    t_proc = _FAMILY_PROCESS_TEMP_C.get(family, 700.0)
    thermal = _thermal_score(t_proc, cmos_limit_c=cmos_limit_c)

    # --- chemical ---
    chemical = _FAMILY_CHEMICAL.get(family, 50.0)
    # Mild penalty if composition contains aggressive oxidizers / oxygen
    if any(el in candidate.composition for el in ("O", "Ba", "Cu")):
        chemical = _clamp(chemical - 10.0)
        notes.append("oxygen / reactive-cation penalty")

    # --- buffer availability (heuristic; no full library yet) ---
    buffers = list(_FAMILY_BUFFERS.get(family, ["TBD"]))
    if family == "tm_nitride" and abs(mismatch_pct or 0) < 3:
        buffer_score = 90.0
        buffers = ["direct_Si", "TiN_seed"]
    elif family == "tm_nitride":
        buffer_score = 75.0
    elif family == "b_doped_si":
        buffer_score = 95.0
    else:
        buffer_score = 55.0

    # --- process maturity ---
    maturity = _FAMILY_MATURITY.get(family, 40.0)
    # NbN / TiN / NbTiN bonus
    formula = candidate.formula
    if any(tag in formula for tag in ("NbN", "TiN", "NbTi", "Nb0", "Ti0")):
        maturity = _clamp(maturity + 5.0)

    components = SiFeasibilityComponents(
        lattice_mismatch=_clamp(lattice_score),
        thermal_budget=_clamp(thermal),
        chemical_compatibility=_clamp(chemical),
        buffer_availability=_clamp(buffer_score),
        process_maturity=_clamp(maturity),
    )

    total = (
        w["lattice_mismatch"] * components.lattice_mismatch
        + w["thermal_budget"] * components.thermal_budget
        + w["chemical_compatibility"] * components.chemical_compatibility
        + w["buffer_availability"] * components.buffer_availability
        + w["process_maturity"] * components.process_maturity
    )
    total = _clamp(round(total, 2))

    # Thickness heuristic (nm): thinner for higher mismatch
    if mismatch_pct is not None and abs(mismatch_pct) > 5:
        thickness = (5.0, 30.0)
    else:
        thickness = (20.0, 100.0)

    note_str = (
        f"v{SCORER_VERSION} heuristic Si-feasibility; "
        + ("; ".join(notes) if notes else "all components from family + lattice rules")
    )

    return SiFeasibilityScore(
        total=total,
        components=components,
        lattice_mismatch_pct=None if mismatch_pct is None else round(mismatch_pct, 3),
        recommended_buffers=buffers,
        recommended_thickness_nm=thickness,
        notes=note_str,
        version=SCORER_VERSION,
    )


def scorer_debug_info(candidate: StructureCandidate) -> dict[str, Any]:
    """Return intermediate values useful for tests / debugging."""
    a = _film_in_plane_a(candidate)
    substrate = candidate.substrate or "Si(001)"
    info: dict[str, Any] = {
        "film_a": a,
        "substrate": substrate,
        "si_a": SI_LATTICE_CONSTANT,
    }
    try:
        info["substrate_target_a"] = substrate_in_plane_spacing(substrate)
    except ValueError:
        info["substrate_target_a"] = None
    info["mismatch_pct"] = _raw_mismatch_percent(candidate)
    return info
