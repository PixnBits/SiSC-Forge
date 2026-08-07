"""Mid-step QE/EPW checkpoint resume tests (fixture workdirs, no real QE)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pymatgen.core import Structure

from siscforge.calculators.qe.qe_checkpoint import (
    clean_step_outputs,
    inspect_nscf_vs_epw_coarse_k,
    invalidate_nscf_epw_for_kmesh,
    nscf_matches_epw_coarse_k,
    parse_nscf_in_kmesh,
    probe_epw,
    probe_nscf,
    probe_phonon,
    probe_scf,
    probe_vc_relax,
    probe_workdir,
    write_nscf_kmesh_sidecar,
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
        config,
        work_dir,
        *,
        prefix="siscforge",
        qe_env=None,
        for_epw=False,
        outdir=None,
        recover=False,
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

    def fake_run_pw(
        structure, config, work_dir, *, calculation, prefix="siscforge", qe_env=None, outdir=None
    ):
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

    def fake_run_ph(
        config,
        work_dir,
        *,
        prefix="siscforge",
        qe_env=None,
        for_epw=False,
        outdir=None,
        recover=False,
    ):
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
    """Garbage partial ph.out (no dyn/_ph0) → clean + full phonon re-run."""
    work = tmp_path / "cand"
    structure = _nbn()
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s00000001.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)
    # no dyn / _ph0 → unrecoverable

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False, phonon_method="dfpt")
    cfg.__dict__["_run_config"] = RunConfig(resume_qe_steps=True)

    calls: list[str] = []
    recover_flags: list[bool] = []

    def fake_run_pw(*args, **kwargs):
        calls.append(f"pw:{kwargs.get('calculation', '?')}")
        raise AssertionError("pw should be skipped")

    def fake_run_ph(
        config,
        work_dir,
        *,
        prefix="siscforge",
        qe_env=None,
        for_epw=False,
        outdir=None,
        recover=False,
    ):
        calls.append("ph")
        recover_flags.append(bool(recover))
        # partial ph.out should have been cleaned for full restart
        assert not (Path(work_dir) / "ph.out").exists()
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
    assert recover_flags == [False]
    assert result.success
    assert any("skip vc-relax" in x for x in step_log)
    assert any("skip SCF" in x for x in step_log)
    assert any("running DFPT" in x for x in step_log)


def test_assess_phonon_recoverable_with_dyn(tmp_path: Path) -> None:
    from siscforge.calculators.qe.qe_checkpoint import assess_phonon_recoverability

    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(scf / "ph.out", _PH_PARTIAL)
    _write(scf / "s.dyn1", "Dynamical matrix file\n partial q-point\n")
    (scf / "_ph0").mkdir()
    _write(scf / "_ph0" / "s.phsave", "x")
    rec = assess_phonon_recoverability(work, prefix="s")
    assert rec.recoverable
    assert "dyn" in rec.message or "artifact" in rec.reason.lower()


def test_assess_phonon_unrecoverable_garbage(tmp_path: Path) -> None:
    from siscforge.calculators.qe.qe_checkpoint import assess_phonon_recoverability

    work = tmp_path / "cand"
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)
    rec = assess_phonon_recoverability(work, prefix="s")
    assert not rec.recoverable


def test_assess_phonon_unrecoverable_cannot_recover_marker(tmp_path: Path) -> None:
    from siscforge.calculators.qe.qe_checkpoint import assess_phonon_recoverability

    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(scf / "s.dyn1", "partial")
    _write(scf / "ph.out", _PH_PARTIAL + "\n cannot recover from previous run\n")
    rec = assess_phonon_recoverability(work, prefix="s")
    assert not rec.recoverable
    assert "unsafe" in rec.reason.lower() or "cannot" in rec.reason.lower()


def test_run_recoverable_partial_ph_uses_recover_flag(tmp_path: Path) -> None:
    """Complete SCF + partial phonon with dyn → recover=.true. path."""
    work = tmp_path / "cand"
    structure = _nbn()
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s00000001.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)
    _write(work / "02_scf" / "s00000001.dyn1", "partial-dyn content\n")
    (work / "02_scf" / "_ph0").mkdir()
    _write(work / "02_scf" / "_ph0" / "keep", "x")

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False, phonon_method="dfpt")
    cfg.__dict__["_run_config"] = RunConfig(resume_qe_steps=True)

    recover_flags: list[bool] = []
    dyn_kept: list[bool] = []

    def fake_run_pw(*args, **kwargs):
        raise AssertionError("pw should be skipped")

    def fake_run_ph(
        config,
        work_dir,
        *,
        prefix="siscforge",
        qe_env=None,
        for_epw=False,
        outdir=None,
        recover=False,
    ):
        recover_flags.append(bool(recover))
        # On recover path, dyn must not have been wiped
        dyn_kept.append((Path(work_dir) / f"{prefix}.dyn1").is_file())
        out = Path(work_dir) / "ph.out"
        _write(out, _PH_OK)
        _write(Path(work_dir) / f"{prefix}.dyn1", "freq ( 1) = 5.0 [THz] = 166.78 [cm-1]\n")
        from siscforge.calculators.qe.recipes import QEStepResult

        return QEStepResult(
            name="ph",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=out,
            input_path=Path(work_dir) / "ph.in",
            success=True,
            message="ph.x rc=0 recover" if recover else "ph.x rc=0",
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

    assert result.success
    assert recover_flags == [True]
    assert dyn_kept == [True]
    assert any("resuming DFPT with QE recover=.true." in x for x in step_log)
    assert any("skip SCF" in x for x in step_log)


def test_run_recover_failure_falls_back_to_full_restart(tmp_path: Path) -> None:
    """recover=.true. hard-fails → clean + full phonon restart."""
    work = tmp_path / "cand"
    structure = _nbn()
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(work / "02_scf" / "scf.out", _SCF_OK)
    (work / "02_scf" / "s00000001.save").mkdir()
    _write(work / "02_scf" / "ph.out", _PH_PARTIAL)
    _write(work / "02_scf" / "s00000001.dyn1", "partial-dyn\n")

    cfg = DFTConfig(do_relax=True, do_phonon=True, do_epw=False, phonon_method="dfpt")
    cfg.__dict__["_run_config"] = RunConfig(resume_qe_steps=True)

    recover_flags: list[bool] = []
    calls = 0

    def fake_run_pw(*args, **kwargs):
        raise AssertionError("pw should be skipped")

    def fake_run_ph(
        config,
        work_dir,
        *,
        prefix="siscforge",
        qe_env=None,
        for_epw=False,
        outdir=None,
        recover=False,
    ):
        nonlocal calls
        calls += 1
        recover_flags.append(bool(recover))
        out = Path(work_dir) / "ph.out"
        from siscforge.calculators.qe.recipes import QEStepResult

        if recover:
            # Simulate QE cannot recover
            err_body = (
                _PH_PARTIAL
                + "\n %%%%\n     Error in routine phq_readin (1):\n"
                + "     cannot recover\n"
            )
            _write(out, err_body)
            return QEStepResult(
                name="ph",
                work_dir=Path(work_dir),
                returncode=1,
                stdout_path=out,
                input_path=Path(work_dir) / "ph.in",
                success=False,
                message="ph.x rc=1 cannot recover",
            )
        # Full restart after clean
        assert not (Path(work_dir) / f"{prefix}.dyn1").exists()
        _write(out, _PH_OK)
        _write(Path(work_dir) / f"{prefix}.dyn1", "freq ( 1) = 5.0 [THz] = 166.78 [cm-1]\n")
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

    assert calls == 2
    assert recover_flags == [True, False]
    assert result.success
    assert any("resuming DFPT with QE recover=.true." in x for x in step_log)
    assert any(
        "DFPT recover failed or unsafe — full phonon step restart" in x for x in step_log
    )


def test_build_ph_input_recover_flag() -> None:
    from siscforge.calculators.qe.inputs import build_ph_input

    text = build_ph_input(recover=True, prefix="s")
    assert "recover = .true." in text
    text_off = build_ph_input(recover=False, prefix="s")
    assert "recover" not in text_off


def test_nscf_in_crystal_mesh_parse() -> None:
    """K_POINTS crystal N is preferred requested-mesh fingerprint."""
    text = "K_POINTS crystal\n216\n  0.0 0.0 0.0 1.0\n"
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "nscf.in"
        p.write_text(text, encoding="utf-8")
        parsed = parse_nscf_in_kmesh(p)
        assert parsed == (6, 6, 6)


def test_nscf_matches_epw_coarse_k_sidecar_and_in(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    scf = work / "02_scf"
    scf.mkdir(parents=True)
    # Stale 6³ via nscf.in crystal count + JOB DONE out
    nscf_in = "K_POINTS crystal\n216\n"
    for i in range(3):
        nscf_in += f"  0.{i} 0.0 0.0 0.001\n"
    _write(scf / "nscf.in", nscf_in)
    _write(
        scf / "nscf.out",
        "     number of k points=   216\n"
        "!    total energy              =    -100.0 Ry\n"
        "     the Fermi energy is    10.0 ev\n"
        "     JOB DONE.\n",
    )
    assert not nscf_matches_epw_coarse_k(work, [8, 8, 8])
    assert nscf_matches_epw_coarse_k(work, [6, 6, 6])

    write_nscf_kmesh_sidecar(work, [8, 8, 8])
    assert nscf_matches_epw_coarse_k(work, [8, 8, 8])
    assert not nscf_matches_epw_coarse_k(work, [6, 6, 6])


def test_probe_nscf_mismatch_incomplete(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(
        scf / "nscf.out",
        "     number of k points=   216\n"
        "!    total energy              =    -100.0 Ry\n"
        "     the Fermi energy is    10.0 ev\n"
        "     JOB DONE.\n",
    )
    _write(scf / "nscf.in", "K_POINTS crystal\n216\n")
    ok = probe_nscf(work, quality_tag="screening", expected_nkc=[6, 6, 6])
    assert ok.complete
    bad = probe_nscf(work, quality_tag="screening", expected_nkc=[8, 8, 8])
    assert not bad.complete
    assert "mismatch" in bad.message.lower()


def test_invalidate_nscf_epw_keeps_phonon(tmp_path: Path) -> None:
    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(scf / "nscf.out", _SCF_OK)
    _write(scf / "nscf.in", "K_POINTS crystal\n216\n")
    _write(scf / "epw.out", _EPW_OK)
    _write(scf / "epw.in", "nk1 = 8\n")
    _write(scf / "ph.out", _PH_OK)
    _write(scf / "s.dyn1", "freq")
    (scf / "_ph0").mkdir()
    (scf / "_ph0" / "x").write_text("dvscf", encoding="utf-8")
    _write(scf / "s.dvscf1", "x")
    _write(scf / "foo.amn", "wannier")
    removed, msg = invalidate_nscf_epw_for_kmesh(
        work, prefix="s", reason="nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF (phonon reused)"
    )
    assert "invalidating NSCF" in msg
    assert not (scf / "nscf.out").exists()
    assert not (scf / "epw.out").exists()
    assert not (scf / "foo.amn").exists()
    assert (scf / "ph.out").is_file()
    assert (scf / "s.dyn1").is_file()
    assert (scf / "_ph0" / "x").is_file()
    assert (scf / "s.dvscf1").is_file()
    assert removed


def test_probe_workdir_stale_nscf_not_skipped(tmp_path: Path) -> None:
    """Phonon done + nscf at 6³ + campaign nkc=8 → nscf incomplete, phonon complete."""
    from siscforge.models.config import EPWConfig

    work = tmp_path / "cand"
    scf = work / "02_scf"
    _write(work / "01_relax" / "vc-relax.out", _RELAX_OK)
    _write(scf / "scf.out", _SCF_OK)
    (scf / "s.save").mkdir()
    _write(scf / "ph.out", _PH_OK)
    _write(scf / "s.dyn0", "Dynamical matrices\n")
    _write(scf / "s.dyn1", "freq ( 1) = 5.0 [THz] = 166.78 [cm-1]\n")
    _write(scf / "nscf.in", "K_POINTS crystal\n216\n")
    _write(
        scf / "nscf.out",
        "     number of k points=   216\n"
        "!    total energy              =    -100.0 Ry\n"
        "     the Fermi energy is    10.0 ev\n"
        "     JOB DONE.\n",
    )
    cfg = DFTConfig(
        do_relax=True,
        do_phonon=True,
        do_epw=True,
        quality_tag="production",
        epw=EPWConfig(enabled=True, nkc=[8, 8, 8], nqc=[4, 4, 4]),
    )
    ckpt = probe_workdir(work, cfg, prefix="s", structure=_nbn(), want_epw=True)
    assert ckpt.is_complete("phonon")
    assert not ckpt.is_complete("nscf")
    assert any("mismatch" in line.lower() for line in ckpt.log)
