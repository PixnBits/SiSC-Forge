"""Phonon failure handling: Errno 36, d_matrix, phq_setup FFT/symmetry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from siscforge.calculators.qe.epw_recipes import (
    classify_epw_failure,
    diagnose_qe_step_failure,
    extract_primary_failure_reason,
    is_d_matrix_failure,
    is_kgrid_inconsistency,
    is_phq_readin_failure,
    is_phq_setup_fft_symmetry_failure,
    is_wrong_niter_ph,
    truncate_for_notes,
)
from siscforge.calculators.qe.parser import parse_ph_output, resolve_text_or_path
from siscforge.cli.main import _primary_failure_hint
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.results import PhononResult
from siscforge.shortlist import filter_stable_evaluations, is_dynamically_stable

FIXTURES = Path(__file__).parent / "fixtures" / "qe"
D_MATRIX_OUT = FIXTURES / "ph_d_matrix_error.out"
FFT_SYMM_OUT = FIXTURES / "ph_fft_symmetry_error.out"


def _d_matrix_blob(*, pad: int = 0) -> str:
    text = D_MATRIX_OUT.read_text(encoding="utf-8")
    if pad:
        text = text + ("x" * pad)
    return text


def _fft_blob(*, pad: int = 0) -> str:
    text = FFT_SYMM_OUT.read_text(encoding="utf-8")
    if pad:
        text = text + ("x" * pad)
    return text


def test_resolve_text_or_path_never_raises_errno36_on_log_blob() -> None:
    """Regression: Path(ph.out body).is_file() used to raise File name too long."""
    blob = _d_matrix_blob(pad=20000)
    # Must not raise OSError Errno 36
    text, src = resolve_text_or_path(blob)
    assert src == "<string>"
    assert "d_matrix" in text
    assert "Program PHONON" in text


def test_parse_ph_output_accepts_long_log_text() -> None:
    blob = _d_matrix_blob(pad=15000)
    ph = parse_ph_output(blob)  # must not raise
    assert ph.status == "failed"
    assert ph.n_modes is None or ph.n_modes == 0 or ph.min_frequency_cm1 is None


def test_parse_ph_output_still_reads_fixture_path() -> None:
    path = FIXTURES / "ph_gamma_snippet.out"
    ph = parse_ph_output(path)
    assert ph.status == "ok"
    assert ph.n_modes == 6


def test_d_matrix_fingerprint_and_primary_reason() -> None:
    blob = _d_matrix_blob()
    assert is_d_matrix_failure(blob) is True
    primary = extract_primary_failure_reason(blob, step_name="phonon")
    assert "d_matrix" in primary.lower()
    assert "orthogonal" in primary.lower() or "D_S" in primary or "symmetry" in primary


def test_diagnose_includes_workdir_and_remediation(tmp_path: Path) -> None:
    blob = _d_matrix_blob()
    # Fixed short paths only — never open blob as path
    ph_out = tmp_path / "ph.out"
    ph_out.write_text(blob, encoding="utf-8")
    diag = diagnose_qe_step_failure(blob, work_dir=tmp_path, step_name="phonon")
    assert "d_matrix" in diag.lower() or "orthogonal" in diag.lower()
    assert str(tmp_path) in diag
    assert "ph.out" in diag
    assert "phonon_retry_on_d_matrix" in diag or "nosym" in diag


def test_cli_primary_hint_for_d_matrix() -> None:
    blob = _d_matrix_blob(pad=5000)
    primary = extract_primary_failure_reason(blob, step_name="phonon")
    notes = (
        "Phonon failed (ph.x): "
        + primary
        + "\nwork_dir=/tmp/qe_work/NbN_deadbeef\n"
        + truncate_for_notes(blob, max_chars=400)
    )
    ev = CandidateEvaluation(
        candidate=StructureCandidate(formula="NbN", material_family="tm_nitride"),
        status="failed",
        errors=[primary, "work_dir=/tmp/qe_work/NbN_deadbeef"],
        notes=notes,
    )
    hint = _primary_failure_hint(ev)
    assert "Errno 36" not in hint
    assert "File name too long" not in hint
    assert "d_matrix" in hint.lower()
    # Keep CLI one-liner short
    assert len(hint) <= 120


def test_truncate_for_notes_caps_blob() -> None:
    blob = "A" * 5000
    out = truncate_for_notes(blob, max_chars=500)
    assert len(out) <= 520
    assert "truncated" in out


def test_phonon_retry_config_default() -> None:
    dft = DFTConfig()
    assert dft.phonon_retry_on_d_matrix is True
    assert dft.phonon_retry_on_fft_symmetry is True
    dft2 = DFTConfig(phonon_retry_on_d_matrix=False, phonon_retry_on_fft_symmetry=False)
    assert dft2.phonon_retry_on_d_matrix is False
    assert dft2.phonon_retry_on_fft_symmetry is False


def test_fft_symmetry_fingerprint_not_epw_kgrid() -> None:
    blob = _fft_blob()
    assert is_phq_setup_fft_symmetry_failure(blob) is True
    assert is_kgrid_inconsistency(blob) is False
    assert classify_epw_failure(blob) != "kgrid_inconsistency"
    primary = extract_primary_failure_reason(blob, step_name="phonon")
    assert "FFT" in primary or "fft" in primary.lower()
    assert "phq_setup" in primary.lower()
    assert "EPW" not in primary
    assert "k-grid inconsistency" not in primary.lower()


def test_phonon_only_with_kpoints_line_not_epw_kgrid() -> None:
    """Regression: ordinary 'number of k points' in ph.out must not → EPW k-grid."""
    blob = _fft_blob()
    assert "number of k points" in blob.lower() or "k points" in blob.lower()
    primary = extract_primary_failure_reason(blob, step_name="phonon")
    assert "EPW: k-grid" not in primary
    diag = diagnose_qe_step_failure(blob, work_dir="/tmp/fake", step_name="phonon")
    assert "EPW: k-grid inconsistency" not in diag
    assert "fft" in diag.lower() or "phq_setup" in diag.lower()


def test_cli_primary_hint_for_fft_symmetry() -> None:
    blob = _fft_blob(pad=2000)
    primary = extract_primary_failure_reason(blob, step_name="phonon")
    notes = (
        "Phonon failed (ph.x): "
        + primary
        + "\nwork_dir=/tmp/qe_work/NbTiN_deadbeef\n"
        + truncate_for_notes(blob, max_chars=400)
    )
    ev = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="Nb0.25Ti0.75N", material_family="tm_nitride"
        ),
        status="failed",
        errors=[primary, "work_dir=/tmp/qe_work/NbTiN_deadbeef"],
        notes=notes,
    )
    hint = _primary_failure_hint(ev)
    assert "EPW" not in hint
    assert "k-grid inconsistency" not in hint.lower()
    assert "fft" in hint.lower() or "phq_setup" in hint.lower()
    assert len(hint) <= 120


def test_failed_phonon_not_dynamically_stable() -> None:
    blob = _fft_blob()
    ph = parse_ph_output(blob)
    assert ph.status == "failed"
    assert ph.dynamically_stable is False
    ev = CandidateEvaluation(
        candidate=StructureCandidate(formula="Nb0.5Ti0.5N", material_family="tm_nitride"),
        status="failed",
        phonon=ph,
    )
    assert is_dynamically_stable(ev) is False
    stable = filter_stable_evaluations([ev], mode="stable_only")
    assert stable == []


def test_fft_symmetry_retry_once_then_stop(tmp_path: Path) -> None:
    """Fingerprint triggers one nosym SCF+PH retry; second failure stops (no loop)."""
    from siscforge.calculators.qe.recipes import (
        QEStepResult,
        QEWorkflowResult,
        _maybe_retry_phonon_setup,
    )
    from siscforge.structure.nitrides import build_ternary_nitride

    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    work = tmp_path / "cand"
    work.mkdir()
    scf = work / "02_scf"
    scf.mkdir()
    (scf / "ph.out").write_text(_fft_blob(), encoding="utf-8")
    (scf / "scf.out").write_text(
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )

    cfg = DFTConfig(
        nproc=1,
        do_phonon=True,
        do_epw=False,
        phonon_retry_on_fft_symmetry=True,
        phonon_retry_on_d_matrix=True,
        pseudo_dir=str(tmp_path),
    )
    fail_step = QEStepResult(
        name="ph",
        work_dir=scf,
        returncode=1,
        stdout_path=scf / "ph.out",
        input_path=scf / "ph.in",
        success=False,
        message="ph.x rc=1",
    )
    result = QEWorkflowResult(work_dir=work, structure=s)
    log: list[str] = []
    pw_calls: list[dict] = []
    ph_calls: list[int] = []

    def fake_run_pw(structure, config, work_dir, **kwargs):
        pw_calls.append(dict(kwargs.get("extra_system") or {}))
        out = Path(work_dir) / "scf.out"
        out.write_text(
            "     the Fermi energy is    20.0000 ev\n"
            "!\n     total energy              =     -100.0 Ry\n"
            "     JOB DONE.\n",
            encoding="utf-8",
        )
        return QEStepResult(
            name="scf",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / "scf.in",
            success=True,
            message="ok",
        )

    def fake_run_ph(config, work_dir, **kwargs):
        ph_calls.append(1)
        out = Path(work_dir) / "ph.out"
        # Second attempt still fails with same class
        out.write_text(_fft_blob(), encoding="utf-8")
        return QEStepResult(
            name="ph",
            work_dir=Path(work_dir),
            returncode=1,
            stdout_path=out,
            input_path=Path(work_dir) / "ph.in",
            success=False,
            message="ph.x rc=1",
        )

    with (
        patch("siscforge.calculators.qe.recipes.run_pw", side_effect=fake_run_pw),
        patch("siscforge.calculators.qe.recipes.run_ph", side_effect=fake_run_ph),
        patch(
            "siscforge.calculators.qe.qe_checkpoint.clean_step_outputs",
            return_value=[],
        ),
    ):
        step2, body2 = _maybe_retry_phonon_setup(
            cfg,
            structure=s,
            work_dir=work,
            scf_dir=scf,
            prefix="siscforge",
            qe_env=None,
            for_epw=False,
            outdir=None,
            log=log,
            step=fail_step,
            result=result,
        )

    assert any("FFT grid incompatible" in line for line in log)
    assert any("nosym" in line for line in log)
    assert pw_calls and pw_calls[0].get("nosym") is True
    assert pw_calls[0].get("noinv") is True
    assert len(ph_calls) == 1  # one phonon re-launch only
    assert step2.success is False
    assert is_phq_setup_fft_symmetry_failure(body2)

    # Disabled flag → no retry
    log2: list[str] = []
    cfg_off = cfg.model_copy(update={"phonon_retry_on_fft_symmetry": False})
    with patch("siscforge.calculators.qe.recipes.run_pw") as mock_pw:
        step3, _ = _maybe_retry_phonon_setup(
            cfg_off,
            structure=s,
            work_dir=work,
            scf_dir=scf,
            prefix="siscforge",
            qe_env=None,
            for_epw=False,
            outdir=None,
            log=log2,
            step=fail_step,
            result=QEWorkflowResult(work_dir=work, structure=s),
        )
        mock_pw.assert_not_called()
    assert any("retry disabled" in line for line in log2)
    assert step3.success is False


def test_simulated_failure_message_path_safe() -> None:
    """Build the same message shape recipes use — no Path(log) open."""
    blob = _d_matrix_blob(pad=12000)
    primary = extract_primary_failure_reason(blob, step_name="phonon")
    diag = diagnose_qe_step_failure(blob, work_dir="/tmp/fake_work", step_name="phonon")
    step_msg = truncate_for_notes("ph.x rc=1\n" + blob, max_chars=600)
    message = (
        "Phonon failed (ph.x): "
        + primary
        + "\nwork_dir=/tmp/fake_work\n"
        + diag
        + "\nstep_message="
        + step_msg
    )
    # Using message as path must not be attempted by our parsers
    ph = parse_ph_output(message)
    assert ph.status == "failed"
    assert "Errno 36" not in message
    assert "d_matrix" in message.lower()


def test_errno36_exception_string_classified() -> None:
    """If an old stack still wraps Errno 36 + log, classify from the blob."""
    blob = _d_matrix_blob(pad=3000)
    exc_str = "[Errno 36] File name too long: " + repr(blob)
    # Classification should still find d_matrix in the huge string
    assert is_d_matrix_failure(exc_str)
    primary = extract_primary_failure_reason(exc_str, step_name="calc")
    assert "d_matrix" in primary.lower()


_MPI_ABORT_ONLY = """
     Calculation of q =    0.0000000   0.4082486   0.0000000
