"""JSON / CSV export helpers (Phase 0 stubs)."""

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


def write_evaluations_csv(
    evaluations: Iterable[CandidateEvaluation],
    path: str | Path,
) -> Path:
    """Write a flat CSV summary of ranked evaluations.

    Only a subset of columns is exported for readability; full detail lives
    in the JSON export.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "rank",
        "candidate_id",
        "formula",
        "material_family",
        "performance_score",
        "si_feasibility_total",
        "composite_score",
        "dynamically_stable",
        "energy_above_hull_eV_per_atom",
        "status",
        "calculator_name",
    ]

    rows: list[dict] = []
    for ev in evaluations:
        si_total = ev.si_feasibility.total if ev.si_feasibility else None
        stable = None
        if ev.phonon is not None:
            stable = ev.phonon.dynamically_stable
        hull = None
        if ev.scf is not None:
            hull = ev.scf.energy_above_hull_eV_per_atom
        rows.append(
            {
                "rank": ev.rank,
                "candidate_id": ev.candidate.candidate_id,
                "formula": ev.candidate.formula,
                "material_family": ev.candidate.material_family,
                "performance_score": ev.performance_score,
                "si_feasibility_total": si_total,
                "composite_score": ev.composite_score,
                "dynamically_stable": stable,
                "energy_above_hull_eV_per_atom": hull,
                "status": ev.status,
                "calculator_name": ev.calculator_name,
            }
        )

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path
