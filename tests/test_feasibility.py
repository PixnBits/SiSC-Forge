"""Tests for the Silicon Feasibility scorer (P2.1 first-class weights)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import (
    CampaignConfig,
    SiFeasibilityConfig,
    SiFeasibilityWeights,
)
from siscforge.silicon.feasibility import (
    COMPONENT_KEYS,
    COMPONENT_WEIGHTS,
    SCORER_VERSION,
    normalize_component_weights,
    rank_by_si_feasibility,
    score_si_feasibility,
)


def _nbn_candidate(**meta_extra: object) -> StructureCandidate:
    meta = {
        "conventional_lattice_a": 4.392,
        "epitaxy_orientation": "auto",
        "use_buffers": True,
    }
    meta.update(meta_extra)
    return StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.392, 4.392, 4.392),
        lattice_angles=(90.0, 90.0, 90.0),
        substrate="Si(001)",
        in_plane_strain=0.0,
        metadata=meta,
    )


def _bsi_candidate() -> StructureCandidate:
    return StructureCandidate(
        formula="Si0.9B0.1",
        material_family="b_doped_si",
        composition={"Si": 0.9, "B": 0.1},
        lattice_abc=(5.43, 5.43, 5.43),
        substrate="Si(001)",
        in_plane_strain=0.0,
    )


def test_scorer_version_is_p21() -> None:
    assert SCORER_VERSION == "0.3"
    assert score_si_feasibility(_nbn_candidate()).version == "0.3"


def test_default_weights_match_component_weights_constant() -> None:
    assert COMPONENT_WEIGHTS == {
        "lattice_mismatch": 0.35,
        "thermal_budget": 0.20,
        "chemical_compatibility": 0.20,
        "buffer_availability": 0.10,
        "process_maturity": 0.15,
    }
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9
    cfg_w = SiFeasibilityWeights().as_dict()
    assert cfg_w == COMPONENT_WEIGHTS


def test_components_always_populated() -> None:
    score = score_si_feasibility(_nbn_candidate())
    assert 0.0 <= score.total <= 100.0
    assert set(score.weights) == set(COMPONENT_KEYS)
    assert abs(sum(score.weights.values()) - 1.0) < 1e-6


def test_normalize_component_weights_zero_sum_and_nonfinite() -> None:
    zeros = normalize_component_weights({k: 0.0 for k in COMPONENT_KEYS})
    assert zeros == COMPONENT_WEIGHTS
    # Non-finite falls back
    nan_w = normalize_component_weights({"lattice_mismatch": float("nan")})
    assert nan_w == COMPONENT_WEIGHTS
    inf_w = normalize_component_weights({"thermal_budget": float("inf")})
    assert inf_w == COMPONENT_WEIGHTS


def test_weight_override_reorders_candidates() -> None:
    nbn = _nbn_candidate()
    bsi = _bsi_candidate()
    default_order = rank_by_si_feasibility([nbn, bsi])
    assert default_order[0][0].formula == "Si0.9B0.1"
    maturity_heavy = {
        "lattice_mismatch": 0.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 0.0,
        "process_maturity": 1.0,
    }
    maturity_order = rank_by_si_feasibility([nbn, bsi], weights=maturity_heavy)
    assert maturity_order[0][0].formula == "NbN"


def test_yaml_config_weights() -> None:
    cfg = CampaignConfig(
        name="si_maturity",
        si_feasibility={
            "weights": {
                "lattice_mismatch": 0.0,
                "thermal_budget": 0.0,
                "chemical_compatibility": 0.0,
                "buffer_availability": 0.0,
                "process_maturity": 1.0,
            }
        },
    )
    ranked = rank_by_si_feasibility([_nbn_candidate(), _bsi_candidate()], config=cfg.si_feasibility)
    assert ranked[0][0].formula == "NbN"


def test_exact_weights_provenance() -> None:
    score = score_si_feasibility(_nbn_candidate())
    assert abs(sum(score.weights.values()) - 1.0) < 1e-12
    # No independent rounding drift
    for v in score.weights.values():
        assert isinstance(v, float)
