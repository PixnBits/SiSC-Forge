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

v0.4 (P2.2) adds **multi-layer buffer stacks** and surfaces
**chemical-compatibility / thermal-window** flags from stack metadata on the
score and synthesis cards. Still heuristic — not CALPHAD.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import SiFeasibilityConfig, SiFeasibilityWeights
from siscforge.models.results import SiFeasibilityComponents, SiFeasibilityScore
from siscforge.silicon.buffers import (
    BUFFER_LIBRARY,
    BufferStack,
    aggregate_stack_flags,
    list_buffers_for_family,
    list_stacks_for_family,
    resolve_stack_layers,
    stack_from_single,
    stack_process_temp_ceiling_c,
    stack_window_notes,
)
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    EpitaxyMatch,
    lattice_mismatch_percent,
    parse_substrate,
    substrate_in_plane_spacing,
)

SCORER_VERSION = "0.4"

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
    return float(max(0.0, min(100.0, score)))


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


def _layer_layer_mismatch_pct(a_bottom: float, a_top: float) -> float:
    """Percent misfit at a stack interface (substrate-side → film-side).

    Uses the same convention as :func:`lattice_mismatch_percent`:
    ``100 * (a_sub - a_film) / a_film`` with *a_bottom* as substrate-side and
    *a_top* as film-side. Layers are ordered substrate → film.
    """
    if a_top <= 0:
        raise ValueError("layer lattice constant must be positive")
    return 100.0 * (a_bottom - a_top) / a_top


def _option_from_stack(
    stack: BufferStack,
    *,
    a_film: float,
    substrate: str,
) -> dict[str, Any] | None:
    """Build a mismatch option dict for a single- or multi-layer stack."""
    layers = resolve_stack_layers(stack)
    if not layers:
        return None
    top = layers[-1]
    bottom = layers[0]
    try:
        m_film_top = lattice_mismatch_percent(
            a_film, substrate, match="cube_on_cube", substrate_a=top.lattice_a_ang
        )
        m_bottom_si = lattice_mismatch_percent(
            bottom.lattice_a_ang, substrate, match="cube_on_cube"
        )
    except ValueError:
        return None

    interface_pcts: list[float] = [m_film_top, m_bottom_si]
    interface_detail: list[str] = [
        f"film–{top.name} {m_film_top:.2f}%",
        f"{bottom.name}–Si {m_bottom_si:.2f}%",
    ]
    intermediate_abs: list[float] = []
    for i in range(len(layers) - 1):
        lo, hi = layers[i], layers[i + 1]
        try:
            m_ll = _layer_layer_mismatch_pct(lo.lattice_a_ang, hi.lattice_a_ang)
        except ValueError:
            continue
        interface_pcts.append(m_ll)
        intermediate_abs.append(abs(m_ll))
        interface_detail.append(f"{lo.name}–{hi.name} {m_ll:.2f}%")

    # Effective mismatch for ranking:
    # - single layer: max(|film–buf|, |buf–Si|) (legacy behaviour)
    # - multi-layer: film–top template match is primary; bottom–Si and interlayers
    #   contribute a soft penalty only (stacks are process templates, not coherent
    #   epitaxial chains — heuristic, not CALPHAD).
    if stack.is_multilayer:
        soft = 0.15 * abs(m_bottom_si)
        if intermediate_abs:
            soft += 0.10 * max(intermediate_abs)
        eff = abs(m_film_top) + soft
        report_pct = m_film_top
    else:
        eff = max(abs(p) for p in interface_pcts)
        report_pct = m_film_top
        for p in interface_pcts:
            if abs(p) >= abs(report_pct):
                report_pct = p

    flags = list(aggregate_stack_flags(stack))
    ceiling = stack_process_temp_ceiling_c(stack)
    path_kind = "stack" if stack.is_multilayer else "buffer"
    notes_bits = [
        f"{'multi-layer stack' if stack.is_multilayer else 'buffer'} {stack.name}: "
        + ", ".join(interface_detail),
    ]
    if stack.notes:
        notes_bits.append(stack.notes)
    if stack.process_note:
        notes_bits.append(stack.process_note)

    return {
        "path": f"{path_kind}/{stack.name}",
        "match": "cube_on_cube",
        "buffer": stack.name,
        "layers": list(stack.layers),
        "is_multilayer": stack.is_multilayer,
        "mismatch_pct": report_pct,
        "mismatch_film_buffer_pct": m_film_top,
        "mismatch_buffer_si_pct": m_bottom_si,
        "mismatch_effective_abs_pct": eff,
        "score": _mismatch_score_from_percent(eff),
        "notes": "; ".join(notes_bits),
        "process_note": stack.process_note,
        "chemical_flags": flags,
        "max_process_temp_c": ceiling,
        "thermal_window_note": stack.thermal_window_note
        or (layers[-1].thermal_window_note if layers else ""),
        "window_notes": stack_window_notes(stack),
    }


