"""Phonon failure handling: Errno 36 path bug + d_matrix classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.calculators.qe.epw_recipes import (
    diagnose_qe_step_failure,
    extract_primary_failure_reason,
    is_d_matrix_failure,
    truncate_for_notes,
)
from siscforge.calculators.qe.parser import parse_ph_output, resolve_text_or_path
from siscforge.cli.main import _primary_failure_hint
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig

FIXTURES = Path(__file__).parent / "fixtures" / "qe"
D_MATRIX_OUT = FIXTURES / "ph_d_matrix_error.out"


def _d_matrix_blob(*, pad: int = 0) -> str:
    text = D_MATRIX_OUT.read_text(encoding="utf-8")
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
    dft2 = DFTConfig(phonon_retry_on_d_matrix=False)
    assert dft2.phonon_retry_on_d_matrix is False


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
