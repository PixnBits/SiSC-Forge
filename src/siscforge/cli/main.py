"""SiSC-Forge CLI entry point (``siscforge``).

Phase 0 subcommands:
  - ``enumerate`` — generate structure candidates (nitrides / B:Si + strain)
  - ``rank``      — rank existing evaluation JSON
  - ``run``       — load campaign, evaluate (dry-run/mock), rank, export
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
    write_candidates_json,
    write_evaluations_csv,
    write_evaluations_json,
)
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import CampaignConfig
from siscforge.ranking import rank_evaluations
from siscforge.silicon.feasibility import score_si_feasibility
from siscforge.structure.generator import generate_candidates, generate_fake_candidates

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
        )

    candidates = generate_candidates(config, n=n)
    table = Table(title=f"Enumerated candidates ({len(candidates)})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Formula")
    table.add_column("Family")
    table.add_column("Substrate")
    table.add_column("Strain")
    table.add_column("a (Å)", justify="right")
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
        ]
        if score_si:
            si = score_si_feasibility(c)
            row.append(f"{si.total:.1f}")
        table.add_row(*row)
    console.print(table)

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
        help="JSON file of CandidateEvaluation objects (list).",
        exists=True,
        dir_okay=False,
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
) -> None:
    """Rank evaluation records from a JSON file and print a table."""
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
        help="Calculator name: mock | qe | quantum-espresso. "
        "Ignored when --dry-run is set.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Override campaign output directory.",
    ),
) -> None:
    """Load a campaign, evaluate candidates, rank, and export results."""
    config = CampaignConfig.from_yaml(campaign)
    if dry_run:
        config = config.model_copy(update={"dry_run": True})

    out = Path(output_dir) if output_dir is not None else Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Enumerate real structures
    candidates = generate_candidates(config)
    console.print(
        f"[bold]Campaign[/bold] {config.name}: {len(candidates)} structure candidates"
    )

    # 2. Select calculator
    calc_name = _resolve_calculator_name(
        config, dry_run=dry_run, calculator=calculator
    )
    # Normalize aliases
    if calc_name in {"quantum-espresso", "quantum_espresso", "espresso"}:
        calc_name = "qe"

    ensure_builtins_loaded()
    try:
        calc = get_calculator(calc_name)
    except KeyError as exc:
        console.print(f"[red]Unknown calculator:[/red] {calc_name}")
        console.print(f"Registered: {', '.join(list_calculators())}")
        raise typer.Exit(code=2) from exc

    calc_params: dict = {}
    for c in config.calculators:
        if c.name in {calc_name, "qe", "quantum-espresso", "mock"}:
            if c.name == calc_name or (calc_name == "qe" and c.name in {"qe", "quantum-espresso"}):
                calc_params = dict(c.parameters)
                break

    # Inject campaign DFT settings for the QE calculator
    if calc_name == "qe":
        calc_params = {**calc_params, "dft": config.dft}
        if config.dft.work_dir is None:
            calc_params.setdefault("work_dir", str(out / "qe_work"))
        console.print(
            f"[bold]Calculator[/bold] qe  "
            f"(pseudo_dir={config.dft.pseudo_dir!r}, "
            f"do_relax={config.dft.do_relax}, do_phonon={config.dft.do_phonon})"
        )
    else:
        console.print(f"[bold]Calculator[/bold] {calc_name}")

    # 3. Score Si-feasibility + evaluate
    evaluations: list[CandidateEvaluation] = []
    for cand in candidates:
        si = score_si_feasibility(cand)
        params = {**calc_params, "si_feasibility": si}
        try:
            result = calc.run(cand, **params)
        except Exception as exc:  # noqa: BLE001 — surface QENotAvailableError cleanly
            from siscforge.calculators.qe import QENotAvailableError

            if isinstance(exc, QENotAvailableError):
                console.print(f"[red]QE not available:[/red]\n{exc}")
                raise typer.Exit(code=3) from exc
            console.print(f"[red]Calculator error for {cand.formula}:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        if not isinstance(result, CandidateEvaluation):
            raise typer.Exit(code=1)
        # Ensure the real Si score is on the evaluation even if calculator ignores kwargs.
        if result.si_feasibility is None or result.si_feasibility.version.endswith("mock"):
            result = result.model_copy(update={"si_feasibility": si})
        evaluations.append(result)

    # 4. Rank
    ranked = rank_evaluations(evaluations, config.ranking)
    _print_rank_table(ranked, title=f"Ranked results — {config.name}")

    # 5. Export
    json_path = write_evaluations_json(ranked, out / "evaluations.json")
    console.print(f"[green]Wrote[/green] {json_path}")
    if "csv" in config.export_formats:
        csv_path = write_evaluations_csv(ranked, out / "evaluations.csv")
        console.print(f"[green]Wrote[/green] {csv_path}")
    write_candidates_json(candidates, out / "candidates.json")
    config.to_yaml(out / "campaign_resolved.yaml")
    console.print(f"[dim]Outputs in {out.resolve()}[/dim]")


def _print_rank_table(
    ranked: list[CandidateEvaluation],
    *,
    title: str = "Ranked candidates",
) -> None:
    table = Table(title=title)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Formula")
    table.add_column("Strain", justify="right")
    table.add_column("Perf", justify="right")
    table.add_column("Si-score", justify="right")
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
        table.add_row(
            str(ev.rank or "—"),
            ev.candidate.formula,
            strain,
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
