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
    next_search_shells_after_bvector_failure,
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
    assert plan.phase == "nkc"
    assert list(new_cfg.epw.nkc) == [8, 8, 8]
    assert list(new_cfg.epw.nqc) == [4, 4, 4]  # nq sacred
    assert "EPW-only" in line or "DFPT reused" in line
    assert "nk=6" in line or "nk=8" in line


def test_search_shells_ladder_12_36_48() -> None:
    assert next_search_shells_after_bvector_failure(None, attempt=0) == 36
    assert next_search_shells_after_bvector_failure(12, attempt=0) == 36
    assert next_search_shells_after_bvector_failure(36, attempt=1) == 48
    assert next_search_shells_after_bvector_failure(48, attempt=0) is None
    assert next_search_shells_after_bvector_failure(12, attempt=2) is None


def test_build_epw_input_emits_search_shells_wdata() -> None:
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    cfg = DFTConfig(
        do_epw=True,
        quality_tag="production",
        nbnd=64,
        epw=EPWConfig(
            enabled=True,
            nkc=[12, 12, 12],
            nqc=[4, 4, 4],
            search_shells=36,
        ),
    )
    text = build_epw_input(cfg, structure=s, fermi_eV=20.0)
    assert "search_shells = 36" in text
    assert "wdata(" in text
    assert "nk1         = 12" in text


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


def test_plan_phase_b_after_nk_ladder_exhausted(tmp_path: Path) -> None:
    """After max nkc (or 8→12 sidecar), Phase B selects search_shells=36."""
    cfg = DFTConfig(
        quality_tag="production",
        epw=EPWConfig(
            nkc=[12, 12, 12],
            auto_retry_kmesh=True,
            auto_retry_search_shells=True,
            max_kmesh_retries=2,
            max_search_shells_retries=2,
        ),
    )
    # Already at top of nkc ladder → Phase B
    plan = plan_kmesh_remediation(cfg, _BVECTOR_FAIL, work_dir=tmp_path)
    assert plan is not None
    assert plan.phase == "search_shells"
    assert plan.re_nscf is False
    assert list(plan.config.epw.nkc) == [12, 12, 12]
    assert plan.config.epw.search_shells == 36
    assert "nk ladder exhausted" in plan.log_line
    assert "search_shells=36" in plan.log_line

    cfg2 = DFTConfig(
        quality_tag="production",
        epw=EPWConfig(
            nkc=[6, 6, 6],
            auto_retry_kmesh=True,
            auto_retry_search_shells=True,
            max_kmesh_retries=2,
            max_search_shells_retries=2,
        ),
    )
    # Exhaust Phase A via sidecar (legacy records without phase still count)
    from siscforge.calculators.qe.epw_recipes import record_epw_remediation_attempt

    record_epw_remediation_attempt(
        tmp_path,
        reason="kmesh_bvector",
        nkc_before=[6, 6, 6],
        nkc_after=[8, 8, 8],
        phase="nkc",
    )
    record_epw_remediation_attempt(
        tmp_path,
        reason="kmesh_bvector",
        nkc_before=[8, 8, 8],
        nkc_after=[12, 12, 12],
        phase="nkc",
    )
    plan_b = plan_kmesh_remediation(cfg2, _BVECTOR_FAIL, work_dir=tmp_path)
    assert plan_b is not None
    assert plan_b.phase == "search_shells"
    assert plan_b.config.epw.search_shells == 36
    # nq sacred even on Phase B
    assert list(plan_b.config.epw.nqc) == list(cfg2.epw.nqc)


