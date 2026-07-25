"""EPW workflow steps on top of relax → SCF → phonon."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from siscforge.calculators.qe.eliashberg import (
    allen_dynes_tc,
    isotropic_eliashberg_tc_from_moments,
    performance_score_from_epw,
)
from siscforge.calculators.qe.env import QEEnvironment, require_epw
from siscforge.calculators.qe.epw_inputs import build_epw_input, write_epw_input
from siscforge.calculators.qe.epw_parser import parse_epw_output
from siscforge.calculators.qe.inputs import build_nscf_epw_input
from siscforge.calculators.qe.parser import parse_ph_output, parse_pw_output
from siscforge.calculators.qe.recipes import (
    QEStepResult,
    QEWorkflowResult,
    run_ph,
    run_pw,
)
from siscforge.models.config import DFTConfig
from siscforge.models.results import ElectronPhononResult


@dataclass
class EPWWorkflowResult(QEWorkflowResult):
    """Extends the phonon workflow with an electron-phonon result."""

    electron_phonon: ElectronPhononResult | None = None
    performance_score: float | None = None
    epw_steps: list[QEStepResult] = field(default_factory=list)


def _find_epw_pp_py() -> Path | None:
    """Locate EPW's ``pp.py`` next to epw.x or under a source tree."""
    env = os.environ.get("QE_BIN")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env) / "pp.py")
        candidates.append(Path(env).parent / "EPW" / "bin" / "pp.py")
    # Common source layout: .../q-e-*/bin/epw.x → .../EPW/bin/pp.py
    which_epw = shutil.which("epw.x")
    if which_epw:
        p = Path(which_epw).resolve()
        candidates.append(p.parent / "pp.py")
        candidates.append(p.parent.parent / "EPW" / "bin" / "pp.py")
        # symlink .../bin/epw.x → .../EPW/src/epw.x
        if p.is_symlink() or "EPW" in str(p):
            for parent in p.parents:
                cand = parent / "EPW" / "bin" / "pp.py"
                candidates.append(cand)
                if parent.name.startswith("q-e"):
                    break
    home = Path.home() / "src"
    if home.is_dir():
        candidates.extend(home.glob("q-e-*/EPW/bin/pp.py"))
    for c in candidates:
        if c.is_file():
            return c
    return None


def run_epw_pp(work_dir: Path, prefix: str) -> QEStepResult:
    """Run EPW ``pp.py`` to assemble the ``save/`` directory for epw.x.

    ``pp.py`` expects CWD to contain ``_ph0/``, ``{prefix}.save/``, and
    ``{prefix}.dyn*`` (QE EPW examples use ``outdir='./'``).
    """
    work_dir = Path(work_dir).resolve()
    out_path = work_dir / "pp.out"
    pp_py = _find_epw_pp_py()
    if pp_py is None:
        return QEStepResult(
            name="epw_pp",
            work_dir=work_dir,
            returncode=1,
            stdout_path=out_path,
            input_path=work_dir / "pp.py",
            success=False,
            message=(
                "EPW pp.py not found. Set QE_BIN to a QE build that includes "
                "EPW/bin/pp.py (source build), or place pp.py next to epw.x."
            ),
        )

    # pp.py prompts for prefix on stdin
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            proc = subprocess.run(
                ["python3", str(pp_py)],
                cwd=str(work_dir),
                input=f"{prefix}\n",
                text=True,
                stdout=fh,
                stderr=subprocess.STDOUT,
                check=False,
            )
        rc = int(proc.returncode)
    except OSError as exc:
        return QEStepResult(
            name="epw_pp",
            work_dir=work_dir,
            returncode=1,
            stdout_path=out_path,
            input_path=pp_py,
            success=False,
            message=f"Failed to run pp.py: {exc}",
        )

    save_dir = work_dir / "save"
    ok = rc == 0 and save_dir.is_dir()
    msg = f"epw pp.py rc={rc}; save_dir={'ok' if save_dir.is_dir() else 'missing'}"
    if not ok and out_path.is_file():
        try:
            msg += "\n" + out_path.read_text(encoding="utf-8", errors="replace")[-800:]
        except OSError:
            pass
    return QEStepResult(
        name="epw_pp",
        work_dir=work_dir,
        returncode=rc,
        stdout_path=out_path,
        input_path=pp_py,
        success=ok,
        message=msg,
    )


