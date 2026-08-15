"""Package version stays aligned with pyproject.toml (issue #25)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from siscforge import __version__
from siscforge.cli.main import app

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == "0.4.4"
    assert __version__ == data["project"]["version"]


def test_init_fallback_matches_pyproject() -> None:
    """Keep the importlib.metadata fallback in lockstep with pyproject.toml."""
    init = (ROOT / "src" / "siscforge" / "__init__.py").read_text(encoding="utf-8")
    fallbacks = re.findall(r'__version__ = "([^"]+)"', init)
    assert fallbacks, "expected a string fallback in __init__.py"
    assert all(v == "0.4.4" for v in fallbacks)


def test_cli_version_flag() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.4.4" in result.stdout
