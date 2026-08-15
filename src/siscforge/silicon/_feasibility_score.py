"""Scoring body for silicon feasibility (P2.3).

Loaded at the end of :mod:`siscforge.silicon.feasibility` to avoid circular
import issues during helper definition.
"""
from __future__ import annotations

from typing import Any

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import SiFeasibilityConfig, SiFeasibilityWeights
from siscforge.models.results import SiFeasibilityComponents, SiFeasibilityScore
from siscforge.silicon.buffers import list_buffers_for_family, list_stacks_for_family
from siscforge.silicon.critical_thickness import (
    estimate_critical_thickness,
    membrane_transfer_heuristic,
)

from siscforge.silicon.feasibility import (
    COMPONENT_KEYS,
    SCORER_VERSION,
    _FAMILY_CHEMICAL,
    _FAMILY_MATURITY,
    _FAMILY_PROCESS_TEMP_C,
    _clamp,
    _direct_mismatch_percent,
    _film_in_plane_a,
    _mismatch_score_from_percent,
    _thermal_score,
    _use_buffers,
    evaluate_mismatch_options,
    normalize_component_weights,
)
from siscforge.structure.strain import parse_substrate

WeightsLike = SiFeasibilityConfig | SiFeasibilityWeights | dict | None


def _is_recognised_si_substrate(substrate: str | None) -> bool:
    """True only for parseable Si(001) / Si(111) labels (default Si(001))."""
    try:
        parse_substrate(substrate or "Si(001)")
    except ValueError:
        return False
    return True


def _chemical_score_for_path(
    family: str,
    candidate: StructureCandidate,
    best: dict[str, Any] | None,
) -> tuple[float, list[str], list[str]]:
    notes: list[str] = []
    chemical = _FAMILY_CHEMICAL.get(family, 50.0)
    flags: list[str] = []
    if best is not None:
        flags = list(best.get("chemical_flags") or [])
    if any(el in candidate.composition for el in ("O", "Ba", "Cu")):
        chemical = _clamp(chemical - 10.0)
        notes.append("oxygen / reactive-cation penalty")
        if "oxygen_window" not in flags:
            flags.append("oxygen_window")
    if "oxide_nitride_interface" in flags:
        chemical = _clamp(chemical - 5.0)
        notes.append("oxide-nitride interface caution")
    if "interdiffusion_caution" in flags:
        chemical = _clamp(chemical - 3.0)
        notes.append("multi-layer interdiffusion caution")
    if "oxygen_window" in flags and family == "tm_nitride":
        chemical = _clamp(chemical - 2.0)
        notes.append("oxygen process window on nitride path")
    if "nitrogen_window" in flags and family in {"tm_nitride", "mgb2_boride"}:
        chemical = _clamp(chemical + 2.0)
    if "direct_on_si" in flags and family == "tm_nitride":
        chemical = _clamp(chemical - 5.0)
        notes.append("direct nitride-on-Si chemical risk")
    return _clamp(chemical), flags, notes


def _thermal_for_path(
    family: str,
    best: dict[str, Any] | None,
    *,
    cmos_limit_c: float,
) -> tuple[float, float, str, list[str]]:
    notes: list[str] = []
    t_family = _FAMILY_PROCESS_TEMP_C.get(family, 700.0)
    t_proc = t_family
    thermal_note = ""
    if best is not None:
        ceiling = best.get("max_process_temp_c")
        if ceiling is not None:
            t_proc = max(t_family, float(ceiling))
            notes.append(f"process temp ceiling ~{t_proc:.0f} C (stack/film)")
        thermal_note = str(best.get("thermal_window_note") or "")
        flags = best.get("chemical_flags") or []
        if "high_thermal_budget" in flags:
            notes.append("high-thermal-budget buffer step (e.g. AlN)")
    score = _thermal_score(t_proc, cmos_limit_c=cmos_limit_c)
    return score, t_proc, thermal_note, notes