def run_nscf_for_epw(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    outdir: Path | None = None,
) -> QEStepResult:
    """Run NSCF on the full crystal k-mesh required by EPW Wannierization.

    Uses ``outdir='./'``-style flat layout when *outdir* equals *work_dir*.
    """
    from siscforge.calculators.qe.recipes import _mpi_prefix, _run_cmd

    qe_env = qe_env or require_epw()
    assert qe_env.pw is not None

    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out = Path(outdir).resolve() if outdir is not None else work_dir
    out.mkdir(parents=True, exist_ok=True)

    # Relative outdir when flat (matches official EPW examples)
    outdir_str = "./" if out.resolve() == work_dir.resolve() else str(out)

    dft = config
    if dft.pseudo_dir:
        dft = dft.model_copy(update={"pseudo_dir": str(Path(dft.pseudo_dir).resolve())})

    nscf_text = build_nscf_epw_input(
        structure,
        dft,
        prefix=prefix,
        outdir=outdir_str,
    )
    in_path = work_dir / "nscf.in"
    out_path = work_dir / "nscf.out"
    # build_nscf returns a string; write directly
    in_path.write_text(nscf_text, encoding="utf-8")

    cmd = [
        *_mpi_prefix(qe_env, config.nproc),
        qe_env.pw,
        "-in",
        in_path.name,
    ]
    rc = _run_cmd(cmd, cwd=work_dir, stdout_path=out_path)
    ok = rc == 0 and out_path.is_file()
    msg = f"pw.x nscf (EPW) rc={rc}"
    if not ok:
        try:
            tail = out_path.read_text(encoding="utf-8", errors="replace")[-800:]
            msg += f"\n--- output tail ---\n{tail}"
        except OSError:
            pass
    return QEStepResult(
        name="nscf",
        work_dir=work_dir,
        returncode=rc,
        stdout_path=out_path,
        input_path=in_path,
        success=ok,
        message=msg,
    )


def _fermi_from_work_dir(work_dir: Path) -> float | None:
    """Prefer NSCF Fermi energy, then SCF (eV)."""
    from siscforge.calculators.qe.parser import parse_fermi_energy_eV

    for name in ("nscf.out", "scf.out"):
        path = work_dir / name
        if path.is_file():
            ef = parse_fermi_energy_eV(path)
            if ef is not None:
                return ef
    return None


# Common EPW / Wannier failure fingerprints → short remediation hints
_EPW_FAILURE_HINTS: list[tuple[str, str]] = [
    (
        "cannot bracket",
        "Fermi bracket failed after Wannier: ensure dis_win_* brackets E_F "
        "(SiSC-Forge sets windows from nscf/scf E_F) and try efermi_read / denser nkf.",
    ),
    (
        "efermig",
        "Fine-mesh Fermi search failed: pin fermi_energy from DFT (efermi_read) "
        "or widen fsthick / denser nkf.",
    ),
    (
        "d_matrix",
        "PAW d_matrix / symmetry crash: re-run vc-relax so the cell matches "
        "the symmetry used in DFPT; avoid nosym on multi-q paths.",
    ),
    (
        "error in routine dafopen",
        "Missing phonon/dvscf files: confirm multi-q DFPT wrote dyn* + fildvscf "
        "and that pp.py created save/ in the same work directory.",
    ),
    (
        "error opening",
        "Missing EPW prerequisite files: check save/, *.save, and nscf wavefunctions "
        "in the work directory (flat outdir layout).",
    ),
    (
        "wannier",
        "Wannierization issue: screening uses proj=random — for production, set "
        "material-specific projections and freeze windows; raise num_iter if needed.",
    ),
    (
        "imaginary",
        "Imaginary / soft modes present: raise eps_acustic, improve structure "
        "relaxation, or denser DFPT q-mesh before trusting λ/Tc.",
    ),
    (
        "not enough bands",
        "Insufficient bands for Wannier: increase dft.nbnd and epw.nbndsub.",
    ),
    (
        "nbndsub",
        "nbndsub / Wannier band count mismatch: set epw.nbndsub consistently "
        "with occupied + empty bands in the window.",
    ),
    (
        "k-point",
        "k-grid inconsistency: nscf crystal mesh must match epw nk1–nk3 (nkc).",
    ),
    (
        "segmentation",
        "EPW segfault: often symmetry/PAW or MPI pool issues — try nproc=1, "
        "npool=1, or re-relax; check QE/EPW build vs Wannier90 version.",
    ),
    (
        "%% error",
        "QE fatal error block in output — see tail of epw.out / workdir logs.",
    ),
]


