"""JSON / CSV / Markdown export helpers."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from siscforge.models.candidate import CandidateEvaluation, StructureCandidate


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
    """Write Markdown synthesis cards (one section per ranked candidate)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"# Synthesis cards{f' — {campaign_name}' if campaign_name else ''}",
        "",
        "Auto-generated by SiSC-Forge Phase 0. Values may be mock or screening-quality.",
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
    for ev in evaluations:
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

    Files: ``candidate_01_<formula>.md`` under *directory*.
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
            f"Rank: {ev.rank if ev.rank is not None else '—'}",
            "",
        ]
        lines.extend(_card_markdown(ev))
        # Compact action line for experimentalists
        eph = ev.electron_phonon
        si = ev.si_feasibility
        tc = eph.best_tc_K() if eph is not None else None
        rq = getattr(ev, "result_quality", "unknown")
        lines.extend(
            [
                "",
                "### Desktop handoff summary",
                f"- **Tc proxy (K)**: {tc if tc is not None else '—'}",
                f"- **λ**: {eph.lambda_total if eph else '—'}",
                f"- **result quality**: `{rq}`",
                f"- **Si-feasibility**: {si.total if si else '—'} "
                f"(v{si.version if si else '—'})",
                f"- **status**: {ev.status}",
                f"- **strain**: {ev.candidate.in_plane_strain}",
                f"- **substrate**: {ev.candidate.substrate or '—'}",
            ]
        )
        if rq in {"screening_suspect", "unreliable"}:
            lines.append(
                f"- **do not cite Tc/λ as production** ({rq}; "
                f"{getattr(ev, 'quality_notes', '') or 'see quality flags'})"
            )
        if si and si.notes:
            lines.append(f"- **Si notes**: {si.notes}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def _fmt_weights(weights: dict[str, float] | None) -> str:
    if not weights:
        return "—"
    parts = [f"{k}={v:g}" for k, v in weights.items()]
    return ", ".join(parts)


def _card_markdown(ev: CandidateEvaluation) -> list[str]:
    c = ev.candidate
    si = ev.si_feasibility
    ph = ev.phonon
    scf = ev.scf
    rank = ev.rank if ev.rank is not None else "—"
    lines = [
        f"## #{rank} — {c.formula}",
        "",
        f"- **candidate_id**: `{c.candidate_id}`",
        f"- **family**: {c.material_family}",
        f"- **composition**: {_fmt_composition(c.composition) or c.formula}",
        f"- **substrate**: {c.substrate or '—'}",
        f"- **in-plane strain**: "
        f"{c.in_plane_strain if c.in_plane_strain is not None else '—'}",
        f"- **status**: {ev.status} (`{ev.calculator_name or 'n/a'}`)",
        f"- **composite score**: {ev.composite_score}",
        f"- **performance score**: {ev.performance_score}",
        f"- **performance source**: {getattr(ev, 'performance_score_source', None) or '—'}",
        f"- **result quality**: `{getattr(ev, 'result_quality', 'unknown')}`",
    ]
    flags = getattr(ev, "quality_flags", None) or []
    if flags:
        lines.append(f"- **quality flags**: {', '.join(flags)}")
    qnotes = getattr(ev, "quality_notes", None) or ""
    if qnotes:
        lines.append(f"- **quality notes**: {qnotes}")
    rq = getattr(ev, "result_quality", "unknown")
    if rq in {"screening_suspect", "unreliable"}:
        lines.append(
            f"- **⚠ trust**: Tc/λ are **{rq}** — do **not** quote as production "
            f"predictions; refine with denser grids / tuned Wannier before citing."
        )
    if c.energy_above_hull_proxy is not None:
        lines.append(f"- **E_hull proxy (eV/atom)**: {c.energy_above_hull_proxy}")

    surr = getattr(ev, "tc_lambda_surrogate", None)
    if surr:
        surr_title = "λ/Tc surrogate"
        if surr.get("bootstrap") or surr.get("quality_tag") in {"stub", "trained"}:
            if surr.get("bootstrap", True):
                surr_title += " (prioritization aid — not experimental Tc)"
        lines.extend(
            [
                "",
                f"### {surr_title}",
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
                "### Active learning (prioritization)",
                f"- acquisition score: {acq}",
                f"- selected for expensive path: "
                f"{getattr(ev, 'al_selected_for_expensive', None)}",
                "- note: prioritization aid — not a measured Tc; real EPW overrides "
                "when present",
            ]
        )

    if si is not None:
        w = getattr(si, "weights", None) or {}
        lines.extend(
            [
                "",
                "### Silicon feasibility",
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
                f"- recommended buffers: {', '.join(si.recommended_buffers) or '—'}",
                f"- notes: {si.notes or '—'}",
            ]
        )

    if ph is not None:
        lines.extend(
            [
                "",
                "### Phonon summary",
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
                "### Electron-phonon / Tc",
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
                "### SCF summary",
                f"- total energy (eV): {scf.total_energy_eV}",
                f"- metallic: {scf.is_metallic}",
                f"- status / quality: {scf.status} / {scf.quality_tag}",
            ]
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
    """Write the standard Phase-0 export set; return map of label → path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = formats or ["json", "csv"]
    written: dict[str, Path] = {}

    written["evaluations_json"] = write_evaluations_json(
        evaluations, out_dir / "evaluations.json"
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
