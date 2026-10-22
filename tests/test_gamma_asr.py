"""Crystal ASR at Gamma: diagnostic only; classification stays on the raw record."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from siscforge.calculators.qe.asr import (
    RY_TO_CMM1,
    _MAX_DYN_BYTES,
    _MAX_NAT,
    acoustic_indices_by_overlap,
    discover_dyn_files,
    find_gamma_dyn,
    gamma_asr_from_dyn,
    parse_qe_dyn,
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
    write_soft_mode_report,
)
from siscforge.store import EvaluationStore

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


def _equal_mass_gamma(
    nat: int,
    acoustic_cm1: float,
    optical_cm1: float,
    mass_ry: float = 9114.44,
) -> tuple[np.ndarray, np.ndarray]:
    """Equal-mass Gamma matrix. The translation subspace carries *acoustic_cm1*."""
    n3 = 3 * nat
    seed = np.eye(n3)
    for direction in range(3):
        vector = np.zeros(n3)
        for atom in range(nat):
            vector[3 * atom + direction] = 1.0
        seed[:, direction] = vector / np.linalg.norm(vector)
    basis, _r = np.linalg.qr(seed)
    matrix = np.zeros((n3, n3))
    targets = [acoustic_cm1] * 3 + [optical_cm1] * (n3 - 3)
    for freq, vector in zip(targets, (basis[:, index] for index in range(n3)), strict=True):
        lam = mass_ry * float(np.sign(freq)) * (abs(freq) / RY_TO_CMM1) ** 2
        matrix += lam * np.outer(vector, vector)
    return matrix, np.full(nat, mass_ry)


def _write_gamma_dyn(
    path: Path,
    matrix: np.ndarray,
    masses: np.ndarray,
    symbols: list[str],
) -> None:
    """Minimal QE ``.dyn1`` (ibrav 0) for a real Hermitian Gamma matrix."""
    nat = int(masses.shape[0])
    species: list[tuple[str, float]] = []
    type_of: list[int] = []
    index: dict[str, int] = {}
    for symbol, mass in zip(symbols, masses, strict=True):
        key = str(symbol)
        if key not in index:
            index[key] = len(species) + 1
            species.append((key, float(mass)))
        type_of.append(index[key])
    lines = [
        "Dynamical matrix file",
        "default",
        f"  {len(species)}  {nat}  0  1.0  0.0  0.0  0.0  0.0  0.0",
        "Basis vectors",
        "  1.0  0.0  0.0",
        "  0.0  1.0  0.0",
        "  0.0  0.0  1.0",
    ]
    for number, (symbol, mass) in enumerate(species, start=1):
        lines.append(f"  {number}  '{symbol}'  {mass:.8f}")
    for atom, typ in enumerate(type_of, start=1):
        lines.append(f"  {atom}  {typ}  0.0  0.0  {atom * 0.1:.1f}")
    lines.extend(
        [
            "",
            "     Dynamical  Matrix in cartesian axes",
            "",
            "     q = (    0.000000000   0.000000000   0.000000000 ) ",
            "",
        ]
    )
    force = np.real(np.asarray(matrix, dtype=float))
    for na in range(nat):
        for nb in range(nat):
            lines.append(f"    {na + 1}    {nb + 1}")
            block = force[3 * na : 3 * na + 3, 3 * nb : 3 * nb + 3]
            for row in range(3):
                parts: list[str] = []
                for col in range(3):
                    parts.append(f"{block[row, col]: .8f}   0.00000000")
                lines.append("  " + "   ".join(parts))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _one_atom_dyn(*, mass: str = "911.444", diag: str = "0.10000000") -> str:
    return (
        "Dynamical matrix file\n"
        "     1    1   1   1.0   0.0   0.0   0.0   0.0   0.0\n"
        f"           1  'Mg'    {mass}\n"
        "    1    1      0.0000000000      0.0000000000      0.0000000000\n"
        "\n"
        "     Dynamical  Matrix in cartesian axes\n"
        "\n"
        "     q = (    0.000000000   0.000000000   0.000000000 ) \n"
        "\n"
        "    1    1\n"
        f"  {diag}   0.00000000     0.00000000   0.00000000     0.00000000   0.00000000\n"
        "  0.00000000   0.00000000     0.10000000   0.00000000     0.00000000   0.00000000\n"
        "  0.00000000   0.00000000     0.00000000   0.00000000     0.10000000   0.00000000\n"
    )


def _sha256_tree(root: Path) -> dict[str, str]:
    digest: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


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
    assert "crystal_asr_applied" not in row["reasons"]
    raw_only = evaluation.model_copy(deep=True)
    raw_only.phonon.raw.pop("gamma_raw_frequencies_cm1")
    raw_only.phonon.raw.pop("gamma_asr_frequencies_cm1")
    raw_row = classify_soft_mode(raw_only)
    assert raw_row["soft_mode_class"] == row["soft_mode_class"]
    assert raw_row["asr_applied"] is None
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


def test_work_dir_provenance_does_not_apply_asr(tmp_path: Path) -> None:
    """A reachable work_dir must not change the class or attach ASR."""
    ph_dir = tmp_path / "02_scf"
    ph_dir.mkdir()
    shutil.copy(FIXTURE, ph_dir / "v3si.dyn1")
    assert find_gamma_dyn(tmp_path) is not None
    raw, corrected = gamma_asr_from_dyn(FIXTURE)
    assert sum(abs(value) < 1.0 for value in corrected) == 3
    phonon = PhononResult(
        min_frequency_cm1=min(raw),
        has_imaginary_modes=True,
        dynamically_stable=False,
        n_modes=len(raw),
        status="ok",
        quality_tag="screening",
        raw={"frequencies_cm1": list(raw), "asr_applied": False, "ph_dir": str(tmp_path)},
    )
    evaluation = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="V3Si",
            material_family="other",
            metadata={"n_atoms": 8, "work_dir": str(tmp_path), "ph_dir": str(ph_dir)},
            source="qe",
        ),
        phonon=phonon,
        status="ok",
        calculator_name="qe",
        provenance=Provenance(parameters={"work_dir": str(tmp_path)}),
        notes=f"work_dir={tmp_path}",
    )
    row = classify_soft_mode(evaluation)
    assert row["soft_mode_class"] == "optical_soft"
    assert row["asr_applied"] is None
    assert row["gamma_raw_frequencies_cm1"] is None
    assert row["gamma_asr_frequencies_cm1"] is None
    assert "crystal_asr_applied" not in row["reasons"]
    assert epw_blocked_on_soft_phonon(evaluation.phonon, DFTConfig(do_epw=True)) is True
    assert is_dynamically_stable(evaluation) is False


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
    assert "crystal_asr_applied" not in row["reasons"]
    assert row["dynamically_stable"] is False
    assert row["has_imaginary_modes"] is True
    corrected = row["gamma_asr_frequencies_cm1"]
    assert corrected is not None
    assert _count_near(corrected, -40.0, 0.1) == 3
    assert sum(abs(value) < 1.0 for value in corrected) == 3
    assert epw_blocked_on_soft_phonon(evaluation.phonon, DFTConfig(do_epw=True)) is True
    assert is_dynamically_stable(evaluation) is False
    assert filter_stable_evaluations([evaluation], mode="stable_only") == []


def test_acoustic_only_negative_stays_raw_class() -> None:
    """Negative acoustic triplet stays non-stable. ASR ~0 is diagnostic only."""
    matrix, masses = _two_atom_force_constants(-120.0, 200.0)
    evaluation = evaluation_from_gamma_matrix(matrix, masses, formula="SynthAc")
    row = classify_gamma_force_constants(matrix, masses, formula="SynthAc")
    raw_only = evaluation.model_copy(deep=True)
    raw_only.phonon.raw.pop("gamma_force_constants")
    raw_only.phonon.raw.pop("gamma_masses_ry")
    bare = classify_soft_mode(raw_only)

    assert row["soft_mode_class"] == bare["soft_mode_class"] == "likely_mesh_artefact"
    assert row["acoustic_vs_optical"] == "acoustic_only_imaginary"
    assert "crystal_asr_applied" not in row["reasons"]
    assert "crystal_asr_applied" not in bare["reasons"]
    assert row["dynamically_stable"] is False
    assert row["has_imaginary_modes"] is True
    assert row["asr_applied"] == "crystal"
    assert bare["asr_applied"] is None
    corrected = row["gamma_asr_frequencies_cm1"]
    assert corrected is not None
    assert sum(abs(value) < 1.0 for value in corrected) == 3
    assert all(value > 100.0 for value in corrected if abs(value) >= 1.0)

    # EPW gate and shortlist read the raw phonon flags, which ASR would have cleaned.
    assert evaluation.phonon.dynamically_stable is False
    assert evaluation.phonon.has_imaginary_modes is True
    assert epw_blocked_on_soft_phonon(evaluation.phonon, DFTConfig(do_epw=True)) is True
    assert is_dynamically_stable(evaluation) is False
    assert filter_stable_evaluations([evaluation], mode="stable_only") == []


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
    assert "crystal_asr_applied" not in row["reasons"]
    assert row["soft_mode_class"] == "optical_soft"
    assert row["dynamically_stable"] is False
    assert is_dynamically_stable(evaluation) is False
    # Raw Γ is still deeply imaginary, so the locus stays what main computes
    # from the stored q-points (ASR does not rewrite Γ before this label).
    assert row["softness_locus"] == "both"
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


def test_gamma_asr_help_names_dyn_files() -> None:
    result = runner.invoke(app, ["gamma-asr", "--help"])
    assert result.exit_code == 0, result.output
    text = _plain(result.stdout)
    assert "{PH_DIR}" in text
    assert "{ph_dir}" not in text
    assert "*.dyn1..dynN" in text
    assert "single *.dynN file" in text


def test_nonfinite_dyn_matrix_and_mass_raise_valueerror(tmp_path: Path) -> None:
    nan_path = tmp_path / "nan.dyn1"
    nan_path.write_text(_one_atom_dyn(diag="NaN"), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        parse_qe_dyn(nan_path)

    inf_path = tmp_path / "infmass.dyn1"
    inf_path.write_text(_one_atom_dyn(mass="1e9999"), encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        parse_qe_dyn(inf_path)


def test_gamma_asr_cli_nan_exits_1(tmp_path: Path) -> None:
    path = tmp_path / "nan.dyn1"
    path.write_text(_one_atom_dyn(diag="NaN"), encoding="utf-8")
    result = runner.invoke(app, ["gamma-asr", str(path)])
    assert result.exit_code == 1
    text = _plain(result.stdout + result.stderr)
    assert "Traceback" not in text
    assert "LinAlgError" not in text
    assert "non-finite" in text
    assert text.count("\n") <= 2


def test_nat_above_limit_rejected(tmp_path: Path) -> None:
    path = tmp_path / "wide.dyn1"
    path.write_text(
        "Dynamical matrix file\n"
        f"     1    {_MAX_NAT + 1}   1   1.0   0.0   0.0   0.0   0.0   0.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nat"):
        parse_qe_dyn(path)


def test_oversize_dyn_rejected(tmp_path: Path) -> None:
    path = tmp_path / "huge.dyn1"
    with path.open("wb") as handle:
        handle.seek(_MAX_DYN_BYTES)
        handle.write(b"x")
    with pytest.raises(ValueError, match="larger than"):
        parse_qe_dyn(path)


def test_dyn_symlink_outside_directory_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.dyn1"
    shutil.copy(FIXTURE, secret)
    ph_dir = tmp_path / "ph"
    ph_dir.mkdir()
    link = ph_dir / "escape.dyn1"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="outside"):
        discover_dyn_files(ph_dir)
    with pytest.raises(ValueError, match="outside"):
        find_gamma_dyn(ph_dir)
    result = runner.invoke(app, ["gamma-asr", str(ph_dir)])
    assert result.exit_code == 1
    assert "outside" in _plain(result.stdout)


def test_acoustic_indices_follow_translation_overlap() -> None:
    """An optical mode nearer zero must not steal the acoustic label."""
    nat = 2
    n3 = 6
    masses = np.array([911.444, 911.444])
    per = np.repeat(masses, 3)

    def _norm(vector: np.ndarray) -> np.ndarray:
        return vector / np.linalg.norm(vector)

    translations: list[np.ndarray] = []
    opticals: list[np.ndarray] = []
    for direction in range(3):
        uniform = np.zeros(n3)
        relative = np.zeros(n3)
        for atom in range(nat):
            uniform[3 * atom + direction] = 1.0
            relative[3 * atom + direction] = 1.0 if atom == 0 else -1.0
        translations.append(_norm(np.sqrt(per) * _norm(uniform)))
        opticals.append(_norm(np.sqrt(per) * _norm(relative)))

    def _lam(freq: float) -> float:
        return float(np.sign(freq)) * (abs(freq) / RY_TO_CMM1) ** 2

    dynamical = np.zeros((n3, n3))
    for vector in translations:
        dynamical += _lam(0.2) * np.outer(vector, vector)
    for vector in opticals:
        dynamical += _lam(0.01) * np.outer(vector, vector)

    chosen = acoustic_indices_by_overlap(dynamical, masses)
    _evals, evecs = np.linalg.eigh(dynamical)
    selected = []
    for index in chosen:
        freq = float(np.sign(_evals[index]) * np.sqrt(abs(_evals[index])) * RY_TO_CMM1)
        selected.append(freq)
        overlap = 0.0
        for vector in translations:
            overlap += float(abs(np.vdot(vector, evecs[:, index])) ** 2)
        assert overlap > 0.99
    assert all(abs(freq - 0.2) < 1e-6 for freq in selected)
    nearest = np.argsort(np.abs(_evals))[:3]
    nearest_freqs = [
        float(np.sign(_evals[index]) * np.sqrt(abs(_evals[index])) * RY_TO_CMM1)
        for index in nearest
    ]
    assert all(abs(freq - 0.01) < 1e-6 for freq in nearest_freqs)


def test_reachable_mgb2_store_is_byte_unchanged(tmp_path: Path) -> None:
    """classify / report must not rewrite a store or reclass from its .dyn."""
    store_root = tmp_path / "store"
    work = store_root / "qe_work" / "02_scf"
    work.mkdir(parents=True)
    matrix, masses = _equal_mass_gamma(3, acoustic_cm1=-80.0, optical_cm1=220.0)
    dyn_path = work / "mgb2.dyn1"
    _write_gamma_dyn(dyn_path, matrix, masses, ["Mg", "B", "B"])
    raw_f, asr_f = gamma_asr_from_dyn(dyn_path)
    assert min(raw_f) < -50.0
    assert sum(abs(value) < 1.0 for value in asr_f) == 3

    phonon = PhononResult(
        min_frequency_cm1=min(raw_f),
        has_imaginary_modes=True,
        dynamically_stable=False,
        n_modes=len(raw_f),
        status="ok",
        quality_tag="screening",
        raw={
            "frequencies_cm1": list(raw_f),
            "asr_applied": False,
            "ph_dir": str(work),
            "work_dir": str(store_root / "qe_work"),
        },
    )
    evaluation = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="MgB2",
            material_family="mgb2_boride",
            metadata={
                "n_atoms": 3,
                "work_dir": str(store_root / "qe_work"),
                "ph_dir": str(work),
            },
            source="qe",
        ),
        phonon=phonon,
        status="ok",
        calculator_name="qe",
        provenance=Provenance(parameters={"work_dir": str(store_root / "qe_work")}),
        notes=f"work_dir={store_root / 'qe_work'}",
    )
    store = EvaluationStore(store_root)
    store.save_candidates([evaluation.candidate])
    store.save_evaluations([evaluation])
    store.save_meta({"campaign": "mgb2_diag"})
    before = _sha256_tree(store_root)
    assert any(name.endswith("mgb2.dyn1") for name in before)

    loaded = store.load_evaluations()
    assert len(loaded) == 1
    row = classify_soft_mode(loaded[0])
    raw_only = loaded[0].model_copy(deep=True)
    raw_only.candidate.metadata = {"n_atoms": 3}
    raw_only.provenance = Provenance()
    raw_only.notes = ""
    raw_only.phonon.raw = {
        "frequencies_cm1": list(raw_f),
        "asr_applied": False,
    }
    bare = classify_soft_mode(raw_only)
    assert row["soft_mode_class"] == bare["soft_mode_class"]
    assert row["soft_mode_class"] != "stable"
    assert row["asr_applied"] is None
    assert "crystal_asr_applied" not in row["reasons"]
    assert epw_blocked_on_soft_phonon(loaded[0].phonon, DFTConfig(do_epw=True)) is True
    assert is_dynamically_stable(loaded[0]) is False

    report = build_soft_mode_report(
        loaded, source_store=str(store_root), campaign_name="mgb2_diag"
    )
    assert report["candidates"][0]["soft_mode_class"] == bare["soft_mode_class"]
    assert report["candidates"][0]["asr_applied"] is None

    write_soft_mode_report(loaded, tmp_path / "report_out", campaign_name="mgb2_diag")
    assert (tmp_path / "report_out").is_dir()
    assert _sha256_tree(store_root) == before
