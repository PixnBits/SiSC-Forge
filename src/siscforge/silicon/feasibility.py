"""Silicon Feasibility scorer (transparent heuristics).

Every component of :class:`~siscforge.models.results.SiFeasibilityComponents`
is always populated. Scores are 0–100 (higher = more Si-process friendly).

v0.2 adds rocksalt **45° epitaxy** matching and a minimal **buffer library**
so nitride cube-on-cube pessimism can be improved when scientifically justified.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from siscforge.models.candidate import StructureCandidate
from siscforge.models.results import SiFeasibilityComponents, SiFeasibilityScore
from siscforge.silicon.buffers import (
    BUFFER_LIBRARY,
    list_buffers_for_family,
)
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    EpitaxyMatch,
    lattice_mismatch_percent,
    parse_substrate,
    substrate_in_plane_spacing,
)

SCORER_VERSION = "0.2"

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
    "tm_nitride": 600.0,
    "b_doped_si": 900.0,
    "mgb2_boride": 750.0,
    "nickelate": 600.0,
    "cuprate": 800.0,
    "other": 700.0,
}

_FAMILY_CHEMICAL: dict[str, float] = {
    "tm_nitride": 80.0,
    "b_doped_si": 95.0,
    "mgb2_boride": 55.0,
    "nickelate": 40.0,
    "cuprate": 35.0,
    "other": 50.0,
}

_FAMILY_MATURITY: dict[str, float] = {
    "tm_nitride": 90.0,
    "b_doped_si": 85.0,
    "mgb2_boride": 50.0,
    "nickelate": 25.0,
    "cuprate": 30.0,
    "other": 40.0,
}

EpitaxyMode = Literal["auto", "cube_on_cube", "45deg"]


def _clamp(score: float) -> float:
    return float(max(0.0, min(100.0, score)))


def _mismatch_score_from_percent(mismatch_pct: float) -> float:
    """Map |misfit|% → 0–100 score.

    ~0% → 100, ~2% → ~75, ~5% → ~45, ≥15% → near 0.
    """
    mag = abs(mismatch_pct)
    return _clamp(100.0 * float(math.exp(-mag / 4.0)))


def _thermal_score(process_temp_c: float, *, cmos_limit_c: float = 450.0) -> float:
    if process_temp_c <= cmos_limit_c:
        return 95.0
    overshoot = process_temp_c - cmos_limit_c
    return _clamp(95.0 - overshoot * 0.12)


def _film_in_plane_a(candidate: StructureCandidate) -> float | None:
    """Best-effort *conventional* cubic *a* (Å) for epitaxy metrics."""
    meta = candidate.metadata or {}
    for key in ("conventional_lattice_a", "rocksalt_a", "a_conventional"):
        if key in meta and meta[key] is not None:
            return float(meta[key])

    if (
        candidate.material_family == "tm_nitride"
        and (
            candidate.in_plane_strain is None
            or abs(float(candidate.in_plane_strain)) < 1e-12
        )
    ):
        from siscforge.structure.nitrides import ROCKSALT_LATTICE_CONSTANTS

        metals = meta.get("metals") or []
        if len(metals) == 1 and metals[0] in ROCKSALT_LATTICE_CONSTANTS:
            return float(ROCKSALT_LATTICE_CONSTANTS[metals[0]])
        formula = (candidate.formula or "").replace(" ", "")
        for m, a in ROCKSALT_LATTICE_CONSTANTS.items():
            if formula in {f"{m}N", f"N{m}"}:
                return float(a)
        # Ternary: Vegard-like mean of known metal *a* if both known
        if formula.startswith("Nb") and "Ti" in formula:
            return 0.5 * (
                ROCKSALT_LATTICE_CONSTANTS["Nb"] + ROCKSALT_LATTICE_CONSTANTS["Ti"]
            )

    if candidate.lattice_abc is not None:
        return float(candidate.lattice_abc[0])
    return None


def _resolve_epitaxy_mode(candidate: StructureCandidate) -> EpitaxyMode:
    meta = candidate.metadata or {}
    mode = meta.get("epitaxy_orientation") or meta.get("epitaxy_match")
    if mode in {"cube_on_cube", "45deg", "auto"}:
        return mode  # type: ignore[return-value]
    # Default: auto for nitrides (pick best of cube vs 45°); else cube-on-cube
    if candidate.material_family == "tm_nitride":
        return "auto"
    return "cube_on_cube"


def _use_buffers(candidate: StructureCandidate) -> bool:
    meta = candidate.metadata or {}
    if "use_buffers" in meta:
        return bool(meta["use_buffers"])
    return True


def evaluate_mismatch_options(
    candidate: StructureCandidate,
) -> list[dict[str, Any]]:
    """Enumerate direct (cube / 45°) and buffer-mediated mismatch options."""
    substrate = candidate.substrate or "Si(001)"
    try:
        parse_substrate(substrate)
    except ValueError:
        return []

    a_film = _film_in_plane_a(candidate)
    if a_film is None:
        return []

    options: list[dict[str, Any]] = []
    mode = _resolve_epitaxy_mode(candidate)
    matches: list[EpitaxyMatch]
    if mode == "auto":
        matches = ["cube_on_cube", "45deg"]
    elif mode == "45deg":
        matches = ["45deg"]
    else:
        matches = ["cube_on_cube"]

    for m in matches:
        try:
            pct = lattice_mismatch_percent(a_film, substrate, match=m)
        except ValueError:
            continue
        options.append(
            {
                "path": f"direct/{m}",
                "match": m,
                "buffer": "direct_Si",
                "mismatch_pct": pct,
                "score": _mismatch_score_from_percent(pct),
                "notes": f"direct on Si with {m} matching",
            }
        )

    if _use_buffers(candidate):
        for buf in list_buffers_for_family(candidate.material_family):
            if buf.name == "direct_Si":
                continue
            # Film vs buffer (cube-on-cube between conventional *a*)
            try:
                m_fb = lattice_mismatch_percent(
                    a_film, substrate, match="cube_on_cube", substrate_a=buf.lattice_a_ang
                )
                m_bs = lattice_mismatch_percent(
                    buf.lattice_a_ang,
                    substrate,
                    match="cube_on_cube",
                )
            except ValueError:
                continue
            # Conservative effective misfit for scoring
            eff = max(abs(m_fb), abs(m_bs))
            # Keep signed film-buffer for reporting
            options.append(
                {
                    "path": f"buffer/{buf.name}",
                    "match": "cube_on_cube",
                    "buffer": buf.name,
                    "mismatch_pct": m_fb if abs(m_fb) >= abs(m_bs) else m_bs,
                    "mismatch_film_buffer_pct": m_fb,
                    "mismatch_buffer_si_pct": m_bs,
                    "score": _mismatch_score_from_percent(eff),
                    "notes": (
                        f"buffer {buf.name}: film–buffer {m_fb:.2f}%, "
                        f"buffer–Si {m_bs:.2f}% ({buf.notes})"
                    ),
                }
            )

    options.sort(key=lambda o: abs(float(o["mismatch_pct"])))
    return options


def _raw_mismatch_percent(candidate: StructureCandidate) -> float | None:
    """Best mismatch % among allowed epitaxy/buffer options (or simple fallback)."""
    opts = evaluate_mismatch_options(candidate)
    if opts:
        return float(opts[0]["mismatch_pct"])
    substrate = candidate.substrate or "Si(001)"
    try:
        parse_substrate(substrate)
    except ValueError:
        return None
    a_film = _film_in_plane_a(candidate)
    if a_film is None:
        return None
    return lattice_mismatch_percent(a_film, substrate, match="cube_on_cube")


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
    w_sum = sum(w.values()) or 1.0
    w = {k: v / w_sum for k, v in w.items()}

    family = candidate.material_family
    notes: list[str] = []

    options = evaluate_mismatch_options(candidate)
    best = options[0] if options else None
    mismatch_pct: float | None
    if best is not None:
        mismatch_pct = float(best["mismatch_pct"])
        lattice_score = float(best["score"])
        notes.append(str(best["notes"]))
        if best.get("buffer") and best["buffer"] != "direct_Si":
            notes.append(f"assumed buffer: {best['buffer']}")
        if best.get("match") == "45deg":
            notes.append("assumed 45° in-plane registry vs Si(001)")
    elif candidate.in_plane_strain is not None:
        mismatch_pct = abs(candidate.in_plane_strain) * 100.0
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        notes.append("mismatch from |in_plane_strain| (no lattice_abc vs substrate)")
    else:
        mismatch_pct = 5.0
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        notes.append("mismatch defaulted (no lattice data)")

    t_proc = _FAMILY_PROCESS_TEMP_C.get(family, 700.0)
    thermal = _thermal_score(t_proc, cmos_limit_c=cmos_limit_c)

    chemical = _FAMILY_CHEMICAL.get(family, 50.0)
    if any(el in candidate.composition for el in ("O", "Ba", "Cu")):
        chemical = _clamp(chemical - 10.0)
        notes.append("oxygen / reactive-cation penalty")

    # Buffer availability from library + best path
    family_bufs = list_buffers_for_family(family)
    buffers = [b.name for b in family_bufs]
    if best is not None and best.get("buffer"):
        # Put chosen buffer first
        chosen = str(best["buffer"])
        buffers = [chosen] + [b for b in buffers if b != chosen]
    if family == "tm_nitride" and abs(mismatch_pct or 0) < 5:
        buffer_score = 90.0
    elif family == "tm_nitride":
        buffer_score = 80.0 if best and best.get("buffer") != "direct_Si" else 70.0
    elif family == "b_doped_si":
        buffer_score = 95.0
    else:
        buffer_score = 55.0

    maturity = _FAMILY_MATURITY.get(family, 40.0)
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
        "epitaxy_mode": _resolve_epitaxy_mode(candidate),
        "options": evaluate_mismatch_options(candidate),
    }
    try:
        info["substrate_target_a"] = substrate_in_plane_spacing(substrate)
    except ValueError:
        info["substrate_target_a"] = None
    info["mismatch_pct"] = _raw_mismatch_percent(candidate)
    return info