def _buffer_availability_score(
    family: str,
    best: dict[str, Any] | None,
    *,
    n_single: int,
    n_stacks: int,
    mismatch_pct: float | None,
) -> float:
    if family == "b_doped_si":
        return 95.0
    if family == "tm_nitride":
        base = 70.0
        if best is not None and best.get("buffer") != "direct_Si":
            base = 80.0
        if n_stacks > 0:
            base = max(base, 85.0)
        if best and best.get("is_multilayer"):
            base = max(base, 90.0)
        if mismatch_pct is not None and abs(mismatch_pct) < 5:
            base = max(base, 90.0)
        base = min(100.0, base + min(5.0, 0.5 * n_single + 1.0 * n_stacks))
        return _clamp(base)
    base = 55.0
    if n_stacks > 0:
        base = 65.0
    if best and best.get("is_multilayer"):
        base = 70.0
    return _clamp(base)


def _recommended_buffer_list(
    family: str,
    options: list[dict[str, Any]],
    best: dict[str, Any] | None,
    *,
    include_library: bool = True,
) -> list[str]:
    names: list[str] = []
    if best is not None and best.get("buffer"):
        names.append(str(best["buffer"]))
    for opt in options:
        b = str(opt.get("buffer") or "")
        if b and b not in names:
            names.append(b)
        if len(names) >= 6:
            break
    if not include_library:
        return names or ["direct_Si"]
    for buf in list_buffers_for_family(family):
        if buf.name not in names:
            names.append(buf.name)
    for stack in list_stacks_for_family(family, multilayer_only=True):
        if stack.name not in names:
            names.append(stack.name)
    return names


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
    lattice_data_missing = False

    options = evaluate_mismatch_options(candidate)
    best = options[0] if options else None
    mismatch_pct: float | None
    if best is not None:
        mismatch_pct = float(best["mismatch_pct"])
        lattice_score = float(best["score"])
        notes.append(str(best["notes"]))
        if best.get("buffer") and best["buffer"] != "direct_Si":
            kind = "stack" if best.get("is_multilayer") else "buffer"
            notes.append(f"assumed {kind}: {best['buffer']}")
            if best.get("layers"):
                notes.append("layers (sub->film): " + " / ".join(best["layers"]))
        if best.get("match") == "45deg":
            notes.append("assumed 45deg in-plane registry vs Si(001)")
        if best.get("process_note"):
            notes.append(str(best["process_note"]))
    elif (
        candidate.in_plane_strain is not None
        and _is_recognised_si_substrate(candidate.substrate)
    ):
        # |\u03b5| is a Si-epitaxy strain, not a match to an unsupported substrate.
        mismatch_pct = abs(candidate.in_plane_strain) * 100.0
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        notes.append("mismatch from |in_plane_strain|")
    else:
        mismatch_pct = 5.0
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        lattice_data_missing = True
        if not _is_recognised_si_substrate(candidate.substrate):
            notes.append(
                f"unsupported / non-Si substrate {candidate.substrate!r}; "
                "lattice score uses conservative missing-data default "
                "(~5% mismatch), not |in_plane_strain|"
            )
        else:
            notes.append("mismatch defaulted (no lattice data)")

    thermal, t_proc, thermal_window_note, thermal_notes = _thermal_for_path(
        family, best, cmos_limit_c=cmos_limit_c
    )
    notes.extend(thermal_notes)
    chemical, chem_flags, chem_notes = _chemical_score_for_path(family, candidate, best)
    notes.extend(chem_notes)

    use_bufs = _use_buffers(candidate)
    if use_bufs:
        n_single = len([b for b in list_buffers_for_family(family) if b.name != "direct_Si"])
        n_stacks = len(list_stacks_for_family(family, multilayer_only=True))
    else:
        n_single = 0
        n_stacks = 0
    buffer_score = _buffer_availability_score(
        family, best, n_single=n_single, n_stacks=n_stacks, mismatch_pct=mismatch_pct
    )
    buffers = _recommended_buffer_list(family, options, best, include_library=use_bufs)

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
    total = _clamp(round(
        w["lattice_mismatch"] * components.lattice_mismatch
        + w["thermal_budget"] * components.thermal_budget
        + w["chemical_compatibility"] * components.chemical_compatibility
        + w["buffer_availability"] * components.buffer_availability
        + w["process_maturity"] * components.process_maturity, 2))

    a_film = _film_in_plane_a(candidate)
    if lattice_data_missing:
        ct_mismatch = None
    elif best is not None and best.get("mismatch_film_buffer_pct") is not None:
        ct_mismatch = float(best["mismatch_film_buffer_pct"])
    else:
        ct_mismatch = mismatch_pct
    ct = estimate_critical_thickness(
        ct_mismatch, formula=formula, material_family=family, film_a_ang=a_film
    )
    thickness = ct.recommended_thickness_nm
    notes.extend(ct.notes)

    direct_mm = _direct_mismatch_percent(candidate, options=options)
    path_eff: float | None = None
    if best is not None:
        path_eff = float(best.get("mismatch_effective_abs_pct", best["mismatch_pct"]))
    elif mismatch_pct is not None:
        path_eff = abs(float(mismatch_pct))

    membrane_flag, membrane_note = membrane_transfer_heuristic(
        direct_mismatch_pct=direct_mm,
        path_mismatch_pct=path_eff,
        hc_nm=ct.hc_primary_nm,
        path=str(best["path"]) if best else None,
        is_multilayer=bool(best.get("is_multilayer")) if best else False,
        material_family=family,
        chemical_flags=chem_flags,
    )
    if membrane_flag:
        notes.append(membrane_note)

    if chem_flags:
        notes.append("chemical flags: " + ", ".join(chem_flags))
    if thermal_window_note:
        notes.append("thermal window: " + thermal_window_note)
    elif best and best.get("window_notes"):
        wn = best["window_notes"][0]
        if wn:
            notes.append("thermal window: " + str(wn))
            thermal_window_note = str(wn)

    note_str = (
        f"v{SCORER_VERSION} heuristic Si-feasibility; "
        + ("; ".join(notes) if notes else "all components from family + lattice rules")
    )
    weights_out = {k: float(w[k]) for k in COMPONENT_KEYS}
    process_ceiling = float(t_proc)
    if best is not None and best.get("max_process_temp_c") is not None:
        process_ceiling = max(process_ceiling, float(best["max_process_temp_c"]))

    return SiFeasibilityScore(
        total=total,
        components=components,
        weights=weights_out,
        lattice_mismatch_pct=None if mismatch_pct is None else round(mismatch_pct, 3),
        recommended_buffers=buffers,
        recommended_thickness_nm=thickness,
        notes=note_str,
        version=SCORER_VERSION,
        chemical_flags=chem_flags,
        thermal_window_note=thermal_window_note,
        process_temp_ceiling_c=process_ceiling,
        critical_thickness_nm=ct.hc_primary_nm,
        critical_thickness_method=ct.method,
        critical_thickness_people_bean_nm=ct.hc_people_bean_nm,
        critical_thickness_inputs=dict(ct.inputs),
        membrane_transfer_candidate=membrane_flag,
        membrane_transfer_note=membrane_note,
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
    from siscforge.structure.strain import SI_LATTICE_CONSTANT, substrate_in_plane_spacing
    from siscforge.silicon.feasibility import (
        COMPONENT_WEIGHTS,
        _direct_mismatch_percent,
        _film_in_plane_a,
        _raw_mismatch_percent,
        _resolve_epitaxy_mode,
        evaluate_mismatch_options,
    )
    from siscforge.silicon.buffers import list_stacks_for_family as _list_stacks

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
        "stacks_for_family": [s.name for s in _list_stacks(candidate.material_family)],
    }
    try:
        info["substrate_target_a"] = substrate_in_plane_spacing(substrate)
    except ValueError:
        info["substrate_target_a"] = None
    info["mismatch_pct"] = _raw_mismatch_percent(candidate)
    info["direct_mismatch_pct"] = _direct_mismatch_percent(candidate)
    return info
