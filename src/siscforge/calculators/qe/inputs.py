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
    """Map elements to UPF filenames.

    Uses ``config.pseudopotentials`` when provided; otherwise scans
    ``config.pseudo_dir`` for ``{Element}*.upf`` (case-insensitive).
    """
    elements = sorted({site.specie.symbol for site in structure})
    if config.pseudopotentials:
        missing = [el for el in elements if el not in config.pseudopotentials]
        if missing:
            raise FileNotFoundError(
                f"Pseudopotential map missing elements: {missing}. "
                f"Provided keys: {sorted(config.pseudopotentials)}"
            )
        return {el: config.pseudopotentials[el] for el in elements}

    if not config.pseudo_dir:
        raise FileNotFoundError(
            "dft.pseudo_dir is not set and dft.pseudopotentials is empty. "
            "Point pseudo_dir at a directory of UPF files (e.g. SSSP or PseudoDojo)."
        )
    pseudo_dir = Path(config.pseudo_dir)
    if not pseudo_dir.is_dir():
        raise FileNotFoundError(f"pseudo_dir does not exist: {pseudo_dir}")

    upfs = list(pseudo_dir.glob("*.upf")) + list(pseudo_dir.glob("*.UPF"))
    resolved: dict[str, str] = {}
    for el in elements:
        matches = [
            p.name
            for p in upfs
            if p.name.lower().startswith(el.lower())
            or f"_{el.lower()}_" in p.name.lower()
            or p.name.lower().startswith(f"{el.lower()}.")
        ]
        # Prefer exact element prefix, e.g. Nb.pbe-... or Nb_ONCV...
        if not matches:
            raise FileNotFoundError(
                f"No UPF for element {el!r} in {pseudo_dir}. "
                f"Found files: {[p.name for p in upfs[:20]]}"
            )
        # Prefer shorter / pbe if multiple
        matches.sort(key=lambda n: (0 if "pbe" in n.lower() else 1, len(n)))
        resolved[el] = matches[0]
    return resolved


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
) -> str:
    """Return a minimal ``ph.x`` input deck as a string.

    Gamma-only: set ``ldisp=False`` and use a single q = (0,0,0) block.
    """
    lines = [
        "&inputph",
        f"  prefix = '{prefix}',",
        f"  outdir = '{outdir}',",
        f"  tr2_ph = {tr2_ph},",
        f"  ldisp = .{str(ldisp).lower()}.,",
        f"  fildyn = '{fildyn}',",
        f"  epsil = .{str(epsil).lower()}.,",
    ]
    if ldisp:
        lines.extend(
            [
                f"  nq1 = {nq1},",
                f"  nq2 = {nq2},",
                f"  nq3 = {nq3},",
            ]
        )
    lines.append("/")
    if not ldisp:
        lines.extend(
            [
                "0.0 0.0 0.0",
            ]
        )
    return "\n".join(lines) + "\n"


def write_ph_input(content: str, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
