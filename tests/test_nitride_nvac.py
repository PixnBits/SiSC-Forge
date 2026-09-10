"""Ordered rocksalt N-vacancy enumeration (nitride phonon-map diversity bet)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pymatgen.core import Structure

from siscforge.models.config import CampaignConfig, EnumerationConfig
from siscforge.structure import generate_candidates
from siscforge.structure.nitrides import (
    DEFAULT_NVAC_SUPERCELL,
    PATTERN_N_VAC_1,
    PATTERN_N_VAC_2,
    PATTERN_STOICHIOMETRIC_SC,
    build_n_vacancy_rocksalt,
    build_nitride_nvac_pattern,
    enumerate_n_vacancy_nitrides,
    nvac_structure_key,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_single_n_vacancy_composition() -> None:
    parent = build_n_vacancy_rocksalt("Nb", n_vacancies=0, supercell=(2, 2, 2))
    vac = build_n_vacancy_rocksalt("Nb", n_vacancies=1, supercell=(2, 2, 2))
    assert len(parent) == 16
    assert int(parent.composition["Nb"]) == 8
    assert int(parent.composition["N"]) == 8
    assert int(vac.composition["Nb"]) == 8
    assert int(vac.composition["N"]) == 7
    assert len(vac) == 15
    assert vac.composition.reduced_formula in {"Nb8N7", "NbN0.875"}


def test_double_n_vacancy_ordered_and_unique() -> None:
    vac2 = build_n_vacancy_rocksalt("Nb", n_vacancies=2, supercell=(2, 2, 2))
    assert int(vac2.composition["N"]) == 6
    assert int(vac2.composition["Nb"]) == 8
    # One representative per (metal, count, supercell) — not combinatorial.
    pairs = enumerate_n_vacancy_nitrides(
        metals=["Nb"],
        n_vacancies=[2],
        supercell=(2, 2, 2),
    )
    assert len(pairs) == 1
    assert pairs[0][1]["vacancy_fraction"] == pytest.approx(0.25)
    assert pairs[0][1]["delta"] == pytest.approx(0.25)


def test_enumerate_default_handful() -> None:
    pairs = enumerate_n_vacancy_nitrides()
    keys = [meta["structure_key"] for _, meta in pairs]
    assert len(keys) == len(set(keys))
    # Default: Nb+Zr × {1,2} → 4 cells
    assert len(pairs) == 4
    metals = {meta["metal"] for _, meta in pairs}
    assert metals == {"Nb", "Zr"}
    counts = {meta["n_vacancies"] for _, meta in pairs}
    assert counts == {1, 2}
    for _, meta in pairs:
        assert "hypothesis_soft_mode_healing" in meta
        assert "Hypothesis" in meta["hypothesis_soft_mode_healing"]


def test_include_stoichiometric_control() -> None:
    pairs = enumerate_n_vacancy_nitrides(
        metals=["Nb"],
        n_vacancies=[1],
        include_stoichiometric=True,
        supercell=DEFAULT_NVAC_SUPERCELL,
    )
    patterns = {meta["vacancy_pattern"] for _, meta in pairs}
    assert PATTERN_STOICHIOMETRIC_SC in patterns
    assert PATTERN_N_VAC_1 in patterns
    stoich = next(m for _, m in pairs if m["n_vacancies"] == 0)
    assert stoich["structure_key"] == nvac_structure_key("Nb", 0, DEFAULT_NVAC_SUPERCELL)


def test_generate_candidates_nvac_only() -> None:
    enum = EnumerationConfig(
        material_families=["tm_nitride"],
        metals=[],
        formulas=[],
        nitride_nvac_metals=["Nb"],
        nitride_nvac_counts=[1, 2],
        nitride_nvac_include_stoichiometric=True,
        strain_values=[0.0],
        substrates=["Si(001)"],
        max_candidates=10,
    )
    cands = generate_candidates(enum)
    assert len(cands) == 3  # δ=0,1/8,2/8
    ids = [c.candidate_id for c in cands]
    assert len(ids) == len(set(ids))
    for c in cands:
        assert c.material_family == "tm_nitride"
        assert c.structure_cif
        assert c.metadata.get("vacancy_pattern")
        restored = Structure.from_str(c.structure_cif, fmt="cif")
        assert len(restored) > 0


def test_nvac_feature_off_when_metals_empty() -> None:
    base = EnumerationConfig(
        material_families=["tm_nitride"],
        formulas=["NbN"],
        strain_values=[0.0],
        substrates=["Si(001)"],
        max_candidates=4,
    )
    with_empty_nvac = EnumerationConfig(
        material_families=["tm_nitride"],
        formulas=["NbN"],
        strain_values=[0.0],
        substrates=["Si(001)"],
        max_candidates=4,
        nitride_nvac_metals=[],
        nitride_nvac_counts=[1, 2],
    )
    a = generate_candidates(base)
    b = generate_candidates(with_empty_nvac)
    assert len(a) == len(b) == 1
    assert a[0].formula == b[0].formula


def test_example_yaml_loads_and_enumerates() -> None:
    path = EXAMPLES / "nbn_n_vacancy_phonon_map.yaml"
    assert path.is_file()
    cfg = CampaignConfig.from_yaml(path)
    assert cfg.dft.do_phonon is True
    assert cfg.dft.do_epw is False
    assert cfg.enumeration.nitride_nvac_metals == ["Nb", "Zr"]
    cands = generate_candidates(cfg.enumeration)
    # Nb×{1,2} + Zr×{1,2} = 4 bulk × 2 strains = 8
    assert len(cands) == 8
    formulas = {c.formula for c in cands}
    assert any("N7" in f or "N0.875" in f or "Nb8N7" in f for f in formulas) or any(
        c.metadata.get("n_vacancies") == 1 for c in cands
    )
    assert all(c.metadata.get("n_vacancies") is not None for c in cands)


def test_pattern_builder_delta() -> None:
    s, meta = build_nitride_nvac_pattern("Zr", PATTERN_N_VAC_2, supercell=(2, 2, 2))
    assert meta["vacancy_pattern"] == PATTERN_N_VAC_2
    assert meta["delta"] == pytest.approx(0.25)
    assert int(s.composition["Zr"]) == 8
    assert int(s.composition["N"]) == 6


def test_too_many_vacancies_raises() -> None:
    with pytest.raises(ValueError, match="Cannot remove"):
        build_n_vacancy_rocksalt("Nb", n_vacancies=9, supercell=(2, 2, 2))