def diagnose_epw_failure(
    text: str | None,
    *,
    work_dir: Path | str | None = None,
    step_name: str = "epw",
) -> str:
    """Return a multi-line diagnostic string for failed Wannier/EPW steps.

    Scans *text* (typically ``epw.out`` or a step message) for known fingerprints
    and appends workdir / quality_tag guidance. Safe for missing files.
    """
    parts: list[str] = [f"[{step_name}] EPW/Wannier diagnostic"]
    if work_dir is not None:
        wd = Path(work_dir)
        parts.append(f"  work_dir: {wd}")
        for name in ("epw.out", "epw.in", "nscf.out", "ph.out", "pp.out", "scf.out"):
            p = wd / name
            if p.is_file():
                try:
                    size = p.stat().st_size
                except OSError:
                    size = -1
                parts.append(f"  present: {name} ({size} bytes)")
        save = wd / "save"
        if save.is_dir():
            n_files = sum(1 for _ in save.rglob("*") if _.is_file())
            parts.append(f"  present: save/ ({n_files} files)")
        else:
            parts.append("  missing: save/  ← run EPW pp.py after multi-q DFPT")

    blob = (text or "").lower()
    hits: list[str] = []
    if blob:
        for needle, hint in _EPW_FAILURE_HINTS:
            if needle.lower() in blob:
                hits.append(f"  · matched '{needle}': {hint}")
        if not hits:
            hits.append(
                "  · no known fingerprint matched — inspect epw.out tail and "
                "Wannier90 .wout if present."
            )
    else:
        hits.append("  · no output text available to scan.")
    parts.append("hints:")
    parts.extend(hits)
    parts.append(
        "  · screening vs denser: raise epw.nkf/nqf and dft.qpoints (nqc must "
        "match DFPT); set dft.quality_tag: production when using denser grids."
    )
    parts.append(
        "  · docs: docs/examples/nbN_epw.md (NbN), docs/examples/mgb2_epw.md (MgB2)"
    )
    return "\n".join(parts)


def _output_tail(path: Path, n_chars: int = 1600) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n_chars:]
    except OSError:
        return ""


