"""Build Quantum ESPRESSO input files from StructureCandidate / pymatgen Structure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pymatgen.core import Structure
from pymatgen.io.pwscf import PWInput

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import DFTConfig

from siscforge.models.config import DFTConfig

# QE 7.3.1 Modules/constants.f90 — BOHR_RADIUS_ANGS (CODATA).
# celldm(1) must use the same Bohr that pw.x uses internally.
QE_BOHR_RADIUS_ANGS: float = 0.529177210903


def is_hexagonal_ibrav4(structure: Structure, *, tol: float = 1.0e-3) -> bool:
    """True when *structure* matches QE ``ibrav=4`` (hexagonal P) metrics.

    Requires a ≈ b, α ≈ β ≈ 90°, γ ≈ 120°. MgB₂ / AlB₂-type cells take this
    path so pw.x gets celldm rather than ibrav=0 + CELL_PARAMETERS (matches
    upstream EPW ``examples/mgb2``).
    """
    a, b, _c = (float(x) for x in structure.lattice.abc)
    alpha, beta, gamma = (float(x) for x in structure.lattice.angles)
    if a <= 0.0 or b <= 0.0:
        return False
    if abs(a - b) / max(a, b) > tol:
        return False
    # Angle tolerance in degrees (tol=1e-3 → 0.1°)
    ang_tol = max(0.1, tol * 100.0)
    if abs(alpha - 90.0) > ang_tol or abs(beta - 90.0) > ang_tol:
        return False
    if abs(gamma - 120.0) > ang_tol:
        return False
    return True


def hexagonal_celldm(structure: Structure) -> tuple[float, float]:
    """Return ``(celldm(1), celldm(3))`` for QE ``ibrav=4``.

    ``celldm(1)`` = a / a₀ (alat in Bohr); ``celldm(3)`` = c/a.
    """
    if not is_hexagonal_ibrav4(structure):
        raise ValueError(
            "hexagonal_celldm requires an ibrav=4-compatible hexagonal cell "
            f"(a≈b, α≈β≈90°, γ≈120°); got abc={structure.lattice.abc}, "
            f"angles={structure.lattice.angles}"
        )
    a, _b, c = (float(x) for x in structure.lattice.abc)
    return a / QE_BOHR_RADIUS_ANGS, c / a


def resolve_ibrav_system(
    structure: Structure,
    config: DFTConfig,
) -> dict[str, Any]:
    """SYSTEM keys for ibrav / celldm, or ``{}`` for the generic path.

    Default (``dft.ibrav`` is None): hexagonal → ``ibrav=4`` + celldm;
    others stay on pymatgen's ibrav=0 + CELL_PARAMETERS. Set ``dft.ibrav=0``
    to force CELL_PARAMETERS even for hexagonal MgB₂ (escape hatch).
    """
    ibrav_cfg = getattr(config, "ibrav", None)
    if ibrav_cfg is not None and int(ibrav_cfg) == 0:
        return {}
    if ibrav_cfg is not None and int(ibrav_cfg) != 4:
        raise ValueError(
            f"dft.ibrav={ibrav_cfg!r} is not supported; use None (auto), 0 "
            "(force CELL_PARAMETERS), or 4 (hexagonal celldm)."
        )
    want_hex = ibrav_cfg is None or int(ibrav_cfg) == 4
    if not want_hex or not is_hexagonal_ibrav4(structure):
        if ibrav_cfg is not None and int(ibrav_cfg) == 4:
            raise ValueError(
                "dft.ibrav=4 requires a hexagonal ibrav=4-compatible cell; "
                f"got abc={structure.lattice.abc}, angles={structure.lattice.angles}"
            )
        return {}
    celldm1, celldm3 = hexagonal_celldm(structure)
    return {
        "ibrav": 4,
        "celldm(1)": celldm1,
        "celldm(3)": celldm3,
    }


def strip_cell_parameters(text: str) -> str:
    """Remove a ``CELL_PARAMETERS`` card (required when ibrav != 0)."""
    import re

    pattern = (
        r"\n?CELL_PARAMETERS[^\n]*\n"
        r"(?:[ \t]*[^\n]+\n){2}"
        r"[ \t]*[^\n]+\n?"
    )
    cleaned, _n = re.subn(pattern, "\n", text, count=1, flags=re.IGNORECASE)
    return cleaned.rstrip() + "\n" if cleaned.strip() else cleaned


def pw_input_to_text(pw_input: PWInput) -> str:
    """Serialize ``PWInput``; drop CELL_PARAMETERS when ``ibrav`` != 0.

    pymatgen always appends ``CELL_PARAMETERS angstrom``; QE hexagonal
    (ibrav=4) inputs must not include that card (upstream mgb2 layout).
    """
    text = str(pw_input)
    system = pw_input.sections.get("system", {}) or {}
    try:
        ibrav_i = int(system.get("ibrav", 0))
    except (TypeError, ValueError):
        ibrav_i = 0
    if ibrav_i != 0:
        text = strip_cell_parameters(text)
    return text if text.endswith("\n") else text + "\n"


def effective_epw_nscf_nosym(config: DFTConfig) -> bool:
    """Whether EPW NSCF should emit ``nosym`` / ``noinv``.

    ``epw.nscf_nosym`` False is an explicit opt-out. None / True keep the
    #89 default (nosym on) so divide_class/epw_setup stays guarded.
    """
    flag = getattr(config.epw, "nscf_nosym", None)
    if flag is False:
        return False
    return True


def effective_ph_search_sym(config: DFTConfig) -> bool:
    """ph.x ``search_sym`` after applying pipeline ``dft.nosym``.

    ``dft.nosym`` forces ``search_sym=.false.`` (ph.x has no nosym namelist;
    crystal symmetries follow the nosym SCF save).
    """
    if config.nosym:
        return False
    return config.ph_search_sym


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
    # Hexagonal MgB₂ / AlB₂-type: ibrav=4 + celldm (QE EPW/examples/mgb2).
    # Generic cells keep ibrav=0 + CELL_PARAMETERS via pw_input_to_text.
    # extra_system may override; dft.ibrav=0 forces the generic path.
    system.update(resolve_ibrav_system(structure, config))
    # Empty bands: DFPT Sternheimer / EPW Wannier need states above E_F.
    # QE default for 18 e⁻ metals is often only ~13 bands → "too few bands".
    # Ternary supercells (e.g. 8-atom NbTiN) need more than binary NbN defaults.
    if config.nbnd is not None:
        system["nbnd"] = int(config.nbnd)
    elif config.occupations == "smearing" and (
        config.do_phonon or config.do_epw or config.epw.enabled
    ):
        n_at = len(structure)
        # ~8 bands/atom for metals with empties; floor 24 for 2-atom cells
        system["nbnd"] = max(24, min(120, 8 * n_at))
    # Pipeline-wide nosym (SCF/relax/nscf via build_pw_input). extra_system wins.
    if bool(getattr(config, "nosym", False)):
        system.setdefault("nosym", True)
        system.setdefault("noinv", True)
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
    path.write_text(pw_input_to_text(pw_input), encoding="utf-8")
    return path


def write_pw_text(text: str, path: Path | str) -> Path:
    """Write raw pw.x input text (e.g. after HUBBARD card injection)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text if text.endswith("\n") else text + "\n"
    path.write_text(body, encoding="utf-8")
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


