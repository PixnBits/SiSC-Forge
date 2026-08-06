"""EPW coarse-k Wannier safety + post-DFPT EPW-only remediation (no real QE)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from siscforge.calculators.qe.epw_inputs import (
    build_epw_input,
    ensure_wannier_safe_nkc,
    minimum_coarse_k_dim,
    next_coarse_k_after_bvector_failure,
    preflight_epw_grids,
    recommended_grids,
)
from siscforge.calculators.qe.epw_recipes import (
    classify_epw_failure,
    diagnose_epw_failure,
    extract_primary_failure_reason,
    is_kmesh_bvector_failure,
    is_remediable_kmesh_failure,
    load_epw_remediation_state,
    plan_kmesh_remediation,
    run_relax_scf_phonon_epw,
)
from siscforge.models.config import DFTConfig, EPWConfig
from siscforge.refine import default_refine_dft
from siscforge.structure.nitrides import build_ternary_nitride

_BVECTOR_FAIL = """
     Program EPW
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     Error in routine kmesh_get_bvector (1):
     Not enough bvectors found
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     stopping ...
"""

_EPW_OK = """
Electron-phonon coupling strength = 1.05
omega_log is 22.0 meV
Estimated Allen-Dynes Tc = 12.0 K for mus = 0.10
     JOB DONE.