def test_plan_phase_b_anti_loop_when_exhausted(tmp_path: Path) -> None:
    from siscforge.calculators.qe.epw_recipes import record_epw_remediation_attempt

    cfg = DFTConfig(
        quality_tag="production",
        epw=EPWConfig(
            nkc=[12, 12, 12],
            search_shells=48,
            auto_retry_kmesh=True,
            auto_retry_search_shells=True,
            max_kmesh_retries=2,
            max_search_shells_retries=2,
        ),
    )
    record_epw_remediation_attempt(
        tmp_path,
        reason="kmesh_bvector",
        nkc_before=[8, 8, 8],
        nkc_after=[12, 12, 12],
        phase="nkc",
    )
    record_epw_remediation_attempt(
        tmp_path,
        reason="kmesh_bvector",
        nkc_before=[12, 12, 12],
        nkc_after=[12, 12, 12],
        phase="search_shells",
        search_shells_before=12,
        search_shells_after=36,
    )
    record_epw_remediation_attempt(
        tmp_path,
        reason="kmesh_bvector",
        nkc_before=[12, 12, 12],
        nkc_after=[12, 12, 12],
        phase="search_shells",
        search_shells_before=36,
        search_shells_after=48,
    )
    assert plan_kmesh_remediation(cfg, _BVECTOR_FAIL, work_dir=tmp_path) is None


def test_remediation_exhaustion_blocks_silent_reepw(tmp_path: Path) -> None:
    """#49: identical (projections, nqc) cannot silently re-run after exhaustion."""
    from siscforge.calculators.qe.epw_recipes import (
        epw_config_fingerprint,
        mark_remediation_exhausted,
        remediation_blocks_silent_reepw,
        stamp_remediation_exhausted_eph,
    )

    cfg = DFTConfig(
        quality_tag="screening",
        epw=EPWConfig(nkc=[12, 12, 12], nqc=[2, 2, 2], search_shells=48),
    )
    mark_remediation_exhausted(tmp_path, cfg)
    blocked, reason = remediation_blocks_silent_reepw(tmp_path, cfg)
    assert blocked is True
    assert "identical" in reason

    # Explicit projection change lifts the block.
    changed_proj = cfg.model_copy(
        update={
            "epw": cfg.epw.model_copy(update={"wannier_projections": "Nb:s;p;d"})
        }
    )
    blocked2, why2 = remediation_blocks_silent_reepw(tmp_path, changed_proj)
    assert blocked2 is False
    assert why2 == "projections_changed"

    # Denser phonon mesh lifts the block.
    denser = cfg.model_copy(
        update={"epw": cfg.epw.model_copy(update={"nqc": [3, 3, 3]})}
    )
    blocked3, why3 = remediation_blocks_silent_reepw(tmp_path, denser)
    assert blocked3 is False
    assert why3 == "nqc_changed"

    # Operator opt-in.
    opt_in = cfg.model_copy(
        update={"epw": cfg.epw.model_copy(update={"allow_retry_exhausted": True})}
    )
    blocked4, why4 = remediation_blocks_silent_reepw(tmp_path, opt_in)
    assert blocked4 is False
    assert why4 == "allow_retry_exhausted"

    eph = stamp_remediation_exhausted_eph(None, cfg)
    assert "epw_remediation_exhausted" in eph.quality_flags
    assert eph.alpha2F_summary.get("remediation_exhausted") is True
    assert epw_config_fingerprint(cfg)["projections"] == "random"