--------------------------------------------------------------------------
MPI_ABORT was invoked on rank 15 in communicator MPI_COMM_WORLD
  Proc: [[47524,1],15]
  Errorcode: 1
--------------------------------------------------------------------------
"""

_CRASH_D_MATRIX = """
 %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     task #        15
     from d_matrix : error #         2
     D_S (l=3) for this symmetry operation is not orthogonal
 %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
"""


def test_mpi_abort_ph_out_alone_is_not_d_matrix() -> None:
    """ph.out often only records MPI_ABORT; that must not hide a CRASH sidecar."""
    assert is_d_matrix_failure(_MPI_ABORT_ONLY) is False
    primary = extract_primary_failure_reason(_MPI_ABORT_ONLY, step_name="phonon")
    assert "d_matrix" not in primary.lower()


def test_crash_sidecar_classifies_d_matrix_over_mpi_abort() -> None:
    combined = _CRASH_D_MATRIX + "\n" + _MPI_ABORT_ONLY
    assert is_d_matrix_failure(combined) is True
    primary = extract_primary_failure_reason(combined, step_name="phonon")
    assert "d_matrix" in primary.lower()


def test_d_matrix_only_in_crash_triggers_nosym_retry(tmp_path: Path) -> None:
    """Regression: CRASH has d_matrix, ph.out only MPI_ABORT → retry must fire."""
    from siscforge.calculators.qe.recipes import (
        QEStepResult,
        QEWorkflowResult,
        _maybe_retry_phonon_setup,
    )
    from siscforge.structure.nitrides import build_binary_nitride

    s = build_binary_nitride("Nb")
    work = tmp_path / "cand"
    scf = work / "02_scf"
    scf.mkdir(parents=True)
    (scf / "ph.out").write_text(_MPI_ABORT_ONLY, encoding="utf-8")
    (scf / "CRASH").write_text(_CRASH_D_MATRIX, encoding="utf-8")
    (scf / "scf.out").write_text(
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )

    cfg = DFTConfig(
        nproc=1,
        do_phonon=True,
        do_epw=False,
        phonon_retry_on_d_matrix=True,
        pseudo_dir=str(tmp_path),
    )
    fail_step = QEStepResult(
        name="ph",
        work_dir=scf,
        returncode=1,
        stdout_path=scf / "ph.out",
        input_path=scf / "ph.in",
        success=False,
        message="ph.x rc=1; phonon: MPI_ABORT was invoked on rank 15",
    )
    log: list[str] = []
    pw_calls: list[dict] = []

    def fake_run_pw(structure, config, work_dir, **kwargs):
        pw_calls.append(dict(kwargs.get("extra_system") or {}))
        out = Path(work_dir) / "scf.out"
        out.write_text("     JOB DONE.\n", encoding="utf-8")
        return QEStepResult(
            name="scf",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / "scf.in",
            success=True,
            message="ok",
        )

    def fake_run_ph(config, work_dir, **kwargs):
        out = Path(work_dir) / "ph.out"
        out.write_text("     JOB DONE.\n     freq (    1) = 5.0 [THz] = 166.8 [cm-1]\n")
        return QEStepResult(
            name="ph",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / "ph.in",
            success=True,
            message="ph.x rc=0",
        )

    with (
        patch("siscforge.calculators.qe.recipes.run_pw", side_effect=fake_run_pw),
        patch("siscforge.calculators.qe.recipes.run_ph", side_effect=fake_run_ph),
        patch(
            "siscforge.calculators.qe.qe_checkpoint.clean_step_outputs",
            return_value=[],
        ),
    ):
        step2, _body2 = _maybe_retry_phonon_setup(
            cfg,
            structure=s,
            work_dir=work,
            scf_dir=scf,
            prefix="siscforge",
            qe_env=None,
            for_epw=False,
            outdir=None,
            log=log,
            step=fail_step,
            result=QEWorkflowResult(work_dir=work, structure=s),
        )

    assert any("d_matrix" in line for line in log)
    assert pw_calls and pw_calls[0].get("nosym") is True
    assert step2.success is True


def test_prior_crash_skips_recover_and_hands_to_retry(tmp_path: Path) -> None:
    """Resume must not recover=.true. into a d_matrix CRASH; skip to nosym retry."""
    from siscforge.calculators.qe.recipes import _run_ph_with_optional_recover

    work = tmp_path / "cand"
    scf = work / "02_scf"
    scf.mkdir(parents=True)
    (scf / "ph.out").write_text(_MPI_ABORT_ONLY, encoding="utf-8")
    (scf / "CRASH").write_text(_CRASH_D_MATRIX, encoding="utf-8")
    (scf / "s.dyn0").write_text("   3   3   3\n  10\n", encoding="utf-8")
    log: list[str] = []
    with patch("siscforge.calculators.qe.recipes.run_ph") as mock_ph:
        step = _run_ph_with_optional_recover(
            DFTConfig(do_phonon=True),
            work_dir=work,
            scf_dir=scf,
            prefix="s",
            qe_env=None,
            for_epw=False,
            outdir=None,
            log=log,
        )
        mock_ph.assert_not_called()
    assert step.success is False
    assert any("remediable setup failure" in line for line in log)


_PHQ_READIN_NITER = """
     Program PHONON v.6.7MaX
     Reading input from ph.in
 %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     Error in routine phq_readin (1):
      Wrong niter_ph
 %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     stopping ...
