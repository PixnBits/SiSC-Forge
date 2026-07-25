"""Golden-system test: bulk rocksalt NbN phonon properties.

Default path (CI / laptops without QE)
--------------------------------------
Uses MockCalculator + fixture parsers. Always runs.

Optional real QE path
---------------------
Set ``SISCFORGE_RUN_QE=1`` and ensure ``pw.x`` / ``ph.x`` are on PATH with
pseudopotentials configured via ``SISCFORGE_PSEUDO_DIR`` (or a local
``pseudos/`` directory). The real run is intentionally small (screening
cutoffs, Gamma or 1×1×1 q) and checks order-of-magnitude stability only.

Reference values are documented in
:mod:`siscforge.calculators.qe.references`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from siscforge.calculators import get
from siscforge.calculators.qe.env import qe_available
from siscforge.calculators.qe.parser import parse_frequency_list, parse_ph_output, parse_pw_output
from siscforge.calculators.qe.references import (
    NBN_FIXTURE_FREQUENCIES_CM1,
    NBN_IMAG_THRESHOLD_CM1,
    NBN_LATTICE_A_ANG,
    NBN_OPTICAL_MAX_CM1_RANGE,
    NBN_REFERENCE_NOTES,
)
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import DFTConfig
from siscforge.models.results import PhononResult
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride

FIXTURES = Path(__file__).parent / "fixtures" / "qe"


def _nbn_candidate():
    s = build_binary_nitride("Nb", a=NBN_LATTICE_A_ANG)
    return structure_to_candidate(
        s,
        material_family="tm_nitride",
        formula="NbN",
        tags=["golden", "nbn", "bulk"],
        quality_tag="screening",
        source="golden_test",
    )


def test_nbn_structure_lattice() -> None:
    s = build_binary_nitride("Nb", a=NBN_LATTICE_A_ANG)
    # Primitive cell: correct density; conventional a is NBN_LATTICE_A_ANG
    assert s.composition.reduced_formula in {"NbN", "NNb"}
    assert s.density == pytest.approx(8.38, rel=5e-2)
    assert s.lattice.a == pytest.approx(NBN_LATTICE_A_ANG / (2**0.5), rel=1e-3)
    conv = build_binary_nitride("Nb", a=NBN_LATTICE_A_ANG, conventional=True)
    assert conv.lattice.a == pytest.approx(NBN_LATTICE_A_ANG, rel=1e-6)


def test_nbn_mock_calculator_phonon() -> None:
    """Mock path: NbN evaluation is schema-valid (always green)."""
    cand = _nbn_candidate()
    calc = get("mock")
    result = calc.run(cand)
    assert isinstance(result, CandidateEvaluation)
    assert result.phonon is not None
    assert isinstance(result.phonon, PhononResult)
    assert result.phonon.status == "mock"
    assert result.scf is not None
    assert result.scf.status == "mock"
    assert result.si_feasibility is not None
    assert 0.0 <= result.si_feasibility.total <= 100.0


def test_nbn_fixture_phonon_reference_range() -> None:
    """Fixture frequencies (stable NbN-like spectrum) match documented ranges."""
    ph = parse_frequency_list(
        list(NBN_FIXTURE_FREQUENCIES_CM1),
        imag_threshold_cm1=NBN_IMAG_THRESHOLD_CM1,
    )
    assert ph.dynamically_stable is True
    assert ph.has_imaginary_modes is False
    assert ph.max_frequency_cm1 is not None
    lo, hi = NBN_OPTICAL_MAX_CM1_RANGE
    assert lo <= ph.max_frequency_cm1 <= hi
    assert ph.min_frequency_cm1 is not None
    assert ph.min_frequency_cm1 > -NBN_IMAG_THRESHOLD_CM1


def test_nbn_fixture_files_parse() -> None:
    scf = parse_pw_output(FIXTURES / "pw_scf_snippet.out")
    ph = parse_ph_output(FIXTURES / "ph_gamma_snippet.out")
    assert scf.status == "ok"
    assert ph.dynamically_stable is True
    assert ph.max_frequency_cm1 is not None
    lo, hi = NBN_OPTICAL_MAX_CM1_RANGE
    assert lo <= ph.max_frequency_cm1 <= hi


def test_reference_notes_present() -> None:
    assert "NbN" in NBN_REFERENCE_NOTES
    assert "phonon" in NBN_REFERENCE_NOTES.lower()


@pytest.mark.skipif(
    os.environ.get("SISCFORGE_RUN_QE") != "1",
    reason="Set SISCFORGE_RUN_QE=1 to enable real QE NbN phonon regression",
)
@pytest.mark.skipif(not qe_available(), reason="pw.x not available")
def test_nbn_real_qe_phonon(tmp_path: Path) -> None:
    """Optional real QE DFPT on bulk NbN (screening settings).

    Requires:
    - ``pw.x`` and ``ph.x`` on PATH
    - ``SISCFORGE_PSEUDO_DIR`` pointing at UPF files for Nb and N
    - ``SISCFORGE_RUN_QE=1``
    """
    pseudo = os.environ.get("SISCFORGE_PSEUDO_DIR")
    if not pseudo:
        pytest.skip("SISCFORGE_PSEUDO_DIR not set")

    cand = _nbn_candidate()
    calc = get("qe")
    dft = DFTConfig(
        engine="qe",
        ecutwfc=40.0,
        ecutrho=320.0,
        kpoints=[2, 2, 2],
        pseudo_dir=pseudo,
        do_relax=False,  # fixed experimental lattice for golden phonon
        do_phonon=True,
        phonon_method="gamma",
        quality_tag="screening",
        work_dir=str(tmp_path / "qe_nbn"),
        nproc=int(os.environ.get("SISCFORGE_QE_NPROC", "1")),
    )
    result = calc.run(cand, dft=dft, work_dir=str(tmp_path / "qe_nbn"))
    assert isinstance(result, CandidateEvaluation)
    assert result.calculator_name == "qe"
    assert result.phonon is not None, result.notes
    assert result.phonon.status == "ok", result.phonon.raw
    assert result.phonon.min_frequency_cm1 is not None
    assert result.phonon.min_frequency_cm1 > -NBN_IMAG_THRESHOLD_CM1
    assert result.phonon.has_imaginary_modes is False
    assert result.phonon.max_frequency_cm1 is not None
    lo, hi = NBN_OPTICAL_MAX_CM1_RANGE
    assert lo <= result.phonon.max_frequency_cm1 <= hi, (
        f"max freq {result.phonon.max_frequency_cm1} outside {lo}-{hi} cm⁻¹"
    )
