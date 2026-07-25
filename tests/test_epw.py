"""Phase 1 EPW / Allen–Dynes / ElectronPhononResult tests (mock-safe)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from siscforge.calculators import get, list_calculators
from siscforge.calculators.qe.eliashberg import allen_dynes_tc, isotropic_eliashberg_tc_from_moments
from siscforge.calculators.qe.env import epw_available
from siscforge.calculators.qe.epw_parser import parse_epw_output
from siscforge.calculators.qe.epw_recipes import electron_phonon_from_lambda_omega
from siscforge.calculators.qe.epw_references import (
    MGB2_FIXTURE_LAMBDA,
    MGB2_FIXTURE_MU_STAR,
    MGB2_FIXTURE_OMEGA_LOG_K,
    MGB2_LAMBDA_RANGE,
    MGB2_OMEGA_LOG_K_RANGE,
    MGB2_TC_K_RANGE,
    NBN_FIXTURE_LAMBDA,
    NBN_FIXTURE_MU_STAR,
    NBN_FIXTURE_OMEGA_LOG_K,
    NBN_LAMBDA_RANGE,
    NBN_OMEGA_LOG_K_RANGE,
    NBN_TC_K_RANGE,
)
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import DFTConfig, EPWConfig
from siscforge.models.results import ElectronPhononResult
from siscforge.ranking import rank_evaluations
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.mgb2 import build_mgb2
from siscforge.structure.nitrides import build_binary_nitride

FIXTURES = Path(__file__).parent / "fixtures" / "qe"


def test_qe_epw_registered() -> None:
    names = list_calculators()
    assert "qe-epw" in names
    assert "epw" in names
    assert get("qe-epw").name == "qe-epw"


def test_allen_dynes_known_range() -> None:
    # Classic strong-coupling-ish: λ=1.0, ω_log=300 K, μ*=0.1 → Tc ~ 15–20 K
    tc = allen_dynes_tc(1.0, 300.0, 0.1)
    assert 5.0 < tc < 40.0
    assert allen_dynes_tc(0.0, 300.0, 0.1) == 0.0
    assert allen_dynes_tc(0.2, 300.0, 0.25) == 0.0  # denom non-positive


def test_parse_epw_fixture() -> None:
    path = FIXTURES / "epw_nbn_snippet.out"
    eph = parse_epw_output(path, mu_star=0.1, quality_tag="screening")
    assert eph.lambda_total == pytest.approx(1.048, rel=1e-3)
    assert eph.omega_log is not None
    # 24.15 meV → K
    assert eph.omega_log == pytest.approx(24.15 * 11.6045, rel=1e-2)
    assert eph.Tc_allen_dynes is not None
    assert eph.Tc_allen_dynes > 5.0
    assert eph.converged is True
    assert eph.status == "ok"
    assert eph.best_tc_K() is not None


def test_nbn_fixture_moments_in_reference_range() -> None:
    eph = electron_phonon_from_lambda_omega(
        NBN_FIXTURE_LAMBDA,
        NBN_FIXTURE_OMEGA_LOG_K,
        mu_star=NBN_FIXTURE_MU_STAR,
    )
    assert NBN_LAMBDA_RANGE[0] <= eph.lambda_total <= NBN_LAMBDA_RANGE[1]
    assert NBN_OMEGA_LOG_K_RANGE[0] <= eph.omega_log <= NBN_OMEGA_LOG_K_RANGE[1]
    tc = eph.best_tc_K()
    assert tc is not None
    assert NBN_TC_K_RANGE[0] <= tc <= NBN_TC_K_RANGE[1]


def test_mock_fills_electron_phonon() -> None:
    s = build_binary_nitride("Nb")
    cand = structure_to_candidate(s, material_family="tm_nitride", formula="NbN")
    result = get("mock").run(cand)
    assert isinstance(result, CandidateEvaluation)
    assert result.electron_phonon is not None
    assert result.electron_phonon.status == "mock"
    assert result.performance_score is not None
    assert result.performance_score == pytest.approx(
        result.electron_phonon.Tc_allen_dynes, rel=1e-6
    )


def test_ranking_prefers_higher_tc() -> None:
    calc = get("mock")
    low = calc.run(
        structure_to_candidate(
            build_binary_nitride("Nb"),
            material_family="tm_nitride",
            formula="NbN",
        )
    )
    high = low.model_copy(
        update={
            "performance_score": 30.0,
            "electron_phonon": ElectronPhononResult(
                lambda_total=1.2,
                omega_log=300.0,
                Tc_allen_dynes=30.0,
                converged=True,
                status="mock",
                quality_tag="mock",
            ),
            "candidate": low.candidate.model_copy(
                update={"candidate_id": "high-tc-id"}
            ),
        }
    )
    low = low.model_copy(
        update={
            "performance_score": 5.0,
            "candidate": low.candidate.model_copy(
                update={"candidate_id": "low-tc-id"}
            ),
        }
    )
    ranked = rank_evaluations([low, high])
    assert ranked[0].candidate.candidate_id == "high-tc-id"
    assert ranked[0].performance_score == 30.0


def test_mgb2_structure() -> None:
    s = build_mgb2()
    assert len(s) == 3
    assert s.composition.reduced_formula in {"MgB2", "B2Mg"}
    assert s.lattice.a == pytest.approx(3.086, rel=1e-4)
    assert s.lattice.c == pytest.approx(3.524, rel=1e-4)
    # Sanity: experimental density ~2.6 g/cm³
    assert 2.3 < s.density < 2.9
    cand = structure_to_candidate(s, material_family="mgb2_boride", formula="MgB2")
    assert cand.material_family == "mgb2_boride"
    assert cand.structure_cif


def test_parse_epw_mgb2_fixture() -> None:
    path = FIXTURES / "epw_mgb2_snippet.out"
    eph = parse_epw_output(path, mu_star=0.1, quality_tag="screening")
    assert eph.lambda_total == pytest.approx(0.85, rel=1e-3)
    assert eph.omega_log is not None
    # 60.32 meV → K
    assert eph.omega_log == pytest.approx(60.32 * 11.6045, rel=1e-2)
    assert eph.Tc_allen_dynes is not None
    assert eph.Tc_allen_dynes == pytest.approx(36.50, rel=1e-3)
    assert eph.converged is True
    assert eph.status == "ok"
    assert MGB2_TC_K_RANGE[0] <= eph.best_tc_K() <= MGB2_TC_K_RANGE[1]


def test_mgb2_fixture_moments_in_reference_range() -> None:
    eph = electron_phonon_from_lambda_omega(
        MGB2_FIXTURE_LAMBDA,
        MGB2_FIXTURE_OMEGA_LOG_K,
        mu_star=MGB2_FIXTURE_MU_STAR,
    )
    assert MGB2_LAMBDA_RANGE[0] <= eph.lambda_total <= MGB2_LAMBDA_RANGE[1]
    assert MGB2_OMEGA_LOG_K_RANGE[0] <= eph.omega_log <= MGB2_OMEGA_LOG_K_RANGE[1]
    tc = eph.best_tc_K()
    assert tc is not None
    assert MGB2_TC_K_RANGE[0] <= tc <= MGB2_TC_K_RANGE[1]


def test_mock_mgb2_fills_electron_phonon() -> None:
    s = build_mgb2()
    cand = structure_to_candidate(s, material_family="mgb2_boride", formula="MgB2")
    result = get("mock").run(cand)
    assert isinstance(result, CandidateEvaluation)
    assert result.electron_phonon is not None
    assert result.electron_phonon.status == "mock"
    assert result.electron_phonon.lambda_total is not None
    assert result.electron_phonon.omega_log is not None
    # MgB2 mock uses high ω_log family defaults
    assert result.electron_phonon.omega_log >= 500.0
    assert result.performance_score is not None
    assert result.performance_score == pytest.approx(
        result.electron_phonon.Tc_allen_dynes, rel=1e-6
    )
    notes = (result.electron_phonon.alpha2F_summary or {}).get("material_notes", "")
    assert "two-gap" in str(notes).lower() or "isotropic" in str(notes).lower()


def test_epw_config_round_trip() -> None:
    cfg = DFTConfig(
        do_epw=True,
        epw=EPWConfig(enabled=True, nkf=[8, 8, 8], mu_star=0.12),
    )
    data = cfg.model_dump()
    restored = DFTConfig.model_validate(data)
    assert restored.do_epw is True
    assert restored.epw.nkf == [8, 8, 8]
    assert restored.epw.mu_star == 0.12


def test_build_epw_input_qe73_namelist() -> None:
    """EPW 5.x rejects integer bands_skipped and multi-assign grid lines."""
    from siscforge.calculators.qe.epw_inputs import build_epw_input

    s = build_binary_nitride("Nb")
    cfg = DFTConfig(do_epw=True, epw=EPWConfig(enabled=True, eliashberg=True))
    text = build_epw_input(cfg, prefix="test", outdir="./", dvscf_dir="./save", structure=s)
    assert "bands_skipped" not in text
    assert "nkf1 = 6, nkf2" not in text
    assert "nkf1" in text and "nkf2" in text and "nkf3" in text
    assert "phonselfen" in text
    assert "a2f" in text
    assert "amass(1)" in text
    assert "amass(2)" in text
    # QE type order: N (Z=7) before Nb (Z=41), not site order
    assert "amass(1)    = 14.007" in text
    assert "amass(2)    = 92.906" in text
    # Header records quality_tag for screening vs denser distinction
    assert "quality_tag=screening" in text
    # Namelist terminator
    assert "\n/" in text or text.rstrip().endswith("/")


def test_recommended_grids_and_diagnose() -> None:
    from siscforge.calculators.qe.epw_inputs import recommended_grids
    from siscforge.calculators.qe.epw_recipes import diagnose_epw_failure

    scr = recommended_grids("tm_nitride", "screening")
    dense = recommended_grids("tm_nitride", "workstation_dense")
    assert scr["quality_tag"] == "screening"
    assert dense["quality_tag"] == "production"
    assert dense["epw"]["nkf"][0] > scr["epw"]["nkf"][0]
    assert dense["qpoints"][0] >= scr["qpoints"][0]

    mg = recommended_grids("mgb2_boride", "workstation_dense")
    assert mg["epw"]["nkf"] == [16, 16, 12]

    diag = diagnose_epw_failure(
        "Error: cannot bracket Ef in efermig",
        work_dir="/tmp/nonexistent_epw_work",
        step_name="epw",
    )
    assert "cannot bracket" in diag.lower() or "fermi" in diag.lower()
    assert "quality_tag" in diag or "docs/examples" in diag


def test_build_epw_input_mgb2_amass() -> None:
    from siscforge.calculators.qe.epw_inputs import build_epw_input, epw_material_notes

    s = build_mgb2()
    cfg = DFTConfig(do_epw=True, epw=EPWConfig(enabled=True, nbndsub=8))
    text = build_epw_input(cfg, prefix="mgb2", outdir="./", dvscf_dir="./save", structure=s)
    # B (Z=5) before Mg (Z=12)
    assert "amass(1)    = 10.811" in text
    assert "amass(2)    = 24.305" in text
    assert "nbndsub     = 8" in text
    note = epw_material_notes(s)
    assert "two-gap" in note.lower() or "isotropic" in note.lower()


def test_build_epw_input_fermi_windows() -> None:
    from siscforge.calculators.qe.epw_inputs import build_epw_input
    from siscforge.calculators.qe.parser import parse_fermi_energy_eV

    s = build_binary_nitride("Nb")
    cfg = DFTConfig(do_epw=True, epw=EPWConfig(enabled=True))
    text = build_epw_input(
        cfg, prefix="t", outdir="./", dvscf_dir="./save", structure=s, fermi_eV=20.739
    )
    assert "efermi_read = .true." in text
    assert "fermi_energy = 20.739000" in text
    assert "dis_win_max = 32.7390" in text
    assert "dis_froz_max= 22.7390" in text
    # Windows must sit above old hard-coded 20 eV for NbN-like E_F
    assert "dis_win_max = 20.0" not in text

    sample = "     the Fermi energy is    20.7390 ev\n"
    assert parse_fermi_energy_eV(sample) == pytest.approx(20.739)


def test_build_nscf_epw_crystal_mesh() -> None:
    from siscforge.calculators.qe.inputs import (
        build_nscf_epw_input,
        uniform_crystal_kpoints,
    )

    pts = uniform_crystal_kpoints(2, 2, 2)
    assert len(pts) == 8
    assert abs(sum(p[3] for p in pts) - 1.0) < 1e-12

    s = build_binary_nitride("Nb")
    cfg = DFTConfig(
        pseudo_dir="/tmp/fake_pseudo",
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
        epw=EPWConfig(enabled=True, nkc=[2, 2, 2], nbndsub=8),
    )
    # Ensure fake pseudos exist for resolution
    Path("/tmp/fake_pseudo").mkdir(parents=True, exist_ok=True)
    (Path("/tmp/fake_pseudo") / "Nb.upf").touch()
    (Path("/tmp/fake_pseudo") / "N.upf").touch()
    text = build_nscf_epw_input(s, cfg, prefix="t", outdir="./", nk=(2, 2, 2))
    assert "calculation" in text.lower()
    assert "nscf" in text.lower()
    assert "K_POINTS crystal" in text
    assert "\n8\n" in text or text.count("0.00000000") >= 1
    assert "nbnd" in text.lower()


def test_isotropic_eliashberg_factor() -> None:
    tc0 = allen_dynes_tc(1.2, 300.0, 0.1)
    tc1 = isotropic_eliashberg_tc_from_moments(1.2, 300.0, 0.1, omega_2_K=400.0)
    assert tc1 >= tc0 * 0.9  # correction should not collapse Tc


@pytest.mark.skipif(
    os.environ.get("SISCFORGE_RUN_EPW") != "1",
    reason="Set SISCFORGE_RUN_EPW=1 for real EPW NbN regression",
)
@pytest.mark.skipif(not epw_available(), reason="epw.x not available")
def test_nbn_real_epw_optional(tmp_path: Path) -> None:
    """Optional real EPW on bulk NbN (screening grids)."""
    pseudo = os.environ.get("SISCFORGE_PSEUDO_DIR")
    if not pseudo:
        pytest.skip("SISCFORGE_PSEUDO_DIR not set")

    cand = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
    )
    calc = get("qe-epw")
    dft = DFTConfig(
        engine="qe-epw",
        ecutwfc=40.0,
        ecutrho=320.0,
        kpoints=[2, 2, 2],
        qpoints=[1, 1, 1],
        pseudo_dir=pseudo,
        do_relax=False,
        do_phonon=True,
        do_epw=True,
        phonon_method="gamma",
        epw=EPWConfig(enabled=True, nkf=[4, 4, 4], nqf=[4, 4, 4], mu_star=0.1),
        quality_tag="screening",
        work_dir=str(tmp_path / "epw_nbn"),
    )
    result = calc.run(cand, dft=dft, work_dir=str(tmp_path / "epw_nbn"))
    assert result.electron_phonon is not None
    assert result.electron_phonon.lambda_total is not None
    assert result.performance_score is not None
    lo, hi = NBN_TC_K_RANGE
    assert lo * 0.5 <= result.performance_score <= hi * 1.5


@pytest.mark.skipif(
    os.environ.get("SISCFORGE_RUN_EPW") != "1",
    reason="Set SISCFORGE_RUN_EPW=1 for real EPW MgB2 regression",
)
@pytest.mark.skipif(not epw_available(), reason="epw.x not available")
def test_mgb2_real_epw_optional(tmp_path: Path) -> None:
    """Optional real EPW on bulk MgB2 (screening grids; isotropic average)."""
    pseudo = os.environ.get("SISCFORGE_PSEUDO_DIR")
    if not pseudo:
        pytest.skip("SISCFORGE_PSEUDO_DIR not set")

    cand = structure_to_candidate(
        build_mgb2(),
        material_family="mgb2_boride",
        formula="MgB2",
    )
    calc = get("qe-epw")
    dft = DFTConfig(
        engine="qe-epw",
        ecutwfc=40.0,
        ecutrho=320.0,
        kpoints=[2, 2, 2],
        qpoints=[1, 1, 1],
        pseudo_dir=pseudo,
        do_relax=False,
        do_phonon=True,
        do_epw=True,
        phonon_method="gamma",
        epw=EPWConfig(
            enabled=True,
            nkf=[4, 4, 2],
            nqf=[4, 4, 2],
            nkc=[2, 2, 1],
            nqc=[1, 1, 1],
            mu_star=0.1,
        ),
        quality_tag="screening",
        work_dir=str(tmp_path / "epw_mgb2"),
    )
    result = calc.run(cand, dft=dft, work_dir=str(tmp_path / "epw_mgb2"))
    assert result.electron_phonon is not None
    assert result.electron_phonon.lambda_total is not None
    assert result.performance_score is not None
    # Loose gate: screening grids will not hit literature Tc tightly
    lo, hi = MGB2_TC_K_RANGE
    assert result.performance_score > 0.0
    assert result.performance_score < hi * 2.5
