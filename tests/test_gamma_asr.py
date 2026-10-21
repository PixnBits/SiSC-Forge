"""Crystal ASR at Gamma: V3Si acoustic triplet and classifier wiring."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from siscforge.calculators.qe.asr import (
    RY_TO_CMM1,
    discover_dyn_files,
    gamma_asr_from_dyn,
)
from siscforge.calculators.qe.epw_recipes import epw_blocked_on_soft_phonon
from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import PhononResult
from siscforge.shortlist import filter_stable_evaluations, is_dynamically_stable
from siscforge.soft_modes import (
    REPORT_VERSION,
    _GAMMA_MILD_CM1,
    _IMAG_THRESHOLD_CM1,
    _LOCUS_GAP_CM1,
    build_soft_mode_report,
    classify_gamma_force_constants,
    classify_ph_directory,
    classify_soft_mode,
    evaluation_from_gamma_matrix,
    evaluation_from_ph_directory,
    render_soft_mode_markdown,
)

FIXTURE = Path(__file__).parent / "fixtures" / "qe" / "v3si_a15_gamma.dyn1"
_FILE_FREQ_RE = re.compile(
    r"=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)\s*\[cm-1\]",
    re.IGNORECASE,
)
runner = CliRunner()


def _file_frequencies(path: Path) -> list[float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    found = _FILE_FREQ_RE.finditer(text)
    return [float(match.group(1).replace("D", "E").replace("d", "e")) for match in found]


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _count_near(freqs: list[float], target: float, tol: float) -> int:
    return sum(1 for value in freqs if abs(value - target) <= tol)


def _two_atom_force_constants(
    acoustic_cm1: float,
    optical_cm1: float,
    mass_ry: float = 911.444,
) -> tuple[np.ndarray, np.ndarray]:
    """Equal-mass 2-atom Gamma matrix in the translation / relative-mode basis.

    Eigenvalues are chosen so the raw spectrum is three copies of each target.
    Crystal ASR then zeros the translation subspace and leaves the optical
    eigenvalues in place.
    """
    n3 = 6
    vectors: list[np.ndarray] = []
    for direction in range(3):
        vector = np.zeros(n3)
        for atom in range(2):
            vector[3 * atom + direction] = 1.0
        vectors.append(vector / np.linalg.norm(vector))
    for direction in range(3):
        vector = np.zeros(n3)
        vector[direction] = 1.0
        vector[3 + direction] = -1.0
        vectors.append(vector / np.linalg.norm(vector))
    matrix = np.zeros((n3, n3))
    targets = [acoustic_cm1] * 3 + [optical_cm1] * 3
    for freq, vector in zip(targets, vectors, strict=True):
        lam = mass_ry * float(np.sign(freq)) * (abs(freq) / RY_TO_CMM1) ** 2
        matrix += lam * np.outer(vector, vector)
    return matrix, np.array([mass_ry, mass_ry])


def test_thresholds_unchanged() -> None:
    assert _IMAG_THRESHOLD_CM1 == 5.0
    assert _GAMMA_MILD_CM1 == 50.0
    assert _LOCUS_GAP_CM1 == 15.0
    assert REPORT_VERSION == 1


def test_v3si_raw_matches_file_and_asr_removes_acoustic_triplet() -> None:
    assert FIXTURE.is_file()
    file_freqs = sorted(_file_frequencies(FIXTURE))
    assert len(file_freqs) == 24
    raw, corrected = gamma_asr_from_dyn(FIXTURE)
    assert len(raw) == 24
    for got, expected in zip(raw, file_freqs, strict=True):
        assert abs(got - expected) <= 0.05
    assert _count_near(raw, -122.019022, 0.05) == 3
    assert _count_near(raw, -54.299347, 0.05) == 3
    assert _count_near(raw, 190.055296, 0.05) == 3
    assert _count_near(raw, 303.425841, 0.05) == 3

    assert sum(1 for value in corrected if abs(value) < 1.0) == 3
    assert _count_near(corrected, -54.299347, 0.1) == 3
    assert _count_near(corrected, 145.431723, 0.05) == 3
    assert _count_near(corrected, 173.770629, 0.05) == 3
    assert _count_near(corrected, 174.772495, 0.05) == 3
    assert _count_near(corrected, 205.449664, 0.05) == 1
    assert _count_near(corrected, 217.849565, 0.05) == 2
    assert _count_near(corrected, 286.404222, 0.05) == 3
    assert _count_near(corrected, 338.059441, 0.05) == 3
    # Naive zeroing of the three lowest raw modes would keep 190 and 303.
    assert _count_near(corrected, 190.055296, 1.0) == 0
    assert _count_near(corrected, 303.425841, 1.0) == 0


def test_v3si_stays_optical_soft_and_epw_blocked() -> None:
    evaluation = evaluation_from_ph_directory(FIXTURE, formula="V3Si")
    stored_min = evaluation.phonon.min_frequency_cm1
    stored_gamma = list(evaluation.phonon.raw["qpoints"][0]["frequencies_cm1"])
    row = classify_soft_mode(evaluation)

    assert row["soft_mode_class"] == "optical_soft"
    assert row["acoustic_vs_optical"] == "optical_imaginary"
    assert row["asr_applied"] == "crystal"
    assert "crystal_asr_applied" in row["reasons"]
    assert row["dynamically_stable"] is False
    assert row["has_imaginary_modes"] is True
    assert _count_near(row["gamma_raw_frequencies_cm1"], -122.019022, 0.05) == 3
    assert sum(abs(value) < 1.0 for value in row["gamma_asr_frequencies_cm1"]) == 3
    assert _count_near(row["gamma_asr_frequencies_cm1"], -54.3, 0.1) == 3

    # Stored phonon stays the raw ph.x spectrum. Existing EPW gating reads that.
    assert evaluation.phonon.min_frequency_cm1 == stored_min
    assert evaluation.phonon.raw["qpoints"][0]["frequencies_cm1"] == stored_gamma
    assert evaluation.phonon.raw["asr_applied"] is False
    assert evaluation.phonon.has_imaginary_modes is True
    assert evaluation.phonon.dynamically_stable is False
    assert epw_blocked_on_soft_phonon(evaluation.phonon, DFTConfig(do_epw=True)) is True
    assert is_dynamically_stable(evaluation) is False
    assert filter_stable_evaluations([evaluation], mode="stable_only") == []

    report = build_soft_mode_report([evaluation], source_store="memory", campaign_name="v3si")
    assert report["version"] == REPORT_VERSION
    assert report["schema"] == "siscforge.soft_mode_report"
    candidate = report["candidates"][0]
    assert candidate["asr_applied"] == "crystal"
    assert candidate["gamma_raw_frequencies_cm1"]
    assert candidate["gamma_asr_frequencies_cm1"]
    markdown = render_soft_mode_markdown(report)
    assert "## Gamma frequencies: raw vs crystal ASR" in markdown
    assert "| mode | raw (cm⁻¹) | ASR (cm⁻¹) |" in markdown
    assert "-122.02" in markdown
    assert "-54.30" in markdown
    assert "173.77" in markdown
    assert "286.40" in markdown


def test_ph_directory_skips_dyn0(tmp_path: Path) -> None:
    shutil.copy(FIXTURE, tmp_path / "cell.dyn1")
    (tmp_path / "cell.dyn0").write_text("q-mesh list, not a dynamical matrix\n", encoding="utf-8")
    found = discover_dyn_files(tmp_path)
    assert [path.name for path in found] == ["cell.dyn1"]
    row = classify_ph_directory(tmp_path, formula="V3Si")
    assert row["soft_mode_class"] == "optical_soft"
    assert row["asr_applied"] == "crystal"


def test_work_dir_provenance_applies_asr_without_explicit_dyn_path(tmp_path: Path) -> None:
    ph_dir = tmp_path / "02_scf"
    ph_dir.mkdir()
    shutil.copy(FIXTURE, ph_dir / "v3si.dyn1")
    raw, _corrected = gamma_asr_from_dyn(FIXTURE)
    phonon = PhononResult(
        min_frequency_cm1=min(raw),
        has_imaginary_modes=True,
        dynamically_stable=False,
        n_modes=len(raw),
        status="ok",
        quality_tag="screening",
        raw={"frequencies_cm1": list(raw), "asr_applied": False},
    )
    evaluation = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="V3Si",
            material_family="other",
            metadata={"n_atoms": 8},
            source="qe",
        ),
        phonon=phonon,
        status="ok",
        calculator_name="qe",
        provenance=Provenance(parameters={"work_dir": str(tmp_path)}),
    )
    row = classify_soft_mode(evaluation)
    assert row["asr_applied"] == "crystal"
    assert row["soft_mode_class"] == "optical_soft"
    assert sum(abs(value) < 1.0 for value in row["gamma_asr_frequencies_cm1"]) == 3


def test_missing_explicit_dyn_path_does_not_fall_through(tmp_path: Path) -> None:
    ph_dir = tmp_path / "ph"
    ph_dir.mkdir()
    shutil.copy(FIXTURE, ph_dir / "v3si.dyn1")
    raw, _corrected = gamma_asr_from_dyn(FIXTURE)
    phonon = PhononResult(
        min_frequency_cm1=min(raw),
        has_imaginary_modes=True,
        dynamically_stable=False,
        n_modes=len(raw),
        status="ok",
        quality_tag="screening",
        raw={
            "frequencies_cm1": list(raw),
            "gamma_dyn_path": str(ph_dir / "missing.dyn1"),
            "asr_applied": False,
        },
    )
    evaluation = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="V3Si",
            material_family="other",
            metadata={"n_atoms": 8, "work_dir": str(ph_dir)},
            source="qe",
        ),
        phonon=phonon,
        status="ok",
        calculator_name="qe",
        notes=f"work_dir={ph_dir}",
    )
    row = classify_soft_mode(evaluation)
    assert row["asr_applied"] is None
    assert row["gamma_raw_frequencies_cm1"] is None
    assert "crystal_asr_applied" not in row["reasons"]


def test_no_dyn_leaves_classification_unchanged() -> None:
    freqs = [12.0, 15.0, 18.0, -40.0, 200.0, 250.0]
    phonon = PhononResult(
        min_frequency_cm1=-40.0,
        has_imaginary_modes=True,
        dynamically_stable=False,
        n_modes=6,
        status="ok",
        quality_tag="screening",
        raw={"frequencies_cm1": freqs},
    )
    evaluation = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="Nb0.5Ti0.5N",
            material_family="tm_nitride",
            metadata={"n_atoms": 2},
        ),
        phonon=phonon,
        status="ok",
        calculator_name="qe",
    )
    row = classify_soft_mode(evaluation)
    assert row["soft_mode_class"] == "optical_soft"
    assert row["acoustic_vs_optical"] == "optical_imaginary"
    assert row["asr_applied"] is None
    assert row["gamma_raw_frequencies_cm1"] is None
    assert row["gamma_asr_frequencies_cm1"] is None
    assert "crystal_asr_applied" not in row["reasons"]
    report = build_soft_mode_report([evaluation])
    markdown = render_soft_mode_markdown(report)
    assert "raw vs crystal ASR" not in markdown


def test_setup_failed_is_not_rescued_by_a_dyn_file(tmp_path: Path) -> None:
    shutil.copy(FIXTURE, tmp_path / "cell.dyn1")
    phonon = PhononResult(
        min_frequency_cm1=None,
        has_imaginary_modes=False,
        dynamically_stable=False,
        n_modes=0,
        status="failed",
        quality_tag="screening",
        raw={"ph_dir": str(tmp_path)},
    )
    evaluation = CandidateEvaluation(
        candidate=StructureCandidate(formula="V3Si", metadata={"n_atoms": 8}),
        phonon=phonon,
        status="failed",
        calculator_name="qe",
    )
    row = classify_soft_mode(evaluation)
    assert row["soft_mode_class"] == "setup_failed"
    assert row["asr_applied"] is None


def test_genuine_optical_instability_stays_optical_soft() -> None:
    matrix, masses = _two_atom_force_constants(-120.0, -40.0)
    evaluation = evaluation_from_gamma_matrix(matrix, masses, formula="SynthOpt")
    row = classify_gamma_force_constants(matrix, masses, formula="SynthOpt")
    assert row["soft_mode_class"] == "optical_soft"
    assert row["acoustic_vs_optical"] == "optical_imaginary"
    assert row["asr_applied"] == "crystal"
    assert row["dynamically_stable"] is False
    assert row["has_imaginary_modes"] is True
    corrected = row["gamma_asr_frequencies_cm1"]
    assert corrected is not None
    assert _count_near(corrected, -40.0, 0.1) == 3
    assert sum(abs(value) < 1.0 for value in corrected) == 3
    assert epw_blocked_on_soft_phonon(evaluation.phonon, DFTConfig(do_epw=True)) is True
    assert is_dynamically_stable(evaluation) is False
    assert filter_stable_evaluations([evaluation], mode="stable_only") == []


def test_acoustic_only_asr_violation_classifies_stable() -> None:
    matrix, masses = _two_atom_force_constants(-120.0, 200.0)
    evaluation = evaluation_from_gamma_matrix(matrix, masses, formula="SynthAc")
    row = classify_gamma_force_constants(matrix, masses, formula="SynthAc")
    assert row["soft_mode_class"] == "stable"
    assert row["asr_applied"] == "crystal"
    assert "crystal_asr_applied" in row["reasons"]
    assert row["dynamically_stable"] is True
    assert row["has_imaginary_modes"] is False
    corrected = row["gamma_asr_frequencies_cm1"]
    assert corrected is not None
    assert sum(abs(value) < 1.0 for value in corrected) == 3
    assert all(value > 100.0 for value in corrected if abs(value) >= 1.0)

    # The stored phonon is still the raw spectrum. Without the matrix the same
    # list is an acoustic-only imaginary cell, not an ASR-stable one.
    assert evaluation.phonon.dynamically_stable is False
    assert evaluation.phonon.has_imaginary_modes is True
    evaluation.phonon.raw.pop("gamma_force_constants")
    evaluation.phonon.raw.pop("gamma_masses_ry")
    bare = classify_soft_mode(evaluation)
    assert bare["asr_applied"] is None
    # Existing rule: imaginary weight only on the first three branches is a
    # mesh-artefact suspect, and it is not the ASR-corrected stable class.
    assert bare["soft_mode_class"] == "likely_mesh_artefact"
    assert bare["acoustic_vs_optical"] == "acoustic_only_imaginary"
    assert "crystal_asr_applied" not in bare["reasons"]


def test_finite_q_imaginary_optical_is_not_removed_by_gamma_asr() -> None:
    matrix, masses = _two_atom_force_constants(-80.0, 180.0)
    evaluation = evaluation_from_gamma_matrix(matrix, masses, formula="FiniteQ")
    finite = [-90.0, -90.0, -90.0, -40.0, 110.0, 130.0]
    evaluation.phonon.raw["qpoints"].append(
        {
            "q": [0.0, 0.0, 0.5],
            "is_gamma": False,
            "frequencies_cm1": finite,
            "min_frequency_cm1": min(finite),
        }
    )
    flat = list(evaluation.phonon.raw["frequencies_cm1"])
    evaluation.phonon.raw["frequencies_cm1"] = flat + finite
    row = classify_soft_mode(evaluation)
    assert row["asr_applied"] == "crystal"
    assert row["soft_mode_class"] == "optical_soft"
    assert row["softness_locus"] == "finite_q"
    assert row["finite_q_min_frequency_cm1"] == pytest.approx(-90.0)
    gamma_stored = evaluation.phonon.raw["qpoints"][0]["frequencies_cm1"]
    assert min(gamma_stored) < -50.0
    finite_stored = evaluation.phonon.raw["qpoints"][1]["frequencies_cm1"]
    assert finite_stored == finite


def test_gamma_asr_cli_on_dyn_file() -> None:
    result = runner.invoke(app, ["gamma-asr", str(FIXTURE), "--formula", "V3Si"])
    assert result.exit_code == 0, result.output
    text = _plain(result.stdout)
    assert "optical_soft" in text
    assert "crystal" in text
    assert "EPW blocked=True" in text
    assert "EPW-eligible survivor=False" in text
    assert "-122.02" in text
    assert "-54.30" in text


def test_gamma_asr_cli_missing_dyn_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["gamma-asr", str(tmp_path)])
    assert result.exit_code == 1
