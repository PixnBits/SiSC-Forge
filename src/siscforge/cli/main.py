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
from siscforge.active_learning import prioritize_candidates
from siscforge.models.provenance import Provenance
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

    # 4. Evaluate expensive path + optional surrogate-only deferred set
    evaluations: list[CandidateEvaluation] = []

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

    for cand in expensive_candidates:
        si = si_by_id[cand.candidate_id]
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
        result = _finalize_eval(result, cand, selected=True)
        evaluations.append(result)
        store.append_evaluation(result)

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
                f"(acq would re-rank after real EPW)"
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

    # 5. Rank + persist ranked (real/mock EPW Tc dominates when present)
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
    table.add_column("Si", justify="right")
    table.add_column("Acq", justify="right")
    table.add_column("Composite", justify="right", style="bold")
    table.add_column("Stable")
    table.add_column("Status")

    for ev in ranked:
        si = f"{ev.si_feasibility.total:.1f}" if ev.si_feasibility else "—"
        perf = f"{ev.performance_score:.1f}" if ev.performance_score is not None else "—"
        comp = f"{ev.composite_score:.1f}" if ev.composite_score is not None else "—"
        acq = (
            f"{ev.acquisition_score:.3f}"
            if ev.acquisition_score is not None
            else "—"
        )
        stable = "—"
        if ev.phonon is not None:
            stable = "yes" if ev.phonon.dynamically_stable else "NO"
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
            si,
            acq,
            comp,
            stable,
            status,
        )
    console.print(table)


def run() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    run()
