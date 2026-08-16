"""Calculator-level EPW gate on imaginary / non-stable phonons (#52)."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.calculators.qe.calculator import QECalculator
from siscforge.calculators.qe.env import QEEnvironment
from siscforge.calculators.qe.epw_recipes import (
    EPW_BLOCKED_SOFT_TOKEN,
    EPWWorkflowResult,
    epw_blocked_on_soft_phonon,
    soft_phonon_epw_block_message,
)
from siscforge.models.config import DFTConfig, EPWConfig
from siscforge.models.results import PhononResult, SCFResult
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride


def _phonon(*, imag: bool = False, stable: bool | None = None) -> PhononResult:
    if stable is None:
        stable = not imag
    return PhononResult(
        status="ok",
        quality_tag="screening",
        has_imaginary_modes=imag,
        dynamically_stable=stable,
        min_frequency_cm1=-12.0 if imag else 80.0,
    )


def test_gate_fires_on_imaginary_modes() -> None:
    cfg = DFTConfig(do_epw=True)
    assert epw_blocked_on_soft_phonon(_phonon(imag=True), cfg) is True


def test_gate_fires_on_not_dynamically_stable() -> None:
    cfg = DFTConfig(do_epw=True)
    ph = _phonon(imag=False, stable=False)
    assert epw_blocked_on_soft_phonon(ph, cfg) is True


def test_gate_does_not_fire_on_stable_cell() -> None:
    cfg = DFTConfig(do_epw=True)
    assert epw_blocked_on_soft_phonon(_phonon(imag=False, stable=True), cfg) is False


def test_allow_on_soft_override() -> None:
    cfg = DFTConfig(do_epw=True, epw=EPWConfig(enabled=True, allow_on_soft=True))
    assert epw_blocked_on_soft_phonon(_phonon(imag=True), cfg) is False
    assert epw_blocked_on_soft_phonon(_phonon(imag=False, stable=False), cfg) is False


def test_none_phonon_does_not_block() -> None:
    assert epw_blocked_on_soft_phonon(None, DFTConfig(do_epw=True)) is False


def test_incomplete_phonon_parse_does_not_block() -> None:
    """Failed DFPT parse sets dynamically_stable=False but is not a soft cell."""
    ph = PhononResult(
        status="failed",
        quality_tag="screening",
        has_imaginary_modes=False,
        dynamically_stable=False,
        min_frequency_cm1=None,
    )
    assert epw_blocked_on_soft_phonon(ph, DFTConfig(do_epw=True)) is False


def test_block_message_is_machine_readable() -> None:
    msg = soft_phonon_epw_block_message(_phonon(imag=True))
    assert EPW_BLOCKED_SOFT_TOKEN in msg
    assert "has_imaginary_modes=true" in msg
    assert "allow_on_soft" in msg


def _candidate():
    return structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
        quality_tag="screening",
        source="epw_soft_gate",
    )


def test_calculator_skips_epw_follow_ons_when_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QECalculator must not launch Wannier/DMFT after a soft-phonon EPW block."""
    env = QEEnvironment(pw="/bin/true", ph="/bin/true", epw="/bin/true")
    monkeypatch.setattr(
        "siscforge.calculators.qe.calculator.require_qe",
        lambda **_kw: env,
    )
    monkeypatch.setattr(
        "siscforge.calculators.qe.pseudos.resolve_pseudopotentials",
        lambda *_a, **_k: {"Nb": "Nb.upf", "N": "N.upf"},
    )

    launched: list[str] = []

    def fake_epw(structure, _dft, work_dir, **_kw):
        launched.append("epw_recipe")
        ph = _phonon(imag=True)
        return EPWWorkflowResult(
            work_dir=Path(work_dir),
            structure=structure,
            scf=SCFResult(status="ok", quality_tag="screening"),
            phonon=ph,
            electron_phonon=None,
            success=True,
            message=soft_phonon_epw_block_message(ph),
        )

    monkeypatch.setattr(
        "siscforge.calculators.qe.calculator.run_relax_scf_phonon_epw",
        fake_epw,
    )

    def boom(*_a, **_k):
        launched.append("wannier")
        raise AssertionError("Wannier must not run after EPW-blocked")

    monkeypatch.setattr(
        "siscforge.calculators.qe.recipes.run_wannier_after_scf",
        boom,
    )

    calc = QECalculator(
        dft=DFTConfig(
            engine="qe-epw",
            do_epw=True,
            do_wannier=True,
            do_phonon=True,
            work_dir=str(tmp_path),
        ),
        work_root=tmp_path,
        force_epw=True,
        force_wannier=True,
    )
    ev = calc.run(_candidate())
    assert launched == ["epw_recipe"]
    assert ev.electron_phonon is None
    assert ev.phonon is not None
    assert ev.phonon.has_imaginary_modes is True
    assert ev.status == "ok"
    assert EPW_BLOCKED_SOFT_TOKEN in (ev.notes or "")


def test_recipe_returns_before_pp_nscf_epw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_relax_scf_phonon_epw`` must skip pp.py / NSCF / EPW on soft DFPT."""
    from unittest.mock import MagicMock

    from siscforge.calculators.qe.epw_recipes import run_relax_scf_phonon_epw
    from siscforge.calculators.qe.qe_checkpoint import StepProbe, WorkdirCheckpoint

    structure = build_binary_nitride("Nb")
    cfg = DFTConfig(do_epw=True, do_relax=True, do_phonon=True, quality_tag="screening")
    launched: list[str] = []

    def boom(name: str):
        def _inner(*_a, **_k):
            launched.append(name)
            raise AssertionError(f"{name} must not run after soft-phonon gate")

        return _inner

    def fake_probe(work_dir, _config, **_kwargs):
        ckpt = WorkdirCheckpoint(work_dir=Path(work_dir), prefix="siscforge")
        ph = _phonon(imag=True)
        ckpt.steps["vc-relax"] = StepProbe(
            name="vc-relax",
            complete=True,
            message="ok",
            relaxed_structure=structure,
        )
        ckpt.steps["scf"] = StepProbe(
            name="scf",
            complete=True,
            message="ok",
            scf=SCFResult(status="ok", quality_tag="screening"),
        )
        ckpt.steps["phonon"] = StepProbe(
            name="phonon", complete=True, message="ok", phonon=ph
        )
        for name in ("epw_pp", "nscf", "epw"):
            ckpt.steps[name] = StepProbe(name=name, complete=False, message="missing")
        ckpt.log = ["skip phonon (checkpoint)"]
        return ckpt

    env = MagicMock()
    env.pw = "/bin/true"
    env.ph = "/bin/true"
    env.epw = "/bin/true"
    env.mpirun = None

    monkeypatch.setattr(
        "siscforge.calculators.qe.epw_recipes.require_epw", lambda: env
    )
    monkeypatch.setattr(
        "siscforge.calculators.qe.qe_checkpoint.probe_workdir", fake_probe
    )
    monkeypatch.setattr(
        "siscforge.calculators.qe.epw_recipes.run_epw_pp", boom("epw_pp")
    )
    monkeypatch.setattr(
        "siscforge.calculators.qe.epw_recipes.run_nscf_for_epw", boom("nscf")
    )
    monkeypatch.setattr("siscforge.calculators.qe.epw_recipes.run_epw", boom("epw"))

    result = run_relax_scf_phonon_epw(structure, cfg, tmp_path, prefix="siscforge")
    assert launched == []
    assert result.success is True
    assert result.electron_phonon is None
    assert result.phonon is not None
    assert result.phonon.has_imaginary_modes is True
    assert EPW_BLOCKED_SOFT_TOKEN in (result.message or "")
