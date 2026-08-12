"""JSON / CSV / Markdown export helpers."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
        lines.extend(
            [
                "",
                "#### Active learning (prioritization)",
                f"- acquisition score: {acq}",
                f"- selected for expensive path: "
                f"{getattr(ev, 'al_selected_for_expensive', None)}",
                "- note: prioritization aid — not a measured Tc; real EPW overrides "
                "when present",
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
                "- note: cheap DFT+U proxy — see Wannier (P3.2) section when present; "
                "DMFT (P3.3) / pairing (P3.4) not yet attached",
            ]
        )

    wannier = getattr(ev, "wannier", None)
    if wannier is not None:
        avg = wannier.avg_spread_ang2
        avg_s = f"{avg:.3f}" if avg is not None else "—"
        ssum = wannier.spread_sum_ang2
        ssum_s = f"{ssum:.3f}" if ssum is not None else "—"
        gate = "yes" if wannier.ready_for_dmft else "no"
        lines.extend(
            [
                "",
                "#### Wannierization (quality metrics, P3.2)",
                f"- wannier_ok: {wannier.wannier_ok}",
                f"- ready_for_dmft (P3.3 gate): {gate}",
                f"- num_wann / num_bands: {wannier.num_wann} / {wannier.num_bands}",
                f"- projection: {wannier.projection_summary or wannier.projection_mode or '—'}",
                f"- spread sum / avg (Å²): {ssum_s} / {avg_s}",
                f"- failure_class: {wannier.failure_class or '—'}",
                f"- work_dir: {wannier.work_dir or '—'}",
                f"- status / quality: {wannier.status} / {wannier.quality_tag}",
                f"- summary: {wannier.summary_line()}",
            ]
        )
        if wannier.dmft_gate_notes:
            lines.append(f"- dmft_gate_notes: {wannier.dmft_gate_notes}")
        lines.append(
            "- note: screening Wannier may use proj=random + coarse k; "
            "material-specific production projections are a later residual. "
            "P3.3 TRIQS/solid_dmft should refuse launch when ready_for_dmft is false."
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
