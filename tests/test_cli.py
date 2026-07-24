"""CLI smoke tests for enumerate / rank / run --dry-run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from siscforge.cli.main import app, generate_candidates
from siscforge.models import CampaignConfig, CandidateEvaluation

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DUMMY_CAMPAIGN = EXAMPLES / "dummy_campaign.yaml"
NBTI_CAMPAIGN = EXAMPLES / "nbti_n_strain.yaml"


@pytest.fixture
def campaign_yaml(tmp_path: Path) -> Path:
    """Copy-ish minimal campaign pointing at tmp output dir."""
    cfg = CampaignConfig(
        name="cli_test",
        dry_run=True,
        enumeration={
            "formulas": ["NbN", "TiN", "ZrN"],
            "strain_values": [0.0],
            "max_candidates": 3,
        },
        output_dir=str(tmp_path / "out"),
        export_formats=["json", "csv"],
    )
    path = tmp_path / "campaign.yaml"
    cfg.to_yaml(path)
    return path


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "enumerate" in result.stdout
    assert "rank" in result.stdout
    assert "run" in result.stdout


def test_enumerate_help() -> None:
    result = runner.invoke(app, ["enumerate", "--help"])
    assert result.exit_code == 0


def test_rank_help() -> None:
    result = runner.invoke(app, ["rank", "--help"])
    assert result.exit_code == 0


def test_run_help() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.stdout


def test_enumerate_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "cands.json"
    result = runner.invoke(app, ["enumerate", "-n", "4", "-o", str(out)])
    assert result.exit_code == 0, result.stdout
    data = json.loads(out.read_text())
    assert len(data) == 4
    assert "formula" in data[0]
    # Real structures should carry lattice + CIF
    assert data[0].get("lattice_abc") is not None
    assert data[0].get("structure_cif")


def test_run_dry_run(campaign_yaml: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["run", "--dry-run", str(campaign_yaml)],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    out_dir = tmp_path / "out"
    assert (out_dir / "evaluations.json").is_file()
    assert (out_dir / "candidates.json").is_file()
    assert (out_dir / "evaluations.csv").is_file()

    evaluations = json.loads((out_dir / "evaluations.json").read_text())
    assert 3 <= len(evaluations) <= 5
    for item in evaluations:
        ev = CandidateEvaluation.model_validate(item)
        assert ev.status == "mock"
        assert ev.rank is not None
        assert ev.composite_score is not None


def test_run_with_example_campaign(tmp_path: Path) -> None:
    """Exercise the checked-in examples/dummy_campaign.yaml with overridden output."""
    if not DUMMY_CAMPAIGN.is_file():
        pytest.skip("example campaign missing")
    out = tmp_path / "example_out"
    result = runner.invoke(
        app,
        ["run", "--dry-run", str(DUMMY_CAMPAIGN), "-o", str(out)],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads((out / "evaluations.json").read_text())
    assert len(data) >= 3
    ranks = [row["rank"] for row in data]
    assert ranks == sorted(ranks)
    # Real Si-feasibility scorer (not mock-only version)
    for row in data:
        ev = CandidateEvaluation.model_validate(row)
        assert ev.si_feasibility is not None
        assert 0.0 <= ev.si_feasibility.total <= 100.0
        assert ev.candidate.structure_cif


def test_run_nbti_strain_campaign(tmp_path: Path) -> None:
    """End-to-end dry-run with the Nb-Ti-N strain example."""
    if not NBTI_CAMPAIGN.is_file():
        pytest.skip("nbti example campaign missing")
    out = tmp_path / "nbti_out"
    result = runner.invoke(
        app,
        ["run", "--dry-run", str(NBTI_CAMPAIGN), "-o", str(out)],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads((out / "evaluations.json").read_text())
    assert len(data) >= 5
    for row in data:
        ev = CandidateEvaluation.model_validate(row)
        assert ev.status == "mock"
        assert ev.si_feasibility is not None
        assert ev.si_feasibility.version == "0.1"
        assert ev.candidate.material_family == "tm_nitride"
        assert ev.candidate.structure_cif
        assert ev.candidate.strain_tensor is not None


def test_rank_subcommand(tmp_path: Path) -> None:
    cfg = CampaignConfig(name="rank_test", enumeration={"formulas": ["NbN", "TiN"]})
    candidates = generate_candidates(cfg, n=2)
    from siscforge.calculators import get

    calc = get("mock")
    evaluations = [calc.run(c) for c in candidates]
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(
        json.dumps([e.model_dump(mode="json") for e in evaluations], indent=2)
    )
    out = tmp_path / "ranked.json"
    result = runner.invoke(app, ["rank", str(raw_path), "-o", str(out)])
    assert result.exit_code == 0, result.stdout
    ranked = json.loads(out.read_text())
    assert ranked[0]["rank"] == 1
