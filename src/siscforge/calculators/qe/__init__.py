"""Quantum ESPRESSO calculator backend (jobflow recipes + parsers).

The package imports cleanly even when QE binaries or jobflow are absent.
Use :func:`qe_available` / :func:`require_qe` before launching real runs.
"""

from __future__ import annotations

from siscforge.calculators.qe.calculator import (
    QECalculator,
    QEDftuCalculator,
    QEDmftCalculator,
    QEEpwCalculator,
    QEWannierCalculator,
    register_qe_calculators,
)
from siscforge.calculators.qe.eliashberg import allen_dynes_tc
from siscforge.calculators.qe.env import (
    EPWNotAvailableError,
    QENotAvailableError,
    epw_available,
    jobflow_available,
    qe_available,
    require_epw,
    require_qe,
)
from siscforge.calculators.qe.epw_parser import parse_epw_output
from siscforge.calculators.qe.parser import (
    parse_ph_output,
    parse_pw_output,
    parse_relaxed_structure,
    summarize_frequencies,
)
from siscforge.calculators.qe.pseudos import (
    PseudoResolutionError,
    describe_pseudo_dir,
    resolve_pseudopotentials,
)

__all__ = [
    "EPWNotAvailableError",
    "QECalculator",
    "QEDftuCalculator",
    "QEDmftCalculator",
    "QEEpwCalculator",
    "QEWannierCalculator",
    "QENotFoundError",
    "QENotAvailableError",
    "PseudoResolutionError",
    "allen_dynes_tc",
    "describe_pseudo_dir",
    "epw_available",
    "jobflow_available",
    "parse_epw_output",
    "parse_ph_output",
    "parse_pw_output",
    "parse_relaxed_structure",
    "qe_available",
    "register_qe_calculators",
    "require_epw",
    "require_qe",
    "resolve_pseudopotentials",
    "summarize_frequencies",
]

# Back-compat alias
QENotFoundError = QENotAvailableError
