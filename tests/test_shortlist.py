"""Desktop shortlist campaign helper tests."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from siscforge.calculators import get
from siscforge.cli.main import app
from siscforge.models.config import CampaignConfig
from siscforge.shortlist import (
    build_shortlist_campaign,
    evaluation_to_spec,
    select_shortlist_evaluations,
    write_campaign_yaml,
)
from siscforge.store import EvaluationStore
from siscforge.structure.generator import generate_candidates
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.structure.generator import structure_to_candidate

runner = CliRunner()


def _seed_al_store(tmp_path: Path) -> Path:
    """Write a mini store with 3 mock evals, 2 AL-selected."""
    out = tmp_path / "al_store"
    store = EvaluationStore(out)
    formulas = [("Nb", "NbN", True), ("Ti", "TiN", True), ("Zr", "ZrN", False)]
    evals = []
    for i, (metal, formula, selected) in enumerate(formulas):
        cand = structure_to_candidate(
            build_binary_nitride(metal),
            material_family="tm_nitride",
            formula=formula,
            substrate="Si(001)",
            in_plane_strain=-0.01 * i,
        )
        ev = get("mock").run(cand)
        ev = ev.model_copy(
            update={
                "al_selected_for_expensive": selected,
                "acquisition_score": 0.5 - 0.05 * i,
                "rank": i + 1,
            }
        )
        store.append_evaluation(ev)
        evals.append(ev)
    return out


def test_select_al_selected(tmp_path: Path) -> None:
    store = EvaluationStore(_seed_al_store(tmp_path))
    evals = store.load_evaluations()
    chosen = select_shortlist_evaluations(evals, mode="al_selected", max_jobs=6)
    assert len(chosen) == 2
    assert all(e.al_selected_for_expensive for e in chosen)


def test_build_shortlist_campaign_and_enumerate(tmp_path: Path) -> None:
    store_dir = _seed_al_store(tmp_path)
    evals = EvaluationStore(store_dir).load_evaluations()
    cfg, chosen = build_shortlist_campaign(
        evals,
        name="test_short",
        source_store=str(store_dir),
        max_jobs=2,
        mode="al_selected",
        output_dir=str(tmp_path / "epw_out"),
        pseudo_dir="/tmp/fake_pseudo",
    )
    assert len(chosen) == 2
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.dft.do_epw is True
    assert cfg.dft.epw.enabled is True
    assert cfg.run.resume is True
    assert cfg.run.continue_on_error is True
    assert cfg.active_learning.enabled is False
    assert cfg.formation_filter.enabled is False

    cands = generate_candidates(cfg)
    assert len(cands) == 2
    # Preserved ids from store
    ids = {c.candidate_id for c in cands}
    assert ids == {e.candidate.candidate_id for e in chosen}


def test_write_and_cli_shortlist(tmp_path: Path) -> None:
    store_dir = _seed_al_store(tmp_path)
    yaml_path = tmp_path / "shortlist.yaml"
    result = runner.invoke(
        app,
        [
            "shortlist",
            str(store_dir),
            "-o",
            str(yaml_path),
            "-n",
            "2",
            "--name",
            "cli_short",
            "--campaign-output-dir",
            str(tmp_path / "cli_epw"),
            "--pseudo-dir",
            "/usr/share/espresso/pseudo",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert yaml_path.is_file()
    cfg = CampaignConfig.from_yaml(yaml_path)
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.output_dir == str(tmp_path / "cli_epw")

    # Dry-run shortlist
    r2 = runner.invoke(app, ["run", "--dry-run", str(yaml_path)])
    assert r2.exit_code == 0, r2.stdout + r2.stderr
    assert "Checkpoint summary" in r2.stdout
    store = EvaluationStore(tmp_path / "cli_epw")
    assert len(store.load_evaluations()) == 2


def test_require_real_does_not_skip_mock(tmp_path: Path) -> None:
    """qe-epw resume must not treat dry-run mock as finished."""
    from siscforge.resume import is_successful_evaluation

    cand = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
    )
    mock_ev = get("mock").run(cand)
    assert is_successful_evaluation(mock_ev) is True
    assert is_successful_evaluation(mock_ev, require_real=True) is False


def test_evaluation_to_spec_keeps_cif() -> None:
    cand = structure_to_candidate(
        build_binary_nitride("Ti"),
        material_family="tm_nitride",
        formula="TiN",
        in_plane_strain=-0.02,
    )
    ev = get("mock").run(cand)
    spec = evaluation_to_spec(ev)
    assert spec.formula == "TiN"
    assert spec.structure_cif
    assert spec.in_plane_strain == -0.02
