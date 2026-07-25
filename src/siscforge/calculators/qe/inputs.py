"""Build Quantum ESPRESSO input files from StructureCandidate / pymatgen Structure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pymatgen.core import Structure
from pymatgen.io.pwscf import PWInput

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import DFTConfig


def candidate_to_structure(candidate: StructureCandidate) -> Structure:
    """Rebuild a pymatgen Structure from CIF or lattice metadata."""
    if candidate.structure_cif:
        return Structure.from_str(candidate.structure_cif, fmt="cif")
    if candidate.lattice_abc is None:
        raise ValueError(
            f"Candidate {candidate.candidate_id} has no structure_cif or lattice_abc; "
            "cannot build a QE input."
        )
    # Fallback: empty structure is not useful — require CIF for QE.
    raise ValueError(
        f"Candidate {candidate.candidate_id} is missing structure_cif. "
        "Re-enumerate with the structure generator so CIF is attached."
    )


def resolve_pseudopotentials(
    structure: Structure,
    config: DFTConfig,
) -> dict[str, str]:
    """Map elements to UPF filenames (delegates to :mod:`pseudos` helpers)."""
    from siscforge.calculators.qe.pseudos import (
        resolve_pseudopotentials as _resolve,
    )

    return _resolve(structure, config)


def build_pw_input(
    structure: Structure,
    config: DFTConfig,
    *,
    calculation: str = "scf",
    prefix: str = "siscforge",
    outdir: str = "./out",
    extra_control: dict[str, Any] | None = None,
    extra_system: dict[str, Any] | None = None,
) -> PWInput:
    """Construct a pymatgen ``PWInput`` for relax / scf / nscf."""
    pseudo = resolve_pseudopotentials(structure, config)
    control: dict[str, Any] = {
        "calculation": calculation,
        "prefix": prefix,
        "outdir": outdir,
        "pseudo_dir": str(config.pseudo_dir) if config.pseudo_dir else "./pseudo",
        "tprnfor": True,
        "tstress": True,
    }
    if extra_control:
        control.update(extra_control)

    system: dict[str, Any] = {
        "ecutwfc": config.ecutwfc,
        "ecutrho": config.ecutrho,
        "occupations": config.occupations,
        "smearing": config.smearing,
        "degauss": config.degauss,
    }
    # Empty bands: DFPT Sternheimer / EPW Wannier need states above E_F.
    # QE default for 18 e⁻ metals is often only ~13 bands → "too few bands".
    if config.nbnd is not None:
        system["nbnd"] = int(config.nbnd)
    elif config.occupations == "smearing" and (
        config.do_phonon or config.do_epw or config.epw.enabled
    ):
        system["nbnd"] = 24
    if extra_system:
        system.update(extra_system)

    electrons: dict[str, Any] = {"conv_thr": config.conv_thr}
    ions: dict[str, Any] | None = None
    cell: dict[str, Any] | None = None
    if calculation in {"relax", "vc-relax"}:
        ions = {"ion_dynamics": "bfgs"}
    if calculation == "vc-relax":
        cell = {
            "cell_dynamics": "bfgs",
            "press_conv_thr": config.press_conv_thr,
            "cell_dofree": "all",
        }

    kgrid = tuple(int(x) for x in config.kpoints)
    if len(kgrid) != 3:
        raise ValueError(f"kpoints must be length 3, got {config.kpoints}")

    return PWInput(
        structure,
        pseudo=pseudo,
        control=control,
        system=system,
        electrons=electrons,
        ions=ions,
        cell=cell,
        kpoints_mode="automatic",
        kpoints_grid=kgrid,
        kpoints_shift=(0, 0, 0),
    )


def write_pw_input(pw_input: PWInput, path: Path | str) -> Path:
    """Write a PWInput to disk; return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pw_input), encoding="utf-8")
    return path


def uniform_crystal_kpoints(nk1: int, nk2: int, nk3: int) -> list[tuple[float, float, float, float]]:
    """Uniform Γ-centered mesh in crystal coordinates (EPW NSCF convention).

    Returns list of ``(kx, ky, kz, weight)`` with equal weights summing to 1.
    """
    n1, n2, n3 = int(nk1), int(nk2), int(nk3)
    if min(n1, n2, n3) < 1:
        raise ValueError(f"nk mesh must be positive, got {(n1, n2, n3)}")
    ntot = n1 * n2 * n3
    w = 1.0 / float(ntot)
    pts: list[tuple[float, float, float, float]] = []
    for i in range(n1):
        for j in range(n2):
            for k in range(n3):
                pts.append((i / n1, j / n2, k / n3, w))
    return pts


