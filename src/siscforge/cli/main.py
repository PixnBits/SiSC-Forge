"""SiSC-Forge CLI entry point (``siscforge``).

Phase 0 subcommands:
  - ``enumerate`` — generate structure candidates (+ optional formation filter)
  - ``rank``      — rank evaluations from JSON or a campaign store
  - ``run``       — load campaign, filter, evaluate, rank, persist, export
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from siscforge import __version__
from siscforge.calculators import ensure_builtins_loaded, list_calculators
from siscforge.calculators import get as get_calculator
from siscforge.export import (
    export_campaign_bundle,
    write_candidates_json,
    write_evaluations_csv,
    write_evaluations_json,
    write_synthesis_cards,
)
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import CampaignConfig
from siscforge.ranking import rank_evaluations
from siscforge.silicon.feasibility import score_si_feasibility
from siscforge.store import EvaluationStore
from siscforge.structure.generator import generate_candidates, generate_fake_candidates
from siscforge.surrogates.formation import FormationEnergyFilter

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

    ranked = rank_evaluations(evaluations)
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
        help="Use the mock calculator (no DFT). Recommended for Phase 0.",
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
) -> None:
    """Load a campaign, filter, evaluate candidates, rank, persist, and export."""
    config = CampaignConfig.from_yaml(campaign)
    if dry_run:
        config = config.model_copy(update={"dry_run": True})

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
        calc_params = {**calc_params, "dft": dft}
        if dft.work_dir is None:
            calc_params.setdefault("work_dir", str(out / "qe_work"))
        console.print(
            f"[bold]Calculator[/bold] {calc_name}  "
            f"(pseudo_dir={dft.pseudo_dir!r}, "
            f"do_relax={dft.do_relax}, do_phonon={dft.do_phonon}, "
            f"do_epw={dft.do_epw or dft.epw.enabled})"
        )
    else:
        console.print(f"[bold]Calculator[/bold] {calc_name}")

    # 4. Score Si-feasibility + evaluate
    evaluations: list[CandidateEvaluation] = []
    for cand in candidates:
        si = score_si_feasibility(cand)
        params = {**calc_params, "si_feasibility": si}
        try:
            result = calc.run(cand, **params)
        except Exception as exc:  # noqa: BLE001
            from siscforge.calculators.qe import QENotAvailableError

            if isinstance(exc, QENotAvailableError):
                console.print(f"[red]QE not available:[/red]\n{exc}")
                raise typer.Exit(code=3) from exc
            console.print(f"[red]Calculator error for {cand.formula}:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        if not isinstance(result, CandidateEvaluation):
            raise typer.Exit(code=1)
        if result.si_feasibility is None or result.si_feasibility.version.endswith(
            "mock"
        ):
            result = result.model_copy(update={"si_feasibility": si})
        # Keep annotated candidate (hull proxy) on the evaluation
        if result.candidate.energy_above_hull_proxy is None:
            result = result.model_copy(update={"candidate": cand})
        evaluations.append(result)
        store.append_evaluation(result)

    # 5. Rank + persist ranked
    ranked = rank_evaluations(evaluations, config.ranking)
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
    table.add_column("Si", justify="right")
    table.add_column("Composite", justify="right", style="bold")
    table.add_column("Stable")
    table.add_column("Status")

    for ev in ranked:
        si = f"{ev.si_feasibility.total:.1f}" if ev.si_feasibility else "—"
        perf = f"{ev.performance_score:.1f}" if ev.performance_score is not None else "—"
        comp = f"{ev.composite_score:.1f}" if ev.composite_score is not None else "—"
        stable = "—"
        if ev.phonon is not None:
            stable = "yes" if ev.phonon.dynamically_stable else "NO"
        strain = "—"
        if ev.candidate.in_plane_strain is not None:
            strain = f"{ev.candidate.in_plane_strain:+.3f}"
        hull = ev.candidate.energy_above_hull_proxy
        hull_s = f"{hull:.3f}" if hull is not None else "—"
        table.add_row(
            str(ev.rank or "—"),
            ev.candidate.candidate_id[:8] + "…",
            ev.candidate.formula,
            ev.candidate.material_family,
            strain,
            hull_s,
            perf,
            si,
            comp,
            stable,
            ev.status,
        )
    console.print(table)


def run() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    run()
