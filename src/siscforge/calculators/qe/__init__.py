"""Quantum ESPRESSO calculator backend (jobflow recipes + parsers).

The package imports cleanly even when QE binaries or jobflow are absent.
Use :func:`qe_available` / :func:`require_qe` before launching real runs.
"""

from __future__ import annotations

from siscforge.calculators.qe.calculator import QECalculator, register_qe_calculators
from siscforge.calculators.qe.env import (
    QENotAvailableError,
    jobflow_available,
    qe_available,
    require_qe,
)
from siscforge.calculators.qe.parser import (
    parse_ph_output,
    parse_pw_output,
    summarize_frequencies,
)

__all__ = [
    "QECalculator",
    "QENotFoundError",
    "QENotAvailableError",
    "jobflow_available",
    "parse_ph_output",
    "parse_pw_output",
    "qe_available",
    "register_qe_calculators",
    "require_qe",
    "summarize_frequencies",
]

# Back-compat alias
QENotFoundError = QENotAvailableError
