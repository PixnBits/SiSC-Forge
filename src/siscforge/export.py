"""JSON / CSV / Markdown export helpers."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from siscforge.josephson.fabrication import (
    NON_SIS_AB_CAVEAT,
    format_fab_notes_for_csv,
)
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate

# ---------------------------------------------------------------------------
# Process recommendation schema (P2.5 freeze)
# ---------------------------------------------------------------------------
# Stable machine-readable block for experimental handoff. Schema version is
# independent of SiFeasibilityScore.version / ranking config versions.
#
# Top-level keys (see process_recommendation() docstring for field semantics):
#   schema_version, candidate_id, formula, material_family, substrate,
#   in_plane_strain, rank, on_pareto_front,
#   recommended_buffers, recommended_stack,
#   recommended_thickness_nm, critical_thickness_nm, critical_thickness_method,
#   critical_thickness_people_bean_nm,
#   process_temp_ceiling_c, thermal_window_note, chemical_flags,
#   membrane_transfer_candidate, membrane_transfer_note,
#   result_quality, do_not_cite_tc, trust_warning,
#   composite_score, performance_score, performance_score_source,
#   si_feasibility_total, si_scorer_version
# ---------------------------------------------------------------------------

PROCESS_RECOMMENDATION_SCHEMA_VERSION = "1.0"


def evaluations_to_jsonable(evaluations: Iterable[CandidateEvaluation]) -> list[dict]:
    """Convert evaluations to plain JSON-serializable dicts."""
    return [ev.model_dump(mode="json") for ev in evaluations]


def write_evaluations_json(
    evaluations: Iterable[CandidateEvaluation],
    path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    """Write a list of evaluations to a JSON file. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = evaluations_to_jsonable(evaluations)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        fh.write("\n")
    return path