MPI_ABORT was invoked on rank 11 in communicator MPI_COMM_WORLD
"""


def test_wrong_niter_ph_is_classified_not_d_matrix() -> None:
    assert is_wrong_niter_ph(_PHQ_READIN_NITER) is True
    assert is_phq_readin_failure(_PHQ_READIN_NITER) is True
    assert is_d_matrix_failure(_PHQ_READIN_NITER) is False
    primary = extract_primary_failure_reason(_PHQ_READIN_NITER, step_name="phonon")
    assert "niter_ph" in primary.lower()
    assert "QE" in primary or "7.2" in primary


def test_phq_readin_recover_does_not_wipe_dyn(tmp_path: Path) -> None:
    """recover=.true. + Wrong niter_ph must not delete existing dyn files."""
    from siscforge.calculators.qe.recipes import (
        QEStepResult,
        _run_ph_with_optional_recover,
    )

    work = tmp_path / "cand"
    scf = work / "02_scf"
    scf.mkdir(parents=True)
    (scf / "ph.out").write_text(
        "     Program PHONON\n     Representation #   1\n", encoding="utf-8"
    )
    dyn = scf / "s.dyn1"
    dyn.write_text("partial dyn — keep me\n", encoding="utf-8")
    (scf / "_ph0").mkdir()
    (scf / "_ph0" / "keep").write_text("x", encoding="utf-8")
    log: list[str] = []

    def fake_run_ph(config, work_dir, **kwargs):
        assert kwargs.get("recover") is True
        out = Path(work_dir) / "ph.out"
        out.write_text(_PHQ_READIN_NITER, encoding="utf-8")
        (Path(work_dir) / "CRASH").write_text(
            "from phq_readin : error # 1\n Wrong niter_ph \n", encoding="utf-8"
        )
        return QEStepResult(
            name="ph",
            work_dir=Path(work_dir),
            returncode=1,
            stdout_path=out,
            input_path=Path(work_dir) / "ph.in",
            success=False,
            message="ph.x rc=1",
        )

    with patch("siscforge.calculators.qe.recipes.run_ph", side_effect=fake_run_ph):
        step = _run_ph_with_optional_recover(
            DFTConfig(do_phonon=True),
            work_dir=work,
            scf_dir=scf,
            prefix="s",
            qe_env=None,
            for_epw=False,
            outdir=None,
            log=log,
        )
    assert step.success is False
    assert dyn.is_file()
    assert dyn.read_text() == "partial dyn — keep me\n"
    assert any("leaving DFPT artefacts" in line for line in log)
