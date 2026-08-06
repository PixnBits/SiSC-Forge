"""Promote shortlist / store winners to a denser-grid EPW refine campaign.

Workflow
--------
1. Screening shortlist EPW finishes (often ``result_quality=unreliable``).
2. ``siscforge refine outputs/…_shortlist -o examples/…_refine.yaml --mode top_si``
3. ``siscforge run --calculator qe-epw examples/…_refine.yaml``

Uses the same ``candidate_specs`` schema as shortlist (formula × strain × CIF).
Does **not** re-enumerate the full composition grid. Trust layer re-assesses
after refine EPW — quality is not faked cleaner.

**Slice 25:** refine tiers emit Wannier-safe coarse k (≥8³ for supercells).
EPW k-mesh failures after DFPT retry EPW-only — never redo finished phonon.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from siscforge.calculators.qe.epw_inputs import (
    ensure_wannier_safe_nkc,
    recommended_grids,
)
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import (
    CampaignConfig,
    DFTConfig,
    EPWConfig,
    RunConfig,
)
from siscforge.quality import apply_quality_assessment
from siscforge.ranking import rank_evaluations
from siscforge.shortlist import (
    evaluation_to_spec,
    load_evaluations_from_store,
    write_campaign_yaml,
)

RefineMode = Literal["top_si", "top_rank", "ids"]
RefineTier = Literal["workstation_dense", "production"]

# Walltime guidance (desktop, order-of-magnitude, 8-atom nitride supercell)
_TIER_WALLTIME: dict[str, str] = {
    "workstation_dense": (
        "workstation_dense: often multi-hour DFPT per candidate on 8–16 cores; "
        "plan overnight for 2 jobs"
    ),
    "production": (
        "production: denser q/k meshes — expect longer walltime than "
        "workstation_dense; high-end desktop or small cluster"
    ),
}


def filter_refine_pool(
    evaluations: list[CandidateEvaluation],
    *,
    require_epw: bool = True,
    require_ok: bool = True,
) -> list[CandidateEvaluation]:
    """Keep rows suitable for refine (successful EPW by default)."""
    out: list[CandidateEvaluation] = []
    for ev in evaluations:
        if require_ok and ev.status not in {"ok", "mock"}:
            # Allow mock only for dry-run fixtures; real refine wants ok
            if require_epw and ev.status != "ok":
                continue
        if require_epw:
            eph = ev.electron_phonon
            if eph is None or eph.lambda_total is None:
                continue
            if eph.status not in {"ok", "mock"}:
                continue
        out.append(ev)
    return out


def select_refine_evaluations(
    evaluations: list[CandidateEvaluation],
    *,
    mode: RefineMode = "top_si",
    max_jobs: int = 2,
    candidate_ids: list[str] | None = None,
    require_epw: bool = True,
) -> list[CandidateEvaluation]:
    """Select candidates to promote to denser-grid EPW.

    Modes
    -----
    top_si
        Highest Si-feasibility total among successful EPW rows (default —
        trust-weighted ranking is Si-dominated after penalties).
    top_rank
        Trust-weighted rank order (re-assess quality then rank).
    ids
        Explicit candidate_id list (order preserved; missing IDs skipped).
    """
    if max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")

    if mode == "ids":
        if not candidate_ids:
            raise ValueError("mode=ids requires --id / candidate_ids")
        by_id = {e.candidate.candidate_id: e for e in evaluations}
        # Also allow prefix match (8-char CLI short ids)
        chosen: list[CandidateEvaluation] = []
        for cid in candidate_ids:
            if cid in by_id:
                chosen.append(by_id[cid])
                continue
            matches = [
                e
                for e in evaluations
                if e.candidate.candidate_id.startswith(cid)
                or e.candidate.candidate_id.replace("-", "").startswith(
                    cid.replace("-", "")
                )
            ]
            if matches:
                chosen.append(matches[0])
        if not chosen:
            raise ValueError(f"No evaluations matched ids={candidate_ids!r}")
        return chosen[:max_jobs]

    pool = filter_refine_pool(
        evaluations, require_epw=require_epw, require_ok=require_epw
    )
    if not pool:
        # Fall back to any ok evaluations without EPW filter for dry fixtures
        pool = filter_refine_pool(
            evaluations, require_epw=False, require_ok=False
        )
    if not pool:
        raise ValueError("No evaluations available to refine")

    if mode == "top_si":
        ranked = sorted(
            pool,
            key=lambda e: (
                -(e.si_feasibility.total if e.si_feasibility else -1.0),
                e.rank if e.rank is not None else 10**9,
                e.candidate.formula,
            ),
        )
        return ranked[:max_jobs]

    if mode == "top_rank":
        # Re-apply trust ranking so order matches CLI rank table
        ranked = rank_evaluations(pool)
        return ranked[:max_jobs]

    raise ValueError(f"Unknown refine mode: {mode!r}")


def default_refine_dft(
    *,
    tier: RefineTier = "workstation_dense",
    family: Literal["tm_nitride", "mgb2_boride", "generic"] = "tm_nitride",
    pseudo_dir: str | None = None,
    nproc: int = 16,
    n_atoms: int = 8,
) -> DFTConfig:
    """Denser DFT/EPW knobs for refine campaigns (not screening shortlist).

    Coarse electronic k is Wannier-safe: ≥8³ for ≥8-atom cells on dense tiers
    (never 6³ — ``kmesh_get_bvector`` trap after multi-day DFPT).
    """
    grids = recommended_grids(family, tier)
    epw_g = dict(grids.get("epw") or {})
    # workstation_dense still uses production quality_tag label for honesty
    # (recommended_grids already sets quality_tag production for dense tiers)

    nproc = max(1, int(nproc))
    nqc = list(epw_g.get("nqc") or grids.get("qpoints") or [4, 4, 4])
    nqc = (list(nqc) + [4, 4, 4])[:3]
    kpts = list(grids.get("kpoints") or [8, 8, 8])
    qpts = list(grids.get("qpoints") or nqc)

    # Supercell nitrides still need empty bands
    nbnd = 72 if tier == "production" else 64

    nkc_raw = list(epw_g.get("nkc") or [8, 8, 8])[:3]
    nkc, raise_msg = ensure_wannier_safe_nkc(
        nkc_raw,
        quality_tag="production",
        n_atoms=n_atoms,
        tier=tier,
        auto_raise=True,
    )
    # Default fallback was historically 6³ — force floor even if grids lag
    if max(nkc) < 8 and n_atoms >= 8:
        nkc = [8, 8, 8]

    return DFTConfig(
        engine="qe-epw",
        ecutwfc=60.0 if tier == "workstation_dense" else 70.0,
        ecutrho=480.0 if tier == "workstation_dense" else 560.0,
        kpoints=kpts[:3],
        qpoints=qpts[:3],
        do_relax=True,
        do_phonon=True,
        do_epw=True,
        phonon_method="dfpt",
        nbnd=nbnd,
        tr2_ph=1.0e-12 if tier == "production" else 1.0e-11,
        ph_alpha_mix=0.1,
        ph_nmix=12,
        ph_niter=150,
        degauss=0.02,
        quality_tag="production",  # type: ignore[arg-type]
        nproc=nproc,
        pseudo_dir=pseudo_dir or "/usr/share/espresso/pseudo",
        epw=EPWConfig(
            enabled=True,
            nkf=list(epw_g.get("nkf") or [12, 12, 12])[:3],
            nqf=list(epw_g.get("nqf") or [12, 12, 12])[:3],
            nkc=list(nkc)[:3],
            nqc=nqc,
            nbndsub=None,
            auto_nbndsub=True,
            wannier_retry_on_froz_overflow=True,
            auto_retry_kmesh=True,
            max_kmesh_retries=2,
            strict_coarse_k=False,
            mu_star=0.10,
            eliashberg=True,
            allen_dynes_fallback=True,
            fsthick=float(epw_g.get("fsthick") or 0.4),
            degaussw=float(epw_g.get("degaussw") or 0.05),
            degaussq=0.05,
            eps_acustic=float(epw_g.get("eps_acustic") or 5.0),
            npool=nproc,
            strict_parallel=False,
        ),
    )


def evaluation_to_refine_spec(ev: CandidateEvaluation) -> Any:
    """CandidateSpec with refine provenance + instability warnings."""
    spec = evaluation_to_spec(ev)
    meta = dict(spec.metadata or {})
    meta["refine_source"] = True
    if ev.result_quality:
        meta["prior_result_quality"] = ev.result_quality
    if ev.quality_flags:
        meta["prior_quality_flags"] = list(ev.quality_flags)
    if ev.electron_phonon is not None:
        meta["prior_lambda"] = ev.electron_phonon.lambda_total
        meta["prior_Tc"] = ev.electron_phonon.best_tc_K()
    if ev.phonon is not None:
        meta["prior_dynamically_stable"] = ev.phonon.dynamically_stable
        meta["prior_min_frequency_cm1"] = ev.phonon.min_frequency_cm1
        if ev.phonon.has_imaginary_modes or not ev.phonon.dynamically_stable:
            meta["refine_warning"] = (
                "Screening phonon was dynamically unstable / imaginary modes; "
                "refine re-runs vc-relax + DFPT — may still need different strain "
                "or denser settings if soft modes persist."
            )
    return spec.model_copy(update={"metadata": meta})


def build_refine_campaign(
    evaluations: list[CandidateEvaluation],
    *,
    name: str = "nitride_epw_refine",
    source_store: str | None = None,
    max_jobs: int = 2,
    mode: RefineMode = "top_si",
    tier: RefineTier = "workstation_dense",
    candidate_ids: list[str] | None = None,
    output_dir: str | None = None,
    pseudo_dir: str | None = None,
    nproc: int = 16,
    dry_run: bool = False,
    calculator: str = "qe-epw",
    family: Literal["tm_nitride", "mgb2_boride", "generic"] = "tm_nitride",
) -> tuple[CampaignConfig, list[CandidateEvaluation]]:
    """Build a denser-grid refine campaign from store evaluations."""
    # Ensure quality fields present for top_si / messaging
    evals = [apply_quality_assessment(e) for e in evaluations]
    chosen = select_refine_evaluations(
        evals,
        mode=mode,
        max_jobs=max_jobs,
        candidate_ids=candidate_ids,
        require_epw=True,
    )
    if not chosen:
        raise ValueError("No candidates selected for refine")

    specs = [evaluation_to_refine_spec(e) for e in chosen]
    out = output_dir or f"outputs/{name}"
    calc_name = "mock" if dry_run else calculator
    # Infer typical supercell size from first CIF when possible
    n_atoms = 8
    for s in specs:
        if s.structure_cif:
            try:
                from pymatgen.core import Structure

                n_atoms = max(n_atoms, len(Structure.from_str(s.structure_cif, fmt="cif")))
            except Exception:  # noqa: BLE001
                pass
    dft = default_refine_dft(
        tier=tier,
        family=family,
        pseudo_dir=pseudo_dir,
        nproc=nproc,
        n_atoms=n_atoms,
    )

    desc = (
        f"Refine-tier EPW ({tier}, {len(specs)} candidates); "
        f"selection={mode}; denser grids than screening shortlist. "
        f"Do not cite Tc until trust flags improve. "
        f"{_TIER_WALLTIME.get(tier, '')}"
    )
    if source_store:
        desc += f" Source store: {source_store}."

    cfg = CampaignConfig(
        name=name,
        description=desc,
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
        dft=dft,
        ranking={
            "performance_weight": 0.55,
            "si_feasibility_weight": 0.45,
            "prefer_dynamically_stable": True,
            "prefer_low_hull": True,
        },
        run=RunConfig(
            resume=True,
            continue_on_error=True,
            force_rerun=False,
            resume_qe_steps=True,
            heartbeat_seconds=900,
        ),
        output_dir=out,
        export_formats=["json", "csv", "markdown"],
        extras={
            "refine": {
                "source_store": source_store,
                "mode": mode,
                "tier": tier,
                "n_selected": len(specs),
                "formulas": [s.formula for s in specs],
                "walltime_note": _TIER_WALLTIME.get(tier, ""),
                "limitation": (
                    "Random Wannier projections may remain until a projection "
                    "library lands; refine improves grids/quality_tag first. "
                    "EPW coarse k auto-bump; DFPT never redone on Wannier "
                    "k-mesh failure. Auto-nk does not guarantee physical λ/Tc."
                ),
            }
        },
    )
    return cfg, chosen


def write_refine_yaml(config: CampaignConfig, path: str | Path) -> Path:
    """Write refine campaign YAML with strong header comments."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    import yaml

    with path.open("w", encoding="utf-8") as fh:
        fh.write(
            "# Auto-generated REFINE campaign — denser grids than screening shortlist\n"
            "# refinement — do not cite Tc until result_quality flags clear/improve\n"
            f"# Tier notes: {_TIER_WALLTIME.get((config.extras or {}).get('refine', {}).get('tier', ''), '')}\n"
            "# EPW coarse k auto-bump; DFPT never redone on Wannier k-mesh failure\n"
            "# Run: siscforge run --calculator qe-epw " + path.name + "\n"
            "# Resume: re-run the same command (campaign + mid-step QE checkpoints)\n"
            "# Dry-run: siscforge run --dry-run " + path.name + "\n"
            "# Limitation: material-specific Wannier projs not yet automated\n\n"
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


def refine_summary_table(chosen: list[CandidateEvaluation]) -> list[dict[str, Any]]:
    """Flat rows for CLI."""
    rows: list[dict[str, Any]] = []
    for i, ev in enumerate(chosen, start=1):
        c = ev.candidate
        si = ev.si_feasibility
        eph = ev.electron_phonon
        ph = ev.phonon
        rows.append(
            {
                "#": i,
                "formula": c.formula,
                "strain": c.in_plane_strain,
                "si": si.total if si else None,
                "prior_qual": getattr(ev, "result_quality", None) or "unknown",
                "prior_λ": eph.lambda_total if eph else None,
                "prior_Tc": eph.best_tc_K() if eph else None,
                "stable": (
                    "yes"
                    if ph is not None and ph.dynamically_stable
                    else ("NO" if ph is not None else "—")
                ),
                "candidate_id": c.candidate_id[:8],
            }
        )
    return rows