def write_candidates_json(
    candidates: Iterable[StructureCandidate],
    path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    """Write a list of structure candidates to JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [c.model_dump(mode="json") for c in candidates]
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        fh.write("\n")
    return path


def _si_weight(si, key: str) -> float | None:
    """Look up a component weight from a SiFeasibilityScore (if present)."""
    if si is None:
        return None
    weights = getattr(si, "weights", None) or {}
    if key not in weights:
        return None
    return float(weights[key])


def _fmt_thickness_band(band: object) -> str:
    """Format recommended_thickness_nm for CSV / cards."""
    if band is None:
        return ""
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return f"{band[0]:g}–{band[1]:g}"
    return str(band)


def _thickness_jsonable(band: object) -> float | list[float] | None:
    """Serialize recommended_thickness_nm for the process-recommendation schema."""
    if band is None:
        return None
    if isinstance(band, (list, tuple)) and len(band) == 2:
        return [float(band[0]), float(band[1])]
    try:
        return float(band)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ranking_weight(ev: CandidateEvaluation, key: str) -> float | None:
    weights = getattr(ev, "ranking_weights", None) or {}
    if key not in weights:
        return None
    return float(weights[key])


def _breakdown_field(ev: CandidateEvaluation, key: str) -> object:
    bd = getattr(ev, "composite_breakdown", None) or {}
    return bd.get(key)


def _trust_warning(ev: CandidateEvaluation) -> str | None:
    """Human caveat when Tc/λ must not be quoted as production literature.

    Returns None only for ``result_quality == "production"``. Screening,
    suspect, unreliable, and unknown tiers all carry a non-null warning so
    machine consumers and Markdown cards stay aligned with ``do_not_cite_tc``.
    """
    rq = getattr(ev, "result_quality", None) or "unknown"
    if rq == "production":
        src = getattr(ev, "performance_score_source", None) or ""
        if src in {"dmft_pairing", "dmft_pairing_mock"}:
            return (
                "result_quality=production but headline performance is a "
                "DMFT pairing proxy, not Eliashberg/EPW Tc — do not cite as Tc."
            )
        return None
    if rq in {"screening_suspect", "unreliable"}:
        notes = (getattr(ev, "quality_notes", None) or "").strip()
        base = (
            f"result_quality={rq}: do **not** quote Tc/λ as production predictions; "
            "refine with denser grids / tuned Wannier before citing."
        )
        if notes:
            return f"{base} ({notes})"
        return base
    if rq == "screening":
        return (
            "result_quality=screening: Tc/λ are order-of-magnitude only "
            "(screening grids / random Wannier) — not literature-grade; "
            "do not cite as production."
        )
    # unknown and any other non-production tier — conservative default
    return (
        f"result_quality={rq}: trust tier is not production — "
        "do not cite Tc/λ as production predictions."
    )



def process_recommendation(ev: CandidateEvaluation) -> dict[str, Any]:
    """Build the frozen P2.5 process-recommendation dict for one evaluation.

    Schema version ``PROCESS_RECOMMENDATION_SCHEMA_VERSION`` (``1.0``). Keys are
    stable for Phase 2 handoff; consumers should key on ``schema_version``.

    **Identity / ranking**

    - ``schema_version``: schema freeze id (not Si scorer version)
    - ``candidate_id``, ``formula``, ``material_family``
    - ``substrate``, ``in_plane_strain``
    - ``rank``, ``on_pareto_front``

    **Process recommendation (actionable)**

    - ``recommended_buffers``: list of stack labels (best first)
    - ``recommended_stack``: primary stack (first buffer entry or null)
    - ``recommended_thickness_nm``: float or ``[lo, hi]`` band (nm), or null
    - ``critical_thickness_nm``: primary h_c (nm)
    - ``critical_thickness_method``: e.g. Matthews-Blakeslee
    - ``critical_thickness_people_bean_nm``: optional metastable h_c
    - ``process_temp_ceiling_c``: heuristic process temp ceiling (°C)
    - ``thermal_window_note``: short thermal-window prose
    - ``chemical_flags``: e.g. nitrogen_window, oxygen_window
    - ``membrane_transfer_candidate``: bool
    - ``membrane_transfer_note``: short membrane heuristic note

    **Trust**

    - ``result_quality``: production | screening | screening_suspect | unreliable | unknown
    - ``do_not_cite_tc``: ``False`` only when ``result_quality == "production"``;
      ``True`` for screening / screening_suspect / unreliable / unknown (and any
      other non-production tier). Machine consumers must not treat Tc/λ as
      citable production values when this flag is true.
    - ``trust_warning``: human caveat; null only for production


    **Headline scores (also in JSON for machine consumers)**

    - ``composite_score``, ``performance_score``, ``performance_score_source``
    - ``si_feasibility_total``, ``si_scorer_version``
    """
    c = ev.candidate
    si = ev.si_feasibility
    buffers = list(si.recommended_buffers) if si and si.recommended_buffers else []
    primary_stack = buffers[0] if buffers else None
    rq = getattr(ev, "result_quality", None) or "unknown"
    warning = _trust_warning(ev)
    # Only production-quality results are citable; all other tiers set the flag.
    do_not_cite = rq != "production"


    thick = _thickness_jsonable(
        getattr(si, "recommended_thickness_nm", None) if si else None
    )

    return {
        "schema_version": PROCESS_RECOMMENDATION_SCHEMA_VERSION,
        "candidate_id": c.candidate_id,
        "formula": c.formula,
        "material_family": c.material_family,
        "substrate": c.substrate,
        "in_plane_strain": c.in_plane_strain,
        "rank": ev.rank,
        "on_pareto_front": getattr(ev, "on_pareto_front", None),
        "recommended_buffers": buffers,
        "recommended_stack": primary_stack,
        "recommended_thickness_nm": thick,
        "critical_thickness_nm": (
            getattr(si, "critical_thickness_nm", None) if si else None
        ),
        "critical_thickness_method": (
            (getattr(si, "critical_thickness_method", None) or "") if si else ""
        )
        or None,
        "critical_thickness_people_bean_nm": (
            getattr(si, "critical_thickness_people_bean_nm", None) if si else None
        ),
        "process_temp_ceiling_c": (
            getattr(si, "process_temp_ceiling_c", None) if si else None
        ),
        "thermal_window_note": (
            (getattr(si, "thermal_window_note", None) or "") if si else ""
        )
        or None,
        "chemical_flags": (
            list(getattr(si, "chemical_flags", None) or []) if si else []
        ),
        "membrane_transfer_candidate": (
            bool(getattr(si, "membrane_transfer_candidate", False)) if si else False
        ),
        "membrane_transfer_note": (
            (getattr(si, "membrane_transfer_note", None) or "") if si else ""
        )
        or None,
        "result_quality": rq,
        "do_not_cite_tc": do_not_cite,
        "trust_warning": warning,
        "composite_score": ev.composite_score,
        "performance_score": ev.performance_score,
        "performance_score_source": getattr(ev, "performance_score_source", None),
        "si_feasibility_total": si.total if si else None,
        "si_scorer_version": si.version if si else None,
    }


def write_process_recommendations_json(
    evaluations: Iterable[CandidateEvaluation],
    path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    """Write campaign-level ``process_recommendations.json`` (list of objects).

    Each element is the dict from :func:`process_recommendation`. Order matches
    the evaluation list (typically already ranked).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [process_recommendation(ev) for ev in evaluations]
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        fh.write("\n")
    return path


def _jj(ev: CandidateEvaluation):
    """JosephsonMetrics or None."""
    return getattr(ev, "josephson", None)


def _jj_fab(ev: CandidateEvaluation):
    """Nested P4.2 fabrication hints or None."""
    jj = _jj(ev)
    return getattr(jj, "fabrication", None) if jj is not None else None


def _evaluation_row(ev: CandidateEvaluation) -> dict[str, object]:
    """Flat row for CSV / tables with Phase-0 summary fields."""
    si = ev.si_feasibility
    comps = si.components if si else None
    phonon = ev.phonon
    scf = ev.scf
    cand = ev.candidate
    hull = cand.energy_above_hull_proxy
    if hull is None and scf is not None:
        hull = scf.energy_above_hull_eV_per_atom
    jj = _jj(ev)
    fab = _jj_fab(ev)

    return {
        "rank": ev.rank,
        "candidate_id": cand.candidate_id,
        "formula": cand.formula,
        "composition": _fmt_composition(cand.composition),
        "material_family": cand.material_family,
        "substrate": cand.substrate or "",
        "in_plane_strain": cand.in_plane_strain,
        "performance_score": ev.performance_score,
        "performance_score_source": getattr(ev, "performance_score_source", None),
        "lambda_total": (
            ev.electron_phonon.lambda_total if ev.electron_phonon else None
        ),
        "omega_log_K": ev.electron_phonon.omega_log if ev.electron_phonon else None,
        "Tc_allen_dynes": (
            ev.electron_phonon.Tc_allen_dynes if ev.electron_phonon else None
        ),
        "Tc_eliashberg": (
            ev.electron_phonon.Tc_eliashberg if ev.electron_phonon else None
        ),
        "surrogate_lambda": (
            (ev.tc_lambda_surrogate or {}).get("predicted_lambda")
            if getattr(ev, "tc_lambda_surrogate", None)
            else None
        ),
        "surrogate_omega_log_K": (
            (ev.tc_lambda_surrogate or {}).get("predicted_omega_log")
            if getattr(ev, "tc_lambda_surrogate", None)
            else None
        ),
        "surrogate_Tc": (
            (ev.tc_lambda_surrogate or {}).get("predicted_Tc")
            if getattr(ev, "tc_lambda_surrogate", None)
            else None
        ),
        "surrogate_uncertainty": (
            (ev.tc_lambda_surrogate or {}).get("uncertainty")
            if getattr(ev, "tc_lambda_surrogate", None)
            else None
        ),
        "surrogate_model_version": (
            (ev.tc_lambda_surrogate or {}).get("model_version")
            if getattr(ev, "tc_lambda_surrogate", None)
            else None
        ),
        "acquisition_score": getattr(ev, "acquisition_score", None),
        "al_selected_for_expensive": getattr(ev, "al_selected_for_expensive", None),
        "acquisition_pool": getattr(ev, "acquisition_pool", None),
        "acquisition_mode": getattr(ev, "acquisition_mode", None),
        "si_feasibility_total": si.total if si else None,
        "si_scorer_version": si.version if si else None,
        "si_lattice_mismatch": comps.lattice_mismatch if comps else None,
        "si_thermal_budget": comps.thermal_budget if comps else None,
        "si_chemical": comps.chemical_compatibility if comps else None,
        "si_buffer": comps.buffer_availability if comps else None,
        "si_process_maturity": comps.process_maturity if comps else None,
        "si_w_lattice_mismatch": _si_weight(si, "lattice_mismatch"),
        "si_w_thermal_budget": _si_weight(si, "thermal_budget"),
        "si_w_chemical": _si_weight(si, "chemical_compatibility"),
        "si_w_buffer": _si_weight(si, "buffer_availability"),
        "si_w_process_maturity": _si_weight(si, "process_maturity"),
        "composite_score": ev.composite_score,
        "on_pareto_front": getattr(ev, "on_pareto_front", None),
        "ranking_w_performance": _ranking_weight(ev, "performance"),
        "ranking_w_si_feasibility": _ranking_weight(ev, "si_feasibility"),
        "ranking_w_uncertainty": _ranking_weight(ev, "uncertainty"),
        "ranking_performance_ceiling_K": _ranking_weight(ev, "performance_ceiling_K"),
        "composite_perf_norm": _breakdown_field(ev, "performance_norm"),
        "composite_si": _breakdown_field(ev, "si_feasibility"),
        "composite_certainty_norm": _breakdown_field(ev, "certainty_norm"),
        "composite_pre_penalty": _breakdown_field(ev, "pre_penalty"),
        "result_quality": getattr(ev, "result_quality", None) or "unknown",
        "quality_flags": ";".join(getattr(ev, "quality_flags", None) or []),
        "quality_notes": getattr(ev, "quality_notes", None) or "",
        "dynamically_stable": phonon.dynamically_stable if phonon else None,
        "min_frequency_cm1": phonon.min_frequency_cm1 if phonon else None,
        "max_frequency_cm1": phonon.max_frequency_cm1 if phonon else None,
        "has_imaginary_modes": phonon.has_imaginary_modes if phonon else None,
        "energy_above_hull_proxy": hull,
        "total_energy_eV": scf.total_energy_eV if scf else None,
        "status": ev.status,
        "calculator_name": ev.calculator_name or "",
        "quality_tag": (
            phonon.quality_tag
            if phonon is not None
            else (scf.quality_tag if scf is not None else cand.quality_tag)
        ),
        "recommended_buffers": (
            ";".join(si.recommended_buffers) if si and si.recommended_buffers else ""
        ),
        "si_chemical_flags": (
            ";".join(si.chemical_flags) if si and getattr(si, "chemical_flags", None) else ""
        ),
        "si_thermal_window": (
            getattr(si, "thermal_window_note", "") or "" if si else ""
        ),
        "si_process_temp_ceiling_c": (
            getattr(si, "process_temp_ceiling_c", None) if si else None
        ),
        "si_recommended_thickness_nm": (
            _fmt_thickness_band(getattr(si, "recommended_thickness_nm", None)) if si else ""
        ),
        "si_critical_thickness_nm": (
            getattr(si, "critical_thickness_nm", None) if si else None
        ),
        "si_critical_thickness_method": (
            getattr(si, "critical_thickness_method", "") or "" if si else ""
        ),
        "si_critical_thickness_people_bean_nm": (
            getattr(si, "critical_thickness_people_bean_nm", None) if si else None
        ),
        "si_membrane_transfer_candidate": (
            bool(getattr(si, "membrane_transfer_candidate", False)) if si else False
        ),
        "si_membrane_transfer_note": (
            getattr(si, "membrane_transfer_note", "") or "" if si else ""
        ),
        # P3.1 DFT+U summary columns (empty when disabled / absent)
        "dftu_U_eV": (ev.dftu.U_eV if ev.dftu is not None else None),
        "dftu_J_eV": (ev.dftu.J_eV if ev.dftu is not None else None),
        "dftu_total_magnetization": (
            ev.dftu.total_magnetization if ev.dftu is not None else None
        ),
        "dftu_total_energy_eV": (
            ev.dftu.total_energy_eV if ev.dftu is not None else None
        ),
        "dftu_status": (ev.dftu.status if ev.dftu is not None else ""),
        "dftu_summary": (ev.dftu.summary_line() if ev.dftu is not None else ""),
        # P3.2 Wannier quality columns (empty when disabled / absent)
        "wannier_ok": (
            ev.wannier.wannier_ok if getattr(ev, "wannier", None) is not None else None
        ),
        "wannier_ready_for_dmft": (
            ev.wannier.ready_for_dmft
            if getattr(ev, "wannier", None) is not None
            else None
        ),
        "wannier_spread_sum_ang2": (
            ev.wannier.spread_sum_ang2
            if getattr(ev, "wannier", None) is not None
            else None
        ),
        "wannier_avg_spread_ang2": (
            ev.wannier.avg_spread_ang2
            if getattr(ev, "wannier", None) is not None
            else None
        ),
        "wannier_num_wann": (
            ev.wannier.num_wann if getattr(ev, "wannier", None) is not None else None
        ),
        "wannier_failure_class": (
            (ev.wannier.failure_class or "")
            if getattr(ev, "wannier", None) is not None
            else ""
        ),
        "wannier_status": (
            ev.wannier.status if getattr(ev, "wannier", None) is not None else ""
        ),
        "wannier_summary": (
            ev.wannier.summary_line()
            if getattr(ev, "wannier", None) is not None
            else ""
        ),
        # P3.3 DMFT columns (empty when disabled / absent)
        "dmft_status": (
            ev.dmft.status if getattr(ev, "dmft", None) is not None else ""
        ),
        "dmft_solver": (
            ev.dmft.solver if getattr(ev, "dmft", None) is not None else ""
        ),
        "dmft_converged": (
            ev.dmft.converged if getattr(ev, "dmft", None) is not None else None
        ),
        "dmft_U_eV": (
            ev.dmft.U_eV if getattr(ev, "dmft", None) is not None else None
        ),
        "dmft_J_eV": (
            ev.dmft.J_eV if getattr(ev, "dmft", None) is not None else None
        ),
        "dmft_filling": (
            ev.dmft.filling if getattr(ev, "dmft", None) is not None else None
        ),
        "dmft_mass_enhancement": (
            ev.dmft.mass_enhancement
            if getattr(ev, "dmft", None) is not None
            else None
        ),
        "dmft_leading_pairing_eigenvalue": (
            ev.dmft.leading_pairing_eigenvalue
            if getattr(ev, "dmft", None) is not None
            else None
        ),
        "dmft_pairing_symmetry": (
            ev.dmft.pairing_symmetry or ""
            if getattr(ev, "dmft", None) is not None
            else ""
        ),
        "dmft_summary": (
            ev.dmft.summary_line()
            if getattr(ev, "dmft", None) is not None
            else ""
        ),
        # P4.1 / P4.2 Josephson (empty when disabled / absent)
        "josephson_approximate": True if jj is not None else None,
        "josephson_status": (jj.status if jj is not None else ""),
        "josephson_secondary_ranking": (
            (jj.secondary_ranking or "") if jj is not None else ""
        ),
        "josephson_secondary_order": (
            jj.secondary_order if jj is not None else None
        ),
        "josephson_gap_meV": (jj.gap_meV if jj is not None else None),
        "josephson_gap_source": (
            (jj.gap_source or "") if jj is not None else ""
        ),
        "josephson_tc_used_K": (jj.tc_used_K if jj is not None else None),
        "josephson_icrn_mV": (jj.icrn_mV if jj is not None else None),
        "josephson_jc_A_per_cm2": (
            jj.jc_A_per_cm2 if jj is not None else None
        ),
        "josephson_switching_energy_eV": (
            jj.switching_energy_eV if jj is not None else None
        ),
        "josephson_method": (jj.method if jj is not None else ""),
        "josephson_notes": (jj.notes if jj is not None else ""),
        "josephson_junction_class": (
            (fab.suggested_junction_class or "") if fab is not None else ""
        ),
        "josephson_beol_friendly": (
            fab.beol_friendly if fab is not None else None
        ),
        "josephson_thermal_caution": (
            fab.thermal_budget_caution if fab is not None else None
        ),
        "josephson_fab_flags": (
            ";".join(fab.flags) if fab is not None else ""
        ),
        "josephson_fab_notes": (
            format_fab_notes_for_csv(fab.notes) if fab is not None else ""
        ),
    }


def _fmt_composition(comp: dict[str, float]) -> str:
    if not comp:
        return ""
    parts = [f"{el}{frac:g}" for el, frac in sorted(comp.items())]
    return "".join(parts)


CSV_FIELDNAMES = [
    "rank",
    "candidate_id",
    "formula",
    "composition",
    "material_family",
    "substrate",
    "in_plane_strain",
    "performance_score",
    "performance_score_source",
    "lambda_total",
    "omega_log_K",
    "Tc_allen_dynes",
    "Tc_eliashberg",
    "surrogate_lambda",
    "surrogate_omega_log_K",
    "surrogate_Tc",
    "surrogate_uncertainty",
    "surrogate_model_version",
    "acquisition_score",
    "al_selected_for_expensive",
    "acquisition_pool",
    "acquisition_mode",
    "si_feasibility_total",
    "si_scorer_version",
    "si_lattice_mismatch",
    "si_thermal_budget",
    "si_chemical",
    "si_buffer",
    "si_process_maturity",
    "si_w_lattice_mismatch",
    "si_w_thermal_budget",
    "si_w_chemical",
    "si_w_buffer",
    "si_w_process_maturity",
    "composite_score",
    "on_pareto_front",
    "ranking_w_performance",
    "ranking_w_si_feasibility",
    "ranking_w_uncertainty",
    "ranking_performance_ceiling_K",
    "composite_perf_norm",
    "composite_si",
    "composite_certainty_norm",
    "composite_pre_penalty",
    "result_quality",
    "quality_flags",
    "quality_notes",
    "dynamically_stable",
    "min_frequency_cm1",
    "max_frequency_cm1",
    "has_imaginary_modes",
    "energy_above_hull_proxy",
    "total_energy_eV",
    "status",
    "calculator_name",
    "quality_tag",
    "recommended_buffers",
    "si_chemical_flags",
    "si_thermal_window",
    "si_process_temp_ceiling_c",
    "si_recommended_thickness_nm",
    "si_critical_thickness_nm",
    "si_critical_thickness_method",
    "si_critical_thickness_people_bean_nm",
    "si_membrane_transfer_candidate",
    "si_membrane_transfer_note",
    "dftu_U_eV",
    "dftu_J_eV",
    "dftu_total_magnetization",
    "dftu_total_energy_eV",
    "dftu_status",
    "dftu_summary",
    "wannier_ok",
    "wannier_ready_for_dmft",
    "wannier_spread_sum_ang2",
    "wannier_avg_spread_ang2",
    "wannier_num_wann",
    "wannier_failure_class",
    "wannier_status",
    "wannier_summary",
    "dmft_status",
    "dmft_solver",
    "dmft_converged",
    "dmft_U_eV",
    "dmft_J_eV",
    "dmft_filling",
    "dmft_mass_enhancement",
    "dmft_leading_pairing_eigenvalue",
    "dmft_pairing_symmetry",
    "dmft_summary",
    "josephson_approximate",
    "josephson_status",
    "josephson_secondary_ranking",
    "josephson_secondary_order",
    "josephson_gap_meV",
    "josephson_gap_source",
    "josephson_tc_used_K",
    "josephson_icrn_mV",
    "josephson_jc_A_per_cm2",
    "josephson_switching_energy_eV",
    "josephson_method",
    "josephson_notes",
    "josephson_junction_class",
    "josephson_beol_friendly",
    "josephson_thermal_caution",
    "josephson_fab_flags",
    "josephson_fab_notes",
]


def write_evaluations_csv(
    evaluations: Iterable[CandidateEvaluation],
    path: str | Path,
) -> Path:
    """Write a flat CSV summary of ranked evaluations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_evaluation_row(ev) for ev in evaluations]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_synthesis_cards(
    evaluations: Iterable[CandidateEvaluation],
    path: str | Path,
    *,
    campaign_name: str = "",
    bootstrap: bool | None = None,
    bootstrap_message: str | None = None,
    model_version: str | None = None,
    training_set_size: int | None = None,
) -> Path:
    """Write Markdown synthesis cards (one section per ranked candidate).

    P2.5 layout per card: Identity → Headline scores → Process recommendation
    (human bullets + fenced JSON) → Supporting detail.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"# Synthesis cards{f' — {campaign_name}' if campaign_name else ''}",
        "",
        "Auto-generated by SiSC-Forge. Values may be mock or screening-quality. "
        f"Process recommendation JSON uses schema version "
        f"{PROCESS_RECOMMENDATION_SCHEMA_VERSION} "
        "(see docs/process-recommendation-schema.md).",
        "",
    ]

    # AC15: bootstrap / low-data regime visible on synthesis cards
    if bootstrap or bootstrap_message:
        msg = bootstrap_message or (
            "BOOTSTRAP MODE — rankings are prioritization aids, not quantitative "
            "predictions. Promote clean EPW results and retrain to improve."
        )
        lines.extend(
            [
                f"> **{msg}**",
                "",
                (
                    f"> Surrogate model: `{model_version or 'heuristic'}`"
                    + (
                        f" · training-set size: {training_set_size}"
                        if training_set_size is not None
                        else ""
                    )
                ),
                "",
            ]
        )
    # Ranking provenance banner (P2.4) — take weights from first ranked row
    eval_list = list(evaluations)
    if eval_list:
        rw = getattr(eval_list[0], "ranking_weights", None) or {}
        if rw:
            lines.extend(
                [
                    "### Ranking axes (campaign)",
                    (
                        f"- weights: performance={rw.get('performance', '—')}, "
                        f"si_feasibility={rw.get('si_feasibility', '—')}, "
                        f"uncertainty={rw.get('uncertainty', '—')}"
                    ),
                    (
                        f"- performance ceiling: "
                        f"{rw.get('performance_ceiling_K', 40)} K"
                    ),
                    "",
                ]
            )
        # P4.2: label presentation-only JJ secondary sort (rank identity unchanged)
        sec_modes = {
            getattr(getattr(ev, "josephson", None), "secondary_ranking", None)
            for ev in eval_list
            if getattr(ev, "josephson", None) is not None
        }
        sec_modes.discard(None)
        sec_modes.discard("")
        sec_modes.discard("none")
        if sec_modes:
            mode_s = ", ".join(sorted(str(m) for m in sec_modes))
            lines.extend(
                [
                    "### Josephson shortlist presentation (P4.2)",
                    (
                        f"- **secondary sort**: `{mode_s}` — reorders only rows "
                        "that already carry Josephson metrics. "
                        "`rank` / `composite_score` are **unchanged**. "
                        "Prefer `.rank` and `.josephson.secondary_order`; "
                        "do not assume list index equals rank."
                    ),
                    "- fabrication class labels are **heuristics, not process qualification**.",
                    "",
                ]
            )
    for ev in eval_list:
        lines.extend(_card_markdown(ev))
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_candidate_onepagers(
    evaluations: Iterable[CandidateEvaluation],
    directory: str | Path,
    *,
    campaign_name: str = "",
    max_candidates: int = 10,
) -> list[Path]:
    """Write one Markdown one-pager per top ranked candidate (desktop handoff).

    Files: ``candidate_01_<formula>.md`` under *directory*. Layout matches
    synthesis cards (Identity → Headline → Process recommendation → detail).
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    ranked = sorted(
        list(evaluations),
        key=lambda e: (e.rank if e.rank is not None else 10**9),
    )
    for i, ev in enumerate(ranked[:max_candidates], start=1):
        formula = "".join(
            ch if ch.isalnum() else "_" for ch in ev.candidate.formula
        )[:24]
        path = directory / f"candidate_{i:02d}_{formula}.md"
        lines = [
            f"# Candidate one-pager — {ev.candidate.formula}",
            "",
            f"Campaign: {campaign_name or '—'}",
            "",
        ]
        lines.extend(_card_markdown(ev))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def _fmt_weights(weights: dict[str, float] | None) -> str:
    if not weights:
        return "—"
    parts = [f"{k}={v:g}" for k, v in weights.items()]
    return ", ".join(parts)


def _fmt_num(value: object, *, digits: int | None = None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if digits is not None:
            return f"{value:.{digits}f}".rstrip("0").rstrip(".")
        return f"{value:g}"
    return str(value)


def _card_markdown(ev: CandidateEvaluation) -> list[str]:
    """P2.5 scannable card: Identity → Headline → Process → Supporting detail."""
    c = ev.candidate
    si = ev.si_feasibility
    ph = ev.phonon
    scf = ev.scf
    rank = ev.rank if ev.rank is not None else "—"
    pareto = getattr(ev, "on_pareto_front", None)
    if pareto is True:
        pareto_s = "yes"
    elif pareto is False:
        pareto_s = "no"
    else:
        pareto_s = "—"
    rq = getattr(ev, "result_quality", "unknown") or "unknown"
    rec = process_recommendation(ev)

    lines: list[str] = [
        f"## #{rank} — {c.formula}",
        "",
        "### Identity",
        f"- **formula**: {c.formula}",
        f"- **candidate_id**: `{c.candidate_id}`",
        f"- **family**: {c.material_family}",
        f"- **composition**: {_fmt_composition(c.composition) or c.formula}",
        f"- **substrate**: {c.substrate or '—'}",
        f"- **in-plane strain**: "
        f"{c.in_plane_strain if c.in_plane_strain is not None else '—'}",
        f"- **rank**: {rank}",
        f"- **on Pareto front**: {pareto_s}",
        f"- **status**: {ev.status} (`{ev.calculator_name or 'n/a'}`)",
        "",
        "### Headline scores",
        f"- **composite score**: {_fmt_num(ev.composite_score)}",
        f"- **performance / Tc proxy**: {_fmt_num(ev.performance_score)}"
        f" ({getattr(ev, 'performance_score_source', None) or '—'})",
        f"- **Si-feasibility total**: "
        f"{_fmt_num(si.total if si else None)}"
        + (f" / 100 (v{si.version})" if si else ""),
        f"- **result quality**: `{rq}`",
    ]
    src = getattr(ev, "performance_score_source", None) or ""
    if src == "dmft_pairing_mock":
        lines.append(
            "- **performance origin**: DMFT pairing eigenvalue "
            "(illustrative mock — **not** Eliashberg/EPW Tc, not quantitative)"
        )
    elif src == "dmft_pairing":
        lines.append(
            "- **performance origin**: DMFT pairing eigenvalue "
            "(Tc-like ranking proxy — **not** Eliashberg/EPW Tc)"
        )
    warning = rec.get("trust_warning")
    if warning:
        lines.append(f"- **trust caveat**: {warning}")
    flags = getattr(ev, "quality_flags", None) or []
    if flags:
        lines.append(f"- **quality flags**: {', '.join(flags)}")
    if c.energy_above_hull_proxy is not None:
        lines.append(f"- **E_hull proxy (eV/atom)**: {c.energy_above_hull_proxy}")

    # --- Process recommendation (human + machine) ---
    lines.extend(["", "### Process recommendation"])
    stack = rec.get("recommended_stack")
    buffers = rec.get("recommended_buffers") or []
    if stack:
        extra = ""
        if len(buffers) > 1:
            extra = f" (also: {', '.join(buffers[1:4])}" + (
                ", …" if len(buffers) > 4 else ""
            ) + ")"
        lines.append(f"- **recommended buffer / stack**: `{stack}`{extra}")
    else:
        lines.append("- **recommended buffer / stack**: —")

    thick_s = _fmt_thickness_band(
        getattr(si, "recommended_thickness_nm", None) if si else None
    )
    ct_nm = rec.get("critical_thickness_nm")
    ct_method = rec.get("critical_thickness_method") or "—"
    ct_pb = rec.get("critical_thickness_people_bean_nm")
    thick_line = f"- **recommended thickness (nm)**: {thick_s or '—'}"
    if ct_nm is not None:
        thick_line += f" · **h_c**: {_fmt_num(ct_nm)} nm [{ct_method}]"
        if ct_pb is not None:
            thick_line += f" (People–Bean metastable {_fmt_num(ct_pb)} nm)"
    lines.append(thick_line)

    ceil = rec.get("process_temp_ceiling_c")
    thermal = rec.get("thermal_window_note") or "—"
    lines.append(
        f"- **process temp ceiling**: "
        f"{_fmt_num(ceil)} °C · **thermal window**: {thermal}"
    )
    chem = rec.get("chemical_flags") or []
    lines.append(
        f"- **chemical / N–O window flags**: {', '.join(chem) if chem else '—'}"
    )
    mem = rec.get("membrane_transfer_candidate")
    mem_note = rec.get("membrane_transfer_note")
    mem_s = "yes" if mem else "no"
    if mem_note:
        lines.append(f"- **membrane-transfer candidate**: {mem_s} — {mem_note}")
    else:
        lines.append(f"- **membrane-transfer candidate**: {mem_s}")
    if rec.get("do_not_cite_tc"):
        lines.append(
            f"- **⚠ do not cite Tc/λ as production** "
            f"(`{rq}`"
            + (
                f"; {getattr(ev, 'quality_notes', '') or 'see quality flags'}"
                if getattr(ev, "quality_notes", None)
                else ""
            )
            + ")"
        )
    elif warning:
        lines.append(f"- **Tc/λ caveat**: {warning}")

    # Machine-readable freeze (schema v1.0)
    lines.extend(
        [
            "",
            "```json",
            json.dumps(rec, indent=2, sort_keys=True),
            "```",
        ]
    )

    # --- Supporting detail ---
    lines.extend(["", "### Supporting detail"])

    rw = getattr(ev, "ranking_weights", None) or {}
    bd = getattr(ev, "composite_breakdown", None) or {}
    if rw or bd:
        lines.extend(["", "#### Ranking provenance"])
        if rw:
            lines.append(
                f"- **ranking weights**: perf={rw.get('performance', '—')}, "
                f"Si={rw.get('si_feasibility', '—')}, "
                f"uncertainty={rw.get('uncertainty', '—')} "
                f"(ceiling {rw.get('performance_ceiling_K', 40)} K)"
            )
        if bd:
            lines.append(
                f"- **composite breakdown**: perf_norm={bd.get('performance_norm')}, "
                f"Si={bd.get('si_feasibility')}, "
                f"certainty_norm={bd.get('certainty_norm', '—')}, "
                f"pre_penalty={bd.get('pre_penalty')}"
            )

    if si is not None:
        w = getattr(si, "weights", None) or {}
        lines.extend(
            [
                "",
                "#### Silicon feasibility breakdown",
                f"- **total**: {si.total:.1f} / 100 (v{si.version})",
                f"- **weights**: {_fmt_weights(w)}",
                f"- lattice mismatch: {si.components.lattice_mismatch:.1f}"
                + (f" (w={w['lattice_mismatch']:g})" if "lattice_mismatch" in w else ""),
                f"- thermal budget: {si.components.thermal_budget:.1f}"
                + (f" (w={w['thermal_budget']:g})" if "thermal_budget" in w else ""),
                f"- chemical compatibility: {si.components.chemical_compatibility:.1f}"
                + (
                    f" (w={w['chemical_compatibility']:g})"
                    if "chemical_compatibility" in w
                    else ""
                ),
                f"- buffer availability: {si.components.buffer_availability:.1f}"
                + (
                    f" (w={w['buffer_availability']:g})"
                    if "buffer_availability" in w
                    else ""
                ),
                f"- process maturity: {si.components.process_maturity:.1f}"
                + (
                    f" (w={w['process_maturity']:g})"
                    if "process_maturity" in w
                    else ""
                ),
                f"- recommended buffers (full list): "
                f"{', '.join(si.recommended_buffers) or '—'}",
                f"- notes: {si.notes or '—'}",
            ]
        )

    surr = getattr(ev, "tc_lambda_surrogate", None)
    if surr:
        surr_title = "λ/Tc surrogate"
        if surr.get("bootstrap") or surr.get("quality_tag") in {"stub", "trained"}:
            if surr.get("bootstrap", True):
                surr_title += " (prioritization aid — not experimental Tc)"
        lines.extend(
            [
                "",
                f"#### {surr_title}",
                f"- **model**: {surr.get('model_version', '—')} "
                f"(`{surr.get('quality_tag', 'stub')}` / {surr.get('method', '—')})",
                f"- predicted λ: {surr.get('predicted_lambda')}",
                f"- predicted ω_log (K): {surr.get('predicted_omega_log')}",
                f"- predicted Tc (K): {surr.get('predicted_Tc')}",
                f"- uncertainty (0–1): {surr.get('uncertainty')}",
                f"- training_set_size: {surr.get('training_set_size', '—')}",
                f"- bootstrap: {surr.get('bootstrap', '—')}",
                f"- notes: {surr.get('notes', '—')}",
            ]
        )

    acq = getattr(ev, "acquisition_score", None)
    if acq is not None:
        pool = getattr(ev, "acquisition_pool", None)
        mode = getattr(ev, "acquisition_mode", None)
        pool_reason = getattr(ev, "acquisition_pool_reason", None)
        lines.extend(
            [
                "",
                "#### Active learning (prioritization)",
                f"- acquisition score: {acq}",
                f"- selected for expensive path: "
                f"{getattr(ev, 'al_selected_for_expensive', None)}",
                f"- pool: {pool or '—'}"
                + (f" ({pool_reason})" if pool_reason else ""),
                f"- acquisition mode: {mode or '—'}",
                "- note: prioritization aid — not a measured Tc; real EPW overrides "
                "when present. Mixed conventional/unconventional lists are for "
                "prioritization only (P3.6).",
            ]
        )

    if ph is not None:
        lines.extend(
            [
                "",
                "#### Phonon summary",
                f"- dynamically stable: {ph.dynamically_stable}",
                f"- imaginary modes: {ph.has_imaginary_modes}",
                f"- min / max frequency (cm⁻¹): {ph.min_frequency_cm1} / {ph.max_frequency_cm1}",
                f"- status / quality: {ph.status} / {ph.quality_tag}",
            ]
        )

    eph = ev.electron_phonon
    if eph is not None:
        lines.extend(
            [
                "",
                "#### Electron-phonon / Tc",
                f"- λ: {eph.lambda_total}",
                f"- ω_log (K): {eph.omega_log}",
                f"- μ*: {eph.mu_star}",
                f"- Tc Allen–Dynes (K): {eph.Tc_allen_dynes}",
                f"- Tc Eliashberg (K): {eph.Tc_eliashberg}",
                f"- converged: {eph.converged}",
                f"- status / engine quality_tag: {eph.status} / {eph.quality_tag}",
            ]
        )
        eph_rq = getattr(eph, "result_quality", None) or getattr(
            ev, "result_quality", "unknown"
        )
        lines.append(f"- result_quality (trust): {eph_rq}")
        if getattr(ev, "result_quality", None) in {
            "screening_suspect",
            "unreliable",
            "screening",
        }:
            lines.append(
                "- **caveat**: screening EPW Tc is order-of-magnitude only; "
                "high λ often reflects soft modes / coarse grids / random Wannier "
                "— not production literature values."
            )

    if scf is not None:
        lines.extend(
            [
                "",
                "#### SCF summary",
                f"- total energy (eV): {scf.total_energy_eV}",
                f"- metallic: {scf.is_metallic}",
                f"- status / quality: {scf.status} / {scf.quality_tag}",
            ]
        )

    dftu = getattr(ev, "dftu", None)
    if dftu is not None:
        # Prefer scalar U/J when uniform; otherwise show per-species maps so
        # multi-species cells do not print a bare "None".
        if dftu.U_eV is not None:
            u_line = f"- U (eV): {dftu.U_eV}"
        elif dftu.U_by_species:
            u_line = (
                "- U (eV): "
                + ", ".join(f"{k}={v:g}" for k, v in sorted(dftu.U_by_species.items()))
            )
        else:
            u_line = "- U (eV): —"
        if dftu.J_eV is not None:
            j_line = f"- J (eV): {dftu.J_eV}"
        elif dftu.J_by_species:
            j_line = (
                "- J (eV): "
                + ", ".join(f"{k}={v:g}" for k, v in sorted(dftu.J_by_species.items()))
            )
        else:
            j_line = "- J (eV): —"
        mag = dftu.total_magnetization
        mag_s = f"{mag:g}" if mag is not None else "—"
        energy = dftu.total_energy_eV
        energy_s = f"{energy:g}" if energy is not None else "—"
        lines.extend(
            [
                "",
                "#### DFT+U (correlated proxy, P3.1)",
                u_line,
                j_line,
                f"- Hubbard species: {', '.join(dftu.hubbard_species) or '—'}",
                f"- projectors: {dftu.hubbard_projectors or '—'}",
                f"- total magnetization (μ_B): {mag_s}",
                f"- occupancy: "
                + (
                    ", ".join(f"{k}={v}" for k, v in dftu.occupancy_summary.items())
                    if dftu.occupancy_summary
                    else "—"
                ),
                f"- total energy (eV): {energy_s}",
                f"- status / quality: {dftu.status} / {dftu.quality_tag}",
                f"- summary: {dftu.summary_line()}",
                "- note: cheap DFT+U proxy — see Wannier (P3.2) / DMFT (P3.3) "
                "sections when present; pairing → performance_score is P3.4",
            ]
        )

    wannier = getattr(ev, "wannier", None)
    if wannier is not None:
        avg = wannier.avg_spread_ang2
        avg_s = f"{avg:.3f}" if avg is not None else "—"
        ssum = wannier.spread_sum_ang2
        ssum_s = f"{ssum:.3f}" if ssum is not None else "—"
        gate = "yes" if wannier.ready_for_dmft else "no"
        kmesh = list(wannier.kmesh or [])
        kmesh_s = "×".join(str(x) for x in kmesh) if kmesh else "—"
        lines.extend(
            [
                "",
                "#### Wannierization (standalone quality metrics, P3.2)",
                "- **independent of EPW-internal Wannier** "
                "(`electron_phonon.wannier_ok`); this is the correlated-pathway "
                "prep + metrics step, not the conventional EPW Wannierization.",
                f"- wannier_ok: {wannier.wannier_ok}",
                f"- ready_for_dmft (P3.3 gate): {gate}",
                f"- num_wann / num_bands: {wannier.num_wann} / {wannier.num_bands}",
                f"- projection: {wannier.projection_summary or wannier.projection_mode or '—'}",
                f"- k-mesh (actual): {kmesh_s}",
                f"- spread sum / avg (Å²): {ssum_s} / {avg_s}",
                f"- failure_class: {wannier.failure_class or '—'}",
                f"- work_dir: {wannier.work_dir or '—'}",
                f"- status / quality: {wannier.status} / {wannier.quality_tag}",
                f"- summary: {wannier.summary_line()}",
            ]
        )
        if wannier.dmft_gate_notes:
            lines.append(f"- dmft_gate_notes: {wannier.dmft_gate_notes}")
        next_step = None
        raw = getattr(wannier, "raw", None) or {}
        if isinstance(raw, dict):
            next_step = raw.get("operator_next_step")
        if not next_step:
            cls = wannier.failure_class
            if cls == "nscf_failed":
                next_step = (
                    "inspect wannier/nscf.out; upstream SCF/DFT+U kept; "
                    "fix nscf and re-invoke"
                )
            elif cls == "pw2wannier_failed":
                next_step = (
                    "inspect wannier/pw2wan.out; upstream SCF/DFT+U kept; "
                    "fix pw2wannier90 and re-invoke"
                )
            elif cls == "binary_missing":
                next_step = (
                    "install pw.x, pw2wannier90.x, and wannier90.x (or set QE_BIN) "
                    "and re-invoke; or stage .amn/.mmn manually"
                )
            elif cls == "missing_files":
                next_step = (
                    "install pw.x + pw2wannier90.x and re-invoke for automated "
                    "nscf + pw2wannier90, or stage `.amn`/`.mmn` into `work_dir`"
                )
        if next_step:
            lines.append(f"- **operator next step**: {next_step}")
        lines.append(
            "- note: screening defaults may use proj=random + coarse k; "
            "material-specific production projections and spinor/collinear-spin "
            "manifolds are later residuals. "
            "P3.3 TRIQS/solid_dmft refuses launch when ready_for_dmft is false "
            "(unless explicit mock bypass / allow_without_wannier_gate)."
        )

    dmft = getattr(ev, "dmft", None)
    if dmft is not None:
        fill = dmft.filling
        fill_s = f"{fill:g}" if fill is not None else "—"
        mass = dmft.mass_enhancement
        mass_s = f"{mass:g}" if mass is not None else "—"
        occ = (
            ", ".join(f"{k}={v:g}" for k, v in sorted(dmft.occupancy_summary.items()))
            if dmft.occupancy_summary
            else "—"
        )
        eig = dmft.leading_pairing_eigenvalue
        eig_s = f"{eig:g}" if eig is not None else "—"
        src = getattr(ev, "performance_score_source", None) or ""
        mapped = src in {"dmft_pairing", "dmft_pairing_mock"}
        lines.extend(
            [
                "",
                "#### DMFT (TRIQS / solid_dmft, P3.3/P3.4)",
                f"- solver: {dmft.solver}",
                f"- converged: {dmft.converged}",
                f"- U / J (eV): {dmft.U_eV if dmft.U_eV is not None else '—'} / "
                f"{dmft.J_eV if dmft.J_eV is not None else '—'}",
                f"- occupancy: {occ}",
                f"- filling: {fill_s}",
                f"- mass enhancement m*/m: {mass_s}",
                f"- leading pairing eigenvalue: {eig_s}",
                f"- pairing symmetry: {dmft.pairing_symmetry or '—'} "
                f"(metadata only; does not enter the score)",
                f"- Wannier input: ready={dmft.wannier_ready_for_dmft} "
                f"work_dir={dmft.wannier_work_dir or '—'}",
                f"- status / quality: {dmft.status} / {dmft.quality_tag}",
                f"- summary: {dmft.summary_line()}",
            ]
        )
        conv_blob = (
            (dmft.raw or {}).get("convergence") if isinstance(dmft.raw, dict) else None
        )
        conv_src = None
        if isinstance(conv_blob, dict):
            conv_src = conv_blob.get("source")
        if not conv_src and isinstance(dmft.raw, dict):
            conv_src = (dmft.raw.get("metrics") or {}).get("converged_source")
        if conv_src and conv_src not in {"last_row_heuristic"}:
            lines.append(f"- convergence source: {conv_src}")
        if mapped:
            lines.append(
                f"- pairing → performance (P3.4): {_fmt_num(ev.performance_score)} K "
                f"proxy (`{src}`)"
            )
        if dmft.gate_notes:
            lines.append(f"- gate_notes: {dmft.gate_notes}")
        if dmft.failure_class:
            lines.append(f"- failure_class: {dmft.failure_class}")
        launch = (dmft.raw or {}).get("launch") if isinstance(dmft.raw, dict) else None
        if isinstance(launch, dict) and launch.get("status"):
            lines.append(f"- launch: {launch.get('status')}")
            nxt = launch.get("operator_next")
            if nxt:
                lines.append(f"- operator next step: {nxt}")
            elif launch.get("status") == "invoked":
                lines.append("- launch: solid_dmft invoke completed (p3_x_real_launch)")
            elif launch.get("status") == "drop_in":
                lines.append(
                    "- launch: parsed drop-in observables.json "
                    "(no TRIQS required to ingest)"
                )
            elif str(launch.get("status") or "").startswith("native_"):
                kind = str(launch.get("status")).removeprefix("native_")
                lines.append(
                    f"- launch: parsed native solid_dmft {kind} "
                    "(JSON materialized for resume when possible)"
                )
            elif launch.get("status") == "skipped_solver_missing":
                lines.append(
                    "- launch: TRIQS/solid_dmft missing — run package written; "
                    "drop observables.json / native .dat or install the stack"
                )
            elif launch.get("status") == "deferred":
                lines.append(
                    "- launch: auto_launch=false — run package written "
                    "(see dmft/LAUNCH.md)"
                )
        if src == "dmft_pairing_mock":
            lines.append(
                "- note: mock pairing eigenvalues are **illustrative**, "
                "not literature-validated. Headline performance is a ranking "
                "proxy, not a citable Tc."
            )
        elif mapped:
            lines.append(
                "- note: headline performance is the P3.4 pairing proxy "
                "(not Eliashberg/EPW Tc). Ranking/Pareto consume "
                "`performance_score` only — no family forks."
            )
        else:
            lines.append(
                "- note: pairing eigenvalue is stored; headline performance "
                "is **not** from DMFT pairing "
                f"(source=`{src or '—'}`)."
            )

    jj = getattr(ev, "josephson", None)
    if jj is not None:
        lines.extend(
            [
                "",
                "#### Josephson metrics (P4.1) — **approximate / ranking only**",
                "- **caveat**: Tier-1 analytic estimates "
                "(Ambegaokar–Baratoff + documented geometry). "
                "**Not** a device-design value. Do not cite as measured Ic / Jc.",
                f"- approximate: **{bool(getattr(jj, 'approximate', True))}**",
                f"- gap Δ (meV): {_fmt_num(getattr(jj, 'gap_meV', None))} "
                f"({getattr(jj, 'gap_source', None) or '—'})",
                f"- Tc used (K): {_fmt_num(getattr(jj, 'tc_used_K', None))} "
                f"({getattr(jj, 'tc_source', None) or '—'})",
                f"- IcRn (mV): {_fmt_num(getattr(jj, 'icrn_mV', None))} "
                f"[Ambegaokar–Baratoff]",
                f"- Jc proxy (A/cm²): {_fmt_num(getattr(jj, 'jc_A_per_cm2', None))} "
                f"(RnA={_fmt_num(getattr(jj, 'rna_ohm_um2', None))} Ω·μm²)",
                f"- switching / EJ (eV): {_fmt_num(getattr(jj, 'switching_energy_eV', None))} "
                f"(EJ/kB={_fmt_num(getattr(jj, 'ej_K', None))} K; "
                f"A={_fmt_num(getattr(jj, 'reference_area_um2', None))} μm²)",
                f"- method / tier: {getattr(jj, 'method', '—')} / "
                f"{getattr(jj, 'model_tier', '—')}",
                f"- status / quality: {getattr(jj, 'status', '—')} / "
                f"{getattr(jj, 'quality_tag', '—')}",
                f"- summary: {jj.summary_line() if hasattr(jj, 'summary_line') else '—'}",
            ]
        )
        if getattr(jj, "notes", None):
            lines.append(f"- notes: {jj.notes}")
        fab = getattr(jj, "fabrication", None)
        if fab is not None:
            alts = ", ".join(getattr(fab, "alternative_classes", None) or []) or "—"
            flags = ", ".join(getattr(fab, "flags", None) or []) or "—"
            beol = getattr(fab, "beol_friendly", None)
            beol_s = "yes" if beol is True else "no" if beol is False else "unknown"
            lines.extend(
                [
                    "",
                    "#### Fabrication compatibility (P4.2) — "
                    "**heuristic, not process qualification**",
                    "- **caveat**: labels reuse Si-feasibility signals "
                    "(process-temp ceiling, chemical flags, stacks, membrane). "
                    "**Not** a foundry PDK or process sign-off. "
                    "Repeat: **approximate / ranking only**.",
                    "- suggested junction class: "
                    f"`{getattr(fab, 'suggested_junction_class', 'unknown')}` "
                    f"(alternatives: {alts})",
                ]
            )
            if getattr(fab, "suggested_junction_class", "unknown") != "SIS":
                lines.append(f"- **Tier-1 formula note**: {NON_SIS_AB_CAVEAT}")
            lines.extend(
                [
                    f"- BEOL-friendly (CMOS-ish ≤ "
                    f"{_fmt_num(getattr(fab, 'beol_temp_ceiling_c', None))} °C): "
                    f"**{beol_s}**",
                    f"- thermal-budget caution: "
                    f"**{'yes' if getattr(fab, 'thermal_budget_caution', False) else 'no'}** "
                    "(process ceiling "
                    f"{_fmt_num(getattr(fab, 'process_temp_ceiling_c', None))} °C)",
                    f"- recommended stacks: "
                    f"{', '.join(getattr(fab, 'recommended_stacks', None) or []) or '—'}",
                    f"- flags: {flags}",
                    f"- summary: {fab.summary_line() if hasattr(fab, 'summary_line') else '—'}",
                ]
            )
            for note in list(getattr(fab, "notes", None) or [])[:8]:
                lines.append(f"- note: {note}")
        sec_mode = getattr(jj, "secondary_ranking", None)
        sec_ord = getattr(jj, "secondary_order", None)
        if sec_mode or sec_ord is not None:
            lines.append(
                f"- Josephson secondary presentation order: "
                f"{sec_ord if sec_ord is not None else '—'} "
                f"(key=`{sec_mode or '—'}`; campaign rank unchanged)"
            )

    if ev.notes:
        lines.extend(["", f"_Notes: {ev.notes}_"])
    return lines


def export_campaign_bundle(
    evaluations: list[CandidateEvaluation],
    out_dir: str | Path,
    *,
    formats: list[str] | None = None,
    campaign_name: str = "",
    candidates: list[StructureCandidate] | None = None,
    bootstrap: bool | None = None,
    bootstrap_message: str | None = None,
    model_version: str | None = None,
    training_set_size: int | None = None,
) -> dict[str, Path]:
    """Write the standard Phase-0/2 export set; return map of label → path.

    Always writes ``process_recommendations.json`` (P2.5 schema freeze) alongside
    evaluations JSON so experimental consumers get a single actionable list.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = formats or ["json", "csv"]
    written: dict[str, Path] = {}

    written["evaluations_json"] = write_evaluations_json(
        evaluations, out_dir / "evaluations.json"
    )
    written["process_recommendations"] = write_process_recommendations_json(
        evaluations, out_dir / "process_recommendations.json"
    )
    if "csv" in formats:
        written["csv"] = write_evaluations_csv(evaluations, out_dir / "evaluations.csv")
    if "markdown" in formats or "md" in formats:
        written["markdown"] = write_synthesis_cards(
            evaluations,
            out_dir / "synthesis_cards.md",
            campaign_name=campaign_name,
            bootstrap=bootstrap,
            bootstrap_message=bootstrap_message,
            model_version=model_version,
            training_set_size=training_set_size,
        )
        onepagers = write_candidate_onepagers(
            evaluations,
            out_dir / "candidate_onepagers",
            campaign_name=campaign_name,
            max_candidates=10,
        )
        if onepagers:
            written["candidate_onepagers"] = onepagers[0].parent
    if candidates is not None:
        written["candidates"] = write_candidates_json(
            candidates, out_dir / "candidates.json"
        )
    return written
