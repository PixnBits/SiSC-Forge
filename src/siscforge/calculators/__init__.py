"""Calculator backends and registry."""

from siscforge.calculators.base import BaseCalculator, Calculator
from siscforge.calculators.mock import MockCalculator
from siscforge.calculators.registry import (
    clear_registry,
    ensure_builtins_loaded,
    get,
    list_calculators,
    register,
)

# Ensure mock (+ qe aliases) are registered when the package is imported.
ensure_builtins_loaded()

# Optional QE exports (always importable; binaries may still be missing)
from siscforge.calculators.qe import (  # noqa: E402
    QECalculator,
    QEDftuCalculator,
    QEDmftCalculator,
    QEEpwCalculator,
    QENotAvailableError,
    epw_available,
    qe_available,
)

__all__ = [
    "BaseCalculator",
    "Calculator",
    "MockCalculator",
    "QECalculator",
    "QEDftuCalculator",
    "QEDmftCalculator",
    "QEEpwCalculator",
    "QENotAvailableError",
    "clear_registry",
    "ensure_builtins_loaded",
    "epw_available",
    "get",
    "list_calculators",
    "qe_available",
    "register",
]