def evaluate_mismatch_options(candidate: StructureCandidate) -> list[dict[str, Any]]:
    """Enumerate direct epitaxy and buffer/stack paths ranked by |mismatch|.

    Each option may include ``chemical_flags``, ``max_process_temp_c``,
    ``thermal_window_note``, and ``layers`` (P2.2). Multi-layer stacks appear
    alongside single buffers when ``use_buffers`` is enabled.
    """
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
        direct = BUFFER_LIBRARY["direct_Si"]
        options.append({
            "path": f"direct/{m}",
            "match": m,
            "buffer": "direct_Si",
            "layers": ["direct_Si"],
            "is_multilayer": False,
            "mismatch_pct": pct,
            "mismatch_effective_abs_pct": abs(pct),
            "score": _mismatch_score_from_percent(pct),
            "notes": f"direct on Si with {m} matching",
            "process_note": "Direct epitaxy on Si (no buffer).",
            "chemical_flags": list(direct.chemical_flags),
            "max_process_temp_c": None,
            "thermal_window_note": direct.thermal_window_note,
            "window_notes": [direct.thermal_window_note] if direct.thermal_window_note else [],
        })
    if _use_buffers(candidate):
        family = candidate.material_family
        # Single-layer buffers
        for buf in list_buffers_for_family(family):
            if buf.name == "direct_Si":
                continue
            opt = _option_from_stack(
                stack_from_single(buf), a_film=a_film, substrate=substrate
            )
            if opt is not None:
                # Preserve legacy path prefix for single buffers
                opt["path"] = f"buffer/{buf.name}"
                options.append(opt)
        # Multi-layer stacks
        for stack in list_stacks_for_family(family, multilayer_only=True):
            opt = _option_from_stack(stack, a_film=a_film, substrate=substrate)
            if opt is not None:
                options.append(opt)
    options.sort(
        key=lambda o: (
            abs(float(o.get("mismatch_effective_abs_pct", o["mismatch_pct"]))),
            0 if o.get("is_multilayer") else 1,  # prefer informative stacks on ties
            str(o.get("buffer", "")),
        )
    )
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


def _chemical_score_for_path(
    family: str,
    candidate: StructureCandidate,
    best: dict[str, Any] | None,
) -> tuple[float, list[str], list[str]]:
    """Return (score, flags, extra_notes) using family base + stack flags."""
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

    # Stack-driven refinements (small, documented — not full thermo)
    if "oxide_nitride_interface" in flags:
        chemical = _clamp(chemical - 5.0)
        notes.append("oxide–nitride interface caution")
    if "interdiffusion_caution" in flags:
        chemical = _clamp(chemical - 3.0)
        notes.append("multi-layer interdiffusion caution")
    if "oxygen_window" in flags and family == "tm_nitride":
        chemical = _clamp(chemical - 2.0)
        notes.append("oxygen process window on nitride path")
    if "nitrogen_window" in flags and family in {"tm_nitride", "mgb2_boride"}:
        # Slight credit for known N-compatible buffer chemistry
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
    """Return (score, process_temp_c, thermal_window_note, extra_notes)."""
    notes: list[str] = []
    t_family = _FAMILY_PROCESS_TEMP_C.get(family, 700.0)
    t_proc = t_family
    thermal_note = ""
    if best is not None:
        ceiling = best.get("max_process_temp_c")
        if ceiling is not None:
            t_proc = max(t_family, float(ceiling))
            notes.append(f"process temp ceiling ~{float(ceiling):.0f} °C (stack/film)")
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
    """Richer buffer_availability using library depth (P2.2)."""
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
        # small credit for library breadth
        base = min(100.0, base + min(5.0, 0.5 * n_single + 1.0 * n_stacks))
        return _clamp(base)
    # other families: modest credit when stacks exist
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
    """Ordered recommendations: best path first, then other options, then library.

    When *include_library* is False (buffers disabled), only paths present in
    *options* / *best* are listed — no stack/buffer library expansion.
    """
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
                notes.append("layers (sub→film): " + " / ".join(best["layers"]))
        if best.get("match") == "45deg":
            notes.append("assumed 45° in-plane registry vs Si(001)")
        if best.get("process_note"):
            notes.append(str(best["process_note"]))
    elif candidate.in_plane_strain is not None:
        mismatch_pct = abs(candidate.in_plane_strain) * 100.0
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        notes.append("mismatch from |in_plane_strain| (no lattice_abc vs substrate)")
    else:
        mismatch_pct = 5.0
        lattice_score = _mismatch_score_from_percent(mismatch_pct)
        notes.append("mismatch defaulted (no lattice data)")

    thermal, t_proc, thermal_window_note, thermal_notes = _thermal_for_path(
        family, best, cmos_limit_c=cmos_limit_c
    )
    notes.extend(thermal_notes)

    chemical, chem_flags, chem_notes = _chemical_score_for_path(family, candidate, best)
    notes.extend(chem_notes)

    use_bufs = _use_buffers(candidate)
    if use_bufs:
        n_single = len(
            [b for b in list_buffers_for_family(family) if b.name != "direct_Si"]
        )
        n_stacks = len(list_stacks_for_family(family, multilayer_only=True))
    else:
        # Do not credit or recommend inaccessible library paths when opt-out.
        n_single = 0
        n_stacks = 0
    buffer_score = _buffer_availability_score(
        family,
        best,
        n_single=n_single,
        n_stacks=n_stacks,
        mismatch_pct=mismatch_pct,
    )
    buffers = _recommended_buffer_list(
        family, options, best, include_library=use_bufs
    )

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

    # Surface chemical / thermal window on the notes string for card visibility
    if chem_flags:
        notes.append("chemical flags: " + ", ".join(chem_flags))
    if thermal_window_note:
        notes.append("thermal window: " + thermal_window_note)
    elif best and best.get("window_notes"):
        # take first window note if dedicated field empty
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
        "stacks_for_family": [
            s.name for s in list_stacks_for_family(candidate.material_family)
        ],
    }
    try:
        info["substrate_target_a"] = substrate_in_plane_spacing(substrate)
    except ValueError:
        info["substrate_target_a"] = None
    info["mismatch_pct"] = _raw_mismatch_percent(candidate)
    return info
