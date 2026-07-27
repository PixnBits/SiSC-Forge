"""QE subprocess progress heartbeats (no real QE)."""

from __future__ import annotations

from pathlib import Path

from siscforge.calculators.qe.recipes import (
    _format_elapsed,
    _heartbeat_seconds_from_config,
    _log_peek,
    _run_cmd,
)
from siscforge.models.config import DFTConfig, RunConfig


def test_format_elapsed() -> None:
    assert _format_elapsed(5) == "5s"
    assert _format_elapsed(65) == "1m05s"
    assert _format_elapsed(3661) == "1h01m"


def test_heartbeat_seconds_from_config() -> None:
    cfg = DFTConfig()
    assert _heartbeat_seconds_from_config(cfg) == 900  # no run_config → default
    cfg.__dict__["_run_config"] = RunConfig(heartbeat_seconds=300)
    assert _heartbeat_seconds_from_config(cfg) == 300
    cfg.__dict__["_run_config"] = RunConfig(heartbeat_seconds=0)
    assert _heartbeat_seconds_from_config(cfg) == 0


def test_log_peek_prefers_iter_line(tmp_path: Path) -> None:
    log = tmp_path / "ph.out"
    log.write_text(
        "header junk\n"
        "     Representation #   2 mode #   3\n"
        "      iter #  12 total cpu time :  100.0 secs\n"
        "noise\n",
        encoding="utf-8",
    )
    peek = _log_peek(log)
    assert "iter" in peek.lower() or "Representation" in peek


def test_run_cmd_emits_heartbeat(tmp_path: Path) -> None:
    """Sleep 2s with 1s heartbeat → at least one still-running line."""
    out = tmp_path / "out.txt"
    lines: list[str] = []

    def capture(msg: str) -> None:
        lines.append(msg)

    # Use python -c sleep so no QE dependency
    import sys

    rc = _run_cmd(
        [sys.executable, "-c", "import time; print('iter # 1'); time.sleep(2.2)"],
        cwd=tmp_path,
        stdout_path=out,
        heartbeat_seconds=1,
        step_label="test-step",
        on_heartbeat=capture,
    )
    assert rc == 0
    assert any("still running" in ln for ln in lines)
    assert any("test-step" in ln for ln in lines)
    assert any("finished" in ln for ln in lines)
    assert out.is_file()


def test_run_cmd_no_heartbeat_when_zero(tmp_path: Path) -> None:
    import sys

    out = tmp_path / "out.txt"
    lines: list[str] = []
    rc = _run_cmd(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        stdout_path=out,
        heartbeat_seconds=0,
        step_label="quiet",
        on_heartbeat=lambda m: lines.append(m),
    )
    assert rc == 0
    assert lines == []  # no heartbeats when disabled