def test_phase_b_epw_only_no_phonon_clean_after_nk_exhausted(tmp_path: Path) -> None:
    """Simulated 8→12 already done + bvector → Phase B search_shells; phonon intact."""
    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    work = tmp_path / "cand"
    work.mkdir()
    scf = work / "02_scf"
    scf.mkdir()
    (scf / "ph.out").write_text("     JOB DONE.\n", encoding="utf-8")
    (scf / "siscforge.dyn0").write_text("Dynamical matrices\n", encoding="utf-8")
    (scf / "siscforge.dyn1").write_text("freq = 100.0\n", encoding="utf-8")
    (scf / "_ph0").mkdir()
    (scf / "_ph0" / "keep").write_text("dvscf", encoding="utf-8")
    (scf / "scf.out").write_text(
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )
    (scf / "siscforge.save").mkdir()
    # Successful NSCF at 12³ (keep sacred across Phase B)
    (scf / "nscf.in").write_text("K_POINTS crystal\n1728\n", encoding="utf-8")
    (scf / "nscf.out").write_text(
        "     number of k points=   1728\n"
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )
    (scf / "epw.out").write_text(_BVECTOR_FAIL, encoding="utf-8")
    (scf / "epw.in").write_text(
        "  nk1         = 12\n  nk2 = 12\n  nk3 = 12\n", encoding="utf-8"
    )
    # Phase A already done (8→12) like production Nb0.25Ti0.75N refine
    from siscforge.calculators.qe.epw_recipes import record_epw_remediation_attempt

    record_epw_remediation_attempt(
        scf,
        reason="kmesh_bvector",
        nkc_before=[8, 8, 8],
        nkc_after=[12, 12, 12],
        phase="nkc",
        note="EPW failed (kmesh_get_bvector @ nk=8) — retrying EPW-only with nk=12",
    )
    (work / "01_relax").mkdir()
    (work / "01_relax" / "vc-relax.out").write_text(
        "     JOB DONE.\n", encoding="utf-8"
    )

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
            nkc=[12, 12, 12],
            nqc=[4, 4, 4],
            npool=1,
            auto_retry_kmesh=True,
            auto_retry_search_shells=True,
            max_kmesh_retries=2,
            max_search_shells_retries=2,
            auto_nbndsub=True,
            wannier_retry_on_froz_overflow=False,
        ),
        pseudo_dir=str(tmp_path),
    )

    epw_shells: list[int | None] = []
    nscf_runs: list[bool] = []
    cleaned: list[str] = []

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None, **kwargs):
        out = Path(stdout_path)
        label = str(kwargs.get("step_label") or "")
        if out.name == "nscf.out" or "nscf" in label.lower():
            nscf_runs.append(True)
            out.write_text(
                "     number of k points=   1728\n"
                "     the Fermi energy is    20.0000 ev\n"
                "!\n     total energy              =     -100.0 Ry\n"
                "     JOB DONE.\n",
                encoding="utf-8",
            )
            return 0
        if out.name == "epw.out" or label.lower().startswith("epw.x"):
            shells = None
            epw_in = Path(cwd) / "epw.in"
            if epw_in.is_file():
                text = epw_in.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "search_shells" in line:
                        # wdata(1) = 'search_shells = 36'
                        if "=" in line:
                            part = line.split("search_shells")[-1]
                            digits = "".join(c for c in part if c.isdigit())
                            if digits:
                                shells = int(digits)
            epw_shells.append(shells)
            # First launch (resume plan may already set 36) succeeds when shells set
            if shells is not None and shells >= 36:
                out.write_text(_EPW_OK, encoding="utf-8")
                return 0
            out.write_text(_BVECTOR_FAIL, encoding="utf-8")
            return 1
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
        cleaned.append(step)
        return real_clean(work_dir, step, prefix=prefix)

    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"
    fake_env.pw = "/usr/bin/pw.x"
    fake_env.ph = "/usr/bin/ph.x"
    fake_env.mpirun = None

    def fake_pp(work_dir, prefix):
        save = Path(work_dir) / "save"
        save.mkdir(exist_ok=True)
        (save / "x").write_text("1", encoding="utf-8")
        return QEStepResult(
            name="epw_pp",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=Path(work_dir) / "pp.out",
            input_path=Path(work_dir) / "pp.py",
            success=True,
            message="ok",
        )

    step_log: list[str] = []
    with (
        patch("siscforge.calculators.qe.epw_recipes.require_epw", return_value=fake_env),
        patch("siscforge.calculators.qe.recipes.require_qe", return_value=fake_env),
        patch("siscforge.calculators.qe.recipes._run_cmd", side_effect=fake_run_cmd),
        patch("siscforge.calculators.qe.recipes._mpi_prefix", return_value=[]),
        patch(
            "siscforge.calculators.qe.qe_checkpoint.clean_step_outputs",
            side_effect=tracking_clean,
        ),
        patch("siscforge.calculators.qe.epw_recipes.run_epw_pp", side_effect=fake_pp),
        patch(
            "siscforge.calculators.qe.inputs.resolve_pseudopotentials",
            return_value={"N": "N.upf", "Nb": "Nb.upf", "Ti": "Ti.upf"},
        ),
        patch(
            "siscforge.calculators.qe.pseudos.resolve_pseudopotentials",
            return_value={"N": "N.upf", "Nb": "Nb.upf", "Ti": "Ti.upf"},
        ),
    ):
        result = run_relax_scf_phonon_epw(
            s, cfg, work, prefix="siscforge", step_log=step_log
        )

    # Phonon sacred — never cleaned
    assert "phonon" not in cleaned
    assert (scf / "ph.out").is_file()
    assert (scf / "siscforge.dyn1").is_file()
    assert (scf / "_ph0" / "keep").is_file()

    # Phase B: no re-NSCF (nscf at 12 already matches)
    assert not nscf_runs, f"Phase B should not re-NSCF; log={step_log}"
    assert any(s is not None and s >= 36 for s in epw_shells), epw_shells
    assert any("nk ladder exhausted" in line for line in step_log), step_log
    assert any("search_shells" in line for line in step_log), step_log
    state = load_epw_remediation_state(scf)
    phases = [a.get("phase") for a in state.get("attempts") or []]
    assert "search_shells" in phases
    assert result.success



