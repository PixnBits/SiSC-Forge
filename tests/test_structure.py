"""Tests for structure generation, strain, and B:Si prototypes."""

from __future__ import annotations

import pytest
from pymatgen.core import Structure

from siscforge.models.config import CampaignConfig, EnumerationConfig
from siscforge.structure import (
    apply_biaxial_strain,
    apply_epitaxial_strain,
    build_b_doped_si,
    build_binary_nitride,
    build_ternary_nitride,
    generate_candidates,
    lattice_mismatch_percent,
    structure_to_candidate,
)
from siscforge.structure.nitrides import ROCKSALT_LATTICE_CONSTANTS, enumerate_nitrides


def test_binary_nitride_is_valid_structure() -> None:
    s = build_binary_nitride("Nb")
    assert isinstance(s, Structure)
    assert len(s) == 2
    assert s.composition.reduced_formula in {"NbN", "NNb"}
    # Primitive rocksalt: density ~8.4 g/cm³ (NOT simple-cubic a=4.39 with 2 atoms)
    assert s.density == pytest.approx(8.38, rel=5e-2)
    a_conv = ROCKSALT_LATTICE_CONSTANTS["Nb"]
    assert s.lattice.a == pytest.approx(a_conv / (2**0.5), rel=1e-3)
    # Conventional cell still available for epitaxy metrics
    conv = build_binary_nitride("Nb", conventional=True)
    assert len(conv) == 8
    assert conv.lattice.a == pytest.approx(a_conv, rel=1e-6)
    assert conv.density == pytest.approx(s.density, rel=1e-6)


def test_ternary_nitride_composition() -> None:
    s = build_ternary_nitride("Nb", "Ti", 0.5, supercell=(2, 2, 1), ordered=True)
    assert isinstance(s, Structure)
    metal_count = sum(1 for site in s if site.specie.symbol in {"Nb", "Ti"})
    n_nb = sum(1 for site in s if site.specie.symbol == "Nb")
    assert metal_count > 0
    assert n_nb == pytest.approx(0.5 * metal_count, abs=1)


def test_biaxial_strain_scales_lattice() -> None:
    bulk = build_binary_nitride("Ti")
    a0 = bulk.lattice.a
    c0 = bulk.lattice.c
    eps = 0.02
    strained, tensor = apply_biaxial_strain(bulk, eps, poisson_ratio=0.25)
    assert strained.lattice.a == pytest.approx(a0 * (1 + eps), rel=1e-9)
    assert strained.lattice.b == pytest.approx(a0 * (1 + eps), rel=1e-9)
    # Out-of-plane contracts for tensile in-plane strain (ν > 0)
    assert strained.lattice.c < c0
    assert tensor[0] == pytest.approx(eps)
    assert tensor[1] == pytest.approx(eps)
    assert tensor[2] < 0


def test_epitaxial_match_substrate_changes_a() -> None:
    bulk = build_binary_nitride("Nb")
    strained, tensor, eps = apply_epitaxial_strain(
        bulk, "Si(001)", match_substrate=True
    )
    # Matching Si(001) cube-on-cube pulls a toward 5.43 Å
    assert strained.lattice.a == pytest.approx(5.4307, rel=1e-3)
    assert abs(eps) > 0.1  # large for rocksalt nitrides
    assert tensor[0] == pytest.approx(eps)


def test_lattice_mismatch_sign() -> None:
    # Film smaller than Si → positive mismatch (substrate larger)
    m = lattice_mismatch_percent(4.392, "Si(001)")
    assert m > 0


def test_structure_to_candidate_round_trip_cif() -> None:
    s = build_binary_nitride("Zr")
    cand = structure_to_candidate(s, material_family="tm_nitride", substrate="Si(001)")
    assert cand.structure_cif
    assert cand.lattice_abc is not None
    restored = Structure.from_str(cand.structure_cif, fmt="cif")
    assert len(restored) == len(s)
    assert restored.lattice.a == pytest.approx(s.lattice.a, rel=1e-5)


def test_generate_candidates_from_formulas() -> None:
    cfg = CampaignConfig(
        name="t",
        enumeration=EnumerationConfig(
            formulas=["NbN", "TiN"],
            strain_values=[0.0, -0.01],
            substrates=["Si(001)"],
            max_candidates=10,
        ),
    )
    cands = generate_candidates(cfg)
    assert len(cands) == 4  # 2 formulas × 2 strains
    for c in cands:
        assert c.material_family == "tm_nitride"
        assert c.structure_cif
        assert c.lattice_abc is not None
        assert c.substrate == "Si(001)"
        assert c.strain_tensor is not None
        assert c.source == "structure_generator"


def test_generate_nbti_series() -> None:
    enum = EnumerationConfig(
        material_families=["tm_nitride"],
        metals=["Nb", "Ti"],
        ternary_metals=["Nb", "Ti"],
        x_values=[0.25, 0.5, 0.75],
        strain_values=[-0.02, 0.0, 0.02],
        substrates=["Si(001)"],
        supercell=[2, 2, 1],
        max_candidates=30,
    )
    cands = generate_candidates(enum)
    # 2 binaries + 3 ternaries = 5 bulks × 3 strains = 15
    assert len(cands) == 15
    formulas = {c.formula for c in cands}
    assert any("Nb" in f for f in formulas)
    assert any("Ti" in f for f in formulas)


def test_b_doped_si() -> None:
    s = build_b_doped_si(0.1, supercell=(2, 2, 2), seed=0)
    assert "B" in s.composition
    frac = s.composition["B"] / s.composition.num_atoms
    assert 0.05 < frac < 0.2


def test_enumerate_nitrides_formulas() -> None:
    pairs = enumerate_nitrides(formulas=["NbN", "Nb0.5Ti0.5N"])
    assert len(pairs) == 2
    assert all(isinstance(s, Structure) for s, _ in pairs)
