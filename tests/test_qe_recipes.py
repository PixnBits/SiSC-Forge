"""Tests for QE recipe scaffolding (no pw.x required)."""

from __future__ import annotations

from siscforge.calculators import get, list_calculators
from siscforge.calculators.qe.env import detect_qe_environment, jobflow_available, qe_available
from siscforge.calculators.qe.inputs import build_ph_input, build_pw_input, candidate_to_structure
from siscforge.calculators.qe.recipes import build_relax_scf_phonon_flow, recipe_info
from siscforge.models.config import DFTConfig
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride


def test_qe_registered() -> None:
    names = list_calculators()
    assert "mock" in names
    assert "qe" in names
    assert "quantum-espresso" in names
    calc = get("qe")
    assert calc.name == "qe"


def test_build_pw_input_with_explicit_pseudos() -> None:
    s = build_binary_nitride("Nb")
    cfg = DFTConfig(
        pseudo_dir="/tmp/fake_pseudos",
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
        ecutwfc=40.0,
        kpoints=[2, 2, 2],
    )
    pw = build_pw_input(s, cfg, calculation="scf")
    text = str(pw)
    assert "calculation" in text.lower() or "scf" in text.lower()
    assert "Nb" in text
    assert "N" in text


def test_build_ph_input_gamma() -> None:
    text = build_ph_input(ldisp=False, prefix="test")
    assert "inputph" in text
    assert "0.0 0.0 0.0" in text


def test_build_ph_input_ldisp() -> None:
    text = build_ph_input(ldisp=True, nq1=2, nq2=2, nq3=2)
    assert "nq1" in text
    assert "ldisp = .true." in text


def test_candidate_to_structure() -> None:
    import pytest

    s = build_binary_nitride("Nb")
    cand = structure_to_candidate(s, material_family="tm_nitride")
    s2 = candidate_to_structure(cand)
    assert len(s2) == len(s)
    assert s2.lattice.a == pytest.approx(s.lattice.a, rel=1e-5)


def test_recipe_info() -> None:
    info = recipe_info()
    steps_blob = " ".join(info["steps"]).lower()
    assert "scf" in steps_blob or any("scf" in s.lower() for s in info["steps"])
    assert "engine" in info


def test_flow_build_optional() -> None:
    s = build_binary_nitride("Nb")
    cfg = DFTConfig(pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"}, pseudo_dir="/tmp")
    flow = build_relax_scf_phonon_flow(s, cfg, "/tmp/qe_test_flow")
    if jobflow_available():
        assert flow is not None
        assert len(flow.jobs) >= 2
    else:
        assert flow is None


def test_env_detect_smoke() -> None:
    env = detect_qe_environment()
    # Just ensure it returns a structured result; availability is environment-dependent.
    assert hasattr(env, "pw")
    assert qe_available() is bool(env.pw)
