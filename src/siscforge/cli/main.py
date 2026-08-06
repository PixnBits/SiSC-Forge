"""SiSC-Forge CLI entry point (``siscforge``).

Subcommands:
  - ``enumerate`` — generate structure candidates (+ optional filters)
  - ``rank``      — rank evaluations from JSON or a campaign store
  - ``run``       — load campaign, filter, evaluate (mock/QE/EPW), rank, export
  - ``shortlist`` — build a focused EPW campaign from an AL dry-run store
  - ``refine``    — promote store winners to denser-grid / production-tier EPW
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from siscforge import __version__
from siscforge.active_learning import prioritize_candidates
from siscforge.calculators import ensure_builtins_loaded, list_calculators
from siscforge.calculators import get as get_calculator
from siscforge.export import (
    export_campaign_bundle,
    write_candidates_json,
    write_evaluations_csv,
    write_evaluations_json,
    write_synthesis_cards,
)
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import CampaignConfig, RunConfig
from siscforge.models.provenance import Provenance
from siscforge.ranking import rank_evaluations
from siscforge.resume import (
    find_resumable_evaluation,
    is_successful_evaluation,
    resume_fingerprint,
)
from siscforge.silicon.feasibility import score_si_feasibility
from siscforge.store import EvaluationStore
from siscforge.structure.generator import generate_candidates, generate_fake_candidates
from siscforge.surrogates.formation import FormationEnergyFilter
from siscforge.surrogates.tc_lambda import TcLambdaSurrogate

app = typer.Typer(
    name="siscforge",
    help="SiSC-Forge: silicon-compatible superconductor materials discovery.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Re-export for tests that import the old name.
__all__ = ["app", "generate_candidates", "generate_fake_candidates", "run"]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"siscforge {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """SiSC-Forge command-line interface."""
    ensure_builtins_loaded()


# ---------------------------------------------------------------------------
# enumerate
# ---------------------------------------------------------------------------


@app.command("enumerate")
def enumerate_cmd(
    campaign: Path | None = typer.Option(
        None,
        "--campaign",
        "-c",
        help="Campaign YAML to drive structure enumeration.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    n: int | None = typer.Option(
        None,
        "--n",
        "-n",
        help="Optional cap on number of candidates.",
        min=1,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write candidates JSON to this path.",
    ),
    score_si: bool = typer.Option(
        True,
        "--score-si/--no-score-si",
        help="Attach Silicon Feasibility scores (printed as a column).",
    ),
    apply_filter: bool = typer.Option(
        True,
        "--filter/--no-filter",
        help="Apply campaign formation-energy pre-filter when a campaign is set.",
    ),
) -> None:
    """Enumerate structure candidates (nitrides / B:Si + epitaxial strain)."""
    if campaign is not None:
        config = CampaignConfig.from_yaml(campaign)
    else:
        config = CampaignConfig(
            name="ad_hoc_enumerate",
            enumeration={
                "material_families": ["tm_nitride"],
                "metals": ["Nb", "Ti", "Zr", "Hf"],
                "strain_values": [0.0],
                "max_candidates": n or 5,
            },
            formation_filter={"enabled": False},
        )

    candidates = generate_candidates(config, n=n)
    n_raw = len(candidates)

    if apply_filter and campaign is not None:
        filt = FormationEnergyFilter(config.formation_filter)
        fres = filt.filter(candidates)
        candidates = fres.kept
        console.print(
            f"[dim]Formation filter:[/dim] kept {fres.n_kept}/{n_raw} "
            f"(rejected {fres.n_rejected})"
        )
    else:
        candidates = [FormationEnergyFilter().annotate(c) for c in candidates]

    table = Table(title=f"Enumerated candidates ({len(candidates)})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Formula")
    table.add_column("Family")
    table.add_column("Substrate")
    table.add_column("Strain")
    table.add_column("a (Å)", justify="right")
    table.add_column("E_hull*", justify="right")
    if score_si:
        table.add_column("Si-score", justify="right")

    for c in candidates:
        row = [
            c.candidate_id[:8] + "…",
            c.formula,
            c.material_family,
            c.substrate or "—",
            f"{c.in_plane_strain:.3f}" if c.in_plane_strain is not None else "—",
            f"{c.lattice_abc[0]:.3f}" if c.lattice_abc else "—",
            f"{c.energy_above_hull_proxy:.3f}"
            if c.energy_above_hull_proxy is not None
            else "—",
        ]
        if score_si:
            si = score_si_feasibility(c)
            row.append(f"{si.total:.1f}")
        table.add_row(*row)
    console.print(table)
    console.print("[dim]* E_hull is a Phase-0 heuristic proxy (eV/atom), not DFT.[/dim]")

    if output is not None:
        path = write_candidates_json(candidates, output)
        console.print(f"[green]Wrote[/green] {path}")


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


@app.command("rank")
def rank_cmd(
    input_json: Path = typer.Argument(
        ...,
        help="JSON file of CandidateEvaluation objects (list), or a store directory.",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write ranked evaluations JSON.",
    ),
    csv_output: Path | None = typer.Option(
        None,
        "--csv",
        help="Optional CSV summary path.",
    ),
    markdown: Path | None = typer.Option(
        None,
        "--markdown",
        "--md",
        help="Optional Markdown synthesis cards path.",
    ),
    stable_first: bool = typer.Option(
        False,
        "--stable-first",
        help="Sort dynamically stable rows above unstable (phonon-map stores).",
    ),
) -> None:
    """Rank evaluation records from a JSON file or campaign store directory."""
    if input_json.is_dir():
        store = EvaluationStore(input_json)
        evaluations = store.load_evaluations(ranked=False)
        if not evaluations:
            raise typer.BadParameter(f"No evaluations found in store {input_json}")
    else:
        with input_json.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list):
            raise typer.BadParameter("Input JSON must be a list of evaluations")
        evaluations = [CandidateEvaluation.model_validate(item) for item in raw]

    ranked = rank_evaluations(evaluations, stable_first=stable_first)
    _print_rank_table(ranked)

    if output is not None:
        path = write_evaluations_json(ranked, output)
        console.print(f"[green]Wrote[/green] {path}")
    if csv_output is not None:
        path = write_evaluations_csv(ranked, csv_output)
        console.print(f"[green]Wrote[/green] {path}")
    if markdown is not None:
        path = write_synthesis_cards(ranked, markdown)
        console.print(f"[green]Wrote[/green] {path}")

    if input_json.is_dir():
        store = EvaluationStore(input_json)
        store.save_evaluations(ranked, ranked=True)


# ---------------------------------------------------------------------------
# shortlist
# ---------------------------------------------------------------------------


@app.command("shortlist")
def shortlist_cmd(
    store_dir: Path = typer.Argument(
        ...,
        help="Campaign output directory from an AL dry-run or phonon map "
        "(contains evaluations.json).",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Path for the generated shortlist campaign YAML.",
    ),
    max_jobs: int = typer.Option(
        6,
        "--max-jobs",
        "-n",
        help="Maximum number of candidates in the shortlist.",
        min=1,
        max=32,
    ),
    mode: str = typer.Option(
        "al_selected",
        "--mode",
        "-m",
        help=(
            "Selection: al_selected | top_acquisition | top_rank | "
            "stable_only | stable_or_soft"
        ),
    ),
    soft_min_cm1: float = typer.Option(
        0.0,
        "--soft-min-cm1",
        help=(
            "For stable_or_soft: require min phonon frequency ≥ this (cm⁻¹). "
            "Default 0 (no imaginary). Slightly negative tolerates numeric noise."
        ),
    ),
    stable_sort: str = typer.Option(
        "si",
        "--stable-sort",
        help="Among stable survivors: si (highest Si-feasibility) | rank.",
    ),
    name: str = typer.Option(
        "nitride_epw_shortlist",
        "--name",
        help="Campaign name (also used in default output_dir).",
    ),
    campaign_output_dir: Path | None = typer.Option(
        None,
        "--campaign-output-dir",
        help="output_dir written into the shortlist YAML "
        "(default: outputs/<name>).",
    ),
    pseudo_dir: Path | None = typer.Option(
        None,
        "--pseudo-dir",
        help="UPF directory for real EPW (written into dft.pseudo_dir).",
    ),
    nproc: int = typer.Option(
        4,
        "--nproc",
        help="MPI ranks for QE/EPW in the generated campaign.",
        min=1,
    ),
    calculator: str = typer.Option(
        "qe-epw",
        "--calculator",
        "-C",
        help="Calculator name embedded in the shortlist YAML.",
    ),
) -> None:
    """Build a focused EPW shortlist campaign from an AL / phonon store.

    Selects top-k candidates (AL-selected by default, or stability-gated),
    writes a campaign YAML with exact ``candidate_specs`` (formula × strain ×
    CIF) and screening EPW grids. Then run::

        siscforge run --calculator qe-epw <shortlist.yaml>

    Phonon-first (machine-2 stability map)::

        siscforge shortlist outputs/nbti_n_phonon_map -o epw.yaml --mode stable_only
    """
    from siscforge.shortlist import (
        build_shortlist_campaign,
        load_evaluations_from_store,
        shortlist_summary_table,
        write_campaign_yaml,
    )

    mode_norm = mode.strip().lower().replace("-", "_")
    allowed = {
        "al_selected",
        "top_acquisition",
        "top_rank",
        "stable_only",
        "stable_or_soft",
    }
    if mode_norm not in allowed:
        raise typer.BadParameter(
            "mode must be al_selected | top_acquisition | top_rank | "
            "stable_only | stable_or_soft"
        )
    sort_norm = stable_sort.strip().lower()
    if sort_norm not in {"si", "rank"}:
        raise typer.BadParameter("stable-sort must be si | rank")

    evals = load_evaluations_from_store(store_dir)
    if not evals:
        console.print(f"[red]No evaluations found in[/red] {store_dir}")
        raise typer.Exit(code=1)

    try:
        cfg, chosen = build_shortlist_campaign(
            evals,
            name=name,
            source_store=str(store_dir.resolve()),
            max_jobs=max_jobs,
            mode=mode_norm,  # type: ignore[arg-type]
            soft_min_cm1=soft_min_cm1,
            stable_sort=sort_norm,  # type: ignore[arg-type]
            output_dir=(
                str(campaign_output_dir)
                if campaign_output_dir is not None
                else f"outputs/{name}"
            ),
            pseudo_dir=str(pseudo_dir) if pseudo_dir else None,
            nproc=nproc,
            dry_run=False,
            calculator=calculator,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    path = write_campaign_yaml(cfg, output)
    console.print(
        f"[bold]Shortlist[/bold] {len(chosen)} candidates → [green]{path}[/green]"
    )
    console.print(f"[dim]Campaign output_dir:[/dim] {cfg.output_dir}")
    console.print(f"[dim]dft.pseudo_dir:[/dim] {cfg.dft.pseudo_dir}")
    console.print(
        f"[dim]calculator:[/dim] {calculator}  nproc={nproc}  mode={mode_norm}"
    )
    if mode_norm in {"stable_only", "stable_or_soft"}:
        console.print(
            f"[dim]stability filter:[/dim] {mode_norm}  "
            f"soft_min_cm1={soft_min_cm1:g}  sort={sort_norm}"
        )

    table = Table(title="Shortlist for expensive path")
    table.add_column("#", justify="right")
    table.add_column("Formula")
    table.add_column("Strain", justify="right")
    table.add_column("Si", justify="right")
    table.add_column("Stable", justify="center")
    table.add_column("min ω", justify="right")
    table.add_column("Acq", justify="right")
    table.add_column("Prior status")
    for row in shortlist_summary_table(chosen):
        min_f = row.get("min_freq")
        table.add_row(
            str(row["#"]),
            str(row["formula"]),
            f"{row['strain']:+.3f}" if row["strain"] is not None else "—",
            f"{row['si']:.1f}" if row["si"] is not None else "—",
            str(row.get("stable") or "—"),
            f"{min_f:.1f}" if min_f is not None else "—",
            f"{row['acq']:.3f}" if row["acq"] is not None else "—",
            str(row["status"]),
        )
    console.print(table)
    console.print(
        "[bold]Next:[/bold]\n"
        f"  siscforge run --dry-run {path}   # mock smoke\n"
        f"  siscforge run --calculator qe-epw {path}   # real EPW (resume-safe)"
    )


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------


@app.command("refine")
def refine_cmd(
    store_dir: Path = typer.Argument(
        ...,
        help="Campaign store with evaluations.json (e.g. shortlist EPW output).",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Path for the generated refine campaign YAML.",
    ),
    max_jobs: int = typer.Option(
        2,
        "--max-jobs",
        "-n",
        help="Maximum candidates to refine (desktop default 2).",
        min=1,
        max=6,
    ),
    mode: str = typer.Option(
        "top_si",
        "--mode",
        "-m",
        help="Selection: top_si | top_rank | ids",
    ),
    tier: str = typer.Option(
        "workstation_dense",
        "--tier",
        "-t",
        help="Grid tier: workstation_dense | production",
    ),
    candidate_id: list[str] | None = typer.Option(
        None,
        "--id",
        help="Candidate ID (or prefix) for mode=ids; repeatable.",
    ),
    name: str = typer.Option(
        "nitride_epw_refine",
        "--name",
        help="Campaign name (also default output_dir stem).",
    ),
    campaign_output_dir: Path | None = typer.Option(
        None,
        "--campaign-output-dir",
        help="output_dir in the refine YAML (default: outputs/<name>).",
    ),
    pseudo_dir: Path | None = typer.Option(
        None,
        "--pseudo-dir",
        help="UPF directory for real EPW.",
    ),
    nproc: int = typer.Option(
        16,
        "--nproc",
        help="MPI ranks; epw.npool is set equal to nproc.",
        min=1,
    ),
    calculator: str = typer.Option(
        "qe-epw",
        "--calculator",
        "-C",
        help="Calculator name embedded in the refine YAML.",
    ),
) -> None:
    """Build a denser-grid refine campaign from an existing store.

    Promotes shortlist winners (exact CIF × strain) to workstation_dense or
    production EPW without re-enumerating the full grid. Trust layer re-assesses
    after refine runs — do not cite Tc until quality flags improve.

    Example::

        siscforge refine outputs/nbti_n_al_broad_shortlist \\\\
          -o examples/nbti_n_al_refine.yaml --mode top_si -n 2
        siscforge run --calculator qe-epw examples/nbti_n_al_refine.yaml
    """
    from siscforge.refine import (
        build_refine_campaign,
        refine_summary_table,
        write_refine_yaml,
    )
    from siscforge.shortlist import load_evaluations_from_store

    mode_norm = mode.strip().lower().replace("-", "_")
    if mode_norm not in {"top_si", "top_rank", "ids"}:
        raise typer.BadParameter("mode must be top_si | top_rank | ids")
    tier_norm = tier.strip().lower().replace("-", "_")
    if tier_norm not in {"workstation_dense", "production"}:
        raise typer.BadParameter("tier must be workstation_dense | production")
    if mode_norm == "ids" and not candidate_id:
        raise typer.BadParameter("mode=ids requires at least one --id")

    evals = load_evaluations_from_store(store_dir)
    if not evals:
        console.print(f"[red]No evaluations found in[/red] {store_dir}")
        raise typer.Exit(code=1)

    try:
        cfg, chosen = build_refine_campaign(
            evals,
            name=name,
            source_store=str(store_dir.resolve()),
            max_jobs=max_jobs,
            mode=mode_norm,  # type: ignore[arg-type]
            tier=tier_norm,  # type: ignore[arg-type]
            candidate_ids=list(candidate_id) if candidate_id else None,
            output_dir=(
                str(campaign_output_dir)
                if campaign_output_dir is not None
                else f"outputs/{name}"
            ),
            pseudo_dir=str(pseudo_dir) if pseudo_dir else None,
            nproc=nproc,
            dry_run=False,
            calculator=calculator,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    path = write_refine_yaml(cfg, output)
    console.print(
        f"[bold]Refine[/bold] {len(chosen)} candidates "
        f"({tier_norm}) → [green]{path}[/green]"
    )
    console.print(f"[dim]Campaign output_dir:[/dim] {cfg.output_dir}")
    console.print(
        f"[dim]dft:[/dim] quality_tag={cfg.dft.quality_tag} "
        f"k={cfg.dft.kpoints} q={cfg.dft.qpoints} "
        f"nkf={cfg.dft.epw.nkf} nqf={cfg.dft.epw.nqf} nqc={cfg.dft.epw.nqc}"
    )
    console.print(
        f"[dim]nproc={cfg.dft.nproc} epw.npool={cfg.dft.epw.npool} "
        f"pseudo_dir={cfg.dft.pseudo_dir}[/dim]"
    )
    console.print(
        "[yellow]refinement — do not cite Tc until trust flags clear/improve[/yellow]"
    )

    table = Table(title="Refine selection (from store)")
    table.add_column("#", justify="right")
    table.add_column("Formula")
    table.add_column("Strain", justify="right")
    table.add_column("Si", justify="right")
    table.add_column("Prior qual")
    table.add_column("Prior λ", justify="right")
    table.add_column("Prior Tc", justify="right")
    table.add_column("Stable")
    for row in refine_summary_table(chosen):
        table.add_row(
            str(row["#"]),
            str(row["formula"]),
            f"{row['strain']:+.3f}" if row["strain"] is not None else "—",
            f"{row['si']:.1f}" if row["si"] is not None else "—",
            str(row["prior_qual"]),
            f"{row['prior_λ']:.2f}" if row["prior_λ"] is not None else "—",
            f"{row['prior_Tc']:.1f}" if row["prior_Tc"] is not None else "—",
            str(row["stable"]),
        )
    console.print(table)
    unstable = [
        e
        for e in chosen
        if e.phonon is not None
        and (e.phonon.has_imaginary_modes or not e.phonon.dynamically_stable)
    ]
    if unstable:
        console.print(
            f"[yellow]Warning:[/yellow] {len(unstable)} selected candidate(s) had "
            f"unstable screening phonons — refine re-runs vc-relax+DFPT but may "
            f"still need different strain if soft modes persist."
        )
    console.print(
        "[bold]Next:[/bold]\n"
        f"  siscforge run --dry-run {path}\n"
        f"  siscforge run --calculator qe-epw {path}\n"
        "  # after runs: rank/export — check result_quality before citing Tc"
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _resolve_calculator_name(
    config: CampaignConfig,
    *,
    dry_run: bool,
    calculator: str | None,
) -> str:
    """Resolve active calculator name from CLI flags + campaign config.

    Priority: ``--dry-run`` → mock; ``--calculator``; campaign ``calculators[0]``;
    campaign ``dft.engine``; finally ``mock``.
    """
    if dry_run or config.dry_run:
        return "mock"
    if calculator:
        return calculator.strip().lower()
    if config.calculators:
        return config.calculators[0].name.strip().lower()
    if config.dft.engine and config.dft.engine != "mock":
        return config.dft.engine
    return "mock"


def _resolve_run_config(
    run: RunConfig,
    *,
    force_rerun: bool,
    fail_fast: bool,
    heartbeat_seconds: int | None = None,
) -> RunConfig:
    """Merge CLI overrides into campaign ``run`` block."""
    updates: dict = {}
    if force_rerun:
        updates["force_rerun"] = True
    if fail_fast:
        updates["continue_on_error"] = False
    if heartbeat_seconds is not None:
        updates["heartbeat_seconds"] = int(heartbeat_seconds)
    return run.model_copy(update=updates) if updates else run


def _primary_failure_hint(result: CandidateEvaluation, *, max_len: int = 110) -> str:
    """One-line reason for CLI progress from evaluation errors/notes."""
    from siscforge.calculators.qe.epw_recipes import extract_primary_failure_reason

    # Prefer structured errors (often include diagnose text)
    blob_parts: list[str] = []
    if result.errors:
        blob_parts.extend(result.errors)
    if result.notes:
        blob_parts.append(result.notes)
    eph = result.electron_phonon
    if eph is not None and eph.alpha2F_summary:
        primary = eph.alpha2F_summary.get("primary_failure")
        if primary:
            blob_parts.insert(0, str(primary))
        diag = eph.alpha2F_summary.get("failure_diagnostic")
        if diag:
            blob_parts.append(str(diag))
    blob = "\n".join(blob_parts)
    if not blob.strip():
        return f"status={result.status}"
    # Already a high-signal one-liner from run_epw / phonon diagnostics
    first = blob.splitlines()[0].strip()
    if (
        first.startswith("EPW ")
        or first.startswith("QE ")
        or first.startswith("Phonon failed")
        or "Wannier" in first
        or "d_matrix" in first.lower()
    ):
        # Prefer classified fingerprint over raw "Phonon failed (ph.x): …"
        reason = extract_primary_failure_reason(blob, step_name="calc", max_len=max_len)
        if "d_matrix" in reason.lower() or "orthogonal" in reason.lower():
            return reason[:max_len] + ("…" if len(reason) > max_len else "")
        if first.startswith("QE ") or first.startswith("EPW "):
            return first[:max_len] + ("…" if len(first) > max_len else "")
        return reason[:max_len] + ("…" if len(reason) > max_len else "")
    reason = extract_primary_failure_reason(blob, step_name="calc", max_len=max_len)
    return reason


@app.command("run")
def run_cmd(
    campaign: Path = typer.Argument(
        ...,
        help="Path to campaign YAML.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Use the mock calculator (no DFT/EPW). Default for CI and demos.",
    ),
    calculator: str | None = typer.Option(
        None,
        "--calculator",
        "-C",
        help="Calculator name: mock | qe | qe-epw | epw | quantum-espresso. "
        "Ignored when --dry-run is set.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Override campaign output directory.",
    ),
    no_filter: bool = typer.Option(
        False,
        "--no-filter",
        help="Skip the formation-energy pre-filter.",
    ),
    force_rerun: bool = typer.Option(
        False,
        "--force-rerun",
        help="Ignore successful evaluations already in output_dir and recompute.",
    ),
    fail_fast: bool = typer.Option(
        False,
        "--fail-fast",
        help="Abort the campaign on the first calculator failure "
        "(default is continue-on-error).",
    ),
    heartbeat_seconds: int | None = typer.Option(
        None,
        "--heartbeat-seconds",
        help="Print QE/EPW progress heartbeats every N seconds during long "
        "pw.x/ph.x/epw.x steps (default: run.heartbeat_seconds=900). 0 disables.",
        min=0,
    ),
) -> None:
    """Load a campaign, filter, evaluate candidates, rank, persist, and export.

    Resume/checkpoint (default): re-running the same ``output_dir`` skips
    candidates that already have a successful evaluation (status ok/mock with
    result fields). Failures are recorded and the shortlist continues unless
    ``--fail-fast`` or ``run.continue_on_error: false``.

    Interrupt (Ctrl+C / sleep / power loss) is safe at the process level:
    re-issue the same ``siscforge run ...`` command. Finished candidates and
    completed QE steps are skipped; incomplete DFPT may resume with QE
    ``recover=.true.`` when on-disk state looks recoverable.
    """
    config = CampaignConfig.from_yaml(campaign)
    if dry_run:
        config = config.model_copy(update={"dry_run": True})

    run_cfg = _resolve_run_config(
        config.run,
        force_rerun=force_rerun,
        fail_fast=fail_fast,
        heartbeat_seconds=heartbeat_seconds,
    )
    config = config.model_copy(update={"run": run_cfg})

    out = Path(output_dir) if output_dir is not None else Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = EvaluationStore(out)

    # 1. Enumerate
    candidates = generate_candidates(config)
    n_raw = len(candidates)
    console.print(
        f"[bold]Campaign[/bold] {config.name}: {n_raw} structure candidates enumerated"
    )

    # 2. Formation-energy pre-filter
    filter_cfg = config.formation_filter
    if no_filter:
        filter_cfg = filter_cfg.model_copy(update={"enabled": False})
    fres = FormationEnergyFilter(filter_cfg).filter(candidates)
    candidates = fres.kept
    store.save_filter_summary(
        {
            **fres.summary(),
            "n_enumerated": n_raw,
            "filter_config": filter_cfg.model_dump(mode="json"),
        }
    )
    store.save_candidates(candidates)
    if filter_cfg.enabled:
        console.print(
            f"[bold]Formation filter[/bold] kept {fres.n_kept}/{n_raw} "
            f"(rejected {fres.n_rejected}, max E_hull={filter_cfg.max_e_hull_eV_per_atom})"
        )
    else:
        console.print("[bold]Formation filter[/bold] disabled — keeping all candidates")

    if not candidates:
        console.print("[red]No candidates left after filtering.[/red]")
        raise typer.Exit(code=1)

    # 2b. λ/Tc surrogate pre-filter (optional Phase 1 stub)
    tc_cfg = config.surrogate.tc_lambda
    tc_surrogate = TcLambdaSurrogate(tc_cfg)
    tc_fres = tc_surrogate.filter(candidates)
    candidates = tc_fres.kept
    store.save_json(
        "tc_lambda_surrogate.json",
        {
            **tc_fres.summary(),
            "enabled": tc_cfg.enabled,
            "config": tc_cfg.model_dump(mode="json"),
        },
    )
    if tc_cfg.enabled:
        console.print(
            f"[bold]λ/Tc surrogate[/bold] kept {tc_fres.n_kept} "
            f"(rejected {tc_fres.n_rejected}, model={tc_cfg.version})"
        )
        store.save_candidates(candidates)
    # When disabled, filter() still annotates predictions on all kept candidates
    # without dropping any (export can show stub values if present).

    if not candidates:
        console.print("[red]No candidates left after λ/Tc surrogate filter.[/red]")
        raise typer.Exit(code=1)

    # 2c. Si-feasibility (cheap) + active-learning prioritization
    si_by_id = {c.candidate_id: score_si_feasibility(c) for c in candidates}
    al_cfg = config.active_learning
    al_plan = prioritize_candidates(
        candidates,
        config=al_cfg,
        si_scores=si_by_id,
        predictions=tc_fres.predictions,
    )
    store.save_json(
        "active_learning.json",
        {
            **al_plan.summary(),
            "config": al_cfg.model_dump(mode="json"),
        },
    )
    acq_by_id = {r.candidate_id: r for r in al_plan.ranked}
    if al_cfg.enabled:
        console.print(
            f"[bold]Active learning[/bold] strategy={al_cfg.strategy} "
            f"selected {len(al_plan.selected)}/{len(candidates)} for expensive path "
            f"(max_epw_jobs={al_cfg.max_epw_jobs})"
        )
        _print_acquisition_table(al_plan.ranked, max_rows=15)
        expensive_candidates = list(al_plan.selected)
        deferred_candidates = (
            list(al_plan.deferred) if al_cfg.evaluate_deferred_with_surrogate else []
        )
    else:
        expensive_candidates = list(candidates)
        deferred_candidates = []

    # 3. Select calculator
    calc_name = _resolve_calculator_name(
        config, dry_run=dry_run, calculator=calculator
    )
    if calc_name in {"quantum-espresso", "quantum_espresso", "espresso"}:
        calc_name = "qe"
    if calc_name in {"epw", "qe_epw", "qe+epw"}:
        calc_name = "qe-epw"

    ensure_builtins_loaded()
    try:
        calc = get_calculator(calc_name)
    except KeyError as exc:
        console.print(f"[red]Unknown calculator:[/red] {calc_name}")
        console.print(f"Registered: {', '.join(list_calculators())}")
        raise typer.Exit(code=2) from exc

    calc_params: dict = {}
    for c in config.calculators:
        if c.name == calc_name or (
            calc_name in {"qe", "qe-epw"}
            and c.name in {"qe", "qe-epw", "quantum-espresso", "epw"}
        ):
            calc_params = dict(c.parameters)
            break

    if calc_name in {"qe", "qe-epw"}:
        dft = config.dft
        if calc_name == "qe-epw":
            dft = dft.model_copy(
                update={
                    "do_epw": True,
                    "epw": dft.epw.model_copy(update={"enabled": True}),
                }
            )
        # EPW fine-grid: nproc must equal npool (nimage=1). Auto-fix early so
        # users see the message before multi-hour DFPT, not only at epw.x launch.
        # Phonon-only (do_epw false + calculator qe): skip all EPW preflight noise.
        want_epw = bool(calc_name == "qe-epw" or dft.do_epw or dft.epw.enabled)
        if want_epw:
            from siscforge.calculators.qe.epw_inputs import (
                default_nbndsub_screening,
                preflight_epw_grids,
            )
            from siscforge.calculators.qe.epw_parallel import validate_epw_parallel
            from siscforge.calculators.qe.epw_recipes import (
                resolve_epw_launch_topology,
            )

            raw = validate_epw_parallel(
                max(1, int(dft.nproc)),
                max(1, int(dft.epw.npool)),
                nimage=1,
                fine_grid=True,
            )
            if not raw.ok:
                console.print(
                    f"[yellow]EPW parallel warning:[/yellow] {raw.message}"
                )
            try:
                dft, par_msg = resolve_epw_launch_topology(dft)
                if "auto-set" in par_msg.lower():
                    console.print(f"[cyan]{par_msg}[/cyan]")
                else:
                    console.print(f"[dim]{par_msg}[/dim]")
            except ValueError as exc:
                console.print(f"[red]EPW parallel topology refused:[/red]\n{exc}")
                raise typer.Exit(code=1) from exc

            # Pre-DFPT EPW preflight: Wannier-safe coarse k + nq ↔ DFPT qpoints
            tier_hint = None
            extras = getattr(config, "extras", None) or {}
            if isinstance(extras, dict):
                refine = extras.get("refine") or {}
                if isinstance(refine, dict):
                    tier_hint = refine.get("tier")
            # Prefer real cell size from shortlist/refine CIF so 8-atom floor applies
            n_atoms_hint: int | None = None
            try:
                specs = list(config.enumeration.candidate_specs or [])
                for sp in specs:
                    cif = getattr(sp, "structure_cif", None)
                    if cif:
                        from pymatgen.core import Structure as _S

                        n_atoms_hint = max(n_atoms_hint or 0, len(_S.from_str(cif, fmt="cif")))
                if n_atoms_hint is None and specs:
                    # Ternary 2×2×1 nitride supercell is the desktop default
                    n_atoms_hint = 8
            except Exception:  # noqa: BLE001
                n_atoms_hint = 8 if (dft.quality_tag or "") == "production" else None
            pre = preflight_epw_grids(
                dft, structure=None, n_atoms=n_atoms_hint, tier=tier_hint
            )
            for line in pre.summary_lines:
                if "raised" in line.lower() or "aligned" in line.lower():
                    console.print(f"[cyan]{line}[/cyan]")
                elif line.startswith("STRICT") or not pre.ok:
                    console.print(f"[red]{line}[/red]")
                else:
                    console.print(f"[dim]{line}[/dim]")
            if not pre.ok:
                console.print(
                    "[red]EPW preflight refused launch "
                    "(strict_coarse_k or invalid grids).[/red]"
                )
                raise typer.Exit(code=1)
            dft = pre.config

            # Preflight: warn if nbndsub looks tiny vs nbnd (supercell trap)
            if dft.epw.nbndsub is not None and dft.nbnd is not None:
                if (
                    dft.epw.nbndsub < 16
                    and dft.nbnd >= 32
                    and not dft.epw.auto_nbndsub
                ):
                    console.print(
                        f"[yellow]EPW Wannier warning:[/yellow] "
                        f"epw.nbndsub={dft.epw.nbndsub} looks small vs "
                        f"dft.nbnd={dft.nbnd} (risk: frozen window > target WFs). "
                        f"Enable auto_nbndsub or raise nbndsub."
                    )
            elif dft.epw.auto_nbndsub:
                auto_sub = default_nbndsub_screening(
                    nbnd=dft.nbnd, structure=None, explicit=dft.epw.nbndsub, auto=True
                )
                console.print(
                    f"[dim]EPW Wannier screening nbndsub≈{auto_sub} "
                    f"(auto from nbnd={dft.nbnd}; structure may raise further)[/dim]"
                )
        else:
            # Explicit phonon-only path: keep epw disabled so recipes never
            # branch into EPW, and avoid npool / Wannier preflight noise.
            dft = dft.model_copy(
                update={
                    "do_epw": False,
                    "epw": dft.epw.model_copy(update={"enabled": False}),
                }
            )
            console.print(
                "[bold cyan]Phonon-only campaign[/bold cyan] "
                "(do_epw=false — no EPW launch, no npool preflight)"
            )
        calc_params = {**calc_params, "dft": dft, "run_config": run_cfg}
        if dft.work_dir is None:
            calc_params.setdefault("work_dir", str(out / "qe_work"))
        mode_bits = [
            f"pseudo_dir={dft.pseudo_dir!r}",
            f"do_relax={dft.do_relax}",
            f"do_phonon={dft.do_phonon}",
            f"do_epw={want_epw}",
            f"nproc={dft.nproc}",
        ]
        if want_epw:
            mode_bits.append(f"epw.npool={dft.epw.npool}")
        else:
            mode_bits.append(f"phonon_method={dft.phonon_method}")
            mode_bits.append(f"qpoints={list(dft.qpoints)}")
        console.print(
            f"[bold]Calculator[/bold] {calc_name}  ({', '.join(mode_bits)})"
        )
    else:
        calc_params = {**calc_params, "run_config": run_cfg}
        console.print(f"[bold]Calculator[/bold] {calc_name}")

    console.print(
        f"[dim]Run policy:[/dim] resume={run_cfg.resume} "
        f"continue_on_error={run_cfg.continue_on_error} "
        f"force_rerun={run_cfg.force_rerun} "
        f"resume_qe_steps={run_cfg.resume_qe_steps} "
        f"heartbeat={run_cfg.heartbeat_seconds}s"
    )
    if (
        calc_name in {"qe", "qe-epw"}
        and run_cfg.heartbeat_seconds
        and run_cfg.heartbeat_seconds > 0
    ):
        console.print(
            f"[dim]QE heartbeats every {run_cfg.heartbeat_seconds}s during "
            f"pw.x / ph.x / epw.x (set run.heartbeat_seconds: 0 to disable)[/dim]"
        )

    # 3b. Desktop walltime bands (qe / qe-epw only; mock unchanged)
    walltime_tracker = None
    walltime_est = None
    if calc_name in {"qe", "qe-epw"} and getattr(run_cfg, "estimate_walltime", True):
        from siscforge.walltime import (
            WalltimeTracker,
            estimate_campaign_walltime,
            format_campaign_estimate_lines,
            should_print_walltime_estimate,
        )

        if should_print_walltime_estimate(calc_name, run_cfg):
            dft_for_est = calc_params.get("dft", config.dft)
            walltime_est = estimate_campaign_walltime(
                dft_for_est,
                n_candidates=max(1, len(expensive_candidates)),
                candidates=expensive_candidates,
                scale=float(getattr(run_cfg, "walltime_scale", 1.0) or 1.0),
            )
            walltime_tracker = WalltimeTracker()
            for line in format_campaign_estimate_lines(walltime_est):
                if line.startswith("Estimated"):
                    console.print(f"[bold cyan]{line}[/bold cyan]")
                elif line.startswith("  Tip:"):
                    console.print(f"[dim]{line}[/dim]")
                else:
                    console.print(f"[cyan]{line}[/cyan]")

    # 4. Evaluate expensive path + optional surrogate-only deferred set
    evaluations: list[CandidateEvaluation] = []
    # Prior successes from this output_dir (for skip-finished).
    # Real QE/EPW must not skip dry-run mock rows (require_real).
    require_real = calc_name in {"qe", "qe-epw"}
    resume_by_id, resume_by_fp = (
        store.resume_index(require_real=require_real)
        if run_cfg.resume and not run_cfg.force_rerun
        else ({}, {})
    )
    stats = {"skipped": 0, "ran": 0, "ok": 0, "failed": 0}

    # If resume will skip some, restate remaining campaign band once
    if walltime_est is not None and resume_by_id:
        n_will_skip = 0
        for cand in expensive_candidates:
            if (
                find_resumable_evaluation(
                    cand,
                    by_id=resume_by_id,
                    by_fp=resume_by_fp,
                    force_rerun=False,
                )
                is not None
            ):
                n_will_skip += 1
        n_remaining = len(expensive_candidates) - n_will_skip
        if 0 < n_remaining < len(expensive_candidates):
            from siscforge.walltime import format_campaign_estimate_lines

            console.print(
                f"[dim]Resume: {n_will_skip} already done; "
                f"~{n_remaining} still to run[/dim]"
            )
            for line in format_campaign_estimate_lines(
                walltime_est, remaining_candidates=n_remaining
            )[1:3]:
                console.print(f"[cyan]{line}[/cyan]")

    def _finalize_eval(
        result: CandidateEvaluation,
        cand: StructureCandidate,
        *,
        selected: bool,
    ) -> CandidateEvaluation:
        si = si_by_id[cand.candidate_id]
        if result.si_feasibility is None or str(
            getattr(result.si_feasibility, "version", "")
        ).endswith("mock"):
            result = result.model_copy(update={"si_feasibility": si})
        if result.candidate.energy_above_hull_proxy is None or (
            "tc_lambda_surrogate" in cand.metadata
            and "tc_lambda_surrogate" not in result.candidate.metadata
        ):
            result = result.model_copy(update={"candidate": cand})

        pred = tc_fres.predictions.get(cand.candidate_id)
        acq = acq_by_id.get(cand.candidate_id)
        updates: dict = {
            "al_selected_for_expensive": selected if al_cfg.enabled else None,
            "acquisition_score": (
                acq.acquisition_score
                if (al_cfg.enabled and acq is not None)
                else None
            ),
        }
        if pred is not None:
            updates["tc_lambda_surrogate"] = pred.model_dump(mode="json")
            real_tc = (
                result.electron_phonon.best_tc_K()
                if result.electron_phonon is not None
                else None
            )
            has_real_eph = real_tc is not None and result.electron_phonon is not None
            if has_real_eph:
                if result.performance_score is None:
                    updates["performance_score"] = real_tc
                    updates["performance_score_source"] = (
                        "mock"
                        if result.electron_phonon.status == "mock"
                        else "epw"
                    )
                elif result.performance_score_source is None:
                    updates["performance_score_source"] = (
                        "mock"
                        if result.electron_phonon.status == "mock"
                        else "epw"
                    )
            elif tc_cfg.use_for_ranking_when_no_epw and result.performance_score is None:
                notes = (result.notes or "").strip()
                note_add = (
                    "performance_score from λ/Tc surrogate stub "
                    f"(unc={pred.uncertainty:.2f}; not EPW)"
                )
                updates["performance_score"] = pred.score_for_ranking()
                updates["performance_score_source"] = "surrogate"
                updates["notes"] = f"{notes}; {note_add}" if notes else note_add
        return result.model_copy(update=updates)

    def _progress_label(cand: StructureCandidate) -> str:
        strain = cand.in_plane_strain
        strain_s = f" strain={strain:+.3f}" if strain is not None else ""
        return f"{cand.formula}{strain_s}"

    n_expensive = len(expensive_candidates)
    for idx, cand in enumerate(expensive_candidates, start=1):
        prefix = f"[{idx}/{n_expensive}] {_progress_label(cand)}"
        prior = None
        if run_cfg.resume and not run_cfg.force_rerun:
            prior = find_resumable_evaluation(
                cand,
                by_id=resume_by_id,
                by_fp=resume_by_fp,
                force_rerun=False,
            )
        if prior is not None:
            console.print(
                f"[cyan]{prefix}[/cyan] — skip (already {prior.status})"
            )
            result = _finalize_eval(prior, cand, selected=True)
            notes = (result.notes or "").strip()
            skip_note = "resumed from store (skipped recalculation)"
            if skip_note not in notes:
                result = result.model_copy(
                    update={"notes": f"{notes}; {skip_note}" if notes else skip_note}
                )
            evaluations.append(result)
            store.append_evaluation(result)
            stats["skipped"] += 1
            stats["ok"] += 1
            continue

        console.print(f"[bold]{prefix}[/bold] — running {calc_name}")
        si = si_by_id[cand.candidate_id]
        params = {**calc_params, "si_feasibility": si}
        pred_mid_h = None
        if walltime_est is not None:
            obs_scale = (
                walltime_tracker.observed_scale() if walltime_tracker is not None else None
            )
            if obs_scale is not None and abs(obs_scale - 1.0) > 0.15:
                from siscforge.walltime import estimate_candidate_walltime

                adj = estimate_candidate_walltime(
                    calc_params.get("dft", config.dft),
                    candidate=cand,
                    n_candidates=1,
                    scale=float(getattr(run_cfg, "walltime_scale", 1.0) or 1.0),
                    observed_scale=obs_scale,
                )
                console.print(
                    f"[dim]  walltime hint (adjusted): {adj.per_candidate_line()}[/dim]"
                )
                pred_mid_h = 0.5 * (adj.full_lo_h + adj.full_hi_h)
            else:
                pred_mid_h = 0.5 * (walltime_est.full_lo_h + walltime_est.full_hi_h)
        if walltime_tracker is not None:
            walltime_tracker.start(cand.candidate_id)
        try:
            result = calc.run(cand, **params)
        except KeyboardInterrupt:
            if walltime_tracker is not None:
                walltime_tracker.finish(
                    cand.candidate_id, predicted_mid_h=pred_mid_h
                )
            console.print(
                "\n[yellow]Interrupted.[/yellow] Re-run the same command to resume "
                "from the last safe checkpoint "
                "(finished candidates + completed QE steps; "
                "incomplete DFPT may use QE recover=.true.)."
            )
            raise typer.Exit(code=130) from None
        except Exception as exc:  # noqa: BLE001
            from siscforge.calculators.qe import QENotAvailableError

            if walltime_tracker is not None:
                walltime_tracker.finish(
                    cand.candidate_id, predicted_mid_h=pred_mid_h
                )

            # Missing QE install is environment-wide — always abort.
            if isinstance(exc, QENotAvailableError):
                console.print(f"[red]QE not available:[/red]\n{exc}")
                raise typer.Exit(code=3) from exc
            if not run_cfg.continue_on_error:
                console.print(f"[red]Calculator error for {cand.formula}:[/red] {exc}")
                raise typer.Exit(code=1) from exc
            from siscforge.calculators.qe.epw_recipes import (
                extract_primary_failure_reason,
                truncate_for_notes,
            )

            exc_text = truncate_for_notes(str(exc), max_chars=800)
            hint = extract_primary_failure_reason(str(exc), step_name="calc", max_len=110)
            console.print(
                f"[yellow]{prefix}[/yellow] — failed ({hint}); continuing"
            )
            result = CandidateEvaluation(
                candidate=cand,
                si_feasibility=si,
                status="failed",
                calculator_name=calc_name,
                errors=[hint, exc_text],
                notes=f"Calculator raised: {hint}; {exc_text}",
                provenance=Provenance(
                    source="run_continue_on_error",
                    software={"siscforge": __version__},
                    parent_ids=[cand.candidate_id],
                    notes=f"fingerprint={resume_fingerprint(cand)}",
                ),
            )
            result = _finalize_eval(result, cand, selected=True)
            evaluations.append(result)
            store.append_evaluation(result)
            stats["ran"] += 1
            stats["failed"] += 1
            continue

        if walltime_tracker is not None:
            obs_h = walltime_tracker.finish(
                cand.candidate_id, predicted_mid_h=pred_mid_h
            )
            if obs_h is not None and obs_h >= 1.0 / 60.0:
                # Only mention when at least ~1 min (avoid noise on mock)
                from siscforge.walltime import format_duration_band

                console.print(
                    f"[dim]  observed walltime ~{format_duration_band(obs_h, obs_h)} "
                    f"(recorded for later candidates in this run)[/dim]"
                )

        if not isinstance(result, CandidateEvaluation):
            if not run_cfg.continue_on_error:
                raise typer.Exit(code=1)
            result = CandidateEvaluation(
                candidate=cand,
                si_feasibility=si,
                status="failed",
                calculator_name=calc_name,
                errors=["Calculator returned non-CandidateEvaluation"],
                notes="Invalid calculator return type",
            )
        result = _finalize_eval(result, cand, selected=True)
        evaluations.append(result)
        store.append_evaluation(result)
        stats["ran"] += 1
        if is_successful_evaluation(result, require_real=require_real):
            stats["ok"] += 1
            # Make this success visible to later candidates in the same run
            resume_by_id[result.candidate.candidate_id] = result
            resume_by_fp[resume_fingerprint(cand)] = result
            console.print(f"[green]{prefix}[/green] — ok ({result.status})")
        else:
            stats["failed"] += 1
            err_hint = _primary_failure_hint(result)
            console.print(
                f"[yellow]{prefix}[/yellow] — failed ({err_hint})"
            )
            if not run_cfg.continue_on_error:
                console.print(
                    "[red]Fail-fast: aborting remaining expensive candidates.[/red]"
                )
                raise typer.Exit(code=1)

    for cand in deferred_candidates:
        pred = tc_fres.predictions.get(cand.candidate_id)
        if pred is None:
            pred = TcLambdaSurrogate(tc_cfg).predict(cand)
        si = si_by_id[cand.candidate_id]
        result = CandidateEvaluation(
            candidate=cand,
            si_feasibility=si,
            tc_lambda_surrogate=pred.model_dump(mode="json"),
            performance_score=pred.score_for_ranking(),
            performance_score_source="surrogate",
            status="surrogate_only",
            calculator_name="surrogate",
            notes=(
                "AL deferred expensive calculator; surrogate-only evaluation "
                "(acq would re-rank after real EPW)"
            ),
            provenance=Provenance(
                source="active_learning_deferred",
                software={"siscforge": __version__},
                notes="Phase-1 AL prioritization first cut",
            ),
        )
        result = _finalize_eval(result, cand, selected=False)
        evaluations.append(result)
        store.append_evaluation(result)

    console.print(
        f"[bold]Checkpoint summary[/bold] (expensive path): "
        f"skipped={stats['skipped']}, ran={stats['ran']}, "
        f"ok={stats['ok']}, failed={stats['failed']}"
        + (
            f", deferred_surrogate={len(deferred_candidates)}"
            if deferred_candidates
            else ""
        )
    )

    # 5. Rank + persist ranked (real/mock EPW Tc dominates when present)
    # Phonon-only maps: stable_first makes dynamical stability glanceable.
    phonon_only = False
    if calc_name == "qe":
        dft_used = calc_params.get("dft", config.dft)
        phonon_only = not bool(
            getattr(dft_used, "do_epw", False)
            or getattr(getattr(dft_used, "epw", None), "enabled", False)
        )
    ranked = rank_evaluations(
        evaluations, config.ranking, stable_first=phonon_only
    )
    store.save_evaluations(ranked, ranked=True)
    store.save_evaluations(ranked, ranked=False)  # canonical evaluations.json
    store.save_campaign(config)
    store.save_meta(
        {
            "siscforge_version": __version__,
            "calculator": calc_name,
            "n_candidates": len(candidates),
            "n_evaluations": len(ranked),
            "campaign": config.name,
            "run": run_cfg.model_dump(mode="json"),
            "checkpoint": stats,
        }
    )
    _print_rank_table(ranked, title=f"Ranked results — {config.name}")

    # 6. Export bundle
    formats = list(config.export_formats)
    written = export_campaign_bundle(
        ranked,
        out,
        formats=formats,
        campaign_name=config.name,
        candidates=candidates,
    )
    # Always write synthesis cards for Phase 0 polish
    if "markdown" not in formats and "md" not in formats:
        cards = write_synthesis_cards(
            ranked, out / "synthesis_cards.md", campaign_name=config.name
        )
        written["markdown"] = cards
    for label, path in written.items():
        console.print(f"[green]Wrote[/green] {label}: {path}")
    console.print(f"[dim]Store root: {store.root.resolve()}[/dim]")


def _print_acquisition_table(
    records: list,
    *,
    max_rows: int = 15,
    title: str = "AL acquisition ranking",
) -> None:
    """Print top acquisition scores (selected vs deferred)."""
    table = Table(title=title)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Formula")
    table.add_column("Acq", justify="right", style="bold")
    table.add_column("Tĉ", justify="right")
    table.add_column("Unc", justify="right")
    table.add_column("Si", justify="right")
    table.add_column("Hull*", justify="right")
    table.add_column("EPW?")
    for i, rec in enumerate(records[:max_rows], start=1):
        table.add_row(
            str(i),
            rec.formula,
            f"{rec.acquisition_score:.3f}",
            f"{rec.predicted_tc:.1f}" if rec.predicted_tc is not None else "—",
            f"{rec.uncertainty:.2f}" if rec.uncertainty is not None else "—",
            f"{rec.si_feasibility:.1f}" if rec.si_feasibility is not None else "—",
            (
                f"{rec.energy_above_hull_proxy:.3f}"
                if rec.energy_above_hull_proxy is not None
                else "—"
            ),
            "yes" if rec.selected_for_expensive else "defer",
        )
    if len(records) > max_rows:
        table.caption = f"Showing top {max_rows} of {len(records)} (see active_learning.json)"
    console.print(table)


def _print_rank_table(
    ranked: list[CandidateEvaluation],
    *,
    title: str = "Ranked candidates",
) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("ID", style="dim", max_width=10)
    table.add_column("Formula")
    table.add_column("Family")
    table.add_column("Strain", justify="right")
    table.add_column("E_hull*", justify="right")
    table.add_column("Perf", justify="right")
    table.add_column("Qual", justify="center")
    table.add_column("Si", justify="right")
    table.add_column("Acq", justify="right")
    table.add_column("Composite", justify="right", style="bold")
    table.add_column("Stable")
    table.add_column("min ω", justify="right")
    table.add_column("Status")

    for ev in ranked:
        si = f"{ev.si_feasibility.total:.1f}" if ev.si_feasibility else "—"
        rq = getattr(ev, "result_quality", None) or "unknown"
        if ev.performance_score is not None:
            perf = f"{ev.performance_score:.1f}"
            if rq == "screening_suspect":
                perf = f"{perf}*"
            elif rq == "unreliable":
                perf = f"{perf}!!"
        else:
            perf = "—"
        # Short quality label for glanceability
        qual_map = {
            "production": "prod",
            "screening": "scr",
            "screening_suspect": "susp",
            "unreliable": "bad",
            "unknown": "—",
        }
        qual = qual_map.get(rq, rq[:4])
        flags = getattr(ev, "quality_flags", None) or []
        if "high_lambda" in flags or "extreme_lambda" in flags:
            qual = f"{qual} λ"
        if "imaginary_modes" in flags:
            qual = f"{qual} imag"
        comp = f"{ev.composite_score:.1f}" if ev.composite_score is not None else "—"
        acq = (
            f"{ev.acquisition_score:.3f}"
            if ev.acquisition_score is not None
            else "—"
        )
        stable = "—"
        min_w = "—"
        if ev.phonon is not None:
            stable = "yes" if ev.phonon.dynamically_stable else "NO"
            if ev.phonon.min_frequency_cm1 is not None:
                min_w = f"{ev.phonon.min_frequency_cm1:.1f}"
        strain = "—"
        if ev.candidate.in_plane_strain is not None:
            strain = f"{ev.candidate.in_plane_strain:+.3f}"
        hull = ev.candidate.energy_above_hull_proxy
        hull_s = f"{hull:.3f}" if hull is not None else "—"
        status = ev.status
        if ev.al_selected_for_expensive is False:
            status = f"{status}*"
        table.add_row(
            str(ev.rank or "—"),
            ev.candidate.candidate_id[:8] + "…",
            ev.candidate.formula,
            ev.candidate.material_family,
            strain,
            hull_s,
            perf,
            qual,
            si,
            acq,
            comp,
            stable,
            min_w,
            status,
        )
    console.print(table)


def run() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    run()
