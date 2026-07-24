"""Tests for EvaluationStore and enriched exports."""

from __future__ import annotations

from pathlib import Path

from siscforge.calculators import get
from siscforge.export import (
    CSV_FIELDNAMES,
    write_evaluations_csv,
    write_synthesis_cards,
)
from siscforge.models.config import CampaignConfig, EnumerationConfig
from siscforge.ranking import rank_evaluations
from siscforge.store import EvaluationStore
from siscforge.structure.generator import generate_candidates
from siscforge.surrogates.formation import FormationEnergyFilter


def _tiny_evaluations():
    cands = FormationEnergyFilter().filter(
        generate_candidates(
            EnumerationConfig(formulas=["NbN", "TiN"], strain_values=[0.0], max_candidates=2)
        )
    ).kept
    calc = get("mock")
    return [calc.run(c) for c in cands]


def test_store_round_trip(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "camp")
    evals = _tiny_evaluations()
    store.save_evaluations(evals)
    loaded = store.load_evaluations()
    assert len(loaded) == len(evals)
    assert loaded[0].candidate.formula in {"NbN", "TiN", "NNb", "NTi"}

    ranked = rank_evaluations(loaded)
    store.save_evaluations(ranked, ranked=True)
    again = store.load_evaluations(ranked=True)
    assert again[0].rank == 1


def test_store_append(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "camp")
    evals = _tiny_evaluations()
    store.append_evaluation(evals[0])
    store.append_evaluation(evals[1])
    assert len(store.load_evaluations()) == 2
    # re-append same id replaces
    store.append_evaluation(evals[0])
    assert len(store.load_evaluations()) == 2


def test_csv_has_required_columns(tmp_path: Path) -> None:
    evals = rank_evaluations(_tiny_evaluations())
    path = write_evaluations_csv(evals, tmp_path / "out.csv")
    text = path.read_text()
    header = text.splitlines()[0]
    for col in (
        "candidate_id",
        "formula",
        "material_family",
        "si_feasibility_total",
        "si_lattice_mismatch",
        "status",
        "composition",
    ):
        assert col in header
    assert "rank" in CSV_FIELDNAMES


def test_synthesis_cards(tmp_path: Path) -> None:
    evals = rank_evaluations(_tiny_evaluations())
    path = write_synthesis_cards(evals, tmp_path / "cards.md", campaign_name="test")
    text = path.read_text()
    assert "Synthesis cards" in text
    assert "Silicon feasibility" in text
    assert "Phonon summary" in text


def test_campaign_config_formation_filter_yaml(tmp_path: Path) -> None:
    cfg = CampaignConfig(
        name="f",
        formation_filter={"enabled": True, "max_e_hull_eV_per_atom": 0.1},
    )
    p = tmp_path / "c.yaml"
    cfg.to_yaml(p)
    loaded = CampaignConfig.from_yaml(p)
    assert loaded.formation_filter.enabled is True
    assert loaded.formation_filter.max_e_hull_eV_per_atom == 0.1
