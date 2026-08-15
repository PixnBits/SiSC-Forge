"""Native solid_dmft .dat / h5 extractors (issue #35).

No TRIQS required. HDF5 coverage uses an in-memory tree (and a real
file when h5py happens to be installed). JSON drop-in stays preferred.
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
    discover_dmft_metrics,
    extract_dmft_h5,
    find_dat_observables,
    h5py_available,
    materialize_observables_json,
    metrics_from_h5_tree,
    metrics_to_json_payload,
    metrics_usable,
    parse_dat_group,
    parse_dmft_dat,
)
from siscforge.models import DMFTConfig, WannierResult

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dmft"


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


def test_parse_observables_imp0_dat_fixture() -> None:
    path = FIXTURES / "observables_imp0.dat"
    assert path.is_file()
    metrics = parse_dmft_dat(path)
    assert metrics["filling"] == pytest.approx(8.76)
    assert metrics["occupancy_summary"]["imp0"] == pytest.approx(8.76)
    assert metrics["occupancy_summary"]["imp0_orb0"] == pytest.approx(1.72)
    assert metrics["occupancy_summary"]["imp0_orb4"] == pytest.approx(1.74)
    assert metrics["converged"] is True
    # Z is not in the canonical .dat table
    assert metrics["mass_enhancement"] is None

    via_unified = parse_dmft_observables(path)
    assert via_unified["filling"] == pytest.approx(8.76)
    assert via_unified["converged"] is True


def test_parse_dat_text_via_parse_dmft_observables() -> None:
    text = (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8")
    metrics = parse_dmft_observables(text)
    assert metrics["filling"] == pytest.approx(8.76)


def test_parse_magnetic_dat_group() -> None:
    paths = [
        FIXTURES / "observables_imp0_up.dat",
        FIXTURES / "observables_imp0_down.dat",
    ]
    metrics = parse_dat_group(paths)
    assert metrics["filling"] == pytest.approx(8.76)
    assert metrics["occupancy_summary"]["imp0_up"] == pytest.approx(4.38)
    assert metrics["occupancy_summary"]["imp0_down"] == pytest.approx(4.38)
    assert metrics["converged"] is True


def test_parse_dat_with_z_and_mass_columns() -> None:
    text = (
        " it |         mu |      impurity occ |          Z | mass_enhancement\n"
        "  3 |    1.00000 |           8.50000 |      0.40000 |           2.50000\n"
    )
    metrics = parse_dmft_observables(text)
    assert metrics["filling"] == pytest.approx(8.5)
    assert metrics["mass_enhancement"] == pytest.approx(2.5)


def test_json_drop_in_still_preferred(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables_imp0.dat").write_text(
        (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (wd / "observables.json").write_text(
        json.dumps({"filling": 8.1, "mass_enhancement": 1.8, "converged": True}),
        encoding="utf-8",
    )
    metrics, kind, path = discover_dmft_metrics(wd, write_json=False)
    assert kind == "json"
    assert path is not None and path.name == "observables.json"
    assert metrics["filling"] == pytest.approx(8.1)
    assert metrics["mass_enhancement"] == pytest.approx(1.8)


def test_unusable_json_falls_through_to_dat(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text("{}\n", encoding="utf-8")
    (wd / "observables_imp0.dat").write_text(
        (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    metrics, kind, path = discover_dmft_metrics(wd, write_json=False)
    assert kind == "dat"
    assert path is not None and path.name.endswith(".dat")
    assert metrics["filling"] == pytest.approx(8.76)


def test_dat_under_out_jobname(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    (wd / "out").mkdir(parents=True)
    dest = wd / "out" / "observables_imp0.dat"
    dest.write_text(
        (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    found = find_dat_observables(wd)
    assert dest in found
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=False)
    assert kind == "dat"
    assert metrics["filling"] == pytest.approx(8.76)


def test_native_parse_materializes_observables_json(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables_imp0.dat").write_text(
        (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    metrics, kind, _ = discover_dmft_metrics(wd, write_json=True)
    assert kind == "dat"
    written = wd / "observables.json"
    assert written.is_file()
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["filling"] == pytest.approx(8.76)
    assert payload.get("siscforge_bridge") == "native_solid_dmft"
    round_trip = parse_dmft_observables(written)
    assert round_trip["filling"] == pytest.approx(8.76)
    assert metrics_usable(round_trip)


def test_materialize_does_not_overwrite_operator_json(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        '{"filling": 1.0, "converged": true}\n', encoding="utf-8"
    )
    metrics = parse_dmft_dat(FIXTURES / "observables_imp0.dat")
    dest = materialize_observables_json(wd, metrics, source="observables_imp0.dat")
    assert dest is not None
    payload = json.loads((wd / "observables.json").read_text(encoding="utf-8"))
    assert payload["filling"] == pytest.approx(1.0)


def test_metrics_to_json_payload_round_trip() -> None:
    metrics = {
        "occupancy_summary": {"imp0": 8.76, "imp0_orb0": 1.72},
        "filling": 8.76,
        "mass_enhancement": 2.5,
        "mass_enhancement_by_orbital": {"imp0_orb0": 2.5},
        "converged": True,
        "leading_pairing_eigenvalue": 0.61,
        "pairing_symmetry": "d_x2-y2",
    }
    payload = metrics_to_json_payload(metrics, source="unit")
    again = parse_dmft_observables(payload)
    assert again["filling"] == pytest.approx(8.76)
    assert again["mass_enhancement"] == pytest.approx(2.5)
    assert again["leading_pairing_eigenvalue"] == pytest.approx(0.61)
    assert again["pairing_symmetry"] == "d_x2-y2"
    assert again["converged"] is True


def test_h5_tree_imp_occ_and_orb_z() -> None:
    tree = {
        "DMFT_results": {
            "observables": {
                "imp_occ": [
                    {"up": [4.3, 4.36], "down": [4.3, 4.40]},
                ],
                "orb_Z": [
                    {
                        "up": [[0.40, 0.50], [0.40, 0.50]],
                        "down": [[0.40, 0.50], [0.40, 0.50]],
                    }
                ],
                "iteration": [0, 1],
            },
            "last_iter": {},
        }
    }
    metrics = metrics_from_h5_tree(tree)
    assert metrics_usable(metrics)
    assert metrics["filling"] == pytest.approx(8.76)
    assert metrics["mass_enhancement"] == pytest.approx(2.25)
    assert metrics["converged"] is True


def test_extract_dmft_h5_from_mapping() -> None:
    metrics = extract_dmft_h5(
        {
            "DMFT_results": {
                "observables": {"n_imp": [8.2, 8.72], "converged": True},
            }
        }
    )
    assert metrics["filling"] == pytest.approx(8.72)
    assert metrics["converged"] is True


def test_extract_dmft_h5_missing_file(tmp_path: Path) -> None:
    assert not metrics_usable(extract_dmft_h5(tmp_path / "nope.h5"))


def test_extract_dmft_h5_without_h5py_skips(tmp_path: Path) -> None:
    blob = tmp_path / "seed.h5"
    blob.write_bytes(b"not-a-real-archive")

    def _boom(_path: Path):
        raise RuntimeError("opener must not be required when testing skip")

    if not h5py_available():
        metrics = extract_dmft_h5(blob)
        assert metrics["filling"] is None
        assert not metrics_usable(metrics)
    metrics = extract_dmft_h5(blob, opener=_boom)
    assert not metrics_usable(metrics)


def test_seed_h5_without_dmft_results_is_ignored() -> None:
    """DFTTools / Wannier seed archives must not look like a DMFT result."""
    metrics = metrics_from_h5_tree(
        {
            "dft_input": {"n_k": 64, "density": [0.5, 0.6]},
            "wannier": {"occupancy": {"Ni": 8.8}},
        }
    )
    assert not metrics_usable(metrics)
    assert metrics["filling"] is None


def test_json_imp0_filename_still_parsed_as_json(tmp_path: Path) -> None:
    path = tmp_path / "observables_imp0.json"
    path.write_text(
        '{"occupancy": {"Ni_d": 8.9}, "Z": 0.5, "converged": true}',
        encoding="utf-8",
    )
    metrics = parse_dmft_observables(path)
    assert metrics["filling"] == pytest.approx(8.9)
    assert metrics["mass_enhancement"] == pytest.approx(2.0)


def test_discover_h5_via_injected_opener(tmp_path: Path) -> None:
    wd = tmp_path / "dmft"
    wd.mkdir()
    archive = wd / "siscforge.h5"
    archive.write_bytes(b"fake")

    tree = {
        "DMFT_results": {
            "observables": {
                "imp_occ": [8.72],
                "orb_Z": [0.4],
                "converged": True,
            }
        }
    }

    def opener(_path: Path):
        return tree

    metrics, kind, path = discover_dmft_metrics(
        wd, write_json=True, seedname="siscforge", h5_opener=opener
    )
    assert kind == "h5"
    assert path == archive
    assert metrics["filling"] == pytest.approx(8.72)
    assert metrics["mass_enhancement"] == pytest.approx(2.5)
    assert (wd / "observables.json").is_file()
    again = parse_dmft_observables(wd / "observables.json")
    assert again["filling"] == pytest.approx(8.72)


@pytest.mark.skipif(not h5py_available(), reason="h5py not installed")
def test_real_h5py_flat_datasets(tmp_path: Path) -> None:
    import h5py
    import numpy as np

    path = tmp_path / "out.h5"
    with h5py.File(path, "w") as f:
        obs = f.create_group("DMFT_results").create_group("observables")
        obs.create_dataset("imp_occ", data=np.array([8.1, 8.72]))
        obs.create_dataset("orb_Z", data=np.array([0.5]))
        obs.create_dataset("converged", data=True)
    metrics = extract_dmft_h5(path)
    assert metrics["filling"] == pytest.approx(8.72)
    assert metrics["mass_enhancement"] == pytest.approx(2.0)
    assert metrics["converged"] is True


def test_auto_launch_native_dat_populates_dmft_result(tmp_path: Path) -> None:
    """Successful launch + native .dat (no JSON) is a non-failed DMFTResult."""
    fixture = (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8")

    def _dat_launcher(_cmd, work_dir: Path, _timeout):
        (Path(work_dir) / "observables_imp0.dat").write_text(fixture, encoding="utf-8")
        return LaunchOutcome(returncode=0, command=list(_cmd), source="fake_dat")

    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        formula="NdNiO2",
        launcher=_dat_launcher,
    )
    assert result.status == "ok"
    assert result.converged is True
    assert result.filling == pytest.approx(8.76)
    assert result.occupancy_summary
    assert result.failure_class is None
    assert result.raw["launch"]["status"] == "invoked"
    assert result.raw["launch"]["observables_kind"] == "dat"
    written = tmp_path / "dmft" / "observables.json"
    assert written.is_file()
    assert "siscforge_bridge" in written.read_text(encoding="utf-8")


def test_pre_existing_dat_skips_launcher(tmp_path: Path) -> None:
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("launcher must not run when native .dat is usable")

    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables_imp0.dat").write_text(
        (FIXTURES / "observables_imp0.dat").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = run_solid_dmft(
        cfg=_cfg(),
        wannier=_ready_wannier(tmp_path),
        work_dir=wd,
        quality_tag="screening",
        launcher=boom,
    )
    assert called["n"] == 0
    assert result.status == "ok"
    assert result.filling == pytest.approx(8.76)
    assert result.raw["launch"]["status"] == "native_dat"


def test_empty_dat_does_not_crash(tmp_path: Path) -> None:
    empty = tmp_path / "observables_imp0.dat"
    empty.write_text("# nothing\n", encoding="utf-8")
    metrics = parse_dmft_dat(empty)
    assert not metrics_usable(metrics)
    assert metrics["filling"] is None
