"""Simple name → calculator registry."""

from __future__ import annotations

from siscforge.calculators.base import Calculator

_REGISTRY: dict[str, Calculator] = {}


def register(
    calculator: Calculator,
    *,
    name: str | None = None,
    overwrite: bool = False,
) -> Calculator:
    """Register a calculator instance under ``calculator.name`` (or *name*).

    Returns the calculator for convenient decorator / factory use.
    """
    key = name or calculator.name
    if not key:
        raise ValueError("Calculator must have a non-empty name")
    if key in _REGISTRY and not overwrite:
        raise ValueError(f"Calculator already registered: {key!r}")
    _REGISTRY[key] = calculator
    return calculator


def get(name: str) -> Calculator:
    """Look up a registered calculator by name."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown calculator {name!r}. Registered: {known}") from exc


def list_calculators() -> list[str]:
    """Return sorted registered calculator names."""
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """Remove all registrations (intended for tests)."""
    _REGISTRY.clear()


def ensure_builtins_loaded() -> None:
    """Import built-in calculators so they self-register."""
    # Local import avoids circular import at package load time.
    from siscforge.calculators import mock as _mock  # noqa: F401

    try:
        from siscforge.calculators.qe.calculator import register_qe_calculators

        register_qe_calculators()
    except Exception:  # noqa: BLE001 — QE package must never break mock path
        pass
