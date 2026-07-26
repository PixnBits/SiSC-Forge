"""EPW MPI topology validation and launch-command tests (no real QE)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from siscforge.calculators.qe.epw_parallel import (
    epw_npool_cli_args,
    resolve_epw_parallel,
    validate_epw_parallel,
)
from siscforge.calculators.qe.epw_recipes import resolve_epw_launch_topology, run_epw
from siscforge.models.config import DFTConfig, EPWConfig
from siscforge.structure.nitrides import build_binary_nitride


def test_validate_nproc8_npool1_invalid() -> None:
    plan = validate_epw_parallel(8, 1, 1, fine_grid=True)
    assert not plan.ok
    assert "nproc" in plan.message.lower()
    assert "npool" in plan.message.lower()
    assert "8" in plan.message


def test_validate_nproc8_npool8_ok() -> None:
    plan = validate_epw_parallel(8, 8, 1, fine_grid=True)
    assert plan.ok
    assert plan.npool == 8
    assert plan.nproc == 8


def test_validate_nproc8_npool3_invalid() -> None:
    plan = validate_epw_parallel(8, 3, 1, fine_grid=True)
    assert not plan.ok
    assert "3" in plan.message
    assert "8" in plan.message


def test_validate_fine_grid_rejects_nimage() -> None:
    plan = validate_epw_parallel(8, 4, 2, fine_grid=True)
    assert not plan.ok
    assert "image" in plan.message.lower() or "nimage" in plan.message.lower()


def test_resolve_auto_fix_npool_to_nproc() -> None:
    plan = resolve_epw_parallel(8, 1, 1, fine_grid=True, auto_fix=True)
    assert plan.ok
    assert plan.npool == 8
    assert plan.nproc == 8
    assert plan.auto_fixed is True
    assert plan.original_npool == 1
    assert "auto-set" in plan.message.lower()
    assert "npool=8" in plan.message


def test_resolve_auto_fix_npool3_to_nproc8() -> None:
    plan = resolve_epw_parallel(8, 3, 1, fine_grid=True, auto_fix=True)
    assert plan.ok
    assert plan.npool == 8
    assert plan.auto_fixed is True


def test_resolve_strict_no_auto_fix() -> None:
    plan = resolve_epw_parallel(8, 1, 1, fine_grid=True, auto_fix=False)
    assert not plan.ok


def test_resolve_already_ok_no_auto() -> None:
    plan = resolve_epw_parallel(4, 4, 1, fine_grid=True, auto_fix=True)
    assert plan.ok
    assert plan.auto_fixed is False
    assert plan.npool == 4


def test_epw_npool_cli_args_always_present() -> None:
    assert epw_npool_cli_args(1) == ["-npool", "1"]
    assert epw_npool_cli_args(8) == ["-npool", "8"]
    assert epw_npool_cli_args(0) == ["-npool", "1"]


def test_resolve_epw_launch_topology_mutates_npool() -> None:
    cfg = DFTConfig(
        nproc=8,
        do_epw=True,
        epw=EPWConfig(enabled=True, npool=1),
    )
    fixed, msg = resolve_epw_launch_topology(cfg)
    assert fixed.epw.npool == 8
    assert fixed.nproc == 8
    assert "auto-set" in msg.lower()


def test_resolve_epw_launch_topology_strict_raises() -> None:
    cfg = DFTConfig(
        nproc=8,
        do_epw=True,
        epw=EPWConfig(enabled=True, npool=1, strict_parallel=True),
    )
    with pytest.raises(ValueError, match="nproc"):
        resolve_epw_launch_topology(cfg)


def test_run_epw_command_includes_npool_matching_nproc(tmp_path: Path) -> None:
    """epw.x command must include -npool equal to nproc after auto-fix."""
    s = build_binary_nitride("Nb")
    cfg = DFTConfig(
        nproc=8,
        do_epw=True,
        epw=EPWConfig(enabled=True, npool=1, eliashberg=True),
    )
    captured: dict = {}

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None):
        captured["cmd"] = list(cmd)
        # Minimal JOB DONE so parse does not explode
        Path(stdout_path).write_text(
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
            return_value=["mpirun", "--oversubscribe", "-np", "8"],
        ),
    ):
        step, eph = run_epw(cfg, tmp_path, structure=s, prefix="test")

    cmd = captured["cmd"]
    assert "epw.x" in " ".join(str(c) for c in cmd) or any(
        "epw" in str(c) for c in cmd
    )
    assert "-npool" in cmd
    idx = cmd.index("-npool")
    assert cmd[idx + 1] == "8"
    assert step.success or eph is not None or step.returncode == 0
    assert "npool=8" in step.message or "auto-set" in step.message.lower()


def test_run_epw_strict_refuses_launch(tmp_path: Path) -> None:
    cfg = DFTConfig(
        nproc=8,
        do_epw=True,
        epw=EPWConfig(enabled=True, npool=1, strict_parallel=True),
    )
    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"

    with patch(
        "siscforge.calculators.qe.epw_recipes.require_epw",
        return_value=fake_env,
    ):
        step, eph = run_epw(cfg, tmp_path, prefix="t")

    assert not step.success
    assert eph is None
    assert "nproc" in step.message.lower()
    assert (tmp_path / "epw.out").is_file()
