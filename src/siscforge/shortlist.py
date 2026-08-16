"""Build desktop EPW shortlist campaigns from an AL / phonon / dry-run store.

Workflows
---------
1. AL dry-run → shortlist EPW::

       siscforge run --dry-run examples/nbti_n_al_broad.yaml
       siscforge shortlist outputs/nbti_n_al_broad -o examples/…_shortlist.yaml
       siscforge run --calculator qe-epw examples/…_shortlist.yaml

2. Phonon-first stability map → EPW only on survivors (two-machine loop)::

       siscforge run --calculator qe examples/nbti_n_phonon_map.yaml
       siscforge shortlist outputs/nbti_n_phonon_map -o examples/…_epw.yaml \\
         --mode stable_only
       siscforge run --calculator qe-epw examples/…_epw.yaml
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
    QualityConfig,
    RunConfig,
)
from siscforge.soft_modes import classify_soft_mode, needs_denser_q_before_epw
from siscforge.store import EvaluationStore

SelectMode = Literal[
    "al_selected",
    "top_acquisition",
    "top_rank",
    "stable_only",
    "stable_or_soft",
]

_OK_STATUS = frozenset({"ok", "mock"})


def load_evaluations_from_store(store_dir: str | Path) -> list[CandidateEvaluation]:
    """Load evaluations from a campaign output directory."""
    store = EvaluationStore(store_dir)
    evals = store.load_evaluations(ranked=False)
    if not evals:
        evals = store.load_evaluations(ranked=True)
    return evals


def _has_ok_status(ev: CandidateEvaluation) -> bool:
    return (ev.status or "").lower() in _OK_STATUS


def _phonon_min_freq(ev: CandidateEvaluation) -> float | None:
    if ev.phonon is None:
        return None
    return ev.phonon.min_frequency_cm1


def is_dynamically_stable(ev: CandidateEvaluation) -> bool:
    """True when phonon reports dynamical stability (no imaginary modes).

    Setup failures (``phq_setup`` / empty modes / status failed) are **not**
    stable — only completed phonon with no imaginary modes qualifies.
    """
    ph = ev.phonon
    if ph is None:
        return False
    if ph.status not in {"ok", "mock"}:
        return False
    if ph.n_modes is None or int(ph.n_modes or 0) <= 0:
        # No modes parsed → incomplete phonon, not a stability conclusion
        if ph.min_frequency_cm1 is None:
            return False
    if ph.has_imaginary_modes:
        return False
    if ph.dynamically_stable is False:
        return False
    return True


def is_stable_or_soft(
    ev: CandidateEvaluation,
    *,
    soft_min_cm1: float = 0.0,
) -> bool:
    """Nearly stable: no hard imaginary modes below *soft_min_cm1*.

    Allows soft (low but non-imaginary) modes. When ``min_frequency_cm1`` is
    missing, falls back to :func:`is_dynamically_stable`.
    """
    ph = ev.phonon
    if ph is None:
        return False
    min_f = ph.min_frequency_cm1
    if min_f is not None:
        return float(min_f) >= float(soft_min_cm1)
    # No frequency number — require clean stability flags
    return is_dynamically_stable(ev)


def filter_stable_evaluations(
    evaluations: list[CandidateEvaluation],
    *,
    mode: Literal["stable_only", "stable_or_soft"] = "stable_only",
    soft_min_cm1: float = 0.0,
    require_ok: bool = True,
) -> list[CandidateEvaluation]:
    """Filter evaluations by dynamical stability.

    Parameters
    ----------
    mode
        ``stable_only`` — ``phonon.dynamically_stable`` and no imaginary modes.
        ``stable_or_soft`` — min frequency ≥ *soft_min_cm1* (default 0 cm⁻¹).
    soft_min_cm1
        Floor on min phonon frequency for ``stable_or_soft`` (cm⁻¹). Slightly
        negative values tolerate acoustic numerical noise.
    require_ok
        Require evaluation status in {ok, mock}.
    """
    out: list[CandidateEvaluation] = []
    for ev in evaluations:
        if require_ok and not _has_ok_status(ev):
            continue
        if mode == "stable_only":
            if is_dynamically_stable(ev) and not needs_denser_q_before_epw(ev):
                out.append(ev)
        elif mode == "stable_or_soft":
            if is_stable_or_soft(ev, soft_min_cm1=soft_min_cm1) and not needs_denser_q_before_epw(
                ev
            ):
                out.append(ev)
        else:
            raise ValueError(f"Unknown stability filter mode: {mode!r}")
    return out


def _sort_stable_pool(
    pool: list[CandidateEvaluation],
    *,
    sort_by: Literal["si", "rank"] = "si",
) -> list[CandidateEvaluation]:
    """Prefer highest Si-feasibility among stable survivors (default)."""
    if sort_by == "rank":
        return sorted(
            pool,
            key=lambda e: (
                e.rank if e.rank is not None else 10**9,
                -(e.si_feasibility.total if e.si_feasibility else -1.0),
                e.candidate.formula,
            ),
        )
    # si (default)
    return sorted(
        pool,
        key=lambda e: (
            -(e.si_feasibility.total if e.si_feasibility else -1.0),
            e.rank if e.rank is not None else 10**9,
            e.candidate.formula,
        ),
    )


def select_shortlist_evaluations(
    evaluations: list[CandidateEvaluation],
    *,
    mode: SelectMode = "al_selected",
    max_jobs: int = 6,
    soft_min_cm1: float = 0.0,
    stable_sort: Literal["si", "rank"] = "si",
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
    stable_only
        Only evaluations with ``phonon.dynamically_stable`` (and status ok/mock).
        Sorted by Si-feasibility (or rank if ``stable_sort=rank``).
        **Raises** if none qualify — does not fall back to unstable top-k.
    stable_or_soft
        Like stable_only but allows soft modes with
        ``min_frequency_cm1 >= soft_min_cm1`` (default 0). Still fails clearly
        if the pool is empty.
    """
    if max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")

    if mode in {"stable_only", "stable_or_soft"}:
        pool = filter_stable_evaluations(
            evaluations,
            mode=mode,  # type: ignore[arg-type]
            soft_min_cm1=soft_min_cm1,
            require_ok=True,
        )
        if not pool:
            n_total = len(evaluations)
            n_with_ph = sum(1 for e in evaluations if e.phonon is not None)
            n_imag = sum(
                1
                for e in evaluations
                if e.phonon is not None
                and (e.phonon.has_imaginary_modes or not e.phonon.dynamically_stable)
            )
            if mode == "stable_only":
                raise ValueError(
                    f"No dynamically stable evaluations for shortlist "
                    f"(mode=stable_only). Store has {n_total} evaluations, "
                    f"{n_with_ph} with phonon data, {n_imag} with imaginary/"
                    f"unstable modes. Expand the phonon map, relax strain, or "
                    f"try --mode stable_or_soft (soft_min_cm1={soft_min_cm1:g}). "
                    f"Refusing to fall back to unstable top-k. "
                    f"Next: inspect the campaign soft-mode report "
                    f"(soft_mode_report.json / soft_mode_report.md) and emit a "
                    f"denser-q phonon pilot with `siscforge pilot <store> -o "
                    f"<pilot.yaml> --mode binaries` (or --mode least_soft). "
                    f"Do not launch EPW on imaginary-mode cells."
                )
            raise ValueError(
                f"No stable/soft evaluations for shortlist "
                f"(mode=stable_or_soft, soft_min_cm1={soft_min_cm1:g}). "
                f"Store has {n_total} evaluations, {n_with_ph} with phonon, "
                f"{n_imag} imag/unstable. Lower soft_min_cm1 only for numeric "
                f"noise; do not shortlist hard imaginary cells for EPW. "
                f"Refusing to fall back to unstable top-k."
            )
        ranked = _sort_stable_pool(pool, sort_by=stable_sort)
        return ranked[:max_jobs]

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

    if mode == "top_rank":
        ranked = sorted(
            evaluations,
            key=lambda e: (
                e.rank if e.rank is not None else 10**9,
                e.candidate.formula,
            ),
        )
        return ranked[:max_jobs]

    raise ValueError(
        f"Unknown shortlist mode: {mode!r}. "
        f"Use al_selected | top_acquisition | top_rank | stable_only | stable_or_soft"
    )


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
    if ev.phonon is not None:
        meta["source_dynamically_stable"] = ev.phonon.dynamically_stable
        meta["source_min_frequency_cm1"] = ev.phonon.min_frequency_cm1
        meta["source_has_imaginary_modes"] = ev.phonon.has_imaginary_modes
        row = classify_soft_mode(ev)
        meta["soft_mode_class"] = row["soft_mode_class"]
        meta["known_stable_binary"] = row["is_known_stable_binary"]
    if ev.result_quality is not None:
        meta["source_result_quality"] = ev.result_quality
    if ev.quality_flags:
        meta["source_quality_flags"] = list(ev.quality_flags)
        if "epw_remediation_exhausted" in ev.quality_flags:
            meta["epw_reuse_blocked"] = True
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
            # None → auto_nbndsub from nbnd/cell (not a tiny fixed 10)
            nbndsub=None,
            auto_nbndsub=True,
            wannier_retry_on_froz_overflow=True,
            mu_star=0.10,
            eliashberg=True,
            allen_dynes_fallback=True,
            fsthick=0.6,
            degaussw=0.1,
            degaussq=0.05,
            eps_acustic=15.0,
            # Fine-grid EPW: npool must equal nproc (nimage=1)
            npool=max(1, int(nproc)),
        ),
    )


