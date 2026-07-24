"""Optional phonopy finite-displacement phonon path.

Default phonon workflow remains DFPT (``ph.x``). This module is used only when
``dft.phonon_method: phonopy_fd``. Requires the optional ``phonopy`` package and
still uses ``pw.x`` for displaced supercell force calculations.

If phonopy is not installed, :func:`run_phonopy_fd` raises a clear error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from siscforge.calculators.qe.env import QEEnvironment, require_qe
from siscforge.calculators.qe.parser import parse_frequency_list
from siscforge.calculators.qe.recipes import run_pw
from siscforge.models.config import DFTConfig
from siscforge.models.results import PhononResult


class PhonopyNotAvailableError(RuntimeError):
    """Raised when phonopy_fd is requested but phonopy is not installed."""


def phonopy_available() -> bool:
    try:
        import phonopy  # noqa: F401

        return True
    except ImportError:
        return False


def run_phonopy_fd(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
) -> tuple[PhononResult | None, dict[str, Any]]:
    """Run a minimal phonopy FD workflow: displacements → pw forces → mesh.

    This is a **screening-quality** skeleton: small supercell, coarse k-point
    inheritance, Gamma-centered mesh. Production FD campaigns should tighten
    cutoffs and supercell size.

    Returns ``(PhononResult | None, diagnostics)``.
    """
    if not phonopy_available():
        raise PhonopyNotAvailableError(
            "dft.phonon_method is 'phonopy_fd' but the phonopy package is not installed.\n"
            "Install with: pip install phonopy\n"
            "Or use phonon_method: dfpt | gamma (default ph.x DFPT path)."
        )

    from phonopy import Phonopy
    from phonopy.structure.atoms import PhonopyAtoms

    qe_env = qe_env or require_qe(need_phonon=False)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    diag: dict[str, Any] = {"method": "phonopy_fd", "steps": []}

    # Build phonopy atoms from pymatgen
    lattice = structure.lattice.matrix
    positions = structure.frac_coords
    symbols = [str(sp) for sp in structure.species]
    unitcell = PhonopyAtoms(
        symbols=symbols,
        cell=lattice,
        scaled_positions=positions,
    )
    sc = list(config.phonopy_supercell)
    if len(sc) != 3:
        raise ValueError(f"phonopy_supercell must be length 3, got {sc}")
    phonon = Phonopy(unitcell, supercell_matrix=[[sc[0], 0, 0], [0, sc[1], 0], [0, 0, sc[2]]])
    phonon.generate_displacements(distance=config.phonopy_distance)
    supercells = phonon.supercells_with_displacements
    diag["n_displacements"] = len(supercells)

    force_sets: list[Any] = []
    for i, scell in enumerate(supercells):
        # Convert phonopy supercell → pymatgen Structure
        pm_struct = Structure(
            scell.cell,
            scell.symbols,
            scell.scaled_positions,
        )
        step_dir = work_dir / f"fd_{i:03d}"
        # Force calculation: scf with forces (tprnfor already on)
        step = run_pw(
            pm_struct,
            config,
            step_dir,
            calculation="scf",
            prefix=f"{prefix}_fd{i}",
            qe_env=qe_env,
        )
        diag["steps"].append(
            {"index": i, "success": step.success, "out": str(step.stdout_path)}
        )
        if not step.success:
            return None, {
                **diag,
                "error": f"FD displacement {i} failed: {step.message}",
            }
        forces = _parse_forces_from_pw(step.stdout_path)
        if forces is None:
            return None, {
                **diag,
                "error": f"Could not parse forces from {step.stdout_path}",
            }
        force_sets.append(forces)

    phonon.forces = force_sets
    phonon.produce_force_constants()
    # Mesh for frequencies (coarse)
    phonon.run_mesh([max(1, sc[0]), max(1, sc[1]), max(1, sc[2])])
    mesh_dict = phonon.get_mesh_dict()
    # frequencies in THz
    freqs_thz = mesh_dict["frequencies"].flatten()
    freqs_cm1 = [float(f) * 33.35641 for f in freqs_thz]
    ph = parse_frequency_list(
        freqs_cm1,
        quality_tag=config.quality_tag,
    )
    # Mark source
    ph = ph.model_copy(
        update={
            "raw": {**ph.raw, "method": "phonopy_fd", "supercell": sc},
            "status": "ok",
        }
    )
    diag["success"] = True
    return ph, diag


def _parse_forces_from_pw(path: Path) -> list[list[float]] | None:
    """Parse atomic forces (eV/Å or Ry/au) from pw.x stdout — best effort.

    For a true production FD path, prefer the XML force array. Here we look for
    the ``Forces acting on atoms`` block and convert Ry/bohr → eV/Å if needed.
    """
    import re

    text = path.read_text(encoding="utf-8", errors="replace")
    # Forces acting on atoms (cartesian axes, Ry/au):
    m = re.search(
        r"Forces acting on atoms[^\n]*\n\n((?:\s*atom\s+\d+[^\n]+\n)+)",
        text,
        re.IGNORECASE,
    )
    if not m:
        # try without blank line
        m = re.search(
            r"Forces acting on atoms[^\n]*\n((?:\s*atom\s+\d+[^\n]+\n)+)",
            text,
            re.IGNORECASE,
        )
    if not m:
        return None
    forces: list[list[float]] = []
    for line in m.group(1).splitlines():
        # atom    1 type  1   force =    0.001   0.002   0.003
        parts = line.split("=")
        if len(parts) < 2:
            continue
        try:
            fx, fy, fz = [float(x) for x in parts[1].split()[:3]]
        except (ValueError, IndexError):
            continue
        # Ry/bohr → eV/Å : * (13.6057 / 0.529177)
        scale = 13.605693122994 / 0.529177210903
        forces.append([fx * scale, fy * scale, fz * scale])
    return forces or None
