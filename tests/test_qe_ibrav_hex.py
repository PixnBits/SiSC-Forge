"""Hexagonal ibrav=4 + celldm for MgB₂; generic cells stay ibrav=0."""

from __future__ import annotations

import pytest

from siscforge.calculators.qe.inputs import (
    QE_BOHR_RADIUS_ANGS,
    build_pw_input,
    hexagonal_celldm,
    is_hexagonal_ibrav4,
    pw_input_to_text,
    resolve_ibrav_system,
)
from siscforge.models.config import DFTConfig
from siscforge.structure.mgb2 import MGB2_A_ANG, MGB2_C_ANG, build_mgb2
from siscforge.structure.nitrides import build_binary_nitride


def _pseudo_cfg(**kwargs) -> DFTConfig:
    return DFTConfig(
        pseudo_dir="/tmp/fake_pseudos",
        pseudopotentials={"Mg": "Mg.upf", "B": "B.upf", "Nb": "Nb.upf", "N": "N.upf"},
        ecutwfc=40.0,
        kpoints=[2, 2, 2],
        **kwargs,
    )


def test_mgb2_is_hexagonal_ibrav4() -> None:
    s = build_mgb2()
    assert is_hexagonal_ibrav4(s)
    celldm1, celldm3 = hexagonal_celldm(s)
    assert celldm1 == pytest.approx(MGB2_A_ANG / QE_BOHR_RADIUS_ANGS, rel=1e-12)
    assert celldm3 == pytest.approx(MGB2_C_ANG / MGB2_A_ANG, rel=1e-12)


def test_mgb2_pw_emits_ibrav4_celldm_no_cell_parameters() -> None:
    s = build_mgb2()
    cfg = _pseudo_cfg()
    text = pw_input_to_text(build_pw_input(s, cfg, calculation="scf"))
    low = text.lower()
    assert "ibrav = 4" in low or "ibrav=4" in low.replace(" ", "")
    assert "celldm(1)" in low
    assert "celldm(3)" in low
    assert "cell_parameters" not in low
    extras = resolve_ibrav_system(s, cfg)
    assert extras["ibrav"] == 4
    assert extras["celldm(1)"] == pytest.approx(MGB2_A_ANG / QE_BOHR_RADIUS_ANGS)
    assert extras["celldm(3)"] == pytest.approx(MGB2_C_ANG / MGB2_A_ANG)
    assert str(extras["celldm(1)"]).split(".")[0] in text


def test_generic_nitride_stays_ibrav0_cell_parameters() -> None:
    s = build_binary_nitride("Nb")
    cfg = _pseudo_cfg()
    assert resolve_ibrav_system(s, cfg) == {}
    text = pw_input_to_text(build_pw_input(s, cfg, calculation="scf"))
    low = text.lower()
    assert "ibrav = 0" in low
    assert "cell_parameters" in low
    assert "celldm(1)" not in low


def test_ibrav0_escape_hatch_on_mgb2() -> None:
    s = build_mgb2()
    cfg = _pseudo_cfg(ibrav=0)
    assert resolve_ibrav_system(s, cfg) == {}
    text = pw_input_to_text(build_pw_input(s, cfg, calculation="scf"))
    low = text.lower()
    assert "ibrav = 0" in low
    assert "cell_parameters" in low
    assert "celldm(1)" not in low


def test_nscf_epw_mgb2_also_ibrav4() -> None:
    from siscforge.calculators.qe.inputs import build_nscf_epw_input

    s = build_mgb2()
    cfg = _pseudo_cfg(do_epw=True)
    text = build_nscf_epw_input(s, cfg, prefix="t", outdir="./", nk=(2, 2, 2))
    low = text.lower()
    assert "ibrav = 4" in low
    assert "celldm(1)" in low
    assert "cell_parameters" not in low
