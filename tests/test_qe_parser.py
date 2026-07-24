"""Unit tests for QE / phonon output parsers (no QE binary required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.calculators.qe.parser import (
    parse_frequency_list,
    parse_ph_output,
    parse_pw_output,
    summarize_frequencies,
)

FIXTURES = Path(__file__).parent / "fixtures" / "qe"


def test_parse_pw_scf_fixture() -> None:
    path = FIXTURES / "pw_scf_snippet.out"
    scf = parse_pw_output(path, quality_tag="screening")
    assert scf.status == "ok"
    assert scf.total_energy_eV is not None
    # -120.45678912 Ry * 13.6057 ≈ -1639 eV
    assert scf.total_energy_eV < -1000.0
    assert scf.is_metallic is True
    assert scf.quality_tag == "screening"


def test_parse_ph_gamma_fixture() -> None:
    path = FIXTURES / "ph_gamma_snippet.out"
    ph = parse_ph_output(path)
    assert ph.status == "ok"
    assert ph.n_modes == 6
    assert ph.min_frequency_cm1 is not None
    assert ph.max_frequency_cm1 is not None
    assert ph.min_frequency_cm1 > -1.0
    assert 500.0 < ph.max_frequency_cm1 < 520.0
    assert ph.has_imaginary_modes is False
    assert ph.dynamically_stable is True


def test_parse_ph_qe7_dual_unit_format() -> None:
    """QE ≥ 7 prints freq as THz then cm-1 on the same line."""
    path = FIXTURES / "ph_qe7_dual_unit.out"
    ph = parse_ph_output(path)
    assert ph.status == "ok"
    assert ph.n_modes == 6
    assert ph.min_frequency_cm1 == pytest.approx(-248.561155, rel=1e-5)
    assert ph.max_frequency_cm1 == pytest.approx(70.354733, rel=1e-5)
    assert ph.has_imaginary_modes is True
    assert ph.dynamically_stable is False


def test_parse_ph_imaginary_fixture() -> None:
    path = FIXTURES / "ph_imaginary_snippet.out"
    ph = parse_ph_output(path)
    assert ph.has_imaginary_modes is True
    assert ph.dynamically_stable is False
    assert ph.min_frequency_cm1 is not None
    assert ph.min_frequency_cm1 < 0


def test_summarize_frequencies_empty() -> None:
    s = summarize_frequencies([])
    assert s["n_modes"] == 0
    assert s["dynamically_stable"] is True


def test_parse_frequency_list() -> None:
    ph = parse_frequency_list([1.0, 2.0, 500.0])
    assert ph.n_modes == 3
    assert ph.max_frequency_cm1 == 500.0
    assert ph.dynamically_stable is True