"""


def test_minimum_coarse_k_production_8atom() -> None:
    assert minimum_coarse_k_dim(quality_tag="production", n_atoms=8) == 8
    assert minimum_coarse_k_dim(quality_tag="production", n_atoms=8, tier="workstation_dense") == 8
    assert minimum_coarse_k_dim(quality_tag="screening", n_atoms=8) == 6
    assert minimum_coarse_k_dim(quality_tag="screening", n_atoms=2) == 4


def test_tier_minimum_raises_6_to_8_for_8atom_production() -> None:
    raised, msg = ensure_wannier_safe_nkc(
        [6, 6, 6],
        quality_tag="production",
        n_atoms=8,
        tier="workstation_dense",
        auto_raise=True,
    )
    assert raised == [8, 8, 8]
    assert msg is not None
    assert "8×8×8" in msg
    assert "6×6×6" in msg
    assert "nq unchanged" in msg


def test_recommended_grids_workstation_dense_nkc_not_6() -> None:
    g = recommended_grids("tm_nitride", "workstation_dense")
    nkc = g["epw"]["nkc"]
    assert min(nkc) >= 8
    assert nkc != [6, 6, 6]


def test_default_refine_dft_no_nk6_for_supercell() -> None:
    dft = default_refine_dft(tier="workstation_dense", n_atoms=8)
    assert dft.do_epw
    assert list(dft.epw.nkc) == [8, 8, 8]
    assert list(dft.epw.nqc) == list(dft.qpoints) or dft.epw.nqc[0] == 4
    # nq matches DFPT
    assert dft.epw.nqc == [4, 4, 4]
    assert dft.qpoints == [4, 4, 4]


def test_preflight_detects_nk6_do_epw_dense_tier() -> None:
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    assert len(s) == 8
    cfg = DFTConfig(
        do_epw=True,
        quality_tag="production",
        qpoints=[4, 4, 4],
        epw=EPWConfig(
            enabled=True,
            nkc=[6, 6, 6],
            nqc=[4, 4, 4],
            nkf=[12, 12, 12],
            nqf=[12, 12, 12],
        ),
    )
    pre = preflight_epw_grids(cfg, structure=s, tier="workstation_dense")
    assert pre.ok
    assert pre.nkc_raised
    assert list(pre.config.epw.nkc) == [8, 8, 8]
    assert any("raised to 8" in m for m in pre.messages)
    # nq unchanged
    assert list(pre.config.epw.nqc) == [4, 4, 4]


def test_preflight_strict_fails_instead_of_raise() -> None:
    cfg = DFTConfig(
        do_epw=True,
        quality_tag="production",
        qpoints=[4, 4, 4],
        epw=EPWConfig(
            enabled=True,
            nkc=[6, 6, 6],
            nqc=[4, 4, 4],
            strict_coarse_k=True,
        ),
    )
    pre = preflight_epw_grids(cfg, n_atoms=8, tier="workstation_dense")
    assert not pre.ok
    assert list(pre.config.epw.nkc) == [6, 6, 6]
    assert pre.strict_violations


def test_diagnose_maps_kmesh_get_bvector() -> None:
    assert is_kmesh_bvector_failure(_BVECTOR_FAIL)
    assert is_remediable_kmesh_failure(_BVECTOR_FAIL)
    assert classify_epw_failure(_BVECTOR_FAIL) == "kmesh_bvector"
    reason = extract_primary_failure_reason(_BVECTOR_FAIL, step_name="epw")
    assert "bvector" in reason.lower() or "kmesh" in reason.lower()
    diag = diagnose_epw_failure(_BVECTOR_FAIL, work_dir="/tmp/fake", include_tail=True)
    assert "class: kmesh_bvector" in diag
    assert "DFPT" in diag or "nkc" in diag.lower()


def test_retry_policy_sequence_6_8_12_and_stops() -> None:
    assert next_coarse_k_after_bvector_failure([6, 6, 6], attempt=0) == [8, 8, 8]
    assert next_coarse_k_after_bvector_failure([8, 8, 8], attempt=1) == [12, 12, 12]
    assert next_coarse_k_after_bvector_failure([12, 12, 12], attempt=0) is None
    assert next_coarse_k_after_bvector_failure([6, 6, 6], attempt=2) is None

    cfg = DFTConfig(
        quality_tag="production",
        epw=EPWConfig(nkc=[6, 6, 6], nqc=[4, 4, 4], auto_retry_kmesh=True),
    )
    plan = plan_kmesh_remediation(cfg, _BVECTOR_FAIL, work_dir=None)
    assert plan is not None
    new_cfg, line = plan
    assert list(new_cfg.epw.nkc) == [8, 8, 8]
    assert list(new_cfg.epw.nqc) == [4, 4, 4]  # nq sacred
    assert "EPW-only" in line or "DFPT reused" in line
    assert "nk=6" in line or "nk=8" in line


def test_build_epw_input_raises_nkc_for_production_supercell() -> None:
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    cfg = DFTConfig(
        do_epw=True,
        quality_tag="production",
        nbnd=64,
        epw=EPWConfig(enabled=True, nkc=[6, 6, 6], nqc=[4, 4, 4]),
    )
    text = build_epw_input(cfg, structure=s, fermi_eV=20.0)
    assert "nk1         = 8" in text
    assert "nq1         = 4" in text  # DFPT q unchanged


def test_post_dfpt_bvector_retries_epw_only_phonon_intact(tmp_path: Path) -> None:
    """Simulate finished DFPT + kmesh_get_bvector → EPW-only denser-k retry."""
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    work = tmp_path / "cand"
    work.mkdir()
    scf = work / "02_scf"
    scf.mkdir()
    # Fake completed phonon artifacts (must not be deleted)
    (scf / "ph.out").write_text("     JOB DONE.\n", encoding="utf-8")
    (scf / "siscforge.dyn0").write_text("Dynamical matrices\n", encoding="utf-8")
    (scf / "siscforge.dyn1").write_text("freq = 100.0\n", encoding="utf-8")
    (scf / "scf.out").write_text(
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )
    (work / "01_relax").mkdir()
    (work / "01_relax" / "vc-relax.out").write_text(
        "     JOB DONE.\n", encoding="utf-8"
    )

    # Start at 8 (post-preflight); first epw fails bvector → retry 12
    cfg = DFTConfig(
        nproc=1,
        do_relax=True,
        do_phonon=True,
        do_epw=True,
        quality_tag="production",
        nbnd=64,
        qpoints=[4, 4, 4],
        kpoints=[8, 8, 8],
        epw=EPWConfig(
            enabled=True,
            nkc=[8, 8, 8],
            nqc=[4, 4, 4],
            npool=1,
            auto_retry_kmesh=True,
            max_kmesh_retries=2,
            auto_nbndsub=True,
            wannier_retry_on_froz_overflow=False,
        ),
        pseudo_dir=str(tmp_path),
    )

    epw_launches: list[list[int]] = []
    cleaned_steps: list[str] = []

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None, **kwargs):
        out = Path(stdout_path)
        label = str(kwargs.get("step_label") or "")
        is_epw = out.name == "epw.out" or label.lower().startswith("epw.x")
        if is_epw:
            n = len(epw_launches)
            nk = [8, 8, 8]
            epw_in = Path(cwd) / "epw.in"
            if epw_in.is_file():
                text = epw_in.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "nk1" in line and "=" in line:
                        nk[0] = int(line.split("=")[1].strip())
                    if "nk2" in line and "=" in line:
                        nk[1] = int(line.split("=")[1].strip())
                    if "nk3" in line and "=" in line:
                        nk[2] = int(line.split("=")[1].strip())
            epw_launches.append(list(nk))
            if n == 0:
                out.write_text(_BVECTOR_FAIL, encoding="utf-8")
                return 1
            out.write_text(_EPW_OK, encoding="utf-8")
            return 0
        out.write_text(
            "     the Fermi energy is    20.0000 ev\n"
            "!\n     total energy              =     -100.0 Ry\n"
            "     JOB DONE.\n",
            encoding="utf-8",
        )
        return 0

    from siscforge.calculators.qe import qe_checkpoint
    from siscforge.calculators.qe.recipes import QEStepResult

    real_clean = qe_checkpoint.clean_step_outputs

    def tracking_clean(work_dir, step, prefix="siscforge"):
        cleaned_steps.append(step)
        return real_clean(work_dir, step, prefix=prefix)

    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"
    fake_env.pw = "/usr/bin/pw.x"
    fake_env.ph = "/usr/bin/ph.x"
    fake_env.mpirun = None

    from siscforge.calculators.qe.qe_checkpoint import StepProbe, WorkdirCheckpoint

    def fake_probe(work_dir, config, **kwargs):
        ckpt = WorkdirCheckpoint(work_dir=Path(work_dir), prefix="siscforge")
        ckpt.steps["vc-relax"] = StepProbe(
            name="vc-relax", complete=True, message="ok", relaxed_structure=s
        )
        ckpt.steps["scf"] = StepProbe(name="scf", complete=True, message="ok")
        ckpt.steps["phonon"] = StepProbe(name="phonon", complete=True, message="ok")
        for name in ("epw_pp", "nscf", "epw"):
            ckpt.steps[name] = StepProbe(name=name, complete=False, message="missing")
        ckpt.log = ["skip phonon (checkpoint)"]
        return ckpt

    def fake_pp(work_dir, prefix):
        save = Path(work_dir) / "save"
        save.mkdir(exist_ok=True)
        return QEStepResult(
            name="epw_pp",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=Path(work_dir) / "pp.out",
            input_path=Path(work_dir) / "pp.py",
            success=True,
            message="ok",
        )

    with (
        patch(
            "siscforge.calculators.qe.epw_recipes.require_epw",
            return_value=fake_env,
        ),
        patch(
            "siscforge.calculators.qe.recipes.require_qe",
            return_value=fake_env,
        ),
        patch(
            "siscforge.calculators.qe.recipes._run_cmd",
            side_effect=fake_run_cmd,
        ),
        patch(
            "siscforge.calculators.qe.recipes._mpi_prefix",
            return_value=[],
        ),
        patch(
            "siscforge.calculators.qe.qe_checkpoint.probe_workdir",
            side_effect=fake_probe,
        ),
        patch(
            "siscforge.calculators.qe.qe_checkpoint.clean_step_outputs",
            side_effect=tracking_clean,
        ),
        patch(
            "siscforge.calculators.qe.epw_recipes.run_epw_pp",
            side_effect=fake_pp,
        ),
        patch(
            "siscforge.calculators.qe.inputs.resolve_pseudopotentials",
            return_value={"N": "N.upf", "Nb": "Nb.upf", "Ti": "Ti.upf"},
        ),
        patch(
            "siscforge.calculators.qe.pseudos.resolve_pseudopotentials",
            return_value={"N": "N.upf", "Nb": "Nb.upf", "Ti": "Ti.upf"},
        ),
    ):
        result = run_relax_scf_phonon_epw(s, cfg, work, prefix="siscforge")

    # Phonon never cleaned
    assert "phonon" not in cleaned_steps
    assert (scf / "ph.out").is_file()
    assert (scf / "siscforge.dyn1").is_file()

    # First EPW fails, second succeeds with denser k
    assert len(epw_launches) >= 2
    assert epw_launches[0] == [8, 8, 8]
    assert epw_launches[1] == [12, 12, 12]

    state = load_epw_remediation_state(scf)
    assert len(state.get("attempts") or []) >= 1
    assert result.success
    assert any("EPW-only" in (a.get("note") or "") or "DFPT reused" in (a.get("note") or "") for a in state["attempts"])


def test_plan_stops_after_max_retries(tmp_path: Path) -> None:
    cfg = DFTConfig(
        quality_tag="production",
        epw=EPWConfig(
            nkc=[12, 12, 12],
            auto_retry_kmesh=True,
            max_kmesh_retries=2,
        ),
    )
    # Already at top of ladder
    assert plan_kmesh_remediation(cfg, _BVECTOR_FAIL, work_dir=tmp_path) is None

    cfg2 = DFTConfig(
        quality_tag="production",
        epw=EPWConfig(nkc=[6, 6, 6], auto_retry_kmesh=True, max_kmesh_retries=2),
    )
    # Exhaust attempts via sidecar
    from siscforge.calculators.qe.epw_recipes import record_epw_remediation_attempt

    record_epw_remediation_attempt(
        tmp_path, reason="kmesh_bvector", nkc_before=[6, 6, 6], nkc_after=[8, 8, 8]
    )
    record_epw_remediation_attempt(
        tmp_path, reason="kmesh_bvector", nkc_before=[8, 8, 8], nkc_after=[12, 12, 12]
    )
    assert plan_kmesh_remediation(cfg2, _BVECTOR_FAIL, work_dir=tmp_path) is None