def build_shortlist_campaign(
    evaluations: list[CandidateEvaluation],
    *,
    name: str = "nitride_epw_shortlist",
    source_store: str | None = None,
    max_jobs: int = 6,
    mode: SelectMode = "al_selected",
    soft_min_cm1: float = 0.0,
    stable_sort: Literal["si", "rank"] = "si",
    output_dir: str | None = None,
    pseudo_dir: str | None = None,
    nproc: int = 4,
    dry_run: bool = False,
    calculator: str = "qe-epw",
) -> tuple[CampaignConfig, list[CandidateEvaluation]]:
    """Build a focused campaign for real (or mock) EPW on the shortlist only."""
    chosen = select_shortlist_evaluations(
        evaluations,
        mode=mode,
        max_jobs=max_jobs,
        soft_min_cm1=soft_min_cm1,
        stable_sort=stable_sort,
    )
    if not chosen:
        raise ValueError("No evaluations available to shortlist")

    specs = [evaluation_to_spec(e) for e in chosen]
    out = output_dir or f"outputs/{name}"
    calc_name = "mock" if dry_run else calculator

    desc_parts = [
        f"Desktop EPW shortlist ({len(specs)} candidates)",
        f"selection={mode}",
    ]
    if mode in {"stable_only", "stable_or_soft"}:
        desc_parts.append(
            f"stability-gated (soft_min_cm1={soft_min_cm1:g}, sort={stable_sort})"
        )
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
                "soft_min_cm1": soft_min_cm1 if mode in {"stable_only", "stable_or_soft"} else None,
                "stable_sort": stable_sort if mode in {"stable_only", "stable_or_soft"} else None,
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
        ph = ev.phonon
        if ph is None:
            stable_s = "—"
        elif ph.dynamically_stable and not ph.has_imaginary_modes:
            stable_s = "yes"
        else:
            stable_s = "NO"
        min_f = ph.min_frequency_cm1 if ph is not None else None
        rows.append(
            {
                "#": i,
                "formula": c.formula,
                "strain": c.in_plane_strain,
                "si": si.total if si else None,
                "si_notes": (
                    (si.notes[:80] + "…")
                    if si and si.notes and len(si.notes) > 80
                    else (si.notes if si else None)
                ),
                "acq": ev.acquisition_score,
                "stable": stable_s,
                "min_freq": min_f,
                "status": ev.status,
                "candidate_id": c.candidate_id[:8],
            }
        )
    return rows


# Re-export for callers that want soft-mode defaults
DEFAULT_SOFT_MIN_CM1 = 0.0
DEFAULT_QUALITY_SOFT_CM1 = QualityConfig().min_frequency_cm1_soft
