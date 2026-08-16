"""QECalculator.run() evaluation construction — phonon-only success path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from siscforge import __version__
from siscforge.calculators.qe.calculator import QECalculator
from siscforge.calculators.qe.env import QEEnvironment
from siscforge.calculators.qe.recipes import QEWorkflowResult
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import DFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import PhononResult, SCFResult
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride


def _candidate():
    return structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
        quality_tag="screening",
        source="qe_calculator_regression",
    )


def _successful_phonon_wf(structure, work_dir: Path) -> QEWorkflowResult:
    return QEWorkflowResult(
        work_dir=work_dir,
        structure=structure,
        scf=SCFResult(status="ok", quality_tag="screening", total_energy_eV=-10.0),
        phonon=PhononResult(
            status="ok",
            quality_tag="screening",
            dynamically_stable=True,
            has_imaginary_modes=False,
            min_frequency_cm1=50.0,
        ),
        success=True,
        message="ph.x phonon rc=0",
    )


def _patch_phonon_success(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub env + DFPT so QECalculator.run() hits evaluation construction."""
    env = QEEnvironment(pw="/bin/true", ph="/bin/true")
    monkeypatch.setattr(
        "siscforge.calculators.qe.calculator.require_qe",
        lambda **_kw: env,
    )
    monkeypatch.setattr(
        "siscforge.calculators.qe.pseudos.resolve_pseudopotentials",
        lambda *_a, **_k: {"Nb": "Nb.upf", "N": "N.upf"},
    )
    called: list[str] = []

    def fake_relax_scf_phonon(structure, _dft, work_dir, **_kw):
        called.append("phonon")
        return _successful_phonon_wf(structure, Path(work_dir))

    monkeypatch.setattr(
        "siscforge.calculators.qe.calculator.run_relax_scf_phonon",
        fake_relax_scf_phonon,
    )
    return called


def test_qe_calculator_run_does_not_bind_provenance_locally() -> None:
    """Local `import Provenance` makes the name function-scoped (the bug)."""
    assert "Provenance" not in QECalculator.run.__code__.co_varnames


def test_phonon_only_success_returns_evaluation_with_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phonon-only success must construct CandidateEvaluation.provenance.

    Regression: Phase-3 Wannier/DMFT except blocks imported Provenance
    locally, so this path raised UnboundLocalError after ph.x rc=0.
    """
    _patch_phonon_success(monkeypatch)
    calc = QECalculator(
        dft=DFTConfig(
            engine="qe",
            do_relax=True,
            do_phonon=True,
            do_epw=False,
            do_wannier=False,
            do_dmft=False,
            work_dir=str(tmp_path),
        ),
        work_root=tmp_path,
    )
    ev = calc.run(_candidate())
    assert isinstance(ev, CandidateEvaluation)
    assert ev.status == "ok"
    assert ev.calculator_name == "qe"
    assert ev.phonon is not None
    assert ev.phonon.status == "ok"
    assert ev.wannier is None
    assert ev.dmft is None
    assert isinstance(ev.provenance, Provenance)
    assert ev.provenance.source == "qe_calculator"
    assert ev.provenance.software.get("siscforge") == __version__
    assert ev.provenance.parameters.get("do_epw") is False
    assert ev.provenance.parameters.get("do_wannier") is False
    assert ev.provenance.parameters.get("do_dmft") is False


def test_wannier_exception_still_builds_failure_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wannier except path must still attach a failed WannierResult."""
    _patch_phonon_success(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("wannier90.x exploded")

    monkeypatch.setattr(
        "siscforge.calculators.qe.recipes.run_wannier_after_scf",
        boom,
    )
    calc = QECalculator(
        dft=DFTConfig(
            engine="qe",
            do_phonon=True,
            do_epw=False,
            do_wannier=True,
            do_dmft=False,
            work_dir=str(tmp_path),
        ),
        work_root=tmp_path,
    )
    ev = calc.run(_candidate())
    assert ev.status == "ok"
    assert ev.phonon is not None
    assert ev.wannier is not None
    assert ev.wannier.status == "failed"
    assert ev.wannier.wannier_ok is False
    assert ev.wannier.ready_for_dmft is False
    assert isinstance(ev.wannier.provenance, Provenance)
    assert ev.wannier.provenance.source == "qe_wannier"
    assert ev.wannier.provenance.software.get("siscforge") == __version__
    assert "exploded" in (ev.wannier.raw or {}).get("error", "")
    assert isinstance(ev.provenance, Provenance)
    assert ev.provenance.source == "qe_calculator"


def test_dmft_exception_still_builds_failure_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DMFT except path must still attach a failed DMFTResult."""
    _patch_phonon_success(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("solid_dmft exploded")

    monkeypatch.setattr(
        "siscforge.calculators.qe.recipes.run_dmft_after_wannier",
        boom,
    )
    calc = QECalculator(
        dft=DFTConfig(
            engine="qe",
            do_phonon=True,
            do_epw=False,
            do_wannier=False,
            do_dmft=True,
            work_dir=str(tmp_path),
        ),
        work_root=tmp_path,
    )
    ev = calc.run(_candidate())
    assert ev.status == "ok"
    assert ev.phonon is not None
    assert ev.dmft is not None
    assert ev.dmft.status == "failed"
    assert ev.dmft.converged is False
    assert ev.dmft.failure_class in {"other", "solver_missing"}
    assert isinstance(ev.dmft.provenance, Provenance)
    assert ev.dmft.provenance.source == "qe_dmft"
    assert ev.dmft.provenance.software.get("siscforge") == __version__
    assert "exploded" in ev.dmft.provenance.notes
    assert isinstance(ev.provenance, Provenance)
    assert ev.provenance.source == "qe_calculator"


def test_phonon_only_success_does_not_call_wannier_or_dmft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: the success-path regression must not enter Phase-3 handlers."""
    called = _patch_phonon_success(monkeypatch)
    wannier = MagicMock(side_effect=AssertionError("wannier must not run"))
    dmft = MagicMock(side_effect=AssertionError("dmft must not run"))
    monkeypatch.setattr(
        "siscforge.calculators.qe.recipes.run_wannier_after_scf",
        wannier,
    )
    monkeypatch.setattr(
        "siscforge.calculators.qe.recipes.run_dmft_after_wannier",
        dmft,
    )
    calc = QECalculator(
        dft=DFTConfig(
            do_phonon=True,
            do_epw=False,
            do_wannier=False,
            do_dmft=False,
            work_dir=str(tmp_path),
        ),
        work_root=tmp_path,
    )
    ev = calc.run(_candidate())
    assert called == ["phonon"]
    wannier.assert_not_called()
    dmft.assert_not_called()
    assert ev.status == "ok"
    assert isinstance(ev.provenance, Provenance)
