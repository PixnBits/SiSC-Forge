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
        "si_feasibility_total": si.total if si else None,
        "si_lattice_mismatch": comps.lattice_mismatch if comps else None,
        "si_thermal_budget": comps.thermal_budget if comps else None,
        "si_chemical": comps.chemical_compatibility if comps else None,
        "si_buffer": comps.buffer_availability if comps else None,
        "si_process_maturity": comps.process_maturity if comps else None,
        "composite_score": ev.composite_score,
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
    "si_feasibility_total",
    "si_lattice_mismatch",
    "si_thermal_budget",
    "si_chemical",
    "si_buffer",
    "si_process_maturity",
    "composite_score",
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
    for ev in evaluations:
        lines.extend(_card_markdown(ev))
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


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
    ]
    if c.energy_above_hull_proxy is not None:
        lines.append(f"- **E_hull proxy (eV/atom)**: {c.energy_above_hull_proxy}")

    if si is not None:
        lines.extend(
            [
                "",
                "### Silicon feasibility",
                f"- **total**: {si.total:.1f} / 100 (v{si.version})",
                f"- lattice mismatch: {si.components.lattice_mismatch:.1f}",
                f"- thermal budget: {si.components.thermal_budget:.1f}",
                f"- chemical compatibility: {si.components.chemical_compatibility:.1f}",
                f"- buffer availability: {si.components.buffer_availability:.1f}",
                f"- process maturity: {si.components.process_maturity:.1f}",
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
        )
    if candidates is not None:
        written["candidates"] = write_candidates_json(
            candidates, out_dir / "candidates.json"
        )
    return written
