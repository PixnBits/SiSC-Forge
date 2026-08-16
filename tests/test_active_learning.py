"""Tests for Phase-1 active-learning prioritization (not a retrain loop)."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.active_learning import acquisition_score, prioritize_candidates
from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import (
    ActiveLearningConfig,
    ActiveLearningWeights,
    CampaignConfig,
)
from siscforge.models.results import SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.mgb2 import build_mgb2
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.surrogates.tc_lambda import predict_tc_lambda


def _cand(formula: str, family: str = "tm_nitride", strain: float = 0.0):
    if formula == "MgB2":
        s = build_mgb2()
        family = "mgb2_boride"
    elif formula == "TiN":
        s = build_binary_nitride("Ti")
    else:
        s = build_binary_nitride("Nb")
        formula = formula if formula != "NbN" else "NbN"
    return structure_to_candidate(
        s,
        material_family=family,  # type: ignore[arg-type]
        formula=formula,
        in_plane_strain=strain,
    )


def test_acquisition_score_increases_with_uncertainty() -> None:
    low, _ = acquisition_score(
        uncertainty=0.1, predicted_tc=15.0, si_total=50.0
    )
    high, _ = acquisition_score(
        uncertainty=0.9, predicted_tc=15.0, si_total=50.0
    )
    assert high > low


def test_acquisition_score_increases_with_tc_and_si() -> None:
    base, _ = acquisition_score(
        uncertainty=0.3, predicted_tc=10.0, si_total=40.0
    )
    hi_tc, _ = acquisition_score(
        uncertainty=0.3, predicted_tc=35.0, si_total=40.0
    )
    hi_si, _ = acquisition_score(
        uncertainty=0.3, predicted_tc=10.0, si_total=90.0
    )
    assert hi_tc > base
    assert hi_si > base


def test_default_hull_penalty_is_nontrivial() -> None:
    """#47: default hull_penalty must be visible and non-trivial."""
    assert ActiveLearningWeights().hull_penalty == pytest.approx(0.3)
    cfg = ActiveLearningConfig()
    assert cfg.weights.hull_penalty >= 0.25
    # Weights are copied onto every acquisition record.
    cands = [_cand("NbN"), _cand("TiN")]
    for c in cands:
        c.energy_above_hull_proxy = 0.2
    plan = prioritize_candidates(
        cands, config=ActiveLearningConfig(enabled=True, max_epw_jobs=1)
    )
    assert plan.ranked[0].weights["hull_penalty"] == pytest.approx(0.3)
    assert plan.ranked[0].components["hull_penalty"] > 0.0


def test_acquisition_record_surfaces_quality_flags() -> None:
    """#47: quality_flags / result_quality appear on prioritization records."""
    from siscforge.models.candidate import CandidateEvaluation
    from siscforge.models.results import ElectronPhononResult, PhononResult

    cand = _cand("NbN")
    ev = CandidateEvaluation(
        candidate=cand,
        electron_phonon=ElectronPhononResult(
            lambda_total=1.1,
            Tc_allen_dynes=16.0,
            status="ok",
            quality_tag="screening",
            quality_flags=["wannier_random_proj", "coarse_grids"],
        ),
        phonon=PhononResult(dynamically_stable=True, status="ok"),
        status="ok",
        result_quality="screening_suspect",
        quality_flags=["wannier_random_proj", "high_lambda"],
        performance_score=16.0,
        performance_score_source="epw",
    )
    plan = prioritize_candidates(
        [cand],
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=1),
        evaluations={cand.candidate_id: ev},
    )
    rec = plan.ranked[0]
    assert "wannier_random_proj" in rec.quality_flags
    assert rec.result_quality == "screening_suspect"


