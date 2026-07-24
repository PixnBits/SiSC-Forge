"""EPW workflow steps on top of relax → SCF → phonon."""

from __future__ import annotations

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
from siscforge.calculators.qe.recipes import QEStepResult, QEWorkflowResult, run_relax_scf_phonon
from siscforge.models.config import DFTConfig
from siscforge.models.results import ElectronPhononResult


@dataclass
class EPWWorkflowResult(QEWorkflowResult):
    """Extends the phonon workflow with an electron-phonon result."""

    electron_phonon: ElectronPhononResult | None = None
    performance_score: float | None = None
    epw_steps: list[QEStepResult] = field(default_factory=list)


def _mpi_prefix(qe_env: QEEnvironment, nproc: int) -> list[str]:
    if nproc <= 1 or not qe_env.mpirun:
        return []
    return [qe_env.mpirun, "-np", str(nproc)]


def run_epw(
    config: DFTConfig,
    work_dir: Path,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
) -> tuple[QEStepResult, ElectronPhononResult | None]:
    """Write and run ``epw.x`` in *work_dir*; parse stdout into ElectronPhononResult."""
    qe_env = qe_env or require_epw()
    assert qe_env.epw is not None

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    outdir = work_dir / "out"
    outdir.mkdir(exist_ok=True)

    epw_text = build_epw_input(
        config,
        prefix=prefix,
        outdir=str(outdir),
        dvscf_dir=str(work_dir / "save"),
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

    with out_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir),
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    rc = int(proc.returncode)
    ok = rc == 0 and out_path.is_file()
    msg = f"epw.x rc={rc}"
    if not ok:
        try:
            tail = out_path.read_text(encoding="utf-8", errors="replace")[-1200:]
            msg += f"\n--- output tail ---\n{tail}"
        except OSError:
            pass

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
        # Ensure Allen–Dynes fallback if λ, ω_log present but Tc missing
        if (
            config.epw.allen_dynes_fallback
            and eph.lambda_total is not None
            and eph.omega_log is not None
            and eph.Tc_allen_dynes is None
        ):
            tc = allen_dynes_tc(eph.lambda_total, eph.omega_log, config.epw.mu_star)
            eph = eph.model_copy(update={"Tc_allen_dynes": tc})

    return step, eph


def run_relax_scf_phonon_epw(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
) -> EPWWorkflowResult:
    """Full conventional path: relax → SCF → phonon → EPW (when enabled)."""
    work_dir = Path(work_dir)
    want_epw = config.do_epw or config.epw.enabled
    qe_env = qe_env or (
        require_epw() if want_epw else None
    )

    base = run_relax_scf_phonon(
        structure,
        config,
        work_dir,
        prefix=prefix,
        qe_env=qe_env,
    )
    result = EPWWorkflowResult(
        work_dir=base.work_dir,
        structure=base.structure,
        scf=base.scf,
        phonon=base.phonon,
        steps=list(base.steps),
        relaxed_structure=base.relaxed_structure,
        success=base.success,
        message=base.message,
    )

    if not want_epw:
        return result

    if not base.success:
        result.message = (
            f"Skipping EPW because phonon workflow failed: {base.message}"
        )
        result.success = False
        return result

    # Prefer running EPW from the SCF directory (shared outdir / wavefunctions)
    scf_dir = work_dir / "02_scf"
    if not scf_dir.is_dir():
        scf_dir = work_dir / "03_epw"
        scf_dir.mkdir(parents=True, exist_ok=True)

    step, eph = run_epw(config, scf_dir, prefix=prefix, qe_env=qe_env)
    result.epw_steps.append(step)
    result.steps.append(step)
    result.electron_phonon = eph

    if eph is not None and eph.converged:
        tc = eph.best_tc_K()
        result.performance_score = performance_score_from_epw(tc)
        result.success = True
        result.message = "ok"
    else:
        result.success = False
        result.message = (
            f"EPW failed or did not converge: {step.message}\n"
            "Ensure Wannierization inputs, coarse grids, and dvscf data are present."
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
            "ph.x DFPT",
            "epw.x (Wannier + e-ph + isotropic Eliashberg)",
        ],
        "models": ["SCFResult", "PhononResult", "ElectronPhononResult"],
        "tc": ["Allen-Dynes", "isotropic Eliashberg (EPW or closed-form)"],
    }
