"""Tests for QE hardening: relaxed geometry parse, pseudos, phonopy optional."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.calculators.qe.parser import parse_relaxed_structure
from siscforge.calculators.qe.phonopy_fd import phonopy_available
from siscforge.calculators.qe.pseudos import (
    PseudoResolutionError,
    list_upf_files,
    match_upf_for_element,
    resolve_pseudopotentials,
)
from siscforge.models.config import DFTConfig
from siscforge.structure.nitrides import build_binary_nitride

FIXTURES = Path(__file__).parent / "fixtures" / "qe"


def test_parse_relaxed_structure_final_coords() -> None:
    path = FIXTURES / "pw_vcrelax_snippet.out"
    s = parse_relaxed_structure(path)
    assert s is not None
    assert len(s) == 2
    # Last CELL_PARAMETERS block (final coordinates)
    assert s.lattice.a == pytest.approx(4.41, rel=1e-6)
    assert s.lattice.c == pytest.approx(4.36, rel=1e-6)
    symbols = sorted(site.specie.symbol for site in s)
    assert symbols == ["N", "Nb"]


def test_pseudo_missing_dir() -> None:
    s = build_binary_nitride("Nb")
    cfg = DFTConfig(pseudo_dir="/nonexistent/path/sssp")
    with pytest.raises(PseudoResolutionError, match="pseudo_dir"):
        resolve_pseudopotentials(s, cfg)


def test_pseudo_explicit_map(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    # create dummy upfs
    (tmp_path / "Nb.pbe-test.UPF").write_text("dummy")
    (tmp_path / "N.pbe-test.UPF").write_text("dummy")
    cfg = DFTConfig(
        pseudo_dir=str(tmp_path),
        pseudopotentials={"Nb": "Nb.pbe-test.UPF", "N": "N.pbe-test.UPF"},
    )
    resolved = resolve_pseudopotentials(s, cfg)
    assert resolved["Nb"] == "Nb.pbe-test.UPF"
    assert resolved["N"] == "N.pbe-test.UPF"


def test_pseudo_auto_discover(tmp_path: Path) -> None:
    s = build_binary_nitride("Ti")
    (tmp_path / "Ti.pbe-spn-kjpaw_psl.1.0.0.UPF").write_text("x")
    (tmp_path / "N.pbe-n-kjpaw_psl.1.0.0.UPF").write_text("x")
    cfg = DFTConfig(pseudo_dir=str(tmp_path))
    resolved = resolve_pseudopotentials(s, cfg)
    assert "Ti" in resolved["Ti"]
    assert resolved["N"].startswith("N")


def test_pseudo_missing_element(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    (tmp_path / "N.pbe.UPF").write_text("x")
    # no Nb
    cfg = DFTConfig(pseudo_dir=str(tmp_path))
    with pytest.raises(PseudoResolutionError, match="Nb"):
        resolve_pseudopotentials(s, cfg)


def test_list_upf_and_match(tmp_path: Path) -> None:
    (tmp_path / "Nb.pbe.UPF").write_text("x")
    (tmp_path / "junk.txt").write_text("x")
    files = list_upf_files(tmp_path)
    assert len(files) == 1
    assert match_upf_for_element("Nb", files) == "Nb.pbe.UPF"
    assert match_upf_for_element("Ti", files) is None


def test_phonopy_available_is_bool() -> None:
    assert isinstance(phonopy_available(), bool)

def test_match_upf_boron_not_barium(tmp_path: Path) -> None:
    """Element B must not prefix-match Ba.*.UPF (MgB2 auto-resolve bug)."""
    (tmp_path / "Ba.pbe-spn-kjpaw_psl.1.0.0.UPF").write_text("x")
    (tmp_path / "b_pbe_v1.4.uspp.F.UPF").write_text("x")
    (tmp_path / "Mg.pbe-n-kjpaw_psl.0.3.0.UPF").write_text("x")
    files = list_upf_files(tmp_path)
    assert match_upf_for_element("B", files) == "b_pbe_v1.4.uspp.F.UPF"
    assert match_upf_for_element("Ba", files) == "Ba.pbe-spn-kjpaw_psl.1.0.0.UPF"
    assert match_upf_for_element("Mg", files) == "Mg.pbe-n-kjpaw_psl.0.3.0.UPF"


def test_match_upf_boron_rejects_ba_only(tmp_path: Path) -> None:
    (tmp_path / "Ba.pbe.UPF").write_text("x")
    files = list_upf_files(tmp_path)
    assert match_upf_for_element("B", files) is None
    assert match_upf_for_element("Ba", files) == "Ba.pbe.UPF"