def run_epw(
    config: DFTConfig,
    work_dir: Path,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    structure: Structure | None = None,
    outdir: Path | None = None,
    fermi_eV: float | None = None,
) -> tuple[QEStepResult, ElectronPhononResult | None]:
    """Write and run ``epw.x`` in *work_dir*; parse stdout into ElectronPhononResult."""
    from siscforge.calculators.qe.recipes import _mpi_prefix, _run_cmd

    qe_env = qe_env or require_epw()
    assert qe_env.epw is not None

    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    # EPW examples use outdir='./' (same as work_dir); keep that layout for save/
    out = Path(outdir).resolve() if outdir is not None else work_dir
    out.mkdir(parents=True, exist_ok=True)
    save_dir = work_dir / "save"

    # Prefer relative paths (official EPW decks); absolute still works.
    if out.resolve() == work_dir.resolve():
        outdir_str = "./"
        dvscf_str = "./save"
    else:
        outdir_str = str(out)
        dvscf_str = str(save_dir.resolve())

    if fermi_eV is None:
        fermi_eV = _fermi_from_work_dir(work_dir)

    epw_text = build_epw_input(
        config,
        prefix=prefix,
        outdir=outdir_str,
        dvscf_dir=dvscf_str,
        structure=structure,
        fermi_eV=fermi_eV,
    )
    in_path = work_dir / "epw.in"
    out_path = work_dir / "epw.out"
    write_epw_input(epw_text, in_path)

    cmd = [
        *_mpi_prefix(qe_env, config.nproc),
        qe_env.epw,
    ]
    if config.epw.npool > 1:
        cmd.extend(["-npool", str(config.epw.npool)])
    cmd.extend(["-in", in_path.name])

    rc = _run_cmd(cmd, cwd=work_dir, stdout_path=out_path)
    ok = rc == 0 and out_path.is_file()
    qtag = config.quality_tag
    msg = f"epw.x rc={rc}; quality_tag={qtag}"
    if not ok:
        tail = _output_tail(out_path) if out_path.is_file() else ""
        if tail:
            msg += f"\n--- output tail ---\n{tail}"
        msg += "\n" + diagnose_epw_failure(
            tail or msg, work_dir=work_dir, step_name="epw"
        )

    step = QEStepResult(
        name="epw",
        work_dir=work_dir,
        returncode=rc,
        stdout_path=out_path,
        input_path=in_path,
        success=ok,
        message=msg,
    )

    eph: ElectronPhononResult | None = None
    if out_path.is_file():
        eph = parse_epw_output(
            out_path,
            mu_star=config.epw.mu_star,
            quality_tag=config.quality_tag,
        )
        if (
            config.epw.allen_dynes_fallback
            and eph.lambda_total is not None
            and eph.omega_log is not None
            and eph.Tc_allen_dynes is None
        ):
            tc = allen_dynes_tc(eph.lambda_total, eph.omega_log, config.epw.mu_star)
            eph = eph.model_copy(update={"Tc_allen_dynes": tc})

        # Attach material notes (e.g. MgB2 two-gap → isotropic average)
        from siscforge.calculators.qe.epw_inputs import epw_material_notes

        mat_note = epw_material_notes(structure)
        if mat_note and eph is not None:
            summary = dict(eph.alpha2F_summary or {})
            summary.setdefault("material_notes", mat_note)
            summary.setdefault("tc_model", "isotropic_average")
            summary.setdefault("quality_tag", config.quality_tag)
            eph = eph.model_copy(update={"alpha2F_summary": summary})

        # Partial parse after non-zero rc: surface diagnostics on the result
        if eph is not None and not ok and eph.status != "ok":
            diag = diagnose_epw_failure(
                _output_tail(out_path) if out_path.is_file() else msg,
                work_dir=work_dir,
                step_name="epw",
            )
            summary = dict(eph.alpha2F_summary or {})
            summary["failure_diagnostic"] = diag
            eph = eph.model_copy(update={"alpha2F_summary": summary})

    return step, eph


