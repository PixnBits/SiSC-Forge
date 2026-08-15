"""SiSC-Forge: silicon-compatible superconductor materials discovery."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("siscforge")
except PackageNotFoundError:  # pragma: no cover - source tree without install
    # Fallback only. Authoritative value is [project].version in pyproject.toml.
    __version__ = "0.4.4"

__all__ = ["__version__"]
