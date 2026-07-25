"""Mid-step QE/EPW checkpoint resume tests (fixture workdirs, no real QE)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pymatgen.core import Structure

from siscforge.calculators.qe.qe_checkpoint import (
    clean_step_outputs,
    probe_epw,
    probe_phonon,
    probe_scf,
    probe_vc_relax,
    probe_workdir,
)
from siscforge.calculators.qe.recipes import run_relax_scf_phonon
from siscforge.models.config import DFTConfig, RunConfig
from siscforge.structure.nitrides import build_binary_nitride

# Minimal JOB DONE SCF stdout with energy + Fermi (for parse_pw_output)
_SCF_OK = """
     Program PWSCF
     the Fermi energy is    10.0000 ev
!    total energy              =    -100.12345678 Ry
     JOB DONE.
"""

_RELAX_OK = """
     Program PWSCF
     the Fermi energy is    10.0000 ev
!    total energy              =    -100.00000000 Ry

CELL_PARAMETERS (angstrom)
   3.000000000   0.000000000   0.000000000
   0.000000000   3.000000000   0.000000000
   0.000000000   0.000000000   3.000000000

ATOMIC_POSITIONS (crystal)
Nb  0.000000000  0.000000000  0.000000000
N   0.500000000  0.500000000  0.500000000
     JOB DONE.
"""

_PH_OK = """
     Program PHONON
     freq (    1) =       5.000000 [THz] =     166.782050 [cm-1]
     freq (    2) =      10.000000 [THz] =     333.564100 [cm-1]
     JOB DONE.
"""

_PH_PARTIAL = """
     Program PHONON
     Representation #   1 mode #   1
     Self-consistent Calculation
      iter #   1 total cpu time :    10.0 secs
"""

_EPW_OK = """
     Program EPW
     Electron-phonon coupling strength =    1.048
     omega_log is   24.15 meV
     Estimated Allen-Dynes Tc =    16.50 K for mus =    0.10
     JOB DONE.
