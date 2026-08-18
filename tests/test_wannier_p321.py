"""P3.2.1 — automated nscf + pw2wannier90 orchestration tests."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from siscforge.calculators.qe.env import QEEnvironment
from siscforge.calculators.qe.inputs import build_nscf_wannier_input
from siscforge.calculators.qe.wannier import (
    SAVE_STAGE_SIDECAR,
    WANNIER_FAILURE_CLASSES,
    build_pw2wannier90_input,
    classify_wannier_failure,
    find_upstream_save_dir,
    operator_next_step,
    prepare_amn_mmn,
    resolve_kmesh,
    resolve_nscf_nbnd,
    run_wannier_workflow,
    save_stage_fingerprint,
    save_stage_matches,
    stage_save_for_wannier,
)
from siscforge.export import write_synthesis_cards
from siscforge.models import (
    CandidateEvaluation,
    DFTConfig,
    StructureCandidate,
    WannierConfig,
)
from siscforge.structure.nitrides import build_binary_nitride

_SUCCESS_WOUT = """
 Number of Wannier Functions             :       8
 Number of bands                         :      16
 Final State
 WF centre and spread    1  (  0.000000,  0.000000,  0.000000 )     0.812345
 WF centre and spread    2  (  0.500000,  0.500000,  0.000000 )     0.901234
 Sum of centres and spreads (  0.500000,  0.500000,  0.000000 )     1.713579
 Omega I      =    2.100000
 All done.
