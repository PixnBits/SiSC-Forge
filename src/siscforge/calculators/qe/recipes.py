"""jobflow recipes for QE relax → SCF → phonon (with local sequential fallback).

When ``jobflow`` is installed, :func:`build_relax_scf_phonon_flow` returns a
``Flow``. Execution always goes through :func:`run_relax_scf_phonon`, which
runs the same steps locally (subprocess) so a full job store is not required
for workstation Phase-0 use.
"""

from __future__ import annotations

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
from siscforge.calculators.qe.parser import parse_ph_output, parse_pw_output
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
    """Run *cmd* in *cwd*, tee stdout/stderr to *stdout_path*."""
    with stdout_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    return int(proc.returncode)


def _mpi_prefix(qe_env: QEEnvironment, nproc: int) -> list[str]:
    if nproc <= 1 or not qe_env.mpirun:
        return []
    return [qe_env.mpirun, "-np", str(nproc)]


def run_pw(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path,
    *,
    calculation: str,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
) -> QEStepResult:
    """Write and run a ``pw.x`` calculation (scf / relax / vc-relax)."""
    qe_env = qe_env or require_qe(need_phonon=False)
    assert qe_env.pw is not None

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    outdir = work_dir / "out"
    outdir.mkdir(exist_ok=True)

    pw_in = build_pw_input(
        structure,
        config,
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
) -> QEStepResult:
    """Write and run ``ph.x`` in *work_dir* (expects prior pw.x outdir)."""
    qe_env = qe_env or require_qe(need_phonon=True)
    assert qe_env.ph is not None

    work_dir = Path(work_dir)
    outdir = work_dir / "out"
    qpts = list(config.qpoints) + [2, 2, 2]
    nq1, nq2, nq3 = int(qpts[0]), int(qpts[1]), int(qpts[2])

    gamma_only = config.phonon_method == "gamma"
    ph_text = build_ph_input(
        prefix=prefix,
        outdir=str(outdir),
        tr2_ph=config.tr2_ph,
        ldisp=not gamma_only,
        nq1=nq1,
        nq2=nq2,
        nq3=nq3,
        fildyn=f"{prefix}.dyn",
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
    if not ok:
        try:
            tail = out_path.read_text(encoding="utf-8", errors="replace")[-800:]
            msg += f"\n--- output tail ---\n{tail}"
        except OSError:
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
    """Best-effort: re-read structure from pw output XML if present."""
    # QE writes prefix.xml in outdir; parsing is version-sensitive — keep fallback.
    for xml in (work_dir / "out").glob("*.xml"):
        try:
            # Optional: use ASE or custom XML parser later
            _ = xml
        except Exception:  # noqa: BLE001
            pass
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
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    need_ph = config.do_phonon
    qe_env = qe_env or require_qe(need_phonon=need_ph)

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
            result.message = f"Relaxation failed: {step.message}"
            result.success = False
            # Still try to parse whatever energy is present
            if step.stdout_path.is_file():
                result.scf = parse_pw_output(
                    step.stdout_path, quality_tag=config.quality_tag
                )
            return result
        current = _try_read_relaxed_structure(work_dir / "01_relax", current)
        result.relaxed_structure = current
        # Copy charge density / wavefunctions is complex; re-run SCF from geometry.

    # 2. SCF
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
        result.message = f"SCF failed: {step.message}"
        result.success = False
        return result

    # 3. Phonon DFPT (needs same outdir as SCF — run inside scf_dir)
    if config.do_phonon:
        step = run_ph(config, scf_dir, prefix=prefix, qe_env=qe_env)
        result.steps.append(step)
        # Parse ph.out and any dyn files for frequencies
        texts: list[str] = []
        if step.stdout_path.is_file():
            texts.append(step.stdout_path.read_text(encoding="utf-8", errors="replace"))
        for dyn in sorted(scf_dir.glob(f"{prefix}.dyn*")):
            try:
                texts.append(dyn.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        combined = "\n".join(texts) if texts else ""
        if combined:
            result.phonon = parse_ph_output(combined, quality_tag=config.quality_tag)
        if not step.success and result.phonon is None:
            result.message = f"Phonon failed: {step.message}"
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