def build_nscf_epw_input(
    structure: Structure,
    config: DFTConfig,
    *,
    prefix: str = "siscforge",
    outdir: str = "./",
    nk: tuple[int, int, int] | list[int] | None = None,
    nbnd: int | None = None,
) -> str:
    """Build an EPW-oriented NSCF input with full crystal k-mesh.

    EPW Wannierization requires ``K_POINTS crystal`` with ``nk1*nk2*nk3`` points
    matching ``nk1/nk2/nk3`` in ``epw.in`` (not an automatic reduced mesh).
    """
    nkc = list(nk) if nk is not None else list(config.epw.nkc or config.kpoints)
    nkc = (list(nkc) + [4, 4, 4])[:3]
    nk1, nk2, nk3 = int(nkc[0]), int(nkc[1]), int(nkc[2])

    nbndsub = config.epw.nbndsub if config.epw.nbndsub is not None else 8
    # Enough empty bands for random Wannier projections in metals (screening).
    if nbnd is not None:
        n_bands = int(nbnd)
    elif config.nbnd is not None:
        n_bands = int(config.nbnd)
    else:
        n_bands = max(24, nbndsub + 8)

    pw = build_pw_input(
        structure,
        config,
        calculation="nscf",
        prefix=prefix,
        outdir=outdir,
        extra_system={"nbnd": n_bands},
    )
    text = str(pw)
    # Replace automatic MP block with full crystal mesh required by EPW.
    pts = uniform_crystal_kpoints(nk1, nk2, nk3)
    k_lines = [f"K_POINTS crystal\n{len(pts)}"]
    for kx, ky, kz, w in pts:
        k_lines.append(f"  {kx:.8f}  {ky:.8f}  {kz:.8f}  {w:.8e}")
    k_block = "\n".join(k_lines)

    import re

    new_text, nsub = re.subn(
        r"K_POINTS\s+automatic\s*\n[^\n]+",
        k_block,
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if nsub != 1:
        # Fallback: append if automatic block not found
        new_text = text.rstrip() + "\n" + k_block + "\n"
    return new_text if new_text.endswith("\n") else new_text + "\n"


def build_ph_input(
    *,
    prefix: str = "siscforge",
    outdir: str = "./out",
    tr2_ph: float = 1.0e-12,
    ldisp: bool = True,
    nq1: int = 2,
    nq2: int = 2,
    nq3: int = 2,
    epsil: bool = False,
    fildyn: str = "siscforge.dyn",
    fildvscf: str | None = None,
    alpha_mix: float = 0.3,
    nmix_ph: int = 8,
    niter_ph: int = 100,
) -> str:
    """Return a minimal ``ph.x`` input deck as a string.

    Gamma-only: set ``ldisp=False`` and use a single q = (0,0,0) block.
    For EPW, set ``ldisp=True`` and ``fildvscf='dvscf'``.

    Soft metals often need reduced ``alpha_mix`` and extra empty bands on the
    prior SCF (see ``DFTConfig.nbnd``); otherwise Broyden can diverge
    (``factorization`` / ``|ddv_scf| → ∞``).
    """
    lines = [
        "&inputph",
        f"  prefix = '{prefix}'",
        f"  outdir = '{outdir}'",
        f"  tr2_ph = {tr2_ph}",
        f"  ldisp = .{str(ldisp).lower()}.",
        f"  fildyn = '{fildyn}'",
        f"  epsil = .{str(epsil).lower()}.",
        "  trans = .true.",
        f"  alpha_mix(1) = {alpha_mix}",
        f"  nmix_ph = {int(nmix_ph)}",
        f"  niter_ph = {int(niter_ph)}",
    ]
    if fildvscf:
        lines.append(f"  fildvscf = '{fildvscf}'")
    if ldisp:
        lines.extend(
            [
                f"  nq1 = {nq1}",
                f"  nq2 = {nq2}",
                f"  nq3 = {nq3}",
            ]
        )
    lines.append("/")
    if not ldisp:
        lines.extend(["0.0 0.0 0.0"])
    return "\n".join(lines) + "\n"


def write_ph_input(content: str, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
