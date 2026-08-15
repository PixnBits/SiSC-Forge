"""P3.x real launch — controlled solid_dmft / CTHYB launcher tests.

No TRIQS required. Fake launchers / env stubs cover invoke, failure
classification, sacred-upstream, and drop-in resume. Real-stack cases
are gated like other optional science deps.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from siscforge.calculators.qe.dmft import (
    classify_dmft_failure,
    evaluate_wannier_gate,
    run_dmft_workflow,
    run_solid_dmft,
    triqs_available,
)
from siscforge.calculators.qe.dmft_launch import (
    CONFIG_TOML,
    ENV_LAUNCHER,
    LAUNCH_README,
    RUN_SCRIPT,
    LaunchOutcome,
    discover_solid_dmft_command,
    invoke_solid_dmft,
    render_dmft_config_toml,
    write_solid_dmft_run_package,
)
from siscforge.export import write_synthesis_cards
from siscforge.models import (
    CandidateEvaluation,
    DFTConfig,
    DMFTConfig,
    StructureCandidate,
    WannierResult,
)
from siscforge.scoring.pairing import SOURCE_DMFT_PAIRING, apply_performance_score


def _ready_wannier(tmp_path: Path | None = None, **kwargs) -> WannierResult:
    work = tmp_path / "wannier" if tmp_path is not None else Path("/tmp/w")
    chk = work / "siscforge.chk"
    defaults = dict(
        wannier_ok=True,
        ready_for_dmft=True,
        status="ok",
        quality_tag="screening",
        work_dir=str(work),
        chk_path=str(chk),
    )
    defaults.update(kwargs)
    return WannierResult(**defaults)


def _cfg(**kwargs) -> DMFTConfig:
    base = dict(enabled=True, solver="solid_dmft", U_eV=5.0, J_eV=0.8)
    base.update(kwargs)
    return DMFTConfig(**base)


def _ok_launcher(_cmd, work_dir: Path, _timeout):
    (Path(work_dir) / "observables.json").write_text(
        json.dumps(
            {
                "occupancy": {"Ni_d": 8.72},
                "Z": 0.4,
                "converged": True,
                "leading_pairing_eigenvalue": 0.61,
                "pairing_symmetry": "d_x2-y2",
            }
        ),
        encoding="utf-8",
    )
    return LaunchOutcome(returncode=0, command=list(_cmd), source="fake_ok")


def _fail_launcher(_cmd, work_dir: Path, _timeout):
    (Path(work_dir) / "solid_dmft.log").write_text(
        "did not converge after 4 loops\n", encoding="utf-8"
    )
    return LaunchOutcome(
        returncode=1,
        command=list(_cmd),
        source="fake_fail",
        error="did not converge after 4 loops",
        stdout_tail="did not converge after 4 loops",
    )


def test_run_package_written_without_stack(tmp_path: Path) -> None:
    if triqs_available():
        pytest.skip("TRIQS/solid_dmft is installed in this environment")
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        formula="NdNiO2",
    )
    assert result.status == "skipped"
    assert result.failure_class == "solver_missing"
    wd = tmp_path / "dmft"
    assert (wd / CONFIG_TOML).is_file()
    assert (wd / RUN_SCRIPT).is_file()
    assert (wd / LAUNCH_README).is_file()
    toml = (wd / CONFIG_TOML).read_text()
    assert "n_iter_dmft = 4" in toml
    assert "U = 5" in toml
    assert "beta = 40" in toml
    assert result.raw["launch"]["status"] == "skipped_solver_missing"
    assert result.raw["launch"]["package"]["toml"].endswith(CONFIG_TOML)


def test_drop_in_observables_without_triqs(tmp_path: Path) -> None:
    """Resume path must ingest observables even when TRIQS is absent."""
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        '{"occupancy": {"Ni_d": 8.7}, "Z": 0.5, "converged": true}',
        encoding="utf-8",
    )
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=wd,
        quality_tag="screening",
        formula="NdNiO2",
    )
    assert result.status == "ok"
    assert result.converged is True
    assert result.occupancy_summary.get("Ni_d") == pytest.approx(8.7)
    assert result.mass_enhancement == pytest.approx(2.0)
    assert result.failure_class is None
    assert result.raw["launch"]["status"] == "drop_in"


def test_fake_launcher_populates_dmft_result_and_pairing(tmp_path: Path) -> None:
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        formula="NdNiO2",
        launcher=_ok_launcher,
    )
    assert result.status == "ok"
    assert result.converged is True
    assert result.filling == pytest.approx(8.72)
    assert result.mass_enhancement == pytest.approx(2.5)
    assert result.leading_pairing_eigenvalue == pytest.approx(0.61)
    assert result.pairing_symmetry == "d_x2-y2"
    assert result.raw["launch"]["status"] == "invoked"
    assert result.failure_class is None

    cand = StructureCandidate(formula="NdNiO2", material_family="nickelate")
    ev = CandidateEvaluation(candidate=cand, dmft=result, status="ok")
    ev = apply_performance_score(ev)
    assert ev.performance_score_source == SOURCE_DMFT_PAIRING
    assert ev.performance_score is not None


def test_fake_launcher_failure_classified_not_converged(tmp_path: Path) -> None:
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        launcher=_fail_launcher,
    )
    assert result.status == "failed"
    assert result.converged is False
    assert result.failure_class == "not_converged"
    assert result.raw["launch"]["status"] == "failed"


def test_auto_launch_false_writes_package_does_not_invoke(tmp_path: Path) -> None:
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("launcher must not run when auto_launch is false")

    result = run_solid_dmft(
        cfg=_cfg(auto_launch=False),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        launcher=boom,
    )
    assert called["n"] == 0
    assert result.status == "skipped"
    assert result.failure_class is None
    assert result.raw["launch"]["status"] == "deferred"
    assert (tmp_path / "dmft" / CONFIG_TOML).is_file()
    assert "run_solid_dmft.sh" in (result.raw["launch"]["operator_next"] or "")


def test_drop_in_wins_over_launcher(tmp_path: Path) -> None:
    called = {"n": 0}

    def spy(cmd, work_dir, timeout):
        called["n"] += 1
        return _ok_launcher(cmd, work_dir, timeout)

    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        '{"filling": 8.1, "mass_enhancement": 1.8, "converged": true}',
        encoding="utf-8",
    )
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=wd,
        quality_tag="screening",
        launcher=spy,
    )
    assert called["n"] == 0
    assert result.filling == pytest.approx(8.1)
    assert result.raw["launch"]["status"] == "drop_in"


def test_sacred_upstream_on_launch_failure(tmp_path: Path) -> None:
    upstream = tmp_path / "wannier"
    upstream.mkdir()
    marker = upstream / "siscforge.chk"
    marker.write_text("keep me\n", encoding="utf-8")
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path, work_dir=str(upstream), chk_path=str(marker)),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        launcher=_fail_launcher,
    )
    assert result.status == "failed"
    assert marker.is_file()
    assert marker.read_text() == "keep me\n"
    assert not any(tmp_path.joinpath("wannier").glob("observables.json"))


def test_gate_refusal_unchanged_for_non_mock(tmp_path: Path) -> None:
    dft = DFTConfig(do_dmft=True, dmft=_cfg(allow_without_wannier_gate=False))
    result = run_dmft_workflow(
        dft,
        tmp_path / "dmft",
        wannier=WannierResult(ready_for_dmft=False, wannier_ok=False, status="failed"),
        seed="gate",
    )
    assert result.status == "refused"
    assert result.failure_class == "wannier_gate"
    assert not (tmp_path / "dmft" / CONFIG_TOML).is_file()


def test_workflow_fake_launcher_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "fake_solid_dmft"
    script.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' \'{"occupancy": {"imp": 7.9}, "converged": true}\' '
        "> observables.json\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(ENV_LAUNCHER, str(script))
    # Stage a dummy h5 so the real invoke path does not refuse.
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "siscforge.h5").write_bytes(b"fake-h5")
    dft = DFTConfig(do_dmft=True, dmft=_cfg())
    result = run_dmft_workflow(
        dft,
        wd,
        wannier=_ready_wannier(tmp_path),
        seed="env-launch",
    )
    assert result.status == "ok"
    assert result.occupancy_summary.get("imp") == pytest.approx(7.9)
    assert result.raw["launch"]["status"] == "invoked"
    assert (wd / "solid_dmft.log").is_file()


def test_missing_h5_classifies_without_deleting_upstream(tmp_path: Path) -> None:
    if triqs_available():
        pytest.skip("real stack would attempt an invoke")
    # Force the invoke path via env pointing at a script that should not run
    # because h5 is missing (discover sees env, but stage_h5 fails first).
    script = tmp_path / "should_not_run"
    script.write_text("#!/bin/sh\necho ran > RAN\nexit 0\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch_env = os.environ.copy()
    os.environ[ENV_LAUNCHER] = str(script)
    try:
        upstream = tmp_path / "wannier"
        upstream.mkdir()
        marker = upstream / "keep.chk"
        marker.write_text("sacred\n", encoding="utf-8")
        result = run_solid_dmft(
            cfg=_cfg(),
            wannier=_ready_wannier(tmp_path, work_dir=str(upstream), chk_path=str(marker)),
            work_dir=tmp_path / "dmft",
            quality_tag="screening",
        )
        assert result.status == "failed"
        assert result.failure_class in {"other", "binary_missing"}
        assert "h5" in (result.raw.get("error") or "").lower() or result.raw.get("launch", {}).get(
            "h5"
        )
        assert marker.read_text() == "sacred\n"
        assert not (tmp_path / "dmft" / "RAN").exists()
    finally:
        if ENV_LAUNCHER in os.environ:
            if ENV_LAUNCHER in monkeypatch_env:
                os.environ[ENV_LAUNCHER] = monkeypatch_env[ENV_LAUNCHER]
            else:
                del os.environ[ENV_LAUNCHER]


def test_invoke_timeout(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep.sh"
    sleeper.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    sleeper.chmod(sleeper.stat().st_mode | stat.S_IEXEC)
    outcome = invoke_solid_dmft(
        [str(sleeper)], tmp_path / "dmft", timeout_s=0.2, source="timeout-test"
    )
    assert outcome.timed_out is True
    assert outcome.returncode == 124
    assert "timed out" in (outcome.error or "")


def test_discover_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wrapper = tmp_path / "wrap"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv(ENV_LAUNCHER, str(wrapper))
    cmd, src = discover_solid_dmft_command()
    assert cmd == [str(wrapper)]
    assert src.startswith("env:")


def test_toml_uses_resolved_uj_and_loops() -> None:
    cfg = _cfg(n_loops=6, n_cycles=1234, n_warmup_cycles=77, beta=25.0)
    text = render_dmft_config_toml(cfg, u=4.5, j=0.6, seedname="ndnio2")
    assert 'seedname = "ndnio2"' in text
    assert "U = 4.5" in text
    assert "J = 0.6" in text
    assert "beta = 25" in text
    assert "n_iter_dmft = 6" in text
    assert "n_cycles_tot = 1234" in text
    assert "n_warmup_cycles = 77" in text
    assert 'type = "cthyb"' in text


def test_write_package_chmod_and_refs(tmp_path: Path) -> None:
    cfg = _cfg()
    wannier = _ready_wannier(tmp_path)
    paths = write_solid_dmft_run_package(tmp_path / "dmft", cfg, wannier=wannier, u=5.0, j=0.8)
    script = Path(paths["script"])
    assert os.access(script, os.X_OK)
    readme = Path(paths["readme"]).read_text()
    assert "operator-owned" in readme.lower() or "What remains operator-owned" in readme
    assert wannier.work_dir in readme


def test_cards_note_launch_status(tmp_path: Path) -> None:
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        launcher=_ok_launcher,
    )
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="card-launch",
    )
    ev = CandidateEvaluation(candidate=cand, dmft=result, status="ok")
    ev = apply_performance_score(ev)
    cards = write_synthesis_cards([ev], tmp_path / "cards.md", campaign_name="launch")
    text = cards.read_text()
    assert "launch: invoked" in text
    assert "DMFT" in text


def test_config_defaults_keep_mock_and_conventional() -> None:
    mock = DMFTConfig()
    assert mock.solver == "mock"
    assert mock.auto_launch is True
    assert mock.launch_timeout_s is None
    assert mock.enabled is False
    dft = DFTConfig()
    assert dft.do_dmft is False
    assert dft.dmft.auto_launch is True


def test_gate_helper_still_refuses_without_ready() -> None:
    allowed, notes, _bypass = evaluate_wannier_gate(
        WannierResult(ready_for_dmft=False, status="failed"),
        _cfg(),
        solver="solid_dmft",
    )
    assert allowed is False
    assert "refused" in notes
    assert classify_dmft_failure(notes) == "wannier_gate"


@pytest.mark.skipif(not triqs_available(), reason="TRIQS / solid_dmft not installed")
def test_real_stack_drop_in_still_parsed(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        '{"occupancy": {"Ni_d": 8.7}, "Z": 0.4, "converged": true}',
        encoding="utf-8",
    )
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=wd,
        quality_tag="screening",
    )
    assert result.converged is True
    assert result.occupancy_summary.get("Ni_d") == 8.7
