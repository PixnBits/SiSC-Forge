"""Tests for the mock calculator and registry."""

from __future__ import annotations

from siscforge.calculators import MockCalculator, get, list_calculators
from siscforge.calculators.base import Calculator
from siscforge.models import CandidateEvaluation, StructureCandidate
from siscforge.ranking import rank_evaluations


def test_mock_is_registered() -> None:
    assert "mock" in list_calculators()
    calc = get("mock")
    assert calc.name == "mock"


def test_mock_satisfies_protocol() -> None:
    calc = MockCalculator()
    assert isinstance(calc, Calculator)


def test_mock_returns_valid_evaluation() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        substrate="Si(001)",
        in_plane_strain=0.0,
    )
    calc = MockCalculator()
    result = calc.run(cand)
    assert isinstance(result, CandidateEvaluation)
    assert result.status == "mock"
    assert result.calculator_name == "mock"
    assert result.scf is not None
    assert result.scf.status == "mock"
    assert result.phonon is not None
    assert result.phonon.status == "mock"
    assert result.si_feasibility is not None
    assert 0.0 <= result.si_feasibility.total <= 100.0
    assert result.performance_score is not None
    assert result.composite_score is not None

    # Full model round-trip
    restored = CandidateEvaluation.model_validate(result.model_dump(mode="json"))
    assert restored.candidate.formula == "NbN"


def test_mock_is_deterministic_for_same_id() -> None:
    cand = StructureCandidate(
        candidate_id="fixed-id-0001",
        formula="TiN",
        material_family="tm_nitride",
    )
    calc = MockCalculator()
    a = calc.run(cand)
    b = calc.run(cand)
    assert a.performance_score == b.performance_score
    assert a.scf is not None and b.scf is not None
    assert a.scf.total_energy_eV == b.scf.total_energy_eV
    assert a.si_feasibility is not None and b.si_feasibility is not None
    assert a.si_feasibility.total == b.si_feasibility.total


def test_rank_after_mock() -> None:
    calc = MockCalculator()
    formulas = ["NbN", "TiN", "ZrN"]
    evaluations = [
        calc.run(
            StructureCandidate(
                formula=f,
                material_family="tm_nitride",
                candidate_id=f"id-{f}",
            )
        )
        for f in formulas
    ]
    ranked = rank_evaluations(evaluations)
    assert len(ranked) == 3
    assert [e.rank for e in ranked] == [1, 2, 3]
    scores = [e.composite_score for e in ranked]
    assert scores == sorted(scores, reverse=True)
