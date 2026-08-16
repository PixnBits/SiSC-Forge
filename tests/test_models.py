"""Unit tests for core Pydantic v2 data models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from siscforge.models import (
    CampaignConfig,
    CandidateEvaluation,
    PhononResult,
    Provenance,
    SCFResult,
    SiFeasibilityComponents,
    SiFeasibilityScore,
    StructureCandidate,
)


def test_structure_candidate_round_trip() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.39, 4.39, 4.39),
        lattice_angles=(90.0, 90.0, 90.0),
        substrate="Si(001)",
        in_plane_strain=-0.01,
        tags=["epitaxial"],
    )
    data = cand.model_dump()
    restored = StructureCandidate.model_validate(data)
    assert restored.formula == "NbN"
    assert restored.composition["Nb"] == 0.5
    assert restored.candidate_id == cand.candidate_id


def test_structure_candidate_json_round_trip() -> None:
    cand = StructureCandidate(formula="TiN", material_family="tm_nitride")
    payload = cand.model_dump(mode="json")
    restored = StructureCandidate.model_validate(payload)
    assert restored.model_dump(mode="json") == payload


def test_formula_must_be_nonempty() -> None:
    with pytest.raises(ValidationError):
        StructureCandidate(formula="   ")


def test_si_feasibility_score_bounds() -> None:
    score = SiFeasibilityScore(
        total=72.5,
        components=SiFeasibilityComponents(
            lattice_mismatch=80.0,
            thermal_budget=70.0,
            chemical_compatibility=75.0,
            buffer_availability=60.0,
            process_maturity=78.0,
        ),
        recommended_buffers=["TiN_seed"],
    )
    assert 0 <= score.total <= 100
    data = score.model_dump()
    assert SiFeasibilityScore.model_validate(data).total == 72.5


def test_si_feasibility_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        SiFeasibilityScore(total=150.0)
    with pytest.raises(ValidationError):
        SiFeasibilityComponents(lattice_mismatch=-1.0)


def test_phonon_and_scf_round_trip() -> None:
    phonon = PhononResult(
        min_frequency_cm1=12.3,
        has_imaginary_modes=False,
        dynamically_stable=True,
        status="mock",
        quality_tag="mock",
    )
    scf = SCFResult(
        total_energy_eV=-123.4,
        energy_above_hull_eV_per_atom=0.02,
        is_metallic=True,
        status="mock",
        quality_tag="mock",
    )
    assert PhononResult.model_validate(phonon.model_dump()).min_frequency_cm1 == 12.3
    assert SCFResult.model_validate(scf.model_dump()).is_metallic is True


def test_candidate_evaluation_round_trip() -> None:
    cand = StructureCandidate(formula="NbN", material_family="tm_nitride")
    ev = CandidateEvaluation(
        candidate=cand,
        scf=SCFResult(status="mock", quality_tag="mock"),
        phonon=PhononResult(status="mock", quality_tag="mock", dynamically_stable=True),
        si_feasibility=SiFeasibilityScore(total=80.0),
        performance_score=15.0,
        composite_score=70.0,
        status="mock",
        calculator_name="mock",
        provenance=Provenance(source="test"),
    )
    restored = CandidateEvaluation.model_validate(ev.model_dump(mode="json"))
    assert restored.candidate.formula == "NbN"
    assert restored.si_feasibility is not None
    assert restored.si_feasibility.total == 80.0
    assert restored.status == "mock"


def test_campaign_config_from_dict_and_yaml_round_trip(tmp_path) -> None:
    cfg = CampaignConfig(
        name="test_campaign",
        dry_run=True,
        enumeration={"formulas": ["NbN", "TiN"], "max_candidates": 4},
        ranking={"performance_weight": 0.5, "si_feasibility_weight": 0.5},
    )
    path = tmp_path / "campaign.yaml"
    cfg.to_yaml(path)
    loaded = CampaignConfig.from_yaml(path)
    assert loaded.name == "test_campaign"
    assert loaded.dry_run is True
    assert loaded.enumeration.formulas == ["NbN", "TiN"]
    assert loaded.ranking.performance_weight == 0.5


def test_campaign_config_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        CampaignConfig(name="")


def test_provenance_defaults() -> None:
    p = Provenance()
    assert p.source == "siscforge"
    assert p.created_at is not None
    assert Provenance.model_validate(p.model_dump()).software == {}


def test_nbn_k12_diag_example_loads() -> None:
    """examples/nbn_k12_diag.yaml is a valid phonon-only twin of zrn_k12_diag."""
    root = Path(__file__).resolve().parents[1]
    nbn = CampaignConfig.from_yaml(root / "examples" / "nbn_k12_diag.yaml")
    zrn = CampaignConfig.from_yaml(root / "examples" / "zrn_k12_diag.yaml")
    assert nbn.enumeration.formulas == ["NbN"]
    assert nbn.enumeration.strain_values == [0.0]
    assert nbn.enumeration.max_candidates == 1
    assert nbn.dft.do_relax is True
    assert nbn.dft.do_phonon is True
    assert nbn.dft.do_epw is False
    assert nbn.dft.epw.enabled is False
    assert list(nbn.dft.kpoints) == [12, 12, 12]
    assert list(nbn.dft.qpoints) == [4, 4, 4]
    assert nbn.dft.ecutwfc == 60.0
    assert nbn.dft.ecutrho == 480.0
    assert nbn.dft.ph_niter == 150
    assert nbn.dft.quality_tag == "screening"
    assert nbn.dft.nproc == 16
    assert nbn.dft.pseudo_dir == "/usr/share/espresso/pseudo"
    assert nbn.run.resume is True
    assert nbn.run.force_rerun is False
    assert nbn.run.resume_qe_steps is True
    assert nbn.output_dir == "outputs/nbn_k12_diag"
    assert nbn.output_dir != zrn.output_dir
    assert list(nbn.dft.kpoints) == list(zrn.dft.kpoints)
    assert list(nbn.dft.qpoints) == list(zrn.dft.qpoints)
    assert nbn.dft.ecutwfc == zrn.dft.ecutwfc
    assert nbn.dft.do_epw is zrn.dft.do_epw
