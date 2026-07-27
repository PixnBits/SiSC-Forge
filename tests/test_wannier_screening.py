"""Screening Wannier nbndsub policy + frozen-window failure UX (no real QE)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from siscforge.calculators.qe.epw_inputs import (
    build_epw_input,
    default_nbndsub_screening,
)
from siscforge.calculators.qe.epw_recipes import (
    diagnose_epw_failure,
    extract_primary_failure_reason,
    is_frozen_window_overflow,
    run_epw,
)
from siscforge.models.config import DFTConfig, EPWConfig
from siscforge.structure.nitrides import build_binary_nitride, build_ternary_nitride


_FROZEN_OVERFLOW = """
     Program EPW
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     Error in routine dis_windows (1):
     More states in the frozen window than target WFs
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     stopping ...
"""


def test_default_nbndsub_supercell_not_tiny() -> None:
    # 8-atom ternary-like cell, nbnd=64 → was stuck at 10 historically
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    assert len(s) == 8
    n = default_nbndsub_screening(nbnd=64, structure=s, auto=True)
    assert n >= 16
    assert n <= 64
    # Policy: min(nbnd, max(16, 4*n_at, nbnd//2)) = min(64, max(16, 32, 32)) = 32
    assert n == 32


def test_default_nbndsub_binary_reasonable() -> None:
    s = build_binary_nitride("Nb")
    n = default_nbndsub_screening(nbnd=28, structure=s, auto=True)
    assert n >= 16
    assert n <= 28


def test_default_nbndsub_raises_undersized_explicit() -> None:
    s = build_ternary_nitride("Nb", "Ti", 0.5, supercell=(2, 2, 1))
    n = default_nbndsub_screening(nbnd=64, structure=s, explicit=10, auto=True)
    assert n >= 16
    # Force exact explicit when auto disabled
    n2 = default_nbndsub_screening(nbnd=64, structure=s, explicit=10, auto=False)
    assert n2 == 10


def test_build_epw_input_uses_auto_nbndsub() -> None:
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    cfg = DFTConfig(
        do_epw=True,
        nbnd=64,
        quality_tag="screening",
        epw=EPWConfig(enabled=True, nbndsub=10, auto_nbndsub=True),
    )
    text = build_epw_input(
        cfg, prefix="t", outdir="./", dvscf_dir="./save", structure=s, fermi_eV=20.0
    )
    assert "nbndsub     = 32" in text or "nbndsub     = 3" in text
    # Tight frozen window for screening
    assert "dis_froz_max=" in text
    # Ef+1.0 for tight window (not old +2 with wide freeze)
    assert "dis_froz_max= 21.0000" in text


def test_is_frozen_window_overflow() -> None:
    assert is_frozen_window_overflow(_FROZEN_OVERFLOW)
    assert not is_frozen_window_overflow("JOB DONE\n lambda = 1.0\n")


def test_extract_primary_failure_frozen_window() -> None:
    reason = extract_primary_failure_reason(_FROZEN_OVERFLOW, step_name="epw")
    assert "frozen window" in reason.lower() or "nbndsub" in reason.lower()
    assert "rc=1" not in reason  # not just the return code


def test_diagnose_includes_primary_workdir_tail() -> None:
    diag = diagnose_epw_failure(
        _FROZEN_OVERFLOW,
        work_dir="/tmp/fake_epw_work",
        step_name="epw",
        include_tail=True,
        tail_lines=10,
    )
    assert "primary:" in diag
    assert "work_dir:" in diag
    assert "frozen" in diag.lower() or "nbndsub" in diag.lower()
    assert "More states in the frozen window" in diag
    assert "--- output tail ---" in diag


def test_run_epw_retry_bumps_nbndsub(tmp_path: Path) -> None:
    """First launch hits frozen overflow; retry with larger nbndsub succeeds."""
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    cfg = DFTConfig(
        nproc=2,
        do_epw=True,
        nbnd=64,
        quality_tag="screening",
        epw=EPWConfig(
            enabled=True,
            npool=2,
            nbndsub=10,
            auto_nbndsub=True,
            wannier_retry_on_froz_overflow=True,
        ),
    )
    calls: list[int] = []

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None):
        # Count launches; first fails with frozen window, second succeeds
        n = len(calls)
        calls.append(n)
        out = Path(stdout_path)
        if n == 0:
            out.write_text(_FROZEN_OVERFLOW, encoding="utf-8")
            return 1
        out.write_text(
            "Electron-phonon coupling strength = 1.0\n"
            "omega_log is 20.0 meV\n"
            "Estimated Allen-Dynes Tc = 10.0 K for mus = 0.10\n"
            "JOB DONE.\n",
            encoding="utf-8",
        )
        return 0

    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"
    fake_env.mpirun = "mpirun"
    fake_env.pw = "/usr/bin/pw.x"
    fake_env.ph = "/usr/bin/ph.x"

    with (
        patch(
            "siscforge.calculators.qe.epw_recipes.require_epw",
            return_value=fake_env,
        ),
        patch(
            "siscforge.calculators.qe.recipes._run_cmd",
            side_effect=fake_run_cmd,
        ),
        patch(
            "siscforge.calculators.qe.recipes._mpi_prefix",
            return_value=["mpirun", "-np", "2"],
        ),
    ):
        step, eph = run_epw(cfg, tmp_path, structure=s, prefix="t")

    assert len(calls) == 2  # original + one retry
    assert step.success
    assert "retry" in step.message.lower() or "nbndsub" in step.message.lower()
    assert eph is not None
    assert eph.lambda_total is not None


def test_run_epw_failure_message_is_high_signal(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    cfg = DFTConfig(
        nproc=1,
        do_epw=True,
        nbnd=28,
        quality_tag="screening",
        epw=EPWConfig(
            enabled=True,
            npool=1,
            auto_nbndsub=False,
            nbndsub=10,
            wannier_retry_on_froz_overflow=False,
        ),
    )

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None):
        Path(stdout_path).write_text(_FROZEN_OVERFLOW, encoding="utf-8")
        return 1

    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"

    with (
        patch(
            "siscforge.calculators.qe.epw_recipes.require_epw",
            return_value=fake_env,
        ),
        patch(
            "siscforge.calculators.qe.recipes._run_cmd",
            side_effect=fake_run_cmd,
        ),
        patch(
            "siscforge.calculators.qe.recipes._mpi_prefix",
            return_value=[],
        ),
    ):
        step, eph = run_epw(cfg, tmp_path, structure=s, prefix="t")

    assert not step.success
    # CLI-facing first segment should not be bare "epw.x rc=1"
    first = step.message.splitlines()[0]
    assert "frozen" in first.lower() or "Wannier" in first or "nbndsub" in first.lower()
    assert "work_dir" in step.message
    assert "More states in the frozen window" in step.message
