"""Silicon Feasibility scorer (transparent heuristics).

Every component of :class:`~siscforge.models.results.SiFeasibilityComponents`
is always populated. Scores are 0–100 (higher = more Si-process friendly).

v0.2 adds rocksalt **45° epitaxy** matching and a minimal **buffer library**
so nitride cube-on-cube pessimism can be improved when scientifically justified.

v0.3 (P2.1) makes component **weights first-class and YAML-overridable** via
``CampaignConfig.si_feasibility.weights`` (keys: lattice_mismatch,
thermal_budget, chemical_compatibility, buffer_availability, process_maturity).
Active weights and scorer version are stored on every
:class:`~siscforge.models.results.SiFeasibilityScore` for auditability.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import SiFeasibilityConfig, SiFeasibilityWeights
from siscforge.models.results import SiFeasibilityComponents, SiFeasibilityScore
from siscforge.silicon.buffers import (
    list_buffers_for_family,
)
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    EpitaxyMatch,
    lattice_mismatch_percent,
    parse_substrate,
    substrate_in_plane_spacing,
)

SCORER_VERSION = "0.3"

COMPONENT_WEIGHTS: dict[str, float] = {
    "lattice_mismatch": 0.35,
    "thermal_budget": 0.20,
    "chemical_compatibility": 0.20,
    "buffer_availability": 0.10,
    "process_maturity": 0.15,
}

COMPONENT_KEYS: tuple[str, ...] = (
    "lattice_mismatch",
    "thermal_budget",
    "chemical_compatibility",
    "buffer_availability",
    "process_maturity",
)

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
WeightsLike = Mapping[str, float] | SiFeasibilityWeights | SiFeasibilityConfig | None


def _clamp(score: float) -> float:
    return float(max(0.0, min(100.0, score))


def normalize_component_weights(
    weights: WeightsLike = None,
) -> dict[str, float]:
    """Return a full, non-negative weight vector normalized to sum 1.0.

    An all-zero (or negative-clamped) override falls back to COMPONENT_WEIGHTS.
    Non-finite values (NaN / ±∞) also force fallback so scoring never produces NaN.
    """
    base = dict(COMPONENT_WEIGHTS)
    if weights is None:
        raw = base
    elif isinstance(weights, SiFeasibilityConfig):
        raw = {**base, **weights.weights.as_dict()}
    elif isinstance(weights, SiFeasibilityWeights):
        raw = {**base, **weights.as_dict()}
    else:
        raw = {**base, **{k: float(v) for k, v in weights.items() if k in base}}
    cleaned: dict[str, float] = {}
    for k in COMPONENT_KEYS:
        try:
            v = float(raw.get(k, base[k]))
        except (TypeError, ValueError):
            return dict(COMPONENT_WEIGHTS)
        if not math.isfinite(v):
            return dict(COMPONENT_WEIGHTS)
        cleaned[k] = max(0.0, v)
    w_sum = sum(cleaned.values())
    if not math.isfinite(w_sum) or w_sum <= 0.0:
        return dict(COMPONENT_WEIGHTS)
    return {k: cleaned[k] / w_sum for k in COMPONENT_KEYS}


def _mismatch_score_from_percent(mismatch_pct: float) -> float:
    mag = abs(mismatch_pct)
    return _clamp(100.0 * float(math.exp(-mag / 4.0)))


def _thermal_score(process_temp_c: float, *, cmos_limit_c: float = 450.0) -> float:
    if process_temp_c <= cmos_limit_c:
        return 95.0
    overshoot = process_temp_c - cmos_limit_c
    return _clamp(95.0 - overshoot * 0.12)


def _film_in_plane_a(candidate: StructureCandidate) -> float | None:
    meta = candidate.metadata or {}
    for key in ("conventional_lattice_a", "rocksalt_a", "a_conventional"):
        if key in meta and meta[key] is not None:
            return float(meta[key])
    if candidate.material_family == "tm_nitride" and (
        candidate.in_plane_strain is None or abs(float(candidate.in_plane_strain)) < 1e-12
    ):
        from siscforge.structure.nitrides import ROCKSALT_LATTICE_CONSTANTS
        metals = meta.get("metals") or []
        if len(metals) == 1 and metals[0] in ROCKSALT_LATTICE_CONSTANTS:
            return float(ROCKSALT_LATTICE_CONSTANTS[metals[0]])
        formula = (candidate.formula or "").replace(" ", "")
        for m, a in ROCKSALT_LATTICE_CONSTANTS.items():
            if formula in {f"{m}N", f"N{m}"}:
                return float(a)
        if formula.startswith("Nb") and "Ti" in formula:
            return 0.5 * (ROCKSALT_LATTICE_CONSTANTS["Nb"] + ROCKSALT_LATTICE_CONSTANTS["Ti"])
    if candidate.lattice_abc is not None:
        return float(candidate.lattice_abc[0])
    return None


def _resolve_epitaxy_mode(candidate: StructureCandidate) -> EpitaxyMode:
    meta = candidate.metadata or {}
    mode = meta.get("epitaxy_orientation") or meta.get("epitaxy_match")
    if mode in {"cube_on_cube", "45deg", "auto"}:
        return mode  # type: ignore[return-value]
    if candidate.material_family == "tm_nitride":
        return "auto"
    return "cube_on_cube"


def _use_buffers(candidate: StructureCandidate) -> bool:
    meta = candidate.metadata or {}
    if "use_buffers" in meta:
        return bool(meta["use_buffers"])
    return True


def evaluate_mismatch_options(candidate: StructureCandidate) -> list[dict[str, Any]]:
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
        options.append({
            "path": f"direct/{m}",
            "match": m,
            "buffer": "direct_Si",
            "mismatch_pct": pct,
            "score": _mismatch_score_from_percent(pct),
            "notes": f"direct on Si with {m} matching",
        })
    if _use_buffers(candidate):
        for buf in list_buffers_for_family(candidate.material_family):
            if buf.name == "direct_Si":
                continue
            try:
                m_fb = lattice_mismatch_percent(a_film, substrate, match="cube_on_cube", substrate_a=buf.lattice_a_ang)
                m_bs = lattice_mismatch_percent(buf.lattice_a_ang, substrate, match="cube_on_cube")
            except ValueError:
                continue
            eff = max(abs(m_fb), abs(m_bs))
            options.append({
                "path": f"buffer/{buf.name}",
                "match": "cube_on_cube",
                "buffer": buf.name,
                "mismatch_pct": m_fb if abs(m_fb) >= abs(m_bs) else m_bs,
                "mismatch_film_buffer_pct": m_fb,
                "mismatch_buffer_si_pct": m_bs,
                "score": _mismatch_score_from_percent(eff),
                "notes": f"buffer {buf.name}: film–buffer {m_fb:.2f}%, buffer–Si {m_bs:.2f}% ({buf.notes})",
            })
    options.sort(key=lambda o: abs(float(o["mismatch_pct"])))
    return options


def _raw_mismatch_percent(candidate: StructureCandidate) -> float | None:
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
    weights: WeightsLike = None,
    cmos_limit_c: float | None = None,
    config: SiFeasibilityConfig | None = None,
) -> SiFeasibilityScore:
    if config is not None and weights is None:
        weights = config
    if isinstance(weights, SiFeasibilityConfig):
        if cmos_limit_c is None:
            cmos_limit_c = float(weights.cmos_limit_c)
        w = normalize_component_weights(weights)
    else:
        w = normalize_component_weights(weights)
        if cmos_limit_c is None and config is not None:
            cmos_limit_c = float(config.cmos_limit_c)
    if cmos_limit_c is None:
        cmos_limit_c = 450.0

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

    family_bufs = list_buffers_for_family(family)
    buffers = [b.name for b in family_bufs]
    if best is not None and best.get("buffer"):
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

    # Store the exact normalized vector used for total (audit trail).
    weights_out = {k: float(w[k]) for k in COMPONENT_KEYS}

    return SiFeasibilityScore(
        total=total,
        components=components,
        weights=weights_out,
        lattice_mismatch_pct=None if mismatch_pct is None else round(mismatch_pct, 3),
        recommended_buffers=buffers,
        recommended_thickness_nm=thickness,
        notes=note_str,
        version=SCORER_VERSION,
    )


def rank_by_si_feasibility(
    candidates: list[StructureCandidate],
    *,
    weights: WeightsLike = None,
    cmos_limit_c: float | None = None,
    config: SiFeasibilityConfig | None = None,
) -> list[tuple[StructureCandidate, SiFeasibilityScore]]:
    scored = [
        (c, score_si_feasibility(c, weights=weights, cmos_limit_c=cmos_limit_c, config=config))
        for c in candidates
    ]
    scored.sort(key=lambda pair: pair[1].total, reverse=True)
    return scored


def scorer_debug_info(candidate: StructureCandidate) -> dict[str, Any]:
    a = _film_in_plane_a(candidate)
    substrate = candidate.substrate or "Si(001)"
    info: dict[str, Any] = {
        "film_a": a,
        "substrate": substrate,
        "si_a": SI_LATTICE_CONSTANT,
        "epitaxy_mode": _resolve_epitaxy_mode(candidate),
        "options": evaluate_mismatch_options(candidate),
        "default_weights": dict(COMPONENT_WEIGHTS),
        "scorer_version": SCORER_VERSION,
    }
    try:
        info["substrate_target_a"] = substrate_in_plane_spacing(substrate)
    except ValueError:
        info["substrate_target_a"] = None
    info["mismatch_pct"] = _raw_mismatch_percent(candidate)
    return info