def apply_crystal_kpoints(text: str, nk1: int, nk2: int, nk3: int) -> str:
    """Replace a ``K_POINTS automatic`` block with a full Γ-centered crystal mesh."""
    import re

    pts = uniform_crystal_kpoints(nk1, nk2, nk3)
    k_lines = [f"K_POINTS crystal\n{len(pts)}"]
    for kx, ky, kz, w in pts:
        k_lines.append(f"  {kx:.8f}  {ky:.8f}  {kz:.8f}  {w:.8e}")
    k_block = "\n".join(k_lines)

    new_text, nsub = re.subn(
        r"K_POINTS\s+automatic\s*\n[^\n]+",
        k_block,
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if nsub != 1:
        new_text = text.rstrip() + "\n" + k_block + "\n"
    return new_text if new_text.endswith("\n") else new_text + "\n"


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

    ``nosym`` / ``noinv`` mirror :func:`build_nscf_wannier_input` so the unreduced
    mesh matches EPW ``nk1–nk3``. On QE 7.3.1 this also avoids the
    ``divide_class`` / ``prepare_sym_analysis`` segfault in ``epw_setup``.

    Default on (``epw.nscf_nosym`` None/True). Opt out with ``epw.nscf_nosym:
    false``. For MgB₂-class goldens also set ``dft.nosym`` so SCF+DFPT match;
    nosym-only NSCF on symmetry-DFPT triggers ``gmap_sym`` /
    ``free(): invalid pointer`` after Wannier. After pipeline nosym, the same
    fingerprint under ``npool>1`` may be QE 7.3.1 ``gmap_sym`` heap corruption
    — try ``nproc=1`` / ``epw.npool=1`` (see ``sym_mismatch_remediation``).
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

    # SYSTEM nosym/noinv — epw.nscf_nosym (default on); False opts out even if
    # dft.nosym (must clear build_pw_input setdefault).
    extra: dict[str, Any] = {"nbnd": n_bands}
    if effective_epw_nscf_nosym(config):
        extra["nosym"] = True
        extra["noinv"] = True
    elif bool(getattr(config, "nosym", False)):
        extra["nosym"] = False
        extra["noinv"] = False
    pw = build_pw_input(
        structure,
        config,
        calculation="nscf",
        prefix=prefix,
        outdir=outdir,
        extra_system=extra,
    )
    return apply_crystal_kpoints(pw_input_to_text(pw), nk1, nk2, nk3)


def build_nscf_wannier_input(
    structure: Structure,
    config: DFTConfig,
    *,
    prefix: str = "siscforge",
    outdir: str = "./out",
    nk: tuple[int, int, int] | list[int] | None = None,
    nbnd: int | None = None,
    include_hubbard: bool = False,
) -> str:
    """Build a standalone-Wannier NSCF input with a full crystal k-mesh.

    Uses the Wannier ``.win`` mesh (``resolve_kmesh`` / ``WannierConfig.kmesh``)
    rather than the EPW coarse grid. ``nosym`` / ``noinv`` are set so the
    unreduced mesh matches Wannier90 ``mp_grid``. When *include_hubbard* is
    True, DFT+U SYSTEM extras (and a HUBBARD card if that dialect is selected)
    are injected so nscf can restart from a DFT+U charge density.

    The input is written under the Wannier workdir; ``outdir`` should point at
    an isolated copy of ``{prefix}.save`` so EPW / DFT+U artifacts are not
    overwritten.
    """
    if nk is not None:
        nkc = (list(nk) + [4, 4, 4])[:3]
    else:
        from siscforge.calculators.qe.wannier import resolve_kmesh

        nkc = resolve_kmesh(config, structure)
    nk1, nk2, nk3 = int(nkc[0]), int(nkc[1]), int(nkc[2])

    if nbnd is not None:
        n_bands = int(nbnd)
    elif config.wannier.num_bands is not None:
        n_bands = int(config.wannier.num_bands)
    elif config.nbnd is not None:
        n_bands = int(config.nbnd)
    else:
        n_wann = int(config.wannier.num_wann) if config.wannier.num_wann else 8
        n_bands = max(24, n_wann + 8)

    extra_system: dict[str, Any] = {"nbnd": n_bands}
    extra_control: dict[str, Any] = {"nosym": True, "noinv": True}
    hubbard_dialect = "namelist"
    if include_hubbard:
        from siscforge.calculators.qe.dftu import hubbard_system_extras

        hubbard_dialect = (config.dftu.hubbard_syntax or "namelist").lower()
        extra_system.update(
            hubbard_system_extras(structure, config.dftu, syntax=hubbard_dialect)
        )

    pw = build_pw_input(
        structure,
        config,
        calculation="nscf",
        prefix=prefix,
        outdir=outdir,
        extra_system=extra_system,
        extra_control=extra_control,
    )
    text = apply_crystal_kpoints(pw_input_to_text(pw), nk1, nk2, nk3)
    if include_hubbard and hubbard_dialect == "card":
        from siscforge.calculators.qe.dftu import append_hubbard_card

        text = append_hubbard_card(text, structure, config.dftu)
        if not text.endswith("\n"):
            text += "\n"
    return text


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
    recover: bool = False,
    search_sym: bool = True,
) -> str:
    """Return a minimal ``ph.x`` input deck as a string.

    Gamma-only: set ``ldisp=False`` and use a single q = (0,0,0) block.
    For EPW, set ``ldisp=True`` and ``fildvscf='dvscf'``.

    When *recover* is True, set QE ``recover=.true.`` so ``ph.x`` resumes an
    interrupted DFPT run from on-disk restart files (dyn / ``_ph0`` / outdir).
    Do not combine with ``reduce_io=.true.`` (not set in this builder).

    When *search_sym* is False, emit ``search_sym = .false.`` (skip mode-symmetry
    analysis). Default True omits the card so QE keeps its default ``.true.``.
    Use False for cells that segfault in ``divide_class`` /
    ``prepare_sym_analysis`` (QE 7.3.1; observed on MgB₂).

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
    if recover:
        lines.append("  recover = .true.")
    if not search_sym:
        lines.append("  search_sym = .false.")
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