_KGRID_FAIL = """
     Program EPW
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     Error in routine epw_readin (1):
     k-grid inconsistency between nscf and epw
     Error reading XML file in save directory
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     stopping ...
"""


def test_diagnose_maps_kgrid_and_xml() -> None:
    assert is_kmesh_bvector_failure(_BVECTOR_FAIL)
    from siscforge.calculators.qe.epw_recipes import is_kgrid_inconsistency

    assert is_kgrid_inconsistency(_KGRID_FAIL)
    assert is_remediable_kmesh_failure(_KGRID_FAIL)
    assert classify_epw_failure(_KGRID_FAIL) == "kgrid_inconsistency"
    diag = diagnose_epw_failure(_KGRID_FAIL, work_dir="/tmp/fake", include_tail=False)
    assert "kgrid_inconsistency" in diag
    assert "nscf" in diag.lower() or "invalidat" in diag.lower() or "rebuild" in diag.lower()


def test_stale_nscf_resume_rebuilds_without_touching_phonon(tmp_path: Path) -> None:
    """phonon done + nscf @ 6³ + campaign nkc=8 → re-NSCF, phonon files intact."""
    from siscforge.calculators.qe.qe_checkpoint import nscf_matches_epw_coarse_k
    from siscforge.calculators.qe.recipes import QEStepResult

    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    work = tmp_path / "cand"
    work.mkdir()
    scf = work / "02_scf"
    scf.mkdir()
    (scf / "ph.out").write_text("     JOB DONE.\n", encoding="utf-8")
    (scf / "siscforge.dyn0").write_text("Dynamical matrices\n", encoding="utf-8")
    (scf / "siscforge.dyn1").write_text("freq = 100.0\n", encoding="utf-8")
    (scf / "_ph0").mkdir()
    (scf / "_ph0" / "keep").write_text("dvscf", encoding="utf-8")
    (scf / "siscforge.dvscf1").write_text("x", encoding="utf-8")
    (scf / "scf.out").write_text(
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )
    (scf / "siscforge.save").mkdir()
    (work / "01_relax").mkdir()
    (work / "01_relax" / "vc-relax.out").write_text(
        "     JOB DONE.\n", encoding="utf-8"
    )
    # Stale NSCF at 6³
    (scf / "nscf.in").write_text("K_POINTS crystal\n216\n", encoding="utf-8")
    (scf / "nscf.out").write_text(
        "     number of k points=   216\n"
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )
    (scf / "epw.out").write_text(_KGRID_FAIL, encoding="utf-8")
    (scf / "epw.in").write_text("  nk1         = 8\n  nk2 = 8\n  nk3 = 8\n", encoding="utf-8")

    assert not nscf_matches_epw_coarse_k(work, [8, 8, 8])

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

    nscf_runs: list[list[int]] = []
    epw_runs: list[int] = []
    cleaned: list[str] = []

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None, **kwargs):
        out = Path(stdout_path)
        label = str(kwargs.get("step_label") or "")
        if out.name == "nscf.out" or "nscf" in label.lower():
            # Read written nscf.in mesh if present
            nkc = [8, 8, 8]
            nscf_in = Path(cwd) / "nscf.in"
            if nscf_in.is_file():
                t = nscf_in.read_text(encoding="utf-8")
                if "K_POINTS crystal" in t:
                    for line in t.splitlines():
                        if line.strip().isdigit():
                            n = int(line.strip())
                            c = round(n ** (1 / 3))
                            if c * c * c == n:
                                nkc = [c, c, c]
                            break
            nscf_runs.append(list(nkc))
            out.write_text(
                f"     number of k points=   {nkc[0]*nkc[1]*nkc[2]}\n"
                "     the Fermi energy is    20.0000 ev\n"
                "!\n     total energy              =     -100.0 Ry\n"
                "     JOB DONE.\n",
                encoding="utf-8",
            )
            return 0
        if out.name == "epw.out" or label.lower().startswith("epw.x"):
            epw_runs.append(1)
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

    real_clean = qe_checkpoint.clean_step_outputs

    def tracking_clean(work_dir, step, prefix="siscforge"):
        cleaned.append(step)
        return real_clean(work_dir, step, prefix=prefix)

    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"
    fake_env.pw = "/usr/bin/pw.x"
    fake_env.ph = "/usr/bin/ph.x"
    fake_env.mpirun = None

    def fake_pp(work_dir, prefix):
        save = Path(work_dir) / "save"
        save.mkdir(exist_ok=True)
        (save / "x").write_text("1", encoding="utf-8")
        return QEStepResult(
            name="epw_pp",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=Path(work_dir) / "pp.out",
            input_path=Path(work_dir) / "pp.py",
            success=True,
            message="ok",
        )

    step_log: list[str] = []
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
        result = run_relax_scf_phonon_epw(
            s, cfg, work, prefix="siscforge", step_log=step_log
        )

    # Phonon sacred
    assert "phonon" not in cleaned
    assert (scf / "ph.out").is_file()
    assert (scf / "siscforge.dyn1").is_file()
    assert (scf / "_ph0" / "keep").is_file()
    assert (scf / "siscforge.dvscf1").is_file()

    # Must rebuild NSCF at 8 and run EPW
    assert nscf_runs, f"expected re-NSCF; log={step_log}"
    assert nscf_runs[0] == [8, 8, 8]
    assert epw_runs, "expected EPW launch"
    assert result.success
    assert any("invalidating NSCF" in line for line in step_log), step_log
    assert nscf_matches_epw_coarse_k(work, [8, 8, 8])


