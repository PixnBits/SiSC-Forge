"""Tests for the Silicon Feasibility scorer."""

from __future__ import annotations

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import EnumerationConfig
from siscforge.silicon.feasibility import COMPONENT_WEIGHTS, score_si_feasibility
from siscforge.structure.generator import generate_candidates, structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride


def test_components_always_populated() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.392, 4.392, 4.392),
        lattice_angles=(90.0, 90.0, 90.0),
        substrate="Si(001)",
        in_plane_strain=0.0,
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
    assert score.version == "0.1"


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
