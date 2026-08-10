"""Tests for the Silicon Feasibility scorer (P2.1 first-class weights)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import (
    CampaignConfig,
    EnumerationConfig,
    SiFeasibilityConfig,
    SiFeasibilityWeights,
)
from siscforge.silicon.feasibility import (
    COMPONENT_KEYS,
    COMPONENT_WEIGHTS,
    SCORER_VERSION,
    evaluate_mismatch_options,
    normalize_component_weights,
    rank_by_si_feasibility,
    score_si_feasibility,
)
from siscforge.structure.generator import generate_candidates, structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.structure.strain import lattice_mismatch_percent


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


def _mgb2_candidate() -> StructureCandidate:
    return StructureCandidate(
        formula="MgB2",
        material_family="mgb2_boride",
        composition={"Mg": 1.0 / 3.0, "B": 2.0 / 3.0},
        lattice_abc=(3.086, 3.086, 3.521),
        lattice_angles=(90.0, 90.0, 120.0),
        substrate="Si(001)",
        in_plane_strain=0.0,
        metadata={"conventional_lattice_a": 3.086},
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


def test_components_always_populated() -> None:
    cand = _nbn_candidate()
    score = score_si_feasibility(cand)
    assert 0.0 <= score.total <= 100.0
    c = score.components
    for val in (
        c.lattice_mismatch,
        c.thermal_budget,
        c.chemical_compatibility,
        c.buffer_availability,
        c.process_maturity,
    ):
        assert 0.0 <= val <= 100.0
    assert score.lattice_mismatch_pct is not None
    assert score.recommended_buffers
    assert score.version == SCORER_VERSION
    # P2.1: weights always present and complete
    assert set(score.weights) == set(COMPONENT_KEYS)
    assert abs(sum(score.weights.values()) - 1.0) < 1e-6


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
    # Config defaults must match so YAML omission is a pure no-op
    cfg_w = SiFeasibilityWeights().as_dict()
    assert cfg_w == COMPONENT_WEIGHTS


def test_defaults_reproduce_known_system_scores() -> None:
    """Default weights must keep v0.2-era totals for known fixtures.

    Components are unchanged by P2.1; only provenance (weights, version) grew.
    Totals below were recorded under COMPONENT_WEIGHTS with the v0.2 heuristics.
    """
    nbn = score_si_feasibility(_nbn_candidate())
    mgb2 = score_si_feasibility(_mgb2_candidate())
    bsi = score_si_feasibility(_bsi_candidate())

    # Absolute totals (locked to default weight vector)
    assert nbn.total == pytest.approx(54.16, abs=0.05)
    assert mgb2.total == pytest.approx(35.80, abs=0.05)
    assert bsi.total == pytest.approx(84.34, abs=0.05)

    # Relative ordering: B:Si ≫ NbN ≫ MgB₂ under defaults
    assert bsi.total > nbn.total > mgb2.total

    # Every component always in [0, 100]
    for score in (nbn, mgb2, bsi):
        for key, val in score.components.as_dict().items():
            assert 0.0 <= val <= 100.0, key
        assert score.version == SCORER_VERSION
        assert abs(sum(score.weights.values()) - 1.0) < 1e-6


def test_explicit_weight_override_changes_total() -> None:
    cand = _nbn_candidate()
    default = score_si_feasibility(cand)
    # Emphasize lattice only → total tracks lattice_mismatch
    lattice_heavy = score_si_feasibility(
        cand,
        weights={
            "lattice_mismatch": 1.0,
            "thermal_budget": 0.0,
            "chemical_compatibility": 0.0,
            "buffer_availability": 0.0,
            "process_maturity": 0.0,
        },
    )
    assert lattice_heavy.total == pytest.approx(
        default.components.lattice_mismatch, abs=0.05
    )
    assert lattice_heavy.total != default.total
    assert lattice_heavy.weights["lattice_mismatch"] == pytest.approx(1.0)


def test_weight_override_reorders_candidates() -> None:
    """Changing YAML/config weights reorders a fixed set (P2.1 ranking path).

    NbN has much better process_maturity than MgB₂; MgB₂ has poor lattice match.
    Defaults: NbN > MgB₂. Lattice-only weights: both poor, but NbN's 45° path
    still beats MgB₂. Maturity-only: NbN still wins. Chemical-only: NbN wins.
    To flip order we need a pair where the lower-default candidate wins on the
    emphasized dimension — use B:Si (near-perfect lattice) vs NbN (poor lattice).
    """
    nbn = _nbn_candidate()
    bsi = _bsi_candidate()

    default_order = rank_by_si_feasibility([nbn, bsi])
    assert default_order[0][0].formula == "Si0.9B0.1"

    # Emphasize only process_maturity: NbN (95) > B:Si (85) under maturity tables
    maturity_heavy = {
        "lattice_mismatch": 0.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 0.0,
        "process_maturity": 1.0,
    }
    maturity_order = rank_by_si_feasibility([nbn, bsi], weights=maturity_heavy)
    # Both have high maturity; NbN gets +5 nitride bonus → 95 vs 85
    assert maturity_order[0][0].formula == "NbN"
    assert maturity_order[0][1].total > maturity_order[1][1].total

    # Emphasize lattice only: B:Si should dominate again
    lattice_heavy = {
        "lattice_mismatch": 1.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 0.0,
        "process_maturity": 0.0,
    }
    lattice_order = rank_by_si_feasibility([nbn, bsi], weights=lattice_heavy)
    assert lattice_order[0][0].formula == "Si0.9B0.1"
    assert lattice_order[0][1].total > lattice_order[1][1].total


def test_yaml_config_weights_reorder_via_campaign() -> None:
    """End-to-end: CampaignConfig.si_feasibility.weights from YAML reorders."""
    nbn = _nbn_candidate()
    bsi = _bsi_candidate()

    default_cfg = CampaignConfig(name="si_default")
    default_ranked = rank_by_si_feasibility(
        [nbn, bsi], config=default_cfg.si_feasibility
    )
    assert default_ranked[0][0].formula == "Si0.9B0.1"

    maturity_cfg = CampaignConfig(
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
    maturity_ranked = rank_by_si_feasibility(
        [nbn, bsi], config=maturity_cfg.si_feasibility
    )
    assert maturity_ranked[0][0].formula == "NbN"
    # Totals differ from default-weight scores for the same candidates
    assert maturity_ranked[0][1].total != default_ranked[0][1].total


def test_yaml_round_trip_si_feasibility_block(tmp_path: Path) -> None:
    cfg = CampaignConfig(
        name="weights_demo",
        si_feasibility=SiFeasibilityConfig(
            weights=SiFeasibilityWeights(
                lattice_mismatch=0.5,
                thermal_budget=0.1,
                chemical_compatibility=0.1,
                buffer_availability=0.1,
                process_maturity=0.2,
            ),
            cmos_limit_c=400.0,
        ),
    )
    path = tmp_path / "camp.yaml"
    cfg.to_yaml(path)
    loaded = CampaignConfig.from_yaml(path)
    assert loaded.si_feasibility.weights.lattice_mismatch == 0.5
    assert loaded.si_feasibility.cmos_limit_c == 400.0

    # Raw YAML fragment shape that collaborators will write
    raw = {
        "name": "inline",
        "si_feasibility": {
            "weights": {
                "lattice_mismatch": 0.1,
                "thermal_budget": 0.1,
                "chemical_compatibility": 0.1,
                "buffer_availability": 0.1,
                "process_maturity": 0.6,
            }
        },
    }
    ypath = tmp_path / "inline.yaml"
    ypath.write_text(yaml.safe_dump(raw), encoding="utf-8")
    from_yaml = CampaignConfig.from_yaml(ypath)
    score = score_si_feasibility(_nbn_candidate(), config=from_yaml.si_feasibility)
    assert score.weights["process_maturity"] == pytest.approx(0.6)


def test_normalize_component_weights_partial_and_zero_sum() -> None:
    w = normalize_component_weights({"lattice_mismatch": 2.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["lattice_mismatch"] > COMPONENT_WEIGHTS["lattice_mismatch"]
    # All-zero cannot normalize: fall back to documented defaults (sum == 1)
    zeros = normalize_component_weights({k: 0.0 for k in COMPONENT_KEYS})
    assert zeros == COMPONENT_WEIGHTS
    assert abs(sum(zeros.values()) - 1.0) < 1e-9
    # Empty override dict keeps defaults
    empty = normalize_component_weights({})
    assert empty == pytest.approx(COMPONENT_WEIGHTS)
    # Config model with all-zero weights also falls back
    zero_cfg = SiFeasibilityConfig(
        weights=SiFeasibilityWeights(
            lattice_mismatch=0.0,
            thermal_budget=0.0,
            chemical_compatibility=0.0,
            buffer_availability=0.0,
            process_maturity=0.0,
        )
    )
    assert normalize_component_weights(zero_cfg) == COMPONENT_WEIGHTS
    score = score_si_feasibility(_nbn_candidate(), config=zero_cfg)
    assert abs(sum(score.weights.values()) - 1.0) < 1e-6
    assert score.total > 0.0


def test_45deg_mismatch_better_than_cube_on_cube_for_nbn() -> None:
    a = 4.392
    cube = lattice_mismatch_percent(a, "Si(001)", match="cube_on_cube")
    deg45 = lattice_mismatch_percent(a, "Si(001)", match="45deg")
    assert abs(deg45) < abs(cube)
    # 45° uses a*√2 vs a_Si
    expected = 100.0 * (5.4307 - a * math.sqrt(2)) / (a * math.sqrt(2))
    assert deg45 == pytest.approx(expected, rel=1e-4)


def test_auto_epitaxy_improves_nbn_si_score() -> None:
    base = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.392, 4.392, 4.392),
        substrate="Si(001)",
        metadata={
            "conventional_lattice_a": 4.392,
            "epitaxy_orientation": "cube_on_cube",
            "use_buffers": False,
        },
    )
    auto = base.model_copy(
        update={
            "metadata": {
                "conventional_lattice_a": 4.392,
                "epitaxy_orientation": "auto",
                "use_buffers": True,
            }
        }
    )
    s_cube = score_si_feasibility(base)
    s_auto = score_si_feasibility(auto)
    assert s_auto.components.lattice_mismatch >= s_cube.components.lattice_mismatch
    assert s_auto.total >= s_cube.total
    assert "45" in s_auto.notes or "buffer" in s_auto.notes.lower()


def test_mismatch_options_include_buffer() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        metadata={
            "conventional_lattice_a": 4.392,
            "epitaxy_orientation": "auto",
            "use_buffers": True,
        },
        substrate="Si(001)",
    )
    opts = evaluate_mismatch_options(cand)
    assert any("buffer" in o["path"] for o in opts)
    assert any(o["match"] == "45deg" for o in opts)


def test_weights_sum_to_one() -> None:
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


def test_bsi_scores_high() -> None:
    s_bsi = score_si_feasibility(_bsi_candidate())
    s_nit = score_si_feasibility(_nbn_candidate())
    # B:Si should score at least as high on chemical + maturity dimensions
    assert s_bsi.components.chemical_compatibility >= s_nit.components.chemical_compatibility


def test_score_on_generated_candidates() -> None:
    cands = generate_candidates(
        EnumerationConfig(
            formulas=["NbN"],
            strain_values=[0.0, 0.02],
            substrates=["Si(001)"],
            max_candidates=4,
        )
    )
    for c in cands:
        score = score_si_feasibility(c)
        assert 0.0 <= score.total <= 100.0
        assert score.components.lattice_mismatch >= 0.0
        assert score.weights


def test_from_pymatgen_structure() -> None:
    s = build_binary_nitride("Ti")
    cand = structure_to_candidate(s, material_family="tm_nitride", substrate="Si(001)")
    score = score_si_feasibility(cand)
    assert score.total > 0
    assert score.version == SCORER_VERSION
