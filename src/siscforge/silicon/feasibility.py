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

v0.5 (P2.3) drives **recommended thickness** from Matthews–Blakeslee /
People–Bean critical-thickness estimates and adds **membrane-transfer**
heuristics (ranking / process guidance only — not continuum FEM).
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

SCORER_VERSION = "0.5"

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