def run_relax_scf_phonon_epw(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
) -> EPWWorkflowResult:
    """Conventional path tuned for EPW: SCF → multi-q DFPT → pp.py → EPW.

    Uses a flat work directory layout (``outdir = work_dir``) so EPW's ``pp.py``
    can find ``_ph0/``, ``*.save``, and ``*.dyn*`` as in the official examples.
    """
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    want_epw = config.do_epw or config.epw.enabled
    qe_env = qe_env or (require_epw() if want_epw else None)

    result = EPWWorkflowResult(work_dir=work_dir, structure=structure)
    current = structure

    # Flat outdir for EPW compatibility
    flat_out = work_dir if want_epw else (work_dir / "out")

    # 1. Optional relax — strongly recommended before multi-q DFPT (soft metals).
    # For EPW flat layout, write relax outdir under 01_relax; final SCF is re-done
    # in 02_scf with empty bands + flat outdir for ph/pp/epw.
    if config.do_relax:
        step = run_pw(
            current,
            config,
            work_dir / "01_relax",
            calculation="vc-relax",
            prefix=prefix,
            qe_env=qe_env,
        )
        result.steps.append(step)
        if not step.success:
            result.message = f"Relaxation failed: {step.message}"
            result.success = False
            return result
        from siscforge.calculators.qe.recipes import _try_read_relaxed_structure

        current = _try_read_relaxed_structure(work_dir / "01_relax", current)
        result.relaxed_structure = current

    # 2. SCF (flat outdir for EPW; ensure empty bands via DFTConfig.nbnd)
    scf_dir = work_dir / "02_scf"
    step = run_pw(
        current,
        config,
        scf_dir,
        calculation="scf",
        prefix=prefix,
        qe_env=qe_env,
        outdir=scf_dir if want_epw else None,
    )
    result.steps.append(step)
    if step.stdout_path.is_file():
        result.scf = parse_pw_output(step.stdout_path, quality_tag=config.quality_tag)
    if not step.success:
        result.message = f"SCF failed: {step.message}"
        result.success = False
        return result

    # 3. Phonon (multi-q + dvscf when EPW requested)
    step = run_ph(
        config,
        scf_dir,
        prefix=prefix,
        qe_env=qe_env,
        for_epw=want_epw,
        outdir=scf_dir if want_epw else None,
    )
    result.steps.append(step)
    if step.stdout_path.is_file():
        texts = [step.stdout_path.read_text(encoding="utf-8", errors="replace")]
        for dyn in sorted(scf_dir.glob(f"{prefix}.dyn*")):
            try:
                texts.append(dyn.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        result.phonon = parse_ph_output(
            "\n".join(texts), quality_tag=config.quality_tag
        )
    if not step.success:
        result.message = f"Phonon failed: {step.message}"
        result.success = False
        return result

    if not want_epw:
        result.success = True
        result.message = "ok"
        return result

    # 4. EPW pp.py → save/
    pp_step = run_epw_pp(scf_dir, prefix)
    result.epw_steps.append(pp_step)
    result.steps.append(pp_step)
    if not pp_step.success:
        result.message = (
            f"EPW prep (pp.py) failed: {pp_step.message}\n"
            "Need multi-q DFPT with fildvscf and dyn files in the work directory.\n"
            + diagnose_epw_failure(
                pp_step.message, work_dir=scf_dir, step_name="epw_pp"
            )
        )
        result.success = False
        return result

    # 5. NSCF on full crystal k-mesh (nk1×nk2×nk3) for Wannierization
    nscf_step = run_nscf_for_epw(
        current,
        config,
        scf_dir,
        prefix=prefix,
        qe_env=qe_env,
        outdir=scf_dir,
    )
    result.epw_steps.append(nscf_step)
    result.steps.append(nscf_step)
    if not nscf_step.success:
        result.message = (
            f"EPW NSCF failed: {nscf_step.message}\n"
            + diagnose_epw_failure(
                nscf_step.message, work_dir=scf_dir, step_name="nscf"
            )
        )
        result.success = False
        return result

    # 6. epw.x
    step, eph = run_epw(
        config,
        scf_dir,
        prefix=prefix,
        qe_env=qe_env,
        structure=current,
        outdir=scf_dir,
    )
    result.epw_steps.append(step)
    result.steps.append(step)
    result.electron_phonon = eph

    if eph is not None and (eph.converged or eph.lambda_total is not None):
        tc = eph.best_tc_K()
        result.performance_score = performance_score_from_epw(tc)
        # Accept partial success if λ was extracted even if rc != 0
        result.success = eph.status == "ok" or eph.lambda_total is not None
        if result.success:
            result.message = f"ok (quality_tag={config.quality_tag})"
        else:
            result.message = step.message
    else:
        result.success = False
        result.message = (
            f"EPW failed or did not converge (quality_tag={config.quality_tag}):\n"
            f"{step.message}\n"
            "Common issues: Wannier projections for metals (proj=random is "
            "screening-only), nscf k-grid must match nk1–nk3, and dense enough "
            "q-mesh / soft modes in unstable structures. "
            "See recommended_grids() / docs/examples/nbN_epw.md to tighten settings."
        )

    return result


def electron_phonon_from_lambda_omega(
    lambda_total: float,
    omega_log_K: float,
    *,
    mu_star: float = 0.1,
    quality_tag: str = "screening",
    status: str = "ok",
) -> ElectronPhononResult:
    """Build an ElectronPhononResult from moments (tests / offline)."""
    tc_ad = allen_dynes_tc(lambda_total, omega_log_K, mu_star)
    tc_el = isotropic_eliashberg_tc_from_moments(
        lambda_total, omega_log_K, mu_star
    )
    return ElectronPhononResult(
        lambda_total=lambda_total,
        omega_log=omega_log_K,
        mu_star=mu_star,
        Tc_allen_dynes=tc_ad,
        Tc_eliashberg=tc_el,
        converged=True,
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
        alpha2F_summary={"method": "moments"},
    )


def recipe_epw_info() -> dict[str, Any]:
    return {
        "steps": [
            "vc-relax (optional)",
            "scf",
            "ph.x multi-q DFPT (ldisp + fildvscf)",
            "EPW pp.py → save/",
            "nscf (crystal k-mesh = nk1×nk2×nk3)",
            "epw.x (Wannier + e-ph + isotropic Eliashberg)",
        ],
        "models": ["SCFResult", "PhononResult", "ElectronPhononResult"],
        "tc": ["Allen-Dynes", "isotropic Eliashberg (EPW or closed-form)"],
    }