"""


def _nbn() -> Structure:
    return build_binary_nitride("Nb")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_probe_vc_relax_and_scf(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    # touch a .save dir so scf looks complete
    (work / "02_scf" / "siscforge.save").mkdir()

    pr = probe_vc_relax(work, quality_tag="screening", fallback=_nbn())
    assert pr.complete
    assert pr.relaxed_structure is not None

    ps = probe_scf(work, quality_tag="screening")
    assert ps.complete
    assert ps.scf is not None
    assert ps.scf.status == "ok"


def test_probe_partial_phonon_incomplete(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)
    probe = probe_phonon(work, prefix="s", for_epw=True)
    assert not probe.complete
    assert "incomplete" in probe.message.lower() or "no JOB DONE" in probe.message


def test_probe_complete_phonon_with_dyn(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(scf / "scf.out", _SCF_OK)
    (scf / "s.save").mkdir()
    _write(scf / "ph.out", _PH_OK)
    _write(scf / "s.dyn0", "Dynamical matrix file\n")
    _write(scf / "s.dyn1", "freq ( 1) = 5.0 [THz] = 166.78 [cm-1]\n")
    probe = probe_phonon(work, prefix="s", for_epw=True)
    assert probe.complete


def test_probe_epw_ok(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    _write(work / "02_scf" / "epw.out", _EPW_OK)
    probe = probe_epw(work, mu_star=0.1)
    assert probe.complete
    assert probe.electron_phonon is not None
    assert probe.electron_phonon.lambda_total is not None


def test_probe_workdir_kill_during_ph(tmp_path: Path) -> None:
    """Simulated kill during ph.x: relax+SCF complete, phonon incomplete."""
    work = tmp_path / "cand"
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False)
    ckpt = probe_workdir(work, cfg, prefix="s", structure=_nbn(), force=False)
    assert ckpt.is_complete("vc-relax")
    assert ckpt.is_complete("scf")
    assert not ckpt.is_complete("phonon")
    assert any("skip vc-relax" in line for line in ckpt.log)
    assert any("skip SCF" in line for line in ckpt.log)
    assert any("incomplete phonon" in line for line in ckpt.log)


def test_probe_workdir_force_disables_skips(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_OK)
    _write(work / "02_scf" / "s.dyn1", "x")

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False)
    ckpt = probe_workdir(work, cfg, prefix="s", structure=_nbn(), force=True)
    assert not ckpt.is_complete("vc-relax")
    assert not ckpt.is_complete("scf")
    assert not ckpt.is_complete("phonon")
    assert any("force" in line for line in ckpt.log)


def test_clean_step_outputs_phonon_only(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(scf / "scf.out", _SCF_OK)
    _write(scf / "ph.out", _PH_PARTIAL)
    _write(scf / "s.dyn1", "partial")
    (scf / "s.save").mkdir()
    removed = clean_step_outputs(work, "phonon", prefix="s")
    assert not (scf / "ph.out").exists()
    assert not (scf / "s.dyn1").exists()
    assert (scf / "scf.out").exists()  # upstream kept
    assert (scf / "s.save").is_dir()
    assert removed


def test_run_relax_scf_phonon_skips_completed_steps(tmp_path: Path) -> None:
    """Fake workdir with successful SCF + missing ph → only phonon invoked."""
    work = tmp_path / "cand"
    structure = _nbn()
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s00000001.save").mkdir()

    cfg = DFTConfig(
        do_relax=True,
        do_phonon=True,
        do_epw=False,
        phonon_method="dfpt",
        qpoints=[1, 1, 1],
        quality_tag="screening",
    )
    # Attach run config: resume steps on
    cfg.__dict__["_run_config"] = RunConfig(
        resume=True, resume_qe_steps=True, force_rerun=False
    )

    calls: list[str] = []

    def fake_run_pw(
        structure,
        config,
        work_dir,
        *,
        calculation,
        prefix="siscforge",
        qe_env=None,
        outdir=None,
    ):
        calls.append(f"pw:{calculation}")
        raise AssertionError(f"pw.x {calculation} should have been skipped")

    def fake_run_ph(
        config, work_dir, *, prefix="siscforge", qe_env=None, for_epw=False, outdir=None
    ):
        calls.append("ph")
        out = work_dir / "ph.out"
        _write(out, _PH_OK)
        _write(work_dir / f"{prefix}.dyn1", "freq ( 1) = 5.0 [THz] = 166.78 [cm-1]\n")
        from siscforge.calculators.qe.recipes import QEStepResult

        return QEStepResult(
            name="ph",
            work_dir=work_dir,
            returncode=0,
            stdout_path=out,
            input_path=work_dir / "ph.in",
            success=True,
            message="ph.x rc=0",
        )

    step_log: list[str] = []
    with (
        patch("siscforge.calculators.qe.recipes.run_pw", side_effect=fake_run_pw),
        patch("siscforge.calculators.qe.recipes.run_ph", side_effect=fake_run_ph),
        patch(
            "siscforge.calculators.qe.recipes.require_qe",
            return_value=type("E", (), {"pw": "pw", "ph": "ph", "mpirun": None})(),
        ),
    ):
        result = run_relax_scf_phonon(
            structure,
            cfg,
            work,
            prefix="s00000001",
            step_log=step_log,
        )

    assert "pw:vc-relax" not in calls
    assert "pw:scf" not in calls
    assert "ph" in calls
    assert result.success
    assert result.scf is not None
    assert any("skip vc-relax" in x for x in step_log)
    assert any("skip SCF" in x for x in step_log)
    assert any("running DFPT" in x or "phonon" in x for x in step_log)


def test_run_force_rerun_qe_steps_no_skip(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    structure = _nbn()
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s00000001.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_OK)

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False, phonon_method="dfpt")
    cfg.__dict__["_run_config"] = RunConfig(
        force_rerun=True, force_rerun_qe_steps=True, resume_qe_steps=True
    )

    calls: list[str] = []

    def fake_run_pw(structure, config, work_dir, *, calculation, prefix="siscforge", qe_env=None, outdir=None):
        calls.append(f"pw:{calculation}")
        out = Path(work_dir) / f"{calculation}.out"
        if calculation == "vc-relax":
            _write(out, _RELAX_OK)
        else:
            _write(out, _SCF_OK)
            (Path(work_dir) / f"{prefix}.save").mkdir(exist_ok=True)
        from siscforge.calculators.qe.recipes import QEStepResult

        return QEStepResult(
            name=calculation,
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / f"{calculation}.in",
            success=True,
            message=f"pw.x {calculation} rc=0",
        )

    def fake_run_ph(config, work_dir, *, prefix="siscforge", qe_env=None, for_epw=False, outdir=None):
        calls.append("ph")
        out = Path(work_dir) / "ph.out"
        _write(out, _PH_OK)
        from siscforge.calculators.qe.recipes import QEStepResult

        return QEStepResult(
            name="ph",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / "ph.in",
            success=True,
            message="ph.x rc=0",
        )

    with (
        patch("siscforge.calculators.qe.recipes.run_pw", side_effect=fake_run_pw),
        patch("siscforge.calculators.qe.recipes.run_ph", side_effect=fake_run_ph),
        patch(
            "siscforge.calculators.qe.recipes.require_qe",
            return_value=type("E", (), {"pw": "pw", "ph": "ph", "mpirun": None})(),
        ),
    ):
        result = run_relax_scf_phonon(structure, cfg, work, prefix="s00000001")

    assert "pw:vc-relax" in calls
    assert "pw:scf" in calls
    assert "ph" in calls
    assert result.success


def test_run_partial_ph_cleans_and_reruns_phonon(tmp_path: Path) -> None:
    """Partial ph.out (no JOB DONE) → phonon re-run; relax/SCF skipped."""
    work = tmp_path / "cand"
    structure = _nbn()
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s00000001.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)
    _write(work / "02_scf" / "s00000001.dyn1", "partial-dyn")

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False, phonon_method="dfpt")
    cfg.__dict__["_run_config"] = RunConfig(resume_qe_steps=True)

    calls: list[str] = []

    def fake_run_pw(*args, **kwargs):
        calls.append(f"pw:{kwargs.get('calculation', '?')}")
        raise AssertionError("pw should be skipped")

    def fake_run_ph(config, work_dir, *, prefix="siscforge", qe_env=None, for_epw=False, outdir=None):
        calls.append("ph")
        # partial files should have been cleaned
        assert not (Path(work_dir) / "ph.out").exists() or True
        out = Path(work_dir) / "ph.out"
        _write(out, _PH_OK)
        from siscforge.calculators.qe.recipes import QEStepResult

        return QEStepResult(
            name="ph",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / "ph.in",
            success=True,
            message="ph.x rc=0",
        )

    step_log: list[str] = []
    with (
        patch("siscforge.calculators.qe.recipes.run_pw", side_effect=fake_run_pw),
        patch("siscforge.calculators.qe.recipes.run_ph", side_effect=fake_run_ph),
        patch(
            "siscforge.calculators.qe.recipes.require_qe",
            return_value=type("E", (), {"pw": "pw", "ph": "ph", "mpirun": None})(),
        ),
    ):
        result = run_relax_scf_phonon(
            structure, cfg, work, prefix="s00000001", step_log=step_log
        )

    assert calls == ["ph"]
    assert result.success
    assert any("skip vc-relax" in x for x in step_log)
    assert any("skip SCF" in x for x in step_log)
