"""Tests for the Silicon Feasibility scorer."""

from __future__ import annotations

import math

import pytest

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import EnumerationConfig
from siscforge.silicon.feasibility import (
    COMPONENT_WEIGHTS,
    SCORER_VERSION,
    evaluate_mismatch_options,
    score_si_feasibility,
)
from siscforge.structure.generator import generate_candidates, structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.structure.strain import lattice_mismatch_percent


def test_components_always_populated() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.392, 4.392, 4.392),
        lattice_angles=(90.0, 90.0, 90.0),
        substrate="Si(001)",
        in_plane_strain=0.0,
        metadata={"conventional_lattice_a": 4.392, "epitaxy_orientation": "auto"},
    )
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
    bsi = StructureCandidate(
        formula="Si0.9B0.1",
        material_family="b_doped_si",
        composition={"Si": 0.9, "B": 0.1},
        lattice_abc=(5.43, 5.43, 5.43),
        substrate="Si(001)",
        in_plane_strain=0.0,
    )
    nitride = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.392, 4.392, 4.392),
        substrate="Si(001)",
    )
    # B:Si should score at least as high on chemical + maturity dimensions
    s_bsi = score_si_feasibility(bsi)
    s_nit = score_si_feasibility(nitride)
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


def test_from_pymatgen_structure() -> None:
    s = build_binary_nitride("Ti")
    cand = structure_to_candidate(s, material_family="tm_nitride", substrate="Si(001)")
    score = score_si_feasibility(cand)
    assert score.total > 0
