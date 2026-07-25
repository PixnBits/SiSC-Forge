"""jobflow recipes for QE relax → SCF → phonon (with local sequential fallback).

When ``jobflow`` is installed, :func:`build_relax_scf_phonon_flow` returns a
``Flow``. Execution always goes through :func:`run_relax_scf_phonon`, which
runs the same steps locally (subprocess) so a full job store is not required
for workstation Phase-0 use.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from siscforge.calculators.qe.env import QEEnvironment, detect_qe_environment, require_qe
from siscforge.calculators.qe.inputs import (
    build_ph_input,
    build_pw_input,
    write_ph_input,
    write_pw_input,
)
from siscforge.calculators.qe.parser import (
    parse_ph_output,
    parse_pw_output,
    parse_relaxed_structure,
)
from siscforge.models.config import DFTConfig
from siscforge.models.results import PhononResult, SCFResult


@dataclass
class QEStepResult:
    """Artifacts from one QE executable invocation."""

    name: str
    work_dir: Path
    returncode: int
    stdout_path: Path
    input_path: Path
    success: bool
    message: str = ""


@dataclass
class QEWorkflowResult:
    """Aggregated relax / SCF / phonon results for one structure."""

    work_dir: Path
    structure: Structure
    scf: SCFResult | None = None
    phonon: PhononResult | None = None
    steps: list[QEStepResult] = field(default_factory=list)
    relaxed_structure: Structure | None = None
    success: bool = False
    message: str = ""


def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    env: dict[str, str] | None = None,
) -> int:
    """Run *cmd* in *cwd*, tee stdout/stderr to *stdout_path*.

    stdin is closed so MPI-linked QE binaries never wait on the TTY.
    """
    run_env = os.environ.copy()
    # Avoid OpenMPI fabric probes hanging on desktop installs.
    run_env.setdefault("OMPI_MCA_btl", "^openib")
    if env:
        run_env.update(env)
    with stdout_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=run_env,
            check=False,
        )
    return int(proc.returncode)


def _mpi_prefix(qe_env: QEEnvironment, nproc: int) -> list[str]:
    """Wrap QE executables with mpirun when available.

    Ubuntu/distro ``pw.x`` / ``ph.x`` / ``epw.x`` are typically OpenMPI-linked and
    **hang if started without mpirun**, even for a single rank. Always launch
    via mpirun when ``mpirun`` is on PATH.
    """
    if not qe_env.mpirun:
        return []
    n = max(1, int(nproc))
    # --oversubscribe helps single-node workstations with flexible rank counts
    return [qe_env.mpirun, "--oversubscribe", "-np", str(n)]


def run_pw(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path,
    *,
    calculation: str,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    outdir: Path | None = None,
) -> QEStepResult:
    """Write and run a ``pw.x`` calculation (scf / relax / vc-relax).

    *outdir* defaults to ``work_dir/out``. For EPW prep, pass ``outdir=work_dir``
    so ``_ph0/``, ``*.save``, and ``*.dyn*`` share one directory (as EPW examples).
    """
    qe_env = qe_env or require_qe(need_phonon=False)
    assert qe_env.pw is not None

    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    outdir = Path(outdir).resolve() if outdir is not None else (work_dir / "out").resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # Ensure pseudo_dir is absolute in the input deck
    dft = config
    if dft.pseudo_dir:
        dft = dft.model_copy(update={"pseudo_dir": str(Path(dft.pseudo_dir).resolve())})

    pw_in = build_pw_input(
        structure,
        dft,
        calculation=calculation,
        prefix=prefix,
        outdir=str(outdir),
    )
    in_path = work_dir / f"{calculation}.in"
    out_path = work_dir / f"{calculation}.out"
    write_pw_input(pw_in, in_path)

    cmd = [
        *_mpi_prefix(qe_env, config.nproc),
        qe_env.pw,
        "-in",
        in_path.name,
    ]
    rc = _run_cmd(cmd, cwd=work_dir, stdout_path=out_path)
    ok = rc == 0 and out_path.is_file()
    msg = f"pw.x {calculation} rc={rc}"
    if not ok:
        # Include a short tail for debugging
        try:
            tail = out_path.read_text(encoding="utf-8", errors="replace")[-800:]
            msg += f"\n--- output tail ---\n{tail}"
        except OSError:
            pass
    return QEStepResult(
        name=calculation,
        work_dir=work_dir,
        returncode=rc,
        stdout_path=out_path,
        input_path=in_path,
        success=ok,
        message=msg,
    )


def run_ph(
    config: DFTConfig,
    work_dir: Path,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    for_epw: bool = False,
    outdir: Path | None = None,
) -> QEStepResult:
    """Write and run ``ph.x`` in *work_dir* (expects prior pw.x outdir).

    When *for_epw* is True, force multi-q ``ldisp`` with ``fildvscf='dvscf'``
    (required for EPW's ``save/`` preparation via ``pp.py``).
    """
    qe_env = qe_env or require_qe(need_phonon=True)
    assert qe_env.ph is not None

    work_dir = Path(work_dir).resolve()
    out = Path(outdir).resolve() if outdir is not None else (work_dir / "out").resolve()
    out.mkdir(parents=True, exist_ok=True)

    if for_epw:
        qpts = list(config.epw.nqc) if config.epw.nqc else list(config.qpoints)
        qpts = (list(qpts) + [2, 2, 2])[:3]
        ldisp = True
        fildvscf: str | None = "dvscf"
    else:
        qpts = (list(config.qpoints) + [2, 2, 2])[:3]
        ldisp = config.phonon_method != "gamma"
        fildvscf = None
    nq1, nq2, nq3 = int(qpts[0]), int(qpts[1]), int(qpts[2])

    # Softer mixing for multi-q / EPW metals (zone-boundary often harder)
    alpha_mix = float(config.ph_alpha_mix)
    if for_epw and alpha_mix > 0.2:
        alpha_mix = min(alpha_mix, 0.2)

    ph_text = build_ph_input(
        prefix=prefix,
        outdir=str(out),
        tr2_ph=config.tr2_ph,
        ldisp=ldisp,
        nq1=nq1,
        nq2=nq2,
        nq3=nq3,
        fildyn=f"{prefix}.dyn",
        fildvscf=fildvscf,
        alpha_mix=alpha_mix,
        nmix_ph=config.ph_nmix,
        niter_ph=config.ph_niter,
    )
    in_path = work_dir / "ph.in"
    out_path = work_dir / "ph.out"
    write_ph_input(ph_text, in_path)

    cmd = [
        *_mpi_prefix(qe_env, config.nproc),
        qe_env.ph,
        "-in",
        in_path.name,
    ]
    rc = _run_cmd(cmd, cwd=work_dir, stdout_path=out_path)
    ok = rc == 0 and out_path.is_file()
    msg = f"ph.x rc={rc}"
    # Detect known Ubuntu/distro QE 6.7 fortify crash when reading data-file-schema.xml
    try:
        body = out_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        body = ""
    if "buffer overflow detected" in body or "*** buffer overflow" in body:
        ok = False
        msg = (
            "ph.x aborted with a buffer-overflow fortify trap while reading the SCF "
            "save directory. This is a known failure mode of some distro Quantum "
            "ESPRESSO 6.7 builds (e.g. Ubuntu) even with short paths.\n"
            "Workarounds:\n"
            "  1) Build QE ≥ 7.2 from source (recommended for ph.x + EPW), or\n"
            "  2) Set dft.phonon_method: phonopy_fd and `pip install phonopy` "
            "(uses pw.x finite differences; no ph.x; not a full EPW path).\n"
            f"See ph.out: {out_path}"
        )
    elif not ok:
        try:
            tail = body[-800:]
            msg += f"\n--- output tail ---\n{tail}"
        except Exception:  # noqa: BLE001
            pass
    return QEStepResult(
        name="ph",
        work_dir=work_dir,
        returncode=rc,
        stdout_path=out_path,
        input_path=in_path,
        success=ok,
        message=msg,
    )


def _try_read_relaxed_structure(work_dir: Path, fallback: Structure) -> Structure:
    """Re-read final geometry from vc-relax ``*.out`` (CELL_PARAMETERS block)."""
    # Prefer named outputs written by run_pw
    for name in ("vc-relax.out", "relax.out"):
        out = work_dir / name
        if out.is_file():
            parsed = parse_relaxed_structure(out, fallback=None)
            if parsed is not None:
                return parsed
    for out in sorted(work_dir.glob("*.out")):
        parsed = parse_relaxed_structure(out, fallback=None)
        if parsed is not None:
            return parsed
    return fallback


def run_relax_scf_phonon(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
) -> QEWorkflowResult:
    """Execute relax (optional) → SCF → phonon (optional) in *work_dir*.

    This is the local sequential path used by :class:`QECalculator`.
    After ``vc-relax``, the final geometry is parsed from the pw.x output and
    fed into SCF / phonon. Phonon method is selected via ``config.phonon_method``:
    ``dfpt`` / ``gamma`` (ph.x) or ``phonopy_fd`` (optional).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    use_phonopy = config.do_phonon and config.phonon_method == "phonopy_fd"
    need_ph_binary = config.do_phonon and not use_phonopy
    qe_env = qe_env or require_qe(need_phonon=need_ph_binary)

    result = QEWorkflowResult(work_dir=work_dir, structure=structure)
    current = structure

    # 1. Optional relaxation
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
            result.message = (
                f"Relaxation failed (pw.x vc-relax).\n{step.message}\n"
                f"Check cutoffs, pseudopotentials, and {work_dir / '01_relax'}."
            )
            result.success = False
            if step.stdout_path.is_file():
                result.scf = parse_pw_output(
                    step.stdout_path, quality_tag=config.quality_tag
                )
            return result
        current = _try_read_relaxed_structure(work_dir / "01_relax", current)
        result.relaxed_structure = current

    # 2. SCF on (possibly relaxed) geometry
    scf_dir = work_dir / "02_scf"
    step = run_pw(
        current,
        config,
        scf_dir,
        calculation="scf",
        prefix=prefix,
        qe_env=qe_env,
    )
    result.steps.append(step)
    if step.stdout_path.is_file():
        result.scf = parse_pw_output(step.stdout_path, quality_tag=config.quality_tag)
    if not step.success:
        result.message = (
            f"SCF failed (pw.x scf).\n{step.message}\n"
            f"Work directory: {scf_dir}"
        )
        result.success = False
        return result

    # 3. Phonons
    if config.do_phonon:
        if use_phonopy:
            from siscforge.calculators.qe.phonopy_fd import (
                PhonopyNotAvailableError,
                run_phonopy_fd,
            )

            try:
                ph, diag = run_phonopy_fd(
                    current,
                    config,
                    work_dir / "03_phonopy_fd",
                    prefix=prefix,
                    qe_env=qe_env,
                )
            except PhonopyNotAvailableError as exc:
                result.message = str(exc)
                result.success = False
                return result
            result.phonon = ph
            if ph is None:
                result.message = (
                    f"Phonopy FD phonon failed: {diag.get('error', diag)}\n"
                    f"Work directory: {work_dir / '03_phonopy_fd'}"
                )
                result.success = False
                return result
        else:
            step = run_ph(config, scf_dir, prefix=prefix, qe_env=qe_env)
            result.steps.append(step)
            texts: list[str] = []
            if step.stdout_path.is_file():
                texts.append(
                    step.stdout_path.read_text(encoding="utf-8", errors="replace")
                )
            for dyn in sorted(scf_dir.glob(f"{prefix}.dyn*")):
                try:
                    texts.append(dyn.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
            combined = "\n".join(texts) if texts else ""
            if combined:
                result.phonon = parse_ph_output(
                    combined, quality_tag=config.quality_tag
                )
            if not step.success and result.phonon is None:
                result.message = (
                    f"Phonon failed (ph.x).\n{step.message}\n"
                    f"Ensure ph.x is installed and SCF finished cleanly in {scf_dir}."
                )
                result.success = False
                return result

    result.success = result.scf is not None and result.scf.status in {"ok", "mock"}
    if result.success and config.do_phonon:
        result.success = result.phonon is not None and result.phonon.status in {
            "ok",
            "mock",
        }
    result.message = "ok" if result.success else (result.message or "incomplete")
    return result


# ---------------------------------------------------------------------------
# jobflow wrappers (optional)
# ---------------------------------------------------------------------------


def build_relax_scf_phonon_flow(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    name: str = "qe_relax_scf_phonon",
) -> Any:
    """Build a jobflow ``Flow`` for relax → SCF → phonon when jobflow is installed.

    Returns ``None`` if jobflow is not available. The flow's jobs call the same
    local runners as :func:`run_relax_scf_phonon`.
    """
    try:
        from jobflow import Flow, job
    except ImportError:
        return None

    work_dir = Path(work_dir)

    @job
    def relax_job(struct: Structure, cfg: dict) -> dict:
        dft = DFTConfig.model_validate(cfg)
        step = run_pw(
            struct,
            dft,
            work_dir / "01_relax",
            calculation="vc-relax",
            prefix=prefix,
        )
        return {
            "success": step.success,
            "stdout": str(step.stdout_path),
            "message": step.message,
        }

    @job
    def scf_job(struct: Structure, cfg: dict) -> dict:
        dft = DFTConfig.model_validate(cfg)
        step = run_pw(
            struct,
            dft,
            work_dir / "02_scf",
            calculation="scf",
            prefix=prefix,
        )
        scf = (
            parse_pw_output(step.stdout_path, quality_tag=dft.quality_tag)
            if step.stdout_path.is_file()
            else None
        )
        return {
            "success": step.success,
            "stdout": str(step.stdout_path),
            "scf": scf.model_dump(mode="json") if scf else None,
            "message": step.message,
        }

    @job
    def phonon_job(cfg: dict) -> dict:
        dft = DFTConfig.model_validate(cfg)
        scf_dir = work_dir / "02_scf"
        step = run_ph(dft, scf_dir, prefix=prefix)
        ph = (
            parse_ph_output(step.stdout_path, quality_tag=dft.quality_tag)
            if step.stdout_path.is_file()
            else None
        )
        return {
            "success": step.success,
            "stdout": str(step.stdout_path),
            "phonon": ph.model_dump(mode="json") if ph else None,
            "message": step.message,
        }

    cfg_dict = config.model_dump(mode="json")
    jobs = []
    if config.do_relax:
        j_relax = relax_job(structure, cfg_dict)
        jobs.append(j_relax)
    j_scf = scf_job(structure, cfg_dict)
    jobs.append(j_scf)
    if config.do_phonon:
        j_ph = phonon_job(cfg_dict)
        jobs.append(j_ph)

    return Flow(jobs, name=name)


def recipe_info() -> dict[str, Any]:
    """Metadata for documentation / CLI help."""
    return {
        "steps": ["vc-relax (optional)", "scf", "ph.x DFPT (optional)"],
        "jobflow": detect_qe_environment().jobflow,
        "qe": detect_qe_environment().available,
        "engine": "quantum-espresso",
        "models": ["SCFResult", "PhononResult"],
    }