def test_preflight_raise_invalidates_existing_nscf(tmp_path: Path) -> None:
    """YAML nkc=6 with existing 6³ nscf → preflight 8 → invalidate (no manual rm)."""
    from siscforge.calculators.qe.recipes import QEStepResult

    s = build_ternary_nitride("Nb", "Ti", 0.25, supercell=(2, 2, 1))
    work = tmp_path / "cand"
    work.mkdir()
    scf = work / "02_scf"
    scf.mkdir()
    (scf / "ph.out").write_text("     JOB DONE.\n", encoding="utf-8")
    (scf / "siscforge.dyn1").write_text("freq = 100.0\n", encoding="utf-8")
    (scf / "scf.out").write_text(
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )
    (scf / "siscforge.save").mkdir()
    (work / "01_relax").mkdir()
    (work / "01_relax" / "vc-relax.out").write_text("     JOB DONE.\n", encoding="utf-8")
    (scf / "nscf.in").write_text("K_POINTS crystal\n216\n", encoding="utf-8")
    (scf / "nscf.out").write_text(
        "     number of k points=   216\n"
        "     the Fermi energy is    20.0000 ev\n"
        "!\n     total energy              =     -100.0 Ry\n"
        "     JOB DONE.\n",
        encoding="utf-8",
    )

    cfg = DFTConfig(
        nproc=1,
        do_relax=True,
        do_phonon=True,
        do_epw=True,
        quality_tag="production",
        nbnd=64,
        qpoints=[4, 4, 4],
        epw=EPWConfig(
            enabled=True,
            nkc=[6, 6, 6],  # preflight raises to 8
            nqc=[4, 4, 4],
            npool=1,
            auto_retry_kmesh=True,
            max_kmesh_retries=2,
            auto_nbndsub=True,
            wannier_retry_on_froz_overflow=False,
        ),
        pseudo_dir=str(tmp_path),
    )

    nscf_launched = []

    def fake_run_cmd(cmd, *, cwd, stdout_path, env=None, **kwargs):
        out = Path(stdout_path)
        if out.name == "nscf.out":
            nscf_launched.append(True)
            out.write_text(
                "     number of k points=   512\n"
                "     the Fermi energy is    20.0000 ev\n"
                "!\n     total energy              =     -100.0 Ry\n"
                "     JOB DONE.\n",
                encoding="utf-8",
            )
            return 0
        if out.name == "epw.out":
            out.write_text(_EPW_OK, encoding="utf-8")
            return 0
        out.write_text(
            "     the Fermi energy is    20.0000 ev\n"
            "!\n     total energy              =     -100.0 Ry\n"
            "     JOB DONE.\n",
            encoding="utf-8",
        )
        return 0

    fake_env = MagicMock()
    fake_env.epw = "/usr/bin/epw.x"
    fake_env.pw = "/usr/bin/pw.x"
    fake_env.ph = "/usr/bin/ph.x"
    fake_env.mpirun = None

    def fake_pp(work_dir, prefix):
        save = Path(work_dir) / "save"
        save.mkdir(exist_ok=True)
        (save / "x").write_text("1", encoding="utf-8")
        return QEStepResult(
            name="epw_pp",
            work_dir=Path(work_dir),
            returncode=0,
            stdout_path=Path(work_dir) / "pp.out",
            input_path=Path(work_dir) / "pp.py",
            success=True,
            message="ok",
        )

    step_log: list[str] = []
    with (
        patch("siscforge.calculators.qe.epw_recipes.require_epw", return_value=fake_env),
        patch("siscforge.calculators.qe.recipes.require_qe", return_value=fake_env),
        patch("siscforge.calculators.qe.recipes._run_cmd", side_effect=fake_run_cmd),
        patch("siscforge.calculators.qe.recipes._mpi_prefix", return_value=[]),
        patch("siscforge.calculators.qe.epw_recipes.run_epw_pp", side_effect=fake_pp),
        patch(
            "siscforge.calculators.qe.inputs.resolve_pseudopotentials",
            return_value={"N": "N.upf", "Nb": "Nb.upf", "Ti": "Ti.upf"},
        ),
        patch(
            "siscforge.calculators.qe.pseudos.resolve_pseudopotentials",
            return_value={"N": "N.upf", "Nb": "Nb.upf", "Ti": "Ti.upf"},
        ),
    ):
        result = run_relax_scf_phonon_epw(
            s, cfg, work, prefix="siscforge", step_log=step_log
        )

    assert nscf_launched, step_log
    assert (scf / "ph.out").is_file()
    assert result.success
    assert any("invalidating NSCF" in line or "raised" in line.lower() for line in step_log)
