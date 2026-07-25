"""Build desktop EPW shortlist campaigns from an AL / dry-run store.

Workflow
--------
1. ``siscforge run --dry-run examples/nbti_n_al_broad.yaml``
2. ``siscforge shortlist outputs/nbti_n_al_broad -o examples/…_shortlist.yaml``
3. ``siscforge run --calculator qe-epw examples/…_shortlist.yaml``
   (resume/skip finished ok; continue on failure)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import (
    CampaignConfig,
    CandidateSpec,
    DFTConfig,
    EPWConfig,
    RunConfig,
)
from siscforge.store import EvaluationStore

SelectMode = Literal["al_selected", "top_acquisition", "top_rank"]


def load_evaluations_from_store(store_dir: str | Path) -> list[CandidateEvaluation]:
    """Load evaluations from a campaign output directory."""
    store = EvaluationStore(store_dir)
    evals = store.load_evaluations(ranked=False)
    if not evals:
        evals = store.load_evaluations(ranked=True)
    return evals


def select_shortlist_evaluations(
    evaluations: list[CandidateEvaluation],
    *,
    mode: SelectMode = "al_selected",
    max_jobs: int = 6,
) -> list[CandidateEvaluation]:
    """Pick evaluations for the expensive EPW shortlist.

    Modes
    -----
    al_selected
        Prefer ``al_selected_for_expensive is True``; fall back to top acquisition.
    top_acquisition
        Highest ``acquisition_score`` first.
    top_rank
        Lowest rank number (1 = best) first; unranked last.
    """
    if max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")

    if mode == "al_selected":
        selected = [e for e in evaluations if e.al_selected_for_expensive is True]
        if selected:
            selected = sorted(
                selected,
                key=lambda e: (-(e.acquisition_score or 0.0), e.candidate.formula),
            )
            return selected[:max_jobs]
        mode = "top_acquisition"

    if mode == "top_acquisition":
        ranked = sorted(
            evaluations,
            key=lambda e: (
                -(e.acquisition_score if e.acquisition_score is not None else -1.0),
                e.candidate.formula,
            ),
        )
        return ranked[:max_jobs]

    # top_rank
    ranked = sorted(
        evaluations,
        key=lambda e: (
            e.rank if e.rank is not None else 10**9,
            e.candidate.formula,
        ),
    )
    return ranked[:max_jobs]


def evaluation_to_spec(ev: CandidateEvaluation) -> CandidateSpec:
    """Convert a store evaluation into a shortlist ``CandidateSpec``."""
    c = ev.candidate
    meta = dict(c.metadata or {})
    meta["source_candidate_id"] = c.candidate_id
    if ev.acquisition_score is not None:
        meta["source_acquisition_score"] = ev.acquisition_score
    if ev.si_feasibility is not None:
        meta["source_si_total"] = ev.si_feasibility.total
        meta["source_si_notes"] = ev.si_feasibility.notes
    return CandidateSpec(
        formula=c.formula,
        in_plane_strain=float(c.in_plane_strain or 0.0),
        substrate=c.substrate or "Si(001)",
        material_family=c.material_family,  # type: ignore[arg-type]
        candidate_id=c.candidate_id,
        structure_cif=c.structure_cif,
        metadata=meta,
    )


def default_screening_dft(
    *,
    pseudo_dir: str | None = None,
    nproc: int = 4,
) -> DFTConfig:
    """Workstation screening DFT/EPW knobs (NbN-like ternaries)."""
    return DFTConfig(
        engine="qe-epw",
        ecutwfc=60.0,
        ecutrho=480.0,
        # Coarser SCF k for multi-atom ternary supercells (workstation)
        kpoints=[4, 4, 4],
        qpoints=[2, 2, 2],
        do_relax=True,
        do_phonon=True,
        do_epw=True,
        phonon_method="dfpt",
        # Ternary 2×2×1 supercells need far more bands than 2-atom NbN (28).
        # "too few bands" from pw.x if this is left at binary defaults.
        nbnd=64,
        tr2_ph=1.0e-10,
        ph_alpha_mix=0.1,
        ph_nmix=12,
        ph_niter=150,
        degauss=0.02,
        quality_tag="screening",
        nproc=nproc,
        pseudo_dir=pseudo_dir or "/usr/share/espresso/pseudo",
        epw=EPWConfig(
            enabled=True,
            nkf=[6, 6, 6],
            nqf=[6, 6, 6],
            nkc=[4, 4, 4],
            nqc=[2, 2, 2],
            nbndsub=10,
            mu_star=0.10,
            eliashberg=True,
            allen_dynes_fallback=True,
            fsthick=0.6,
            degaussw=0.1,
            degaussq=0.05,
            eps_acustic=15.0,
        ),
    )


def build_shortlist_campaign(
    evaluations: list[CandidateEvaluation],
    *,
    name: str = "nitride_epw_shortlist",
    source_store: str | None = None,
    max_jobs: int = 6,
    mode: SelectMode = "al_selected",
    output_dir: str | None = None,
    pseudo_dir: str | None = None,
    nproc: int = 4,
    dry_run: bool = False,
    calculator: str = "qe-epw",
) -> tuple[CampaignConfig, list[CandidateEvaluation]]:
    """Build a focused campaign for real (or mock) EPW on the shortlist only."""
    chosen = select_shortlist_evaluations(evaluations, mode=mode, max_jobs=max_jobs)
    if not chosen:
        raise ValueError("No evaluations available to shortlist")

    specs = [evaluation_to_spec(e) for e in chosen]
    out = output_dir or f"outputs/{name}"
    calc_name = "mock" if dry_run else calculator

    desc_parts = [
        f"Desktop EPW shortlist ({len(specs)} candidates)",
        f"selection={mode}",
    ]
    if source_store:
        desc_parts.append(f"from {source_store}")

    cfg = CampaignConfig(
        name=name,
        description="; ".join(desc_parts),
        version="0.2",
        dry_run=dry_run,
        enumeration={
            "material_families": ["tm_nitride"],
            "candidate_specs": [s.model_dump(mode="json") for s in specs],
            "max_candidates": len(specs),
            "epitaxy_orientation": "auto",
            "use_buffers": True,
            "seed": 42,
            "supercell": [2, 2, 1],
        },
        calculators=[{"name": calc_name}],
        formation_filter={"enabled": False},
        surrogate={"tc_lambda": {"enabled": False}},
        active_learning={"enabled": False},
        dft=default_screening_dft(pseudo_dir=pseudo_dir, nproc=nproc),
        ranking={
            "performance_weight": 0.55,
            "si_feasibility_weight": 0.45,
            "prefer_dynamically_stable": True,
            "prefer_low_hull": True,
        },
        run=RunConfig(resume=True, continue_on_error=True, force_rerun=False),
        output_dir=out,
        export_formats=["json", "csv", "markdown"],
        extras={
            "shortlist": {
                "source_store": source_store,
                "mode": mode,
                "n_selected": len(specs),
                "formulas": [s.formula for s in specs],
            }
        },
    )
    return cfg, chosen


def write_campaign_yaml(config: CampaignConfig, path: str | Path) -> Path:
    """Write campaign YAML (CIF bodies may be large — expected for shortlists)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    with path.open("w", encoding="utf-8") as fh:
        fh.write(
            "# Auto-generated shortlist campaign — edit dft.pseudo_dir / nproc as needed\n"
            "# Run: siscforge run --calculator qe-epw " + path.name + "\n"
            "# Resume: re-run the same command (skips finished ok; continues on fail)\n"
            "# Dry-run: siscforge run --dry-run " + path.name + "\n\n"
        )
        yaml.safe_dump(
            data,
            fh,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )
    return path


def shortlist_summary_table(chosen: list[CandidateEvaluation]) -> list[dict[str, Any]]:
    """Flat rows for CLI / docs."""
    rows: list[dict[str, Any]] = []
    for i, ev in enumerate(chosen, start=1):
        c = ev.candidate
        si = ev.si_feasibility
        rows.append(
            {
                "#": i,
                "formula": c.formula,
                "strain": c.in_plane_strain,
                "si": si.total if si else None,
                "si_notes": (si.notes[:80] + "…") if si and si.notes and len(si.notes) > 80 else (si.notes if si else None),
                "acq": ev.acquisition_score,
                "status": ev.status,
                "candidate_id": c.candidate_id[:8],
            }
        )
    return rows