"""


def _write_exe(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _fake_bindir(tmp_path: Path, *, nscf_ok: bool = True, p2w_ok: bool = True) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if nscf_ok:
        _write_exe(
            bindir / "pw.x",
            "#!/bin/sh\necho '     JOB DONE.'\nexit 0\n",
        )
    else:
        _write_exe(
            bindir / "pw.x",
            "#!/bin/sh\necho 'Error in routine electrons (1)'\n"
            "echo 'nscf failed'\nexit 1\n",
        )
    if p2w_ok:
        _write_exe(
            bindir / "pw2wannier90.x",
            "#!/bin/sh\n"
            "echo 'mock amn' > siscforge.amn\n"
            "echo 'mock mmn' > siscforge.mmn\n"
            "echo 'JOB DONE'\n"
            "exit 0\n",
        )
    else:
        _write_exe(
            bindir / "pw2wannier90.x",
            "#!/bin/sh\n"
            "echo 'Error in pw2wannier90: abort'\n"
            "exit 1\n",
        )
    wout = _SUCCESS_WOUT.replace("'", "'\\''")
    _write_exe(
        bindir / "wannier90.x",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-pp\" ]; then\n"
        "  seed=\"${2:-siscforge}\"\n"
        "  echo 'nnkp mock' > \"${seed}.nnkp\"\n"
        "  exit 0\n"
        "fi\n"
        "seed=\"${1:-siscforge}\"\n"
        f"cat > \"${{seed}}.wout\" << 'EOF'\n{wout}\nEOF\n"
        "echo 'mock chk' > \"${seed}.chk\"\n"
        "exit 0\n",
    )
    return bindir


def _fake_env(
    bindir: Path, *, pw: bool = True, p2w: bool = True, w90: bool = True
) -> QEEnvironment:
    return QEEnvironment(
        pw=str(bindir / "pw.x") if pw else None,
        pw2wannier90=str(bindir / "pw2wannier90.x") if p2w else None,
        wannier90=str(bindir / "wannier90.x") if w90 else None,
        mpirun=None,
    )


def _nb_dft(tmp_path: Path, **wannier_kw: object) -> DFTConfig:
    pseudo = tmp_path / "pseudo"
    pseudo.mkdir(exist_ok=True)
    (pseudo / "Nb.upf").write_text("fake\n", encoding="utf-8")
    (pseudo / "N.upf").write_text("fake\n", encoding="utf-8")
    return DFTConfig(
        do_wannier=True,
        nbnd=20,
        quality_tag="screening",
        pseudo_dir=str(pseudo),
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
        wannier=WannierConfig(
            enabled=True,
            seedname="siscforge",
            kmesh=[2, 2, 2],
            **wannier_kw,  # type: ignore[arg-type]
        ),
    )


def _make_save(root: Path, prefix: str = "siscforge") -> Path:
    save = root / "out" / f"{prefix}.save"
    save.mkdir(parents=True)
    (save / "charge-density.dat").write_text("SACRED_CHARGE\n", encoding="utf-8")
    return save


def test_auto_nscf_pw2wannier_default_on() -> None:
    cfg = WannierConfig()
    assert cfg.auto_nscf_pw2wannier is True
    assert cfg.enabled is False  # conventional campaigns still inert


def test_build_nscf_wannier_input_crystal_mesh(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dft = _nb_dft(tmp_path)
    mesh = resolve_kmesh(dft, s)
    text = build_nscf_wannier_input(s, dft, prefix="siscforge", nk=mesh)
    low = text.lower()
    assert "calculation" in low and "nscf" in low
    assert "k_points crystal" in low
    npts = mesh[0] * mesh[1] * mesh[2]
    assert str(npts) in text
    assert "nosym" in low
    assert "noinv" in low
    # Isolated outdir — not the EPW flat './' layout
    assert "outdir" in low


def test_build_nscf_wannier_input_hubbard(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dft = _nb_dft(tmp_path)
    dft = dft.model_copy(update={"do_dftu": True})
    text = build_nscf_wannier_input(
        s, dft, prefix="siscforge", nk=[2, 2, 2], include_hubbard=True
    )
    assert "lda_plus_u" in text.lower() or "hubbard" in text.lower()


def test_build_pw2wannier90_input_flags() -> None:
    text = build_pw2wannier90_input(prefix="siscforge", seedname="siscforge")
    low = text.lower()
    assert "&inputpp" in low
    assert "write_amn = .true." in low
    assert "write_mmn = .true." in low
    assert "seedname = 'siscforge'" in low
    assert "spin_component = 'none'" in low


def test_classify_nscf_and_pw2wannier_failures() -> None:
    assert classify_wannier_failure("nscf failed: pw.x rc=1") == "nscf_failed"
    assert (
        classify_wannier_failure("Error in pw2wannier90: abort") == "pw2wannier_failed"
    )
    assert (
        classify_wannier_failure("pw2wannier90.x: not found on PATH") == "binary_missing"
    )
    assert classify_wannier_failure("pw.x not found") == "binary_missing"
    assert "nscf_failed" in WANNIER_FAILURE_CLASSES
    assert "pw2wannier_failed" in WANNIER_FAILURE_CLASSES


def test_operator_next_step_not_only_manual_stage() -> None:
    nscf = operator_next_step("nscf_failed")
    assert "nscf.out" in nscf
    assert "SCF/DFT+U kept" in nscf
    p2w = operator_next_step("pw2wannier_failed")
    assert "pw2wan.out" in p2w
    miss = operator_next_step("missing_files")
    assert "pw2wannier90" in miss
    assert "install" in miss or "stage" in miss


def test_no_binary_path_still_missing_files(tmp_path: Path) -> None:
    """Without binaries, P3.2.1 soft-skips — same missing_files contract as P3.2."""
    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(enabled=True, seedname="siscforge", kmesh=[2, 2, 2]),
        nbnd=20,
    )
    result = run_wannier_workflow(s, dft, tmp_path / "wannier")
    assert result.wannier_ok is False
    assert result.failure_class == "missing_files"
    assert result.ready_for_dmft is False
    assert result.win_path
    blob = (result.dmft_gate_notes or "") + result.summary_line()
    assert "pw2wannier90" in blob


def test_auto_disabled_keeps_manual_stage(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(
            enabled=True,
            seedname="siscforge",
            auto_nscf_pw2wannier=False,
        ),
        nbnd=20,
    )
    result = run_wannier_workflow(s, dft, tmp_path / "wannier")
    assert result.failure_class == "missing_files"
    assert "auto_nscf_pw2wannier" in (result.raw.get("operator_next_step") or "") or (
        "stage" in result.summary_line()
    )


def test_find_upstream_save_prefers_dftu(tmp_path: Path) -> None:
    scf = tmp_path / "cand"
    _make_save(scf)
    dftu_save = scf / "dftu" / "out" / "siscforge.save"
    dftu_save.mkdir(parents=True)
    (dftu_save / "charge-density.dat").write_text("U_CHARGE\n", encoding="utf-8")
    found = find_upstream_save_dir(scf, prefix="siscforge")
    assert found is not None
    assert "dftu" in found.parts


def test_no_charge_density_classifies_missing_files(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    bindir = _fake_bindir(tmp_path)
    dft = _nb_dft(tmp_path)
    result = run_wannier_workflow(
        s,
        dft,
        tmp_path / "wannier",
        qe_env=_fake_env(bindir),
        scf_work_dir=tmp_path / "empty_scf",
    )
    assert result.failure_class == "missing_files"
    assert "save" in (result.raw.get("note") or "").lower() or "save" in (
        result.dmft_gate_notes or ""
    ).lower()


def test_orchestration_success_with_fakes(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    bindir = _fake_bindir(tmp_path)
    dft = _nb_dft(tmp_path)
    scf = tmp_path / "scf"
    sacred = _make_save(scf)
    result = run_wannier_workflow(
        s,
        dft,
        tmp_path / "wannier",
        qe_env=_fake_env(bindir),
        scf_work_dir=scf,
        prefix="siscforge",
    )
    assert result.failure_class is None
    assert result.wannier_ok is True
    assert result.status == "ok"
    assert result.amn_path and Path(result.amn_path).is_file()
    assert result.mmn_path and Path(result.mmn_path).is_file()
    assert result.chk_path
    assert result.ready_for_dmft is True
    # Isolated copy — upstream untouched
    assert (sacred / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    assert (tmp_path / "wannier" / "out" / "siscforge.save").is_dir()
    assert (tmp_path / "wannier" / "nscf.in").is_file()
    assert (tmp_path / "wannier" / "pw2wan.in").is_file()


def test_nscf_failure_classified_sacred_upstream(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    bindir = _fake_bindir(tmp_path, nscf_ok=False)
    dft = _nb_dft(tmp_path)
    scf = tmp_path / "scf"
    sacred = _make_save(scf)
    marker = scf / "scf.out"
    marker.write_text("JOB DONE\n", encoding="utf-8")
    result = run_wannier_workflow(
        s,
        dft,
        tmp_path / "wannier",
        qe_env=_fake_env(bindir),
        scf_work_dir=scf,
    )
    assert result.wannier_ok is False
    assert result.failure_class == "nscf_failed"
    assert result.ready_for_dmft is False
    assert "nscf" in (result.dmft_gate_notes or "").lower()
    # Sacred: SCF artifacts retained
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "JOB DONE\n"
    assert (sacred / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    assert not (tmp_path / "scf").joinpath("wannier").exists()


def test_pw2wannier_failure_classified_sacred_upstream(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    bindir = _fake_bindir(tmp_path, p2w_ok=False)
    dft = _nb_dft(tmp_path)
    scf = tmp_path / "scf"
    sacred = _make_save(scf)
    result = run_wannier_workflow(
        s,
        dft,
        tmp_path / "wannier",
        qe_env=_fake_env(bindir),
        scf_work_dir=scf,
    )
    assert result.failure_class == "pw2wannier_failed"
    assert result.wannier_ok is False
    assert result.ready_for_dmft is False
    assert (sacred / "charge-density.dat").is_file()
    summary = result.summary_line()
    assert "fail=pw2wannier_failed" in summary
    assert "next=" in summary


def test_stage_save_does_not_delete_src(tmp_path: Path) -> None:
    src = _make_save(tmp_path / "scf")
    dest = stage_save_for_wannier(src, tmp_path / "wannier", prefix="siscforge")
    assert dest.is_dir()
    assert dest.resolve() != src.resolve()
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    # Second call reuses dest, still does not touch src
    dest2 = stage_save_for_wannier(src, tmp_path / "wannier", prefix="siscforge")
    assert dest2 == dest


def test_stage_save_writes_sidecar_on_first_copy(tmp_path: Path) -> None:
    src = _make_save(tmp_path / "scf")
    wd = tmp_path / "wannier"
    dest = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20, include_hubbard=False
    )
    sidecar = wd / "out" / SAVE_STAGE_SIDECAR
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    expected = save_stage_fingerprint(
        src, kmesh=[2, 2, 2], nbnd=20, include_hubbard=False
    )
    assert payload == expected
    assert payload["kmesh"] == [2, 2, 2]
    assert payload["nbnd"] == 20
    assert payload["include_hubbard"] is False
    assert payload["src_save"] == str(src.resolve())
    assert payload["charge_marker"] == "charge-density.dat"
    assert payload["charge_size"] == len("SACRED_CHARGE\n")
    assert dest.is_dir()
    assert save_stage_matches(
        wd, src, kmesh=[2, 2, 2], nbnd=20, include_hubbard=False
    )


def test_stage_save_reuses_dest_when_fingerprint_matches(tmp_path: Path) -> None:
    src = _make_save(tmp_path / "scf")
    wd = tmp_path / "wannier"
    dest = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20
    )
    marker = dest / "reuse_marker"
    marker.write_text("keep\n", encoding="utf-8")
    dest2 = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20
    )
    assert dest2 == dest
    assert marker.read_text(encoding="utf-8") == "keep\n"
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"


@pytest.mark.parametrize(
    "new_kwargs",
    [
        {"kmesh": [4, 4, 4], "nbnd": 20, "include_hubbard": False},
        {"kmesh": [2, 2, 2], "nbnd": 32, "include_hubbard": False},
        {"kmesh": [2, 2, 2], "nbnd": 20, "include_hubbard": True},
    ],
    ids=["kmesh", "nbnd", "hubbard"],
)
def test_stage_save_restages_isolated_copy_on_input_change(
    tmp_path: Path, new_kwargs: dict[str, object]
) -> None:
    src = _make_save(tmp_path / "scf")
    extra = src / "upstream_only.txt"
    extra.write_text("UPSTREAM\n", encoding="utf-8")
    wd = tmp_path / "wannier"
    dest = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20, include_hubbard=False
    )
    (dest / "stale_marker").write_text("old\n", encoding="utf-8")
    (wd / "nscf.out").write_text("     JOB DONE.\n", encoding="utf-8")
    (wd / "nscf.in").write_text("&control\n/\n", encoding="utf-8")
    dest2 = stage_save_for_wannier(src, wd, prefix="siscforge", **new_kwargs)  # type: ignore[arg-type]
    assert dest2 == dest
    assert dest2.is_dir()
    assert not (dest2 / "stale_marker").exists()
    assert (dest2 / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    assert (dest2 / "upstream_only.txt").read_text(encoding="utf-8") == "UPSTREAM\n"
    # Isolated dest replaced; wannier-local nscf logs dropped so nscf re-runs
    assert not (wd / "nscf.out").exists()
    assert not (wd / "nscf.in").exists()
    # Sacred upstream untouched
    assert extra.read_text(encoding="utf-8") == "UPSTREAM\n"
    assert extra.is_file()
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    assert src.is_dir()
    payload = json.loads((wd / "out" / SAVE_STAGE_SIDECAR).read_text(encoding="utf-8"))
    assert payload["kmesh"] == list(new_kwargs["kmesh"])  # type: ignore[arg-type]
    assert payload["nbnd"] == new_kwargs["nbnd"]
    assert payload["include_hubbard"] == new_kwargs["include_hubbard"]


def test_stage_save_restages_legacy_dest_without_sidecar(tmp_path: Path) -> None:
    src = _make_save(tmp_path / "scf")
    wd = tmp_path / "wannier"
    dest = wd / "out" / "siscforge.save"
    dest.mkdir(parents=True)
    (dest / "stale_marker").write_text("legacy\n", encoding="utf-8")
    dest2 = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20
    )
    assert dest2 == dest
    assert not (dest / "stale_marker").exists()
    assert (dest / "charge-density.dat").is_file()
    assert (wd / "out" / SAVE_STAGE_SIDECAR).is_file()
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"


def test_stage_save_restages_when_upstream_charge_marker_changes(
    tmp_path: Path,
) -> None:
    src = _make_save(tmp_path / "scf")
    wd = tmp_path / "wannier"
    dest = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20
    )
    (dest / "reuse_marker").write_text("keep\n", encoding="utf-8")
    (src / "charge-density.dat").write_text("SACRED_CHARGE_V2\n", encoding="utf-8")
    dest2 = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20
    )
    assert dest2 == dest
    assert not (dest2 / "reuse_marker").exists()
    assert (dest2 / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE_V2\n"
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE_V2\n"


def test_stage_save_never_deletes_upstream_on_restage(tmp_path: Path) -> None:
    src = _make_save(tmp_path / "scf")
    sibling = tmp_path / "scf" / "scf.out"
    sibling.write_text("JOB DONE\n", encoding="utf-8")
    wd = tmp_path / "wannier"
    stage_save_for_wannier(src, wd, prefix="siscforge", kmesh=[2, 2, 2], nbnd=20)
    stage_save_for_wannier(src, wd, prefix="siscforge", kmesh=[8, 8, 8], nbnd=40)
    assert src.is_dir()
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    assert sibling.read_text(encoding="utf-8") == "JOB DONE\n"
    # Isolated dest is under wannier/, not scf/
    assert (wd / "out" / "siscforge.save").is_dir()
    assert not (tmp_path / "scf").joinpath("wannier").exists()


def test_prepare_restages_stale_save_without_touching_upstream(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    bindir = _fake_bindir(tmp_path)
    dft = _nb_dft(tmp_path)
    scf = tmp_path / "scf"
    src = _make_save(scf)
    wd = tmp_path / "wannier"
    dest = stage_save_for_wannier(
        src, wd, prefix="siscforge", kmesh=[1, 1, 1], nbnd=4, include_hubbard=False
    )
    (dest / "stale_marker").write_text("old\n", encoding="utf-8")
    (wd / "nscf.out").write_text("     JOB DONE.\n", encoding="utf-8")
    log: list[str] = []
    result = prepare_amn_mmn(
        s,
        dft,
        wd,
        qe_env=_fake_env(bindir),
        scf_work_dir=scf,
        prefix="siscforge",
        step_log=log,
    )
    assert result is None
    assert not (dest / "stale_marker").exists()
    assert (src / "charge-density.dat").read_text(encoding="utf-8") == "SACRED_CHARGE\n"
    assert src.is_dir()
    assert any("staged isolated save" in line for line in log)
    # Stale JOB DONE was dropped so fake nscf re-ran
    assert (wd / "siscforge.amn").is_file()
    mesh = resolve_kmesh(dft, s)
    assert save_stage_matches(
        wd,
        src,
        kmesh=mesh,
        nbnd=resolve_nscf_nbnd(dft),
        include_hubbard=False,
    )


def test_prepare_amn_mmn_none_when_already_staged(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dft = DFTConfig(do_wannier=True, wannier=WannierConfig(enabled=True))
    wd = tmp_path / "wannier"
    wd.mkdir()
    (wd / "siscforge.amn").write_text("a\n", encoding="utf-8")
    (wd / "siscforge.mmn").write_text("m\n", encoding="utf-8")
    assert prepare_amn_mmn(s, dft, wd) is None


def test_nscf_failure_card_operator_next_step(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    bindir = _fake_bindir(tmp_path, nscf_ok=False)
    dft = _nb_dft(tmp_path)
    scf = tmp_path / "scf"
    _make_save(scf)
    result = run_wannier_workflow(
        s, dft, tmp_path / "wannier", qe_env=_fake_env(bindir), scf_work_dir=scf
    )
    ev = CandidateEvaluation(
        candidate=StructureCandidate(
            formula="NbN", material_family="tm_nitride", candidate_id="p321-nscf"
        ),
        wannier=result,
        status="failed",
    )
    cards = write_synthesis_cards([ev], tmp_path / "cards.md", campaign_name="p321")
    text = cards.read_text()
    assert "operator next step" in text.lower()
    assert "nscf.out" in text
    assert "residual P3.2.1" not in text


@pytest.mark.skipif(
    os.environ.get("SISCFORGE_RUN_QE") != "1",
    reason="Set SISCFORGE_RUN_QE=1 with pw.x + pw2wannier90.x for real-QE golden",
)
def test_real_qe_orchestration_optional() -> None:
    from siscforge.calculators.qe.env import detect_qe_environment

    env = detect_qe_environment()
    if not (env.pw and env.pw2wannier90):
        pytest.skip("pw.x / pw2wannier90.x not available")
    # Presence-only gate: a full NdNiO2 nscf golden is workstation-local.
    assert env.pw
    assert env.pw2wannier90
