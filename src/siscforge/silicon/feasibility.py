"""Silicon Feasibility scorer (transparent heuristics).

Every component of :class:`~siscforge.models.results.SiFeasibilityComponents`
is always populated. Scores are 0–100 (higher = more Si-process friendly).

v0.5 (P2.3) drives recommended thickness from Matthews–Blakeslee / People–Bean
and adds membrane-transfer heuristics. Scoring body lives in
:_feasibility_score to keep module load order clean.
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
from siscforge.silicon.critical_thickness import (
    estimate_critical_thickness,
    membrane_transfer_heuristic,
)
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    EpitaxyMatch,
    lattice_mismatch_percent,
    parse_substrate,
    substrate_in_plane_spacing,
)

SCORER_VERSION = "0.6"

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
    """Return a full, non-negative weight vector normalized to sum 1.0."""
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
    if a_top <= 0:
        raise ValueError("layer lattice constant must be positive")
    return 100.0 * (a_bottom - a_top) / a_top


def _option_from_stack(
    stack: BufferStack, *, a_film: float, substrate: str
) -> dict[str, Any] | None:
    layers = resolve_stack_layers(stack)
    if not layers:
        return None
    top, bottom = layers[-1], layers[0]
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
    if mode == "auto":
        matches: list[EpitaxyMatch] = ["cube_on_cube", "45deg"]
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
        for buf in list_buffers_for_family(family):
            if buf.name == "direct_Si":
                continue
            opt = _option_from_stack(
                stack_from_single(buf), a_film=a_film, substrate=substrate
            )
            if opt is not None:
                opt["path"] = f"buffer/{buf.name}"
                options.append(opt)
        for stack in list_stacks_for_family(family, multilayer_only=True):
            opt = _option_from_stack(stack, a_film=a_film, substrate=substrate)
            if opt is not None:
                options.append(opt)
    options.sort(
        key=lambda o: (
            abs(float(o.get("mismatch_effective_abs_pct", o["mismatch_pct"]))),
            0 if o.get("is_multilayer") else 1,
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


def _direct_mismatch_percent(
    candidate: StructureCandidate,
    options: list[dict[str, Any]] | None = None,
) -> float | None:
    """Best direct (no buffer) mismatch for membrane heuristics.

    When *options* is provided, reuse it instead of re-enumerating paths.
    """
    opts = options if options is not None else evaluate_mismatch_options(candidate)
    direct = [o for o in opts if str(o.get("path", "")).startswith("direct/")]
    if not direct:
        a_film = _film_in_plane_a(candidate)
        substrate = candidate.substrate or "Si(001)"
        if a_film is None:
            return None
        try:
            return lattice_mismatch_percent(a_film, substrate, match="cube_on_cube")
        except ValueError:
            return None
    direct.sort(key=lambda o: abs(float(o["mismatch_pct"])))
    return float(direct[0]["mismatch_pct"])


# Scoring implementation (imported last to avoid circular import during helper def)
from siscforge.silicon._feasibility_score import (  # noqa: E402
    score_si_feasibility,
    rank_by_si_feasibility,
    scorer_debug_info,
)
