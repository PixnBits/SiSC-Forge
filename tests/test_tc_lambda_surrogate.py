"""Tests for the Phase-1 λ/Tc surrogate stub."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import (
    CampaignConfig,
    SurrogateConfig,
    TcLambdaSurrogateConfig,
)
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.mgb2 import build_mgb2
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.surrogates.tc_lambda import (
    MODEL_VERSION,
    TcLambdaSurrogate,
    predict_tc_lambda,
)


def _nbn() -> StructureCandidate:
    return structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
    )


def _mgb2() -> StructureCandidate:
    return structure_to_candidate(
        build_mgb2(),
        material_family="mgb2_boride",
        formula="MgB2",
    )


def test_predict_nbn_higher_tc_than_b_si() -> None:
    nbn = predict_tc_lambda(_nbn())
    bsi = predict_tc_lambda(
        StructureCandidate(formula="Si0.9B0.1", material_family="b_doped_si")
    )
    assert nbn.predicted_Tc > bsi.predicted_Tc
    assert 0.0 <= nbn.uncertainty <= 1.0
    assert nbn.model_version == MODEL_VERSION
    assert nbn.quality_tag == "stub"
    assert nbn.predicted_lambda is not None and nbn.predicted_lambda > 0.5


def test_predict_mgb2_high_omega_log() -> None:
    pred = predict_tc_lambda(_mgb2())
    assert pred.predicted_omega_log is not None
    assert pred.predicted_omega_log >= 500.0
    assert pred.predicted_Tc > 20.0
    assert "stub" in pred.notes.lower() or "not trained" in pred.notes.lower()


def test_strain_demotes_tc() -> None:
    bulk = _nbn()
    strained = bulk.model_copy(update={"in_plane_strain": 0.04})
    p0 = predict_tc_lambda(bulk)
    p1 = predict_tc_lambda(strained)
    assert p1.predicted_Tc <= p0.predicted_Tc
    assert p1.uncertainty >= p0.uncertainty


def test_filter_keep_top_n() -> None:
    cands = [
        _nbn(),
        structure_to_candidate(
            build_binary_nitride("Ti"),
            material_family="tm_nitride",
            formula="TiN",
        ),
        _mgb2(),
    ]
    cfg = TcLambdaSurrogateConfig(enabled=True, keep_top_n=2)
    fres = TcLambdaSurrogate(cfg).filter(cands)
    assert fres.n_kept == 2
    assert fres.n_rejected == 1
    assert len(fres.predictions) == 3


def test_filter_disabled_keeps_all() -> None:
    cands = [_nbn(), _mgb2()]
    cfg = TcLambdaSurrogateConfig(enabled=False)
    fres = TcLambdaSurrogate(cfg).filter(cands)
    assert fres.n_kept == 2
    assert fres.n_rejected == 0
    # Still annotated
    assert "tc_lambda_surrogate" in fres.kept[0].metadata


def test_filter_min_tc() -> None:
    cands = [
        StructureCandidate(formula="Si0.95B0.05", material_family="b_doped_si"),
        _mgb2(),
    ]
    cfg = TcLambdaSurrogateConfig(enabled=True, min_predicted_tc_K=20.0)
    fres = TcLambdaSurrogate(cfg).filter(cands)
    assert any(c.formula == "MgB2" for c in fres.kept)
    assert fres.n_rejected >= 1


def test_campaign_config_surrogate_round_trip() -> None:
    cfg = CampaignConfig(
        name="t",
        surrogate=SurrogateConfig(
            tc_lambda=TcLambdaSurrogateConfig(enabled=True, keep_top_n=5)
        ),
    )
    data = cfg.model_dump()
    restored = CampaignConfig.model_validate(data)
    assert restored.surrogate.tc_lambda.enabled is True
    assert restored.surrogate.tc_lambda.keep_top_n == 5


def test_score_for_ranking_penalizes_uncertainty() -> None:
    p = predict_tc_lambda(_nbn())
    # Higher unc → lower ranking score than raw Tc
    assert p.score_for_ranking() <= p.predicted_Tc
    assert p.score_for_ranking() > 0


@pytest.mark.parametrize("path", ["examples/nbti_n_surrogate.yaml"])
def test_example_yaml_loads(path: str) -> None:
    cfg = CampaignConfig.from_yaml(Path(path))
    assert cfg.surrogate.tc_lambda.enabled is True
