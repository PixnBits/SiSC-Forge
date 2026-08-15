"""solid_dmft conv_imp*.dat / convergence_obs → DMFTResult.converged (#37).

No TRIQS required. HDF5 coverage uses an in-memory tree. Last-row
occupancy remains the fallback when conv diagnostics are missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from siscforge.calculators.qe.dmft import (
    parse_dmft_observables,
    run_solid_dmft,
)
from siscforge.calculators.qe.dmft_launch import LaunchOutcome
from siscforge.calculators.qe.dmft_observables import (
    SCREENING_CONV_CUTOFFS,
    apply_convergence_precedence,
    convergence_from_h5_tree,
    discover_convergence_signal,
    discover_dmft_metrics,
    empty_metrics,
    extract_convergence_h5,
    find_conv_dat,
    parse_conv_dat,
    parse_conv_dat_text,
)
from siscforge.models import DMFTConfig, WannierResult

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dmft"

_UNCONVERGED_CONV = "\n".join(
    [
        "it | δμ | δocc orb | δimp occ | δGimp | δG0 | δΣ",
        "  0 | 0.80000 | 0.20000 0.18000 0.16000 | 0.20000 | 0.30000 | 0.28000 | 0.25000",
        "  1 | 0.40000 | 0.16000 0.15000 0.14000 | 0.15000 | 0.22000 | 0.18000 | 0.16000",
        "",
    ]
)


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


def _write_obs(wd: Path) -> None:
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "observables_imp0.dat").write_text(
        (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_delta_sigma_header_survives_final_sigma_lowercase() -> None:
    """Python lowercases word-final Σ to ς; the column must still be d_Sigma."""
    from siscforge.calculators.qe.dmft_observables import _conv_header_kind

    assert _conv_header_kind("δΣ") == "d_Sigma"
    assert _conv_header_kind("δσ") == "d_Sigma"
    assert _conv_header_kind("d_Sigma") == "d_Sigma"


def test_parse_conv_imp0_dat_fixture() -> None:
    path = FIXTURES / "conv_imp0.dat"
    assert path.is_file()
    signal = parse_conv_dat(path)
    assert signal["usable"] is True
    assert signal["source"] == "conv_dat"
    assert signal["converged"] is True
    assert signal["residuals"]["d_imp_occ"] == pytest.approx(0.008)
    assert signal["residuals"]["d_Gimp"] == pytest.approx(0.03)
    assert signal["residuals"]["d_G0"] == pytest.approx(0.025)
    assert signal["residuals"]["d_Sigma"] == pytest.approx(0.02)
    assert signal["residuals"]["d_mu"] == pytest.approx(0.08)
    assert SCREENING_CONV_CUTOFFS["d_imp_occ"] == pytest.approx(0.02)


def test_parse_conv_dat_unicode_header_unconverged() -> None:
    signal = parse_conv_dat_text(_UNCONVERGED_CONV)
    assert signal["usable"] is True
    assert signal["converged"] is False
    assert signal["residuals"]["d_imp_occ"] == pytest.approx(0.15)
    assert "d_imp_occ" in signal["notes"]


def test_parse_conv_dat_ascii_header() -> None:
    text = (
        "it | d_mu | d_occ orb | d_imp occ | d_Gimp | d_G0 | d_Sigma\n"
        " 2 | 0.01 | 0.002 0.001 | 0.004 | 0.01 | 0.02 | 0.015\n"
    )
    signal = parse_conv_dat_text(text)
    assert signal["usable"] is True
    assert signal["converged"] is True
    assert signal["residuals"]["d_imp_occ"] == pytest.approx(0.004)
    assert signal["residuals"]["d_Sigma"] == pytest.approx(0.015)


def test_d_mu_only_is_not_usable() -> None:
    """d_mu is informational — not enough to set converged."""
    text = "it | δμ\n 1 | 0.001\n"
    signal = parse_conv_dat_text(text)
    assert signal["residuals"]["d_mu"] == pytest.approx(0.001)
    assert signal["usable"] is False
    assert signal["converged"] is None


def test_empty_conv_dat_is_not_usable() -> None:
    signal = parse_conv_dat_text("# nothing\n")
    assert signal["usable"] is False
    assert signal["converged"] is None


def test_find_conv_dat_under_out(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    dest = wd / "out" / "conv_imp0.dat"
    dest.parent.mkdir(parents=True)
    dest.write_text((FIXTURES / "conv_imp0.dat").read_text(encoding="utf-8"), encoding="utf-8")
    found = find_conv_dat(wd)
    assert dest in found
    signal = discover_convergence_signal(wd)
    assert signal["usable"] is True
    assert signal["converged"] is True


def test_occupancy_only_stays_non_failed(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    _write_obs(wd)
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=False)
    assert kind == "dat"
    assert metrics["filling"] == pytest.approx(8.76)
    assert metrics["converged"] is True
    assert metrics["converged_source"] == "last_row_heuristic"


def test_conv_fixture_sets_converged_true(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    _write_obs(wd)
    (wd / "conv_imp0.dat").write_text(
        (FIXTURES / "conv_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=True)
    assert kind == "dat"
    assert metrics["converged"] is True
    assert metrics["converged_source"] == "conv_dat"
    assert metrics["convergence"]["residuals"]["d_imp_occ"] == pytest.approx(0.008)
    payload = json.loads((wd / "observables.json").read_text(encoding="utf-8"))
    assert payload["converged"] is True
    assert payload["converged_source"] == "conv_dat"
    assert payload.get("siscforge_bridge") == "native_solid_dmft"


def test_conv_above_cutoff_sets_converged_false(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    _write_obs(wd)
    (wd / "conv_imp0.dat").write_text(_UNCONVERGED_CONV, encoding="utf-8")
    metrics, _, _ = discover_dmft_metrics(wd, write_json=False)
    assert metrics["converged"] is False
    assert metrics["converged_source"] == "conv_dat"
    assert metrics["filling"] == pytest.approx(8.76)


def test_auto_launch_unconverged_conv_is_failed(tmp_path: Path) -> None:
    obs = (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8")

    def _launcher(_cmd, work_dir: Path, _timeout):
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        (Path(work_dir) / "observables_imp0.dat").write_text(obs, encoding="utf-8")
        (Path(work_dir) / "conv_imp0.dat").write_text(_UNCONVERGED_CONV, encoding="utf-8")
        return LaunchOutcome(returncode=0, command=list(_cmd), source="fake_conv")

    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        formula="NdNiO2",
        launcher=_launcher,
    )
    assert result.status == "failed"
    assert result.converged is False
    assert result.failure_class == "not_converged"
    assert result.filling == pytest.approx(8.76)
    assert result.raw.get("convergence", {}).get("source") == "conv_dat"
    assert "conv_dat" in (result.provenance.notes or "")


def test_auto_launch_occupancy_only_still_ok(tmp_path: Path) -> None:
    obs = (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8")

    def _launcher(_cmd, work_dir: Path, _timeout):
        (Path(work_dir) / "observables_imp0.dat").write_text(obs, encoding="utf-8")
        return LaunchOutcome(returncode=0, command=list(_cmd), source="fake_dat")

    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        launcher=_launcher,
    )
    assert result.status == "ok"
    assert result.converged is True
    assert result.raw.get("metrics", {}).get("converged_source") == "last_row_heuristic"
    assert "last_row" not in (result.provenance.notes or "")


def test_operator_json_explicit_false_wins_over_conv(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        json.dumps({"filling": 8.1, "converged": False}),
        encoding="utf-8",
    )
    (wd / "conv_imp0.dat").write_text(
        (FIXTURES / "conv_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=False)
    assert kind == "json"
    assert metrics["converged_explicit"] is True
    assert metrics["converged"] is False
    assert metrics["converged_source"] == "json"


def test_operator_json_explicit_true_wins_over_unconverged_conv(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        json.dumps({"filling": 8.1, "success": True}),
        encoding="utf-8",
    )
    (wd / "conv_imp0.dat").write_text(_UNCONVERGED_CONV, encoding="utf-8")
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=False)
    assert kind == "json"
    assert metrics["converged_explicit"] is True
    assert metrics["converged"] is True
    assert metrics["converged_source"] == "json"


def test_bridged_json_is_not_explicit_live_conv_overrides(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        json.dumps(
            {
                "filling": 8.76,
                "occupancy": {"imp0": 8.76},
                "converged": True,
                "siscforge_bridge": "native_solid_dmft",
                "converged_source": "last_row_heuristic",
            }
        ),
        encoding="utf-8",
    )
    (wd / "conv_imp0.dat").write_text(_UNCONVERGED_CONV, encoding="utf-8")
    parsed = parse_dmft_observables(wd / "observables.json")
    assert parsed["converged_explicit"] is False
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=False)
    assert kind == "json"
    assert metrics["converged"] is False
    assert metrics["converged_source"] == "conv_dat"


def test_bridged_json_sticky_when_live_conv_gone(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        json.dumps(
            {
                "filling": 8.76,
                "occupancy": {"imp0": 8.76},
                "converged": False,
                "siscforge_bridge": "native_solid_dmft",
                "converged_source": "conv_dat",
            }
        ),
        encoding="utf-8",
    )
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=False)
    assert kind == "json"
    assert metrics["converged"] is False
    assert metrics["converged_source"] == "conv_dat"
    assert "stored" in (metrics["convergence"] or {}).get("notes", "")


def test_h5_convergence_obs_tree_sets_converged() -> None:
    tree = {
        "DMFT_results": {
            "observables": {"imp_occ": [8.72]},
            "convergence_obs": {
                "d_mu": [0.2, 0.01],
                "d_imp_occ": [0.08, 0.004],
                "d_Gimp": [0.2, 0.02],
                "d_G0": [0.1, 0.01],
                "d_Sigma": [0.1, 0.015],
            },
        }
    }
    signal = convergence_from_h5_tree(tree)
    assert signal["usable"] is True
    assert signal["source"] == "h5_convergence_obs"
    assert signal["converged"] is True
    assert signal["residuals"]["d_imp_occ"] == pytest.approx(0.004)

    metrics = {
        **empty_metrics(),
        "filling": 8.72,
        "occupancy_summary": {"imp0": 8.72},
    }
    apply_convergence_precedence(metrics, signal=signal)
    assert metrics["converged"] is True
    assert metrics["converged_source"] == "h5_convergence_obs"


def test_h5_convergence_obs_above_cutoff() -> None:
    tree = {
        "DMFT_results": {
            "convergence_obs": {
                "d_imp_occ": [0.2, 0.12],
                "d_Gimp": [0.4, 0.08],
            }
        }
    }
    signal = extract_convergence_h5(tree)
    assert signal["usable"] is True
    assert signal["converged"] is False


def test_h5_explicit_converged_flag() -> None:
    tree = {
        "DMFT_results": {
            "convergence_obs": {
                "d_imp_occ": [0.2],
                "converged": True,
            }
        }
    }
    signal = convergence_from_h5_tree(tree)
    assert signal["usable"] is True
    assert signal["converged"] is True
    assert "explicit" in signal["notes"]


def test_seed_h5_without_dmft_results_is_not_conv() -> None:
    signal = convergence_from_h5_tree(
        {
            "dft_input": {"n_k": 64},
            "wannier": {"occupancy": {"Ni": 8.8}},
        }
    )
    assert signal["usable"] is False
    assert signal["converged"] is None
    assert not signal["residuals"]


def test_discover_h5_conv_via_injected_opener(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "siscforge.h5").write_bytes(b"fake")
    tree = {
        "DMFT_results": {
            "observables": {"imp_occ": [8.72]},
            "convergence_obs": {
                "d_imp_occ": [0.005],
                "d_Gimp": [0.02],
                "d_G0": [0.02],
                "d_Sigma": [0.02],
            },
        }
    }

    def opener(_path: Path):
        return tree

    metrics, kind, _ = discover_dmft_metrics(
        wd, write_json=False, seedname="siscforge", h5_opener=opener
    )
    assert kind == "h5"
    assert metrics["filling"] == pytest.approx(8.72)
    assert metrics["converged"] is True
    assert metrics["converged_source"] == "h5_convergence_obs"


def test_apply_precedence_unit_matrix() -> None:
    usable = empty_metrics()
    usable["filling"] = 8.0
    usable["occupancy_summary"] = {"imp0": 8.0}

    live_false = {
        "converged": False,
        "source": "conv_dat",
        "usable": True,
        "residuals": {"d_imp_occ": 0.2},
        "notes": "above",
        "path": "conv_imp0.dat",
        "cutoffs": dict(SCREENING_CONV_CUTOFFS),
    }
    out = apply_convergence_precedence(dict(usable), signal=live_false)
    assert out["converged"] is False
    assert out["converged_source"] == "conv_dat"

    explicit = dict(usable)
    explicit["converged"] = False
    explicit["converged_explicit"] = True
    live_true = dict(live_false)
    live_true["converged"] = True
    out = apply_convergence_precedence(explicit, signal=live_true)
    assert out["converged"] is False
    assert out["converged_source"] == "json"

    empty_sig = {
        "converged": None,
        "source": None,
        "usable": False,
        "residuals": {},
        "notes": "",
        "path": None,
        "cutoffs": dict(SCREENING_CONV_CUTOFFS),
    }
    out = apply_convergence_precedence(dict(usable), signal=empty_sig)
    assert out["converged"] is True
    assert out["converged_source"] == "last_row_heuristic"

    barren = empty_metrics()
    out = apply_convergence_precedence(barren, signal=empty_sig)
    assert out["converged"] is False
    assert out["converged_source"] is None


def test_parse_json_marks_explicit_only_for_operator_drop_in() -> None:
    op = parse_dmft_observables({"filling": 8.0, "converged": False})
    assert op["converged_explicit"] is True
    assert op["converged"] is False
    job = parse_dmft_observables({"filling": 8.0, "job_done": True})
    assert job["converged_explicit"] is True
    assert job["converged"] is True
    none = parse_dmft_observables({"filling": 8.0})
    assert none["converged_explicit"] is False
    bridged = parse_dmft_observables(
        {
            "filling": 8.0,
            "converged": True,
            "siscforge_bridge": "native_solid_dmft",
        }
    )
    assert bridged["converged_explicit"] is False
    assert bridged["converged"] is True
