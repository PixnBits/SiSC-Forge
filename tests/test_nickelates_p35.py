"""P3.5 — infinite-layer nickelate + oxygen-vacancy enumeration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from siscforge.calculators import get
from siscforge.export import export_campaign_bundle
from siscforge.models.config import CampaignConfig, EnumerationConfig
from siscforge.ranking import rank_evaluations
from siscforge.silicon.feasibility import score_si_feasibility
from siscforge.structure import generate_candidates
from siscforge.structure.nickelates import (
    DEFAULT_PATTERNS,
    INFINITE_LAYER_LATTICE,
    PATTERN_APICAL_O,
    PATTERN_INPLANE_VACANCY,
    PATTERN_STOICHIOMETRIC,
    build_apical_oxygen,
    build_infinite_layer,
    build_inplane_vacancy,
    build_nickelate_pattern,
    enumerate_nickelates,
    structure_from_nickelate_formula,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_infinite_layer_ndnio2_matches_golden() -> None:
    s = build_infinite_layer("Nd")
    assert isinstance(s, Structure)
    assert len(s) == 4
    assert s.composition.reduced_formula == "NdNiO2"
    a, c = INFINITE_LAYER_LATTICE["Nd"]
    assert s.lattice.a == pytest.approx(a, rel=1e-6)
    assert s.lattice.c == pytest.approx(c, rel=1e-6)
    assert s.lattice.alpha == pytest.approx(90.0)
    assert 6.5 < s.density < 8.5
    sga = SpacegroupAnalyzer(s, symprec=0.1)
    assert sga.get_space_group_symbol() == "P4/mmm"
    assert sga.get_space_group_number() == 123


def test_inplane_vacancy_composition_shift() -> None:
    parent = build_infinite_layer("Nd")
    vac = build_inplane_vacancy("Nd", supercell=(2, 2, 1))
    n_parent_o = int((parent * (2, 2, 1)).composition["O"])
    assert n_parent_o == 8
    assert int(vac.composition["O"]) == 7
    assert int(vac.composition["Nd"]) == 4
    assert int(vac.composition["Ni"]) == 4
    assert vac.composition.reduced_formula in {"Nd4Ni4O7", "NdNiO1.75"}
    # One unique representative — 8 in-plane O are equivalent in P4/mmm.
    pairs = enumerate_nickelates(
        rare_earths=["Nd"],
        patterns=[PATTERN_INPLANE_VACANCY],
        supercell=(2, 2, 1),
    )
    assert len(pairs) == 1


def test_apical_o_adds_one_oxygen() -> None:
    il = build_infinite_layer("Pr")
    peri = build_apical_oxygen("Pr")
    assert int(il.composition["O"]) == 2
    assert int(peri.composition["O"]) == 3
    assert peri.composition.reduced_formula == "PrNiO3"
    assert len(peri) == 5


def test_enumerate_default_has_stoich_and_vacancy() -> None:
    pairs = enumerate_nickelates()
    patterns = {meta["vacancy_pattern"] for _, meta in pairs}
    assert PATTERN_STOICHIOMETRIC in patterns
    assert PATTERN_INPLANE_VACANCY in patterns
    assert PATTERN_APICAL_O in patterns
    assert patterns <= set(DEFAULT_PATTERNS) | {PATTERN_APICAL_O}
    formulas = {meta["formula"] for _, meta in pairs}
    assert "NdNiO2" in formulas
    keys = [meta["structure_key"] for _, meta in pairs]
    assert len(keys) == len(set(keys))


def test_max_patterns_cap() -> None:
    pairs = enumerate_nickelates(rare_earths=["Nd"], max_patterns=1)
    assert len(pairs) == 1
    assert pairs[0][1]["vacancy_pattern"] == PATTERN_STOICHIOMETRIC


def test_unsupported_rare_earth() -> None:
    with pytest.raises(ValueError, match="Unsupported rare earth"):
        build_infinite_layer("Y")


def test_formula_rebuild() -> None:
    s, meta = structure_from_nickelate_formula("NdNiO2")
    assert s.composition.reduced_formula == "NdNiO2"
    assert meta["vacancy_pattern"] == PATTERN_STOICHIOMETRIC
    s3, meta3 = structure_from_nickelate_formula("LaNiO3")
    assert s3.composition.reduced_formula == "LaNiO3"
    assert meta3["vacancy_pattern"] == PATTERN_APICAL_O


def test_generate_candidates_unique_ids_and_family() -> None:
    enum = EnumerationConfig(
        material_families=["nickelate"],
        nickelate_rare_earths=["Nd"],
        nickelate_patterns=["stoichiometric", "inplane_vacancy", "apical_o"],
        strain_values=[0.0, -0.01],
        substrates=["Si(001)"],
        max_candidates=20,
    )
    cands = generate_candidates(enum)
    assert len(cands) == 6  # 3 patterns × 2 strains
    ids = [c.candidate_id for c in cands]
    assert len(ids) == len(set(ids))
    for c in cands:
        assert c.material_family == "nickelate"
        assert c.structure_cif
        assert c.lattice_abc is not None
        assert c.strain_tensor is not None
        assert c.metadata.get("vacancy_pattern")
        assert c.metadata.get("structure_key")
        restored = Structure.from_str(c.structure_cif, fmt="cif")
        assert len(restored) > 0
        assert restored.lattice.a == pytest.approx(c.lattice_abc[0], rel=1e-4)
    stoich = [c for c in cands if c.metadata["vacancy_pattern"] == PATTERN_STOICHIOMETRIC]
    vac = [c for c in cands if c.metadata["vacancy_pattern"] == PATTERN_INPLANE_VACANCY]
    assert stoich and vac
    assert any(abs(float(c.in_plane_strain or 0.0)) > 0 for c in cands)


def test_strain_scales_nickelate_lattice() -> None:
    enum = EnumerationConfig(
        material_families=["nickelate"],
        nickelate_rare_earths=["Nd"],
        nickelate_patterns=["stoichiometric"],
        strain_values=[0.0, 0.02],
        substrates=["Si(001)"],
        max_candidates=4,
    )
    cands = generate_candidates(enum)
    by_eps = {round(float(c.in_plane_strain or 0.0), 6): c for c in cands}
    assert 0.0 in by_eps and 0.02 in by_eps
    a0 = by_eps[0.0].lattice_abc[0]
    a1 = by_eps[0.02].lattice_abc[0]
    assert a1 == pytest.approx(a0 * 1.02, rel=1e-6)


def test_non_si_substrate_does_not_crash() -> None:
    enum = EnumerationConfig(
        material_families=["nickelate"],
        nickelate_rare_earths=["Nd"],
        nickelate_patterns=["stoichiometric"],
        substrates=["SrTiO3"],
        strain_values=[0.0],
        max_candidates=2,
    )
    cands = generate_candidates(enum)
    assert len(cands) == 1
    assert cands[0].substrate == "SrTiO3"
    assert cands[0].structure_cif


def test_feature_off_nitride_enumeration_unchanged() -> None:
    base = EnumerationConfig(
        material_families=["tm_nitride"],
        formulas=["NbN", "TiN"],
        strain_values=[0.0, -0.01],
        substrates=["Si(001)"],
        max_candidates=10,
    )
    with_knobs = EnumerationConfig(
        material_families=["tm_nitride"],
        formulas=["NbN", "TiN"],
        strain_values=[0.0, -0.01],
        substrates=["Si(001)"],
        max_candidates=10,
        nickelate_rare_earths=["Nd", "Pr", "La"],
        nickelate_patterns=["stoichiometric", "apical_o", "inplane_vacancy"],
        nickelate_max_patterns=8,
    )
    a = generate_candidates(base)
    b = generate_candidates(with_knobs)
    assert len(a) == len(b) == 4
    assert [c.formula for c in a] == [c.formula for c in b]
    assert all(c.material_family == "tm_nitride" for c in a + b)
    assert all(c.metadata.get("vacancy_pattern") is None for c in b)


def test_mgb2_unchanged_when_nickelate_knobs_present() -> None:
    enum = EnumerationConfig(
        material_families=["mgb2_boride"],
        formulas=["MgB2"],
        strain_values=[0.0],
        nickelate_rare_earths=["Nd"],
        nickelate_patterns=["stoichiometric"],
        max_candidates=4,
    )
    cands = generate_candidates(enum)
    assert len(cands) == 1
    assert cands[0].material_family == "mgb2_boride"
    assert cands[0].formula in {"MgB2", "B2Mg"}


def test_config_defaults_keep_nickelate_off() -> None:
    cfg = CampaignConfig(name="default_off")
    assert cfg.enumeration.material_families == ["tm_nitride"]
    assert cfg.enumeration.nickelate_rare_earths == []
    assert cfg.enumeration.nickelate_patterns == []
    cands = generate_candidates(cfg, n=3)
    assert all(c.material_family == "tm_nitride" for c in cands)


def test_si_feasibility_does_not_block() -> None:
    cands = generate_candidates(
        EnumerationConfig(
            material_families=["nickelate"],
            nickelate_rare_earths=["Nd"],
            nickelate_patterns=["stoichiometric", "inplane_vacancy"],
            strain_values=[0.0],
            max_candidates=4,
        )
    )
    for c in cands:
        score = score_si_feasibility(c)
        assert 0.0 <= score.total <= 100.0


def test_dry_run_rank_export(tmp_path: Path) -> None:
    cfg = CampaignConfig(
        name="p35_rank_export",
        dry_run=True,
        enumeration=EnumerationConfig(
            material_families=["nickelate"],
            nickelate_rare_earths=["Nd"],
            nickelate_patterns=["stoichiometric", "inplane_vacancy"],
            strain_values=[0.0],
            max_candidates=4,
        ),
        formation_filter={"enabled": False},
        output_dir=str(tmp_path / "out"),
    )
    cands = generate_candidates(cfg)
    assert len(cands) >= 2
    calc = get("mock")
    evaluations = [calc.run(c) for c in cands]
    ranked = rank_evaluations(evaluations, config=cfg.ranking)
    assert all(ev.rank is not None for ev in ranked)
    assert all(ev.composite_score is not None for ev in ranked)
    written = export_campaign_bundle(
        ranked,
        tmp_path / "out",
        formats=["json", "csv", "markdown"],
        campaign_name=cfg.name,
        candidates=cands,
    )
    assert written["evaluations_json"].is_file()
    payload = json.loads(written["evaluations_json"].read_text())
    assert len(payload) == len(ranked)
    families = {row["candidate"]["material_family"] for row in payload}
    assert families == {"nickelate"}


def test_example_yaml_enumerates() -> None:
    path = EXAMPLES / "ndnio2_ovac_enumerate.yaml"
    assert path.is_file()
    cfg = CampaignConfig.from_yaml(path)
    assert "nickelate" in cfg.enumeration.material_families
    cands = generate_candidates(cfg)
    patterns = {c.metadata.get("vacancy_pattern") for c in cands}
    assert PATTERN_STOICHIOMETRIC in patterns
    assert PATTERN_INPLANE_VACANCY in patterns or PATTERN_APICAL_O in patterns
    assert all(c.material_family == "nickelate" for c in cands)
    assert all(c.structure_cif for c in cands)


def test_existing_ndnio2_cif_shortlist_still_works() -> None:
    path = EXAMPLES / "ndnio2_dftu_mock.yaml"
    cfg = CampaignConfig.from_yaml(path)
    cands = generate_candidates(cfg)
    assert len(cands) == 1
    assert cands[0].material_family == "nickelate"
    assert cands[0].formula == "NdNiO2"
    assert cands[0].source == "shortlist_cif"


def test_shortlist_rebuild_without_cif() -> None:
    enum = EnumerationConfig(
        candidate_specs=[
            {
                "formula": "NdNiO2",
                "material_family": "nickelate",
                "substrate": "Si(001)",
                "in_plane_strain": 0.0,
            }
        ],
        max_candidates=2,
    )
    cands = generate_candidates(enum)
    assert len(cands) == 1
    assert cands[0].material_family == "nickelate"
    assert cands[0].metadata.get("vacancy_pattern") == PATTERN_STOICHIOMETRIC
    assert cands[0].structure_cif


def test_pattern_metadata_provenance() -> None:
    _, meta = build_nickelate_pattern("Nd", PATTERN_INPLANE_VACANCY)
    assert meta["material_family"] == "nickelate"
    assert meta["screening_only"] is True
    assert meta["n_oxygen"] == 7
    assert meta["n_oxygen_parent"] == 8
    assert meta["vacancy_fraction"] == pytest.approx(0.125)
    dumped = json.dumps(meta)
    assert "inplane_vacancy" in dumped


def test_docs_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs" / "phase3-p35-oxygen-vacancy.md").read_text()
    assert "stoichiometric" in doc
    assert "inplane_vacancy" in doc
    assert "apical_o" in doc
    assert "defect formation" in doc.lower()
    assert "P3.6" in doc
    assert "material_families" in doc
