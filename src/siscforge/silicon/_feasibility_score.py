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
        flags = best.get("chemical_flags") or [])
        if "high_thermal_budget" in flags:
            notes.append("high-thermal-budget buffer step (e.g. AlN)")
    score = _thermal_score(t_proc, cmos_limit_c=cmos_limit_c)
    return score, t_proc, thermal_note, notes