def test_hull_penalty_reduces_score() -> None:
    clean, _ = acquisition_score(
        uncertainty=0.4,
        predicted_tc=20.0,
        si_total=50.0,
        energy_above_hull=0.0,
        weights={
            "uncertainty": 0.3,
            "predicted_tc": 0.3,
            "si_feasibility": 0.3,
            "hull_penalty": 0.5,
        },
    )
    dirty, _ = acquisition_score(
        uncertainty=0.4,
        predicted_tc=20.0,
        si_total=50.0,
        energy_above_hull=0.25,
        weights={
            "uncertainty": 0.3,
            "predicted_tc": 0.3,
            "si_feasibility": 0.3,
            "hull_penalty": 0.5,
        },
    )
    assert dirty < clean


def test_prioritize_selects_top_k() -> None:
    cands = [
        _cand("NbN", strain=0.0),
        _cand("TiN", strain=0.0),
        _cand("MgB2"),
        _cand("NbN", strain=0.03),
    ]
    # Attach hull proxies so sorting is stable
    for c in cands:
        c.energy_above_hull_proxy = 0.02
    si = {c.candidate_id: score_si_feasibility(c) for c in cands}
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    cfg = ActiveLearningConfig(enabled=True, max_epw_jobs=2)
    plan = prioritize_candidates(
        cands, config=cfg, si_scores=si, predictions=preds
    )
    assert plan.enabled is True
    assert len(plan.selected) == 2
    assert len(plan.deferred) == 2
    assert len(plan.ranked) == 4
    assert plan.ranked[0].acquisition_score >= plan.ranked[-1].acquisition_score
    assert plan.ranked[0].selected_for_expensive is True
    assert plan.ranked[-1].selected_for_expensive is False


def test_prioritize_disabled_selects_all() -> None:
    cands = [_cand("NbN"), _cand("TiN")]
    cfg = ActiveLearningConfig(enabled=False)
    plan = prioritize_candidates(cands, config=cfg)
    assert len(plan.selected) == 2
    assert plan.deferred == []


def test_campaign_config_al_round_trip() -> None:
    cfg = CampaignConfig(
        name="al",
        active_learning=ActiveLearningConfig(
            enabled=True,
            max_epw_jobs=3,
            weights=ActiveLearningWeights(uncertainty=0.5, predicted_tc=0.25),
        ),
    )
    data = cfg.model_dump()
    restored = CampaignConfig.model_validate(data)
    assert restored.active_learning.enabled is True
    assert restored.active_learning.max_epw_jobs == 3
    assert restored.active_learning.weights.uncertainty == 0.5


def test_example_al_yaml_loads() -> None:
    cfg = CampaignConfig.from_yaml(Path("examples/nbti_n_al.yaml"))
    assert cfg.active_learning.enabled is True
    assert cfg.active_learning.max_epw_jobs == 5
    assert cfg.surrogate.tc_lambda.enabled is True


def test_example_al_broad_yaml_loads_and_enumerates() -> None:
    """Broader AL campaign: larger grid + Phase-2 Si flags + workstation top-k."""
    from siscforge.structure.generator import generate_candidates

    cfg = CampaignConfig.from_yaml(Path("examples/nbti_n_al_broad.yaml"))
    assert cfg.active_learning.enabled is True
    assert cfg.active_learning.max_epw_jobs == 6
    assert cfg.surrogate.tc_lambda.enabled is True
    assert cfg.formation_filter.enabled is True
    assert cfg.enumeration.epitaxy_orientation == "auto"
    assert cfg.enumeration.use_buffers is True
    assert set(cfg.enumeration.metals) >= {"Nb", "Ti", "Zr", "Hf"}
    # Broader than the toy AL example (~15): 4 binaries + 3 ternaries × 7 strains
    cands = generate_candidates(cfg)
    assert len(cands) >= 40
    assert len(cands) <= cfg.enumeration.max_candidates
    # Si-feasibility metadata for 45° / buffer scoring
    assert all(c.metadata.get("epitaxy_orientation") == "auto" for c in cands)
    si0 = score_si_feasibility(cands[0])
    assert si0.version == "0.5"

    assert 0.0 <= si0.total <= 100.0
    assert si0.weights  # P2.1 provenance

