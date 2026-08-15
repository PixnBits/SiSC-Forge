"""jobflow recipes for QE relax → SCF → phonon (with local sequential fallback).

When ``jobflow`` is installed, :func:`build_relax_scf_phonon_flow` returns a
``Flow``. Execution always goes through :func:`run_relax_scf_phonon`, which
runs the same steps locally (subprocess) so a full job store is not required
for workstation Phase-0 use.

P3.1 adds :func:`run_dftu_scf` / :func:`run_dftu_workflow` for sequential
pw.x DFT+U (Hubbard). P3.2 adds standalone Wannierization after SCF/DFT+U.
P3.3 adds optional DMFT after Wannier (mock or gated solid_dmft).
Pairing → performance_score is P3.4.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
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
from siscforge.models.results import DFTUResult, DMFTResult, PhononResult, SCFResult, WannierResult

# Interesting log lines for heartbeat peeks (ph.x / pw.x / epw.x)
_HEARTBEAT_PEEK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Representation\s*#\s*\d+", re.I),
    re.compile(r"iter\s*#\s*\d+", re.I),
    re.compile(r"mode\s*#\s*\d+", re.I),
    re.compile(r"Self-consistent", re.I),
    re.compile(r"Electron-phonon", re.I),
    re.compile(r"lambda\b", re.I),
    re.compile(r"Wannier", re.I),
    re.compile(r"total energy", re.I),
    re.compile(r"JOB DONE", re.I),
    re.compile(r"Error", re.I),
]


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
    """Aggregated relax / SCF / phonon / DFT+U / Wannier results for one structure."""

    work_dir: Path
    structure: Structure
    scf: SCFResult | None = None
    phonon: PhononResult | None = None
    dftu: DFTUResult | None = None
    wannier: WannierResult | None = None
    dmft: DMFTResult | None = None
    steps: list[QEStepResult] = field(default_factory=list)
    relaxed_structure: Structure | None = None
    success: bool = False
    message: str = ""


def _heartbeat_seconds_from_config(config: DFTConfig | None) -> int:
    """Read ``run.heartbeat_seconds`` attached to DFTConfig (default 900)."""
    if config is None:
        return 900
    run = getattr(config, "_run_config", None)
    if run is None:
        return 900
    try:
        return max(0, int(getattr(run, "heartbeat_seconds", 900) or 0))
    except (TypeError, ValueError):
        return 900


def _heartbeat_eta_enabled(config: DFTConfig | None) -> bool:
    """Whether remaining-time hints are allowed on heartbeats."""
    if config is None:
        return True
    run = getattr(config, "_run_config", None)
    if run is None:
        return True
    return bool(getattr(run, "heartbeat_eta", True))


def _format_elapsed(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _log_text_tail(path: Path, *, max_bytes: int = 16384) -> str:
    """Read a tail chunk of a growing log (for progress parse + peek)."""
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _log_peek(path: Path, *, max_len: int = 90) -> str:
    """Best-effort interesting last line from a growing QE log."""
    if not path.is_file():
        return "(no log yet)"
    chunk = _log_text_tail(path, max_bytes=8192)
    if not chunk:
        return "(log empty)"
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return "(log empty)"
    # Prefer last matching interesting pattern
    for ln in reversed(lines):
        for pat in _HEARTBEAT_PEEK_PATTERNS:
            if pat.search(ln):
                return ln[:max_len] + ("…" if len(ln) > max_len else "")
    return lines[-1][:max_len] + ("…" if len(lines[-1]) > max_len else "")


def _default_heartbeat_print(message: str) -> None:
    """Print heartbeat to stderr so it is not mixed with redirected stdout."""
    print(message, file=sys.stderr, flush=True)


def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    env: dict[str, str] | None = None,
    heartbeat_seconds: int = 0,
    step_label: str = "qe",
    on_heartbeat: Callable[[str], None] | None = None,
    heartbeat_eta: bool = True,
) -> int:
    """Run *cmd* in *cwd*, tee stdout/stderr to *stdout_path*.

    stdin is closed so MPI-linked QE binaries never wait on the TTY.

    When *heartbeat_seconds* > 0, emit a progress line every N seconds while
    the process is alive (step name, elapsed time, healthy/stale log, peek).
    When progress is parseable (e.g. q-point i/N in ph.out), optionally append
    a rough remaining-time band (see ``run.heartbeat_eta``).
    """
    run_env = os.environ.copy()
    # Avoid OpenMPI fabric probes hanging on desktop installs.
    run_env.setdefault("OMPI_MCA_btl", "^openib")
    if env:
        run_env.update(env)

    emit = on_heartbeat or _default_heartbeat_print
    interval = max(0, int(heartbeat_seconds or 0))
    stdout_path = Path(stdout_path)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)

    # Short steps or disabled heartbeats: simple blocking run
    if interval <= 0:
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

    t0 = time.monotonic()
    last_size = 0
    last_mtime = 0.0
    with stdout_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=run_env,
        )
        while True:
            try:
                rc = proc.wait(timeout=interval)
                elapsed = _format_elapsed(time.monotonic() - t0)
                emit(
                    f"  [heartbeat] {step_label} finished after {elapsed} "
                    f"(rc={rc})"
                )
                return int(rc)
            except subprocess.TimeoutExpired:
                elapsed_s = time.monotonic() - t0
                elapsed = _format_elapsed(elapsed_s)
                alive = proc.poll() is None
                size = 0
                mtime = 0.0
                try:
                    st = stdout_path.stat()
                    size = int(st.st_size)
                    mtime = float(st.st_mtime)
                except OSError:
                    pass
                growing = size > last_size or mtime > last_mtime + 0.5
                last_size = max(last_size, size)
                last_mtime = max(last_mtime, mtime)
                if not alive:
                    # Process ended between wait timeout and poll
                    rc = proc.wait()
                    emit(
                        f"  [heartbeat] {step_label} finished after {elapsed} "
                        f"(rc={rc})"
                    )
                    return int(rc)
                if growing:
                    health = "healthy (log growing)"
                else:
                    # Stale for at least one full interval
                    health = "stale log? (process alive, log not growing)"
                log_tail = _log_text_tail(stdout_path)
                peek = _log_peek(stdout_path)
                eta_bit = ""
                if heartbeat_eta:
                    try:
                        from siscforge.walltime import heartbeat_eta_suffix

                        eta_bit = heartbeat_eta_suffix(
                            log_tail, elapsed_s, enabled=True
                        )
                    except Exception:  # noqa: BLE001
                        eta_bit = ""
                emit(
                    f"  [heartbeat] {step_label} still running — "
                    f"elapsed {elapsed}; {health}; "
                    f"log={size // 1024} KiB; peek: {peek}{eta_bit}"
                )


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
    extra_system: dict | None = None,
    extra_control: dict | None = None,
    input_basename: str | None = None,
    hubbard: bool = False,
) -> QEStepResult:
    """Write and run a ``pw.x`` calculation (scf / relax / vc-relax).

    *outdir* defaults to ``work_dir/out``. For EPW prep, pass ``outdir=work_dir``
    so ``_ph0/``, ``*.save``, and ``*.dyn*`` share one directory (as EPW examples).

    *extra_system* / *extra_control* are merged into the pw.x namelists (e.g.
    ``nosym=.true.`` for a conservative d_matrix recovery SCF).

    When *hubbard* is True, inject DFT+U using exactly one dialect from
    ``dft.dftu.hubbard_syntax`` (``namelist`` default or ``card``). *input_basename*
    defaults to *calculation* (e.g. ``scf``, ``dftu``).
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

    system_extra = dict(extra_system or {})
    hubbard_dialect = "namelist"
    if hubbard:
        from siscforge.calculators.qe.dftu import hubbard_system_extras

        hubbard_dialect = (dft.dftu.hubbard_syntax or "namelist").lower()
        system_extra.update(
            hubbard_system_extras(structure, dft.dftu, syntax=hubbard_dialect)
        )

    pw_in = build_pw_input(
        structure,
        dft,
        calculation=calculation,
        prefix=prefix,
        outdir=str(outdir),
        extra_system=system_extra or None,
        extra_control=extra_control,
    )
    base = input_basename or calculation
    in_path = work_dir / f"{base}.in"
    out_path = work_dir / f"{base}.out"
    if hubbard and hubbard_dialect == "card":
        # QE ≥ 7.1 HUBBARD card only — no namelist Hubbard_U (dual syntax invalid)
        from siscforge.calculators.qe.dftu import append_hubbard_card
        from siscforge.calculators.qe.inputs import write_pw_text

        text = append_hubbard_card(str(pw_in), structure, dft.dftu)
        write_pw_text(text, in_path)
    else:
        # namelist dialect (default) or non-Hubbard: write PWInput as-is
        write_pw_input(pw_in, in_path)

    cmd = [
        *_mpi_prefix(qe_env, config.nproc),
        qe_env.pw,
        "-in",
        in_path.name,
    ]
    # Label from *base* (input_basename or calculation) so dftu.in/out steps
    # are not logged as "pw.x scf" when calculation remains "scf".
    step_label = {
        "vc-relax": "vc-relax (pw.x)",
        "relax": "relax (pw.x)",
        "scf": "SCF (pw.x)",
        "nscf": "NSCF (pw.x)",
        "dftu": "DFT+U SCF (pw.x)",
    }.get(base, f"pw.x {base}")
    if hubbard and "DFT+U" not in step_label:
        step_label = f"DFT+U {base} (pw.x)"
    rc = _run_cmd(
        cmd,
        cwd=work_dir,
        stdout_path=out_path,
        heartbeat_seconds=_heartbeat_seconds_from_config(config),
        step_label=step_label,
        heartbeat_eta=_heartbeat_eta_enabled(config),
    )
    ok = rc == 0 and out_path.is_file()
    msg = f"pw.x {base} rc={rc}"
    if not ok:
        # Include a short tail for debugging
        try:
            tail = out_path.read_text(encoding="utf-8", errors="replace")[-800:]
            msg += f"\n--- output tail ---\n{tail}"
        except OSError:
            pass
    return QEStepResult(
        name=base,
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
    recover: bool = False,
) -> QEStepResult:
    """Write and run ``ph.x`` in *work_dir* (expects prior pw.x outdir).

    When *for_epw* is True, force multi-q ``ldisp`` with ``fildvscf='dvscf'``
    (required for EPW's ``save/`` preparation via ``pp.py``).

    When *recover* is True, launch with QE ``recover=.true.`` so interrupted
    DFPT can continue from on-disk dyn / ``_ph0`` state (do not clean those
    first). Caller is responsible for recoverability checks.
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
        recover=recover,
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
    rc = _run_cmd(
        cmd,
        cwd=work_dir,
        stdout_path=out_path,
        heartbeat_seconds=_heartbeat_seconds_from_config(config),
        step_label="phonon / DFPT (ph.x)" + (" +EPW-prep" if for_epw else ""),
        heartbeat_eta=_heartbeat_eta_enabled(config),
    )
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
            from siscforge.calculators.qe.epw_recipes import (
                extract_primary_failure_reason,
                log_tail_lines,
            )

            primary = extract_primary_failure_reason(body, step_name="phonon")
            msg = f"ph.x rc={rc}; {primary}"
            tail = log_tail_lines(body, n_lines=40)
            if tail:
                msg += f"\n--- ph.out tail ---\n{tail}"
            msg += f"\nph.out: {out_path}"
        except Exception:  # noqa: BLE001
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


def _skipped_step(
    name: str,
    work_dir: Path,
    *,
    stdout_path: Path | None = None,
    message: str = "",
) -> QEStepResult:
    """Synthetic step result for a checkpoint-skipped stage."""
    out = stdout_path or (work_dir / f"{name}.out")
    return QEStepResult(
        name=name,
        work_dir=work_dir,
        returncode=0,
        stdout_path=out,
        input_path=work_dir / f"{name}.in",
        success=True,
        message=message or f"skip {name} (checkpoint)",
    )


def _should_resume_qe_steps(config: DFTConfig, *, resume_qe_steps: bool | None) -> bool:
    """Whether mid-step workdir checkpoints are enabled."""
    if resume_qe_steps is not None:
        return bool(resume_qe_steps)
    # Prefer RunConfig if attached via private attr; default True for real path
    run = getattr(config, "_run_config", None)
    if run is not None:
        if getattr(run, "force_rerun", False) or getattr(
            run, "force_rerun_qe_steps", False
        ):
            return False
        return bool(getattr(run, "resume_qe_steps", True))
    return True


def _force_qe_steps(config: DFTConfig, *, force_qe_steps: bool | None) -> bool:
    if force_qe_steps is not None:
        return bool(force_qe_steps)
    run = getattr(config, "_run_config", None)
    if run is not None:
        return bool(
            getattr(run, "force_rerun", False)
            or getattr(run, "force_rerun_qe_steps", False)
        )
    return False



def _run_ph_with_optional_recover(
    config: DFTConfig,
    *,
    work_dir: Path,
    scf_dir: Path,
    prefix: str,
    qe_env: QEEnvironment | None,
    for_epw: bool,
    outdir: Path | None,
    log: list[str],
) -> QEStepResult:
    """Run ``ph.x`` with QE-native recover when partial DFPT looks safe.

    Log lines (product-facing):
    - ``resuming DFPT with QE recover=.true.``
    - ``DFPT recover failed or unsafe — full phonon step restart``
    - ``running DFPT / phonon`` (clean full step)
    """
    from siscforge.calculators.qe.qe_checkpoint import (
        assess_phonon_recoverability,
        clean_step_outputs,
        ph_recover_hard_failure,
    )

    rec = assess_phonon_recoverability(work_dir, prefix=prefix)
    if rec.recoverable:
        log.append(f"resuming DFPT with QE recover=.true. ({rec.message})")
        step = run_ph(
            config,
            scf_dir,
            prefix=prefix,
            qe_env=qe_env,
            for_epw=for_epw,
            outdir=outdir,
            recover=True,
        )
        hard_fail = ph_recover_hard_failure(
            step.stdout_path, returncode=step.returncode
        )
        # Also fall back when recover left no useful state and failed.
        if hard_fail or (
            not step.success
            and not assess_phonon_recoverability(work_dir, prefix=prefix).recoverable
        ):
            log.append(
                "DFPT recover failed or unsafe — full phonon step restart"
            )
            clean_step_outputs(work_dir, "phonon", prefix=prefix)
            step = run_ph(
                config,
                scf_dir,
                prefix=prefix,
                qe_env=qe_env,
                for_epw=for_epw,
                outdir=outdir,
                recover=False,
            )
        return step

    # Unrecoverable / no promising artifacts: clean partials if present, full run
    if (scf_dir / "ph.out").exists() or list(scf_dir.glob(f"{prefix}.dyn*")):
        clean_step_outputs(work_dir, "phonon", prefix=prefix)
    log.append("running DFPT / phonon")
    return run_ph(
        config,
        scf_dir,
        prefix=prefix,
        qe_env=qe_env,
        for_epw=for_epw,
        outdir=outdir,
        recover=False,
    )



def _read_step_log(stdout_path: Path | None) -> str:
    if stdout_path is None or not stdout_path.is_file():
        return ""
    try:
        return stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _maybe_retry_phonon_setup(
    config: DFTConfig,
    *,
    structure: Structure,
    work_dir: Path,
    scf_dir: Path,
    prefix: str,
    qe_env: QEEnvironment | None,
    for_epw: bool,
    outdir: Path | None,
    log: list[str],
    step: QEStepResult,
    result: QEWorkflowResult,
) -> tuple[QEStepResult, str]:
    """One guarded SCF(nosym)+ph retry on remediable phonon *setup* failures.

    Covers:

    * PAW ``d_matrix`` / non-orthogonal ``D_S``
      (``dft.phonon_retry_on_d_matrix``, default True)
    * ``phq_setup`` / ``FFT grid incompatible with symmetry``
      (``dft.phonon_retry_on_fft_symmetry``, default True)

    Policy:
    1. Classify failure from ph.out fingerprint.
    2. Clean phonon partials only for this candidate; re-run SCF with
       ``nosym=.true.`` + ``noinv=.true.``.
    3. Re-run phonon once (no recover from the broken DFPT).
    4. Cap = 1 attempt; never invent geometry hacks; never mark success
       without JOB DONE. Setup failure is **not** dynamical instability.

    Returns ``(final_step, ph_out_text)``.
    """
    from siscforge.calculators.qe.epw_recipes import (
        is_d_matrix_failure,
        is_phq_setup_fft_symmetry_failure,
    )
    from siscforge.calculators.qe.qe_checkpoint import clean_step_outputs

    body = _read_step_log(step.stdout_path)
    if step.success:
        return step, body

    reason: str | None = None
    if is_phq_setup_fft_symmetry_failure(body):
        if not getattr(config, "phonon_retry_on_fft_symmetry", True):
            log.append(
                "phonon FFT/symmetry (phq_setup) failure — retry disabled "
                "(dft.phonon_retry_on_fft_symmetry=false)"
            )
            return step, body
        reason = "fft_symmetry"
    elif is_d_matrix_failure(body):
        if not getattr(config, "phonon_retry_on_d_matrix", True):
            log.append(
                "phonon d_matrix failure — retry disabled "
                "(dft.phonon_retry_on_d_matrix=false)"
            )
            return step, body
        reason = "d_matrix"
    else:
        return step, body

    if reason == "fft_symmetry":
        cli = (
            "phonon failed (FFT grid incompatible with symmetry) — "
            "retrying once with nosym+noinv SCF/PH"
        )
        tag = "fft_symmetry"
    else:
        cli = (
            "phonon d_matrix / D_S not orthogonal — one retry: "
            "re-SCF with nosym=.true. noinv=.true. then ph.x"
        )
        tag = "d_matrix"
    log.append(cli)

    # Drop broken DFPT artifacts for this candidate only
    clean_step_outputs(work_dir, "phonon", prefix=prefix)
    # Re-SCF with reduced symmetry (overwrites scf.out + charge density)
    if (scf_dir / "scf.out").is_file():
        clean_step_outputs(work_dir, "scf", prefix=prefix)
    scf_step = run_pw(
        structure,
        config,
        scf_dir,
        calculation="scf",
        prefix=prefix,
        qe_env=qe_env,
        outdir=outdir if for_epw else None,
        extra_system={"nosym": True, "noinv": True},
    )
    result.steps.append(scf_step)
    if scf_step.stdout_path.is_file():
        result.scf = parse_pw_output(
            scf_step.stdout_path, quality_tag=config.quality_tag
        )
    if not scf_step.success:
        log.append(f"{tag} retry: nosym SCF failed — giving up")
        fail = QEStepResult(
            name="ph",
            work_dir=scf_dir,
            returncode=scf_step.returncode,
            stdout_path=scf_step.stdout_path,
            input_path=scf_step.input_path,
            success=False,
            message=(
                f"{tag} recovery SCF (nosym) failed; "
                + (scf_step.message or "")[:400]
            ),
        )
        result.steps.append(fail)
        return fail, _read_step_log(scf_step.stdout_path)

    log.append(f"{tag} retry: running DFPT / phonon after nosym SCF")
    step2 = run_ph(
        config,
        scf_dir,
        prefix=prefix,
        qe_env=qe_env,
        for_epw=for_epw,
        outdir=outdir,
        recover=False,
    )
    result.steps.append(step2)
    body2 = _read_step_log(step2.stdout_path)
    if step2.success:
        log.append(
            f"{tag} retry: phonon succeeded after nosym SCF "
            f"(setup recovery used; not a default physics change for other cells)"
        )
    else:
        log.append(
            f"{tag} retry: phonon still failed after nosym SCF "
            f"(setup failure — not a dynamical-stability conclusion)"
        )
    return step2, body2


def _maybe_retry_phonon_d_matrix(
    config: DFTConfig,
    *,
    structure: Structure,
    work_dir: Path,
    scf_dir: Path,
    prefix: str,
    qe_env: QEEnvironment | None,
    for_epw: bool,
    outdir: Path | None,
    log: list[str],
    step: QEStepResult,
    result: QEWorkflowResult,
) -> tuple[QEStepResult, str]:
    """Alias for :func:`_maybe_retry_phonon_setup` (d_matrix + FFT/symmetry)."""
    return _maybe_retry_phonon_setup(
        config,
        structure=structure,
        work_dir=work_dir,
        scf_dir=scf_dir,
        prefix=prefix,
        qe_env=qe_env,
        for_epw=for_epw,
        outdir=outdir,
        log=log,
        step=step,
        result=result,
    )


def run_relax_scf_phonon(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    resume_qe_steps: bool | None = None,
    force_qe_steps: bool | None = None,
    step_log: list[str] | None = None,
) -> QEWorkflowResult:
    """Execute relax (optional) → SCF → phonon (optional) in *work_dir*.

    This is the local sequential path used by :class:`QECalculator`.
    After ``vc-relax``, the final geometry is parsed from the pw.x output and
    fed into SCF / phonon. Phonon method is selected via ``config.phonon_method``:
    ``dfpt`` / ``gamma`` (ph.x) or ``phonopy_fd`` (optional).

    Mid-step resume: when enabled (default), successful upstream outputs in
    *work_dir* are re-used so a kill during phonon does not re-run relax/SCF.
    Incomplete DFPT that looks recoverable is re-launched with QE
    ``recover=.true.``; otherwise partial phonon outputs are cleaned and the
    step restarts from scratch (safe fallback).
    """
    from siscforge.calculators.qe.qe_checkpoint import (
        clean_step_outputs,
        probe_workdir,
    )

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    use_phonopy = config.do_phonon and config.phonon_method == "phonopy_fd"
    need_ph_binary = config.do_phonon and not use_phonopy
    qe_env = qe_env or require_qe(need_phonon=need_ph_binary)

    result = QEWorkflowResult(work_dir=work_dir, structure=structure)
    current = structure
    log: list[str] = []

    do_resume = _should_resume_qe_steps(config, resume_qe_steps=resume_qe_steps)
    do_force = _force_qe_steps(config, force_qe_steps=force_qe_steps) or not do_resume
    ckpt = probe_workdir(
        work_dir,
        config,
        prefix=prefix,
        structure=structure,
        want_epw=False,
        force=do_force,
    )
    log.extend(ckpt.log)

    # 1. Optional relaxation
    if config.do_relax:
        if ckpt.is_complete("vc-relax"):
            probe = ckpt.steps["vc-relax"]
            current = probe.relaxed_structure or current
            result.relaxed_structure = current
            result.steps.append(
                _skipped_step(
                    "vc-relax",
                    work_dir / "01_relax",
                    stdout_path=probe.stdout_path,
                    message="skip vc-relax (checkpoint)",
                )
            )
            log.append("skip vc-relax (checkpoint)")
        else:
            if (work_dir / "01_relax").exists():
                clean_step_outputs(work_dir, "vc-relax", prefix=prefix)
            log.append("running vc-relax")
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
    elif config.do_relax is False:
        # Still try to reuse a prior relaxed geometry if present
        prior = _try_read_relaxed_structure(work_dir / "01_relax", current)
        if prior is not current:
            current = prior
            result.relaxed_structure = current

    # 2. SCF on (possibly relaxed) geometry
    scf_dir = work_dir / "02_scf"
    if ckpt.is_complete("scf"):
        probe = ckpt.steps["scf"]
        result.scf = probe.scf
        result.steps.append(
            _skipped_step(
                "scf",
                scf_dir,
                stdout_path=probe.stdout_path,
                message="skip SCF (checkpoint)",
            )
        )
        log.append("skip SCF (checkpoint)")
        # Prefer relaxed geometry from checkpoint when available
        if result.relaxed_structure is not None:
            current = result.relaxed_structure
    else:
        if scf_dir.exists():
            clean_step_outputs(work_dir, "scf", prefix=prefix)
        log.append("running SCF")
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
            result.scf = parse_pw_output(
                step.stdout_path, quality_tag=config.quality_tag
            )
        if not step.success:
            result.message = (
                f"SCF failed (pw.x scf).\n{step.message}\n"
                f"Work directory: {scf_dir}"
            )
            result.success = False
            return result

    # 3. Phonons
    if config.do_phonon:
        if ckpt.is_complete("phonon"):
            probe = ckpt.steps["phonon"]
            result.phonon = probe.phonon
            result.steps.append(
                _skipped_step(
                    "phonon",
                    scf_dir,
                    stdout_path=probe.stdout_path,
                    message="skip phonon (checkpoint)",
                )
            )
            log.append("skip phonon (checkpoint)")
        elif use_phonopy:
            from siscforge.calculators.qe.phonopy_fd import (
                PhonopyNotAvailableError,
                run_phonopy_fd,
            )

            log.append("running phonopy_fd")
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
            step = _run_ph_with_optional_recover(
                config,
                work_dir=work_dir,
                scf_dir=scf_dir,
                prefix=prefix,
                qe_env=qe_env,
                for_epw=False,
                outdir=None,
                log=log,
            )
            result.steps.append(step)
            step, ph_body = _maybe_retry_phonon_setup(
                config,
                structure=current,
                work_dir=work_dir,
                scf_dir=scf_dir,
                prefix=prefix,
                qe_env=qe_env,
                for_epw=False,
                outdir=None,
                log=log,
                step=step,
                result=result,
            )
            texts: list[str] = []
            if ph_body:
                texts.append(ph_body)
            elif step.stdout_path is not None and step.stdout_path.is_file():
                try:
                    texts.append(
                        step.stdout_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                    )
                except OSError:
                    pass
            for dyn in sorted(scf_dir.glob(f"{prefix}.dyn*")):
                try:
                    texts.append(dyn.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
            combined = "\n".join(texts) if texts else ""
            if combined:
                # Pass as raw text (never as a path) — multi-KB logs used to
                # raise Errno 36 via Path(log).is_file() inside parse_ph_output.
                result.phonon = parse_ph_output(
                    combined, quality_tag=config.quality_tag
                )
            if not step.success:
                from siscforge.calculators.qe.epw_recipes import (
                    diagnose_qe_step_failure,
                    extract_primary_failure_reason,
                    log_tail_lines,
                    truncate_for_notes,
                )

                primary = extract_primary_failure_reason(
                    combined or step.message, step_name="phonon"
                )
                diag = diagnose_qe_step_failure(
                    combined or step.message,
                    work_dir=work_dir,
                    step_name="phonon",
                    include_tail=True,
                    tail_lines=40,
                )
                result.message = (
                    f"Phonon failed (ph.x): {primary}\n"
                    f"work_dir={work_dir}\n"
                    f"{diag}\n"
                    f"step_message={truncate_for_notes(step.message, max_chars=600)}"
                )
                result.success = False
                return result

    result.success = result.scf is not None and result.scf.status in {"ok", "mock"}
    if result.success and config.do_phonon:
        result.success = result.phonon is not None and result.phonon.status in {
            "ok",
            "mock",
        }
    if result.success:
        if any("skip" in (s.message or "") for s in result.steps):
            result.message = "ok (mid-step resume)"
        else:
            result.message = "ok"
        if log:
            result.message = f"{result.message} [{'; '.join(log)}]"
    else:
        result.message = result.message or "incomplete"
    if step_log is not None:
        step_log.extend(log)
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



# ---------------------------------------------------------------------------
# P3.1 — DFT+U sequential recipe (pw.x Hubbard only; no Wannier/TRIQS)
# ---------------------------------------------------------------------------


def run_dftu_scf(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    calculation: str = "scf",
) -> tuple[QEStepResult, DFTUResult | None]:
    """Run a single pw.x DFT+U calculation and parse :class:`DFTUResult`.

    Writes ``dftu.in`` / ``dftu.out`` under *work_dir*. Does not require
    Wannier90 or TRIQS.
    """
    from siscforge.calculators.qe.dftu import parse_dftu_output

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    qe_env = qe_env or require_qe(need_phonon=False)
    step = run_pw(
        structure,
        config,
        work_dir,
        calculation=calculation,
        prefix=prefix,
        qe_env=qe_env,
        hubbard=True,
        input_basename="dftu",
    )
    dftu_result: DFTUResult | None = None
    if step.stdout_path.is_file():
        dftu_result = parse_dftu_output(
            step.stdout_path,
            dftu=config.dftu,
            structure=structure,
            quality_tag=config.quality_tag,
            extra_raw={
                "input": str(step.input_path),
                "returncode": step.returncode,
                "success": step.success,
            },
        )
        if not step.success and dftu_result.status == "ok":
            dftu_result = dftu_result.model_copy(update={"status": "failed"})
        # Persist fingerprint so resume only skips matching U/J/structure
        if step.success and dftu_result is not None and dftu_result.status == "ok":
            from siscforge.calculators.qe.dftu import write_dftu_config_sidecar

            write_dftu_config_sidecar(
                work_dir,
                structure,
                config.dftu,
                dft=config,
                quality_tag=config.quality_tag,
                stage="scf",
            )
    return step, dftu_result


def run_dftu_workflow(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    resume_qe_steps: bool | None = None,
    force_qe_steps: bool | None = None,
    step_log: list[str] | None = None,
) -> QEWorkflowResult:
    """Sequential DFT+U path: optional relax → SCF+U.

    Intended for infinite-layer / perovskite-like small cells. Phonon and EPW
    are **not** part of this workflow (conventional path remains separate).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    qe_env = qe_env or require_qe(need_phonon=False)
    result = QEWorkflowResult(work_dir=work_dir, structure=structure)
    current = structure
    log: list[str] = step_log if step_log is not None else []

    do_resume = _should_resume_qe_steps(config, resume_qe_steps=resume_qe_steps)
    do_force = _force_qe_steps(config, force_qe_steps=force_qe_steps) or not do_resume

    # Enter the relax stage when global do_relax is on *or* the campaign
    # explicitly requested U-enabled relaxation (do_relax_with_u), even if
    # DFTConfig.do_relax is False (reachable from additive DFT+U callers).
    want_relax = bool(config.do_relax) or bool(
        getattr(config.dftu, "do_relax_with_u", False)
    )
    if want_relax:
        relax_out = work_dir / "vc-relax.out"
        use_u = bool(config.dftu.do_relax_with_u)
        resume_ok = False
        if do_resume and not do_force and relax_out.is_file():
            from siscforge.calculators.qe.dftu import dftu_checkpoint_matches

            resume_ok = dftu_checkpoint_matches(
                work_dir,
                current,
                config.dftu,
                dft=config,
                quality_tag=config.quality_tag,
                out_name="vc-relax.out",
                stage="relax",
                hubbard_on_relax=use_u,
            )
            if not resume_ok:
                log.append(
                    "vc-relax checkpoint present but fingerprint mismatch "
                    "(or missing sidecar) — re-running relax"
                )
        if resume_ok:
            step = _skipped_step(
                "vc-relax",
                work_dir,
                stdout_path=relax_out,
                message="skip vc-relax (checkpoint)",
            )
            result.steps.append(step)
            log.append("skip vc-relax (checkpoint)")
            current = _try_read_relaxed_structure(work_dir, current)
        else:
            step = run_pw(
                current,
                config,
                work_dir,
                calculation="vc-relax",
                prefix=prefix,
                qe_env=qe_env,
                hubbard=use_u,
            )
            result.steps.append(step)
            log.append(step.message)
            if not step.success:
                result.success = False
                result.message = step.message
                if step_log is not None:
                    step_log[:] = log
                return result
            current = _try_read_relaxed_structure(work_dir, current)
            from siscforge.calculators.qe.dftu import write_dftu_config_sidecar

            write_dftu_config_sidecar(
                work_dir,
                structure,
                config.dftu,
                dft=config,
                quality_tag=config.quality_tag,
                stage="relax",
                hubbard_on_relax=use_u,
            )
        result.relaxed_structure = current

    dftu_out = work_dir / "dftu.out"
    resume_dftu = False
    if do_resume and not do_force and dftu_out.is_file():
        from siscforge.calculators.qe.dftu import dftu_checkpoint_matches

        resume_dftu = dftu_checkpoint_matches(
            work_dir,
            current,
            config.dftu,
            dft=config,
            quality_tag=config.quality_tag,
            out_name="dftu.out",
            stage="scf",
        )
        if not resume_dftu and dftu_out.is_file():
            log.append(
                "dftu checkpoint present but config fingerprint mismatch "
                "(or missing sidecar) — re-running DFT+U"
            )
    if resume_dftu:
        from siscforge.calculators.qe.dftu import parse_dftu_output

        step = _skipped_step(
            "dftu",
            work_dir,
            stdout_path=dftu_out,
            message="skip dftu (checkpoint)",
        )
        result.steps.append(step)
        log.append("skip dftu (checkpoint)")
        result.dftu = parse_dftu_output(
            dftu_out,
            dftu=config.dftu,
            structure=current,
            quality_tag=config.quality_tag,
            extra_raw={"resumed": True},
        )
        result.scf = parse_pw_output(dftu_out, quality_tag=config.quality_tag)
        result.success = result.dftu.status in {"ok", "mock"}
        result.message = "DFT+U resumed from checkpoint"
    else:
        step, dftu_res = run_dftu_scf(
            current,
            config,
            work_dir,
            prefix=prefix,
            qe_env=qe_env,
            calculation="scf",
        )
        result.steps.append(step)
        log.append(step.message)
        result.dftu = dftu_res
        if step.stdout_path.is_file():
            result.scf = parse_pw_output(
                step.stdout_path, quality_tag=config.quality_tag
            )
        result.success = bool(step.success and dftu_res and dftu_res.status == "ok")
        result.message = step.message if not result.success else "DFT+U SCF ok"

    if step_log is not None:
        step_log[:] = log
    return result



def run_wannier_after_scf(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    fermi_eV: float | None = None,
    qe_env: QEEnvironment | None = None,
    scf_work_dir: Path | str | None = None,
    step_log: list[str] | None = None,
) -> WannierResult:
    """Run standalone Wannierization after a finished SCF / DFT+U.

    Sacred-upstream contract: *scf_work_dir* (and any finished DFT+U artifacts)
    are never deleted on remediable Wannier failures — same philosophy as
    EPW-after-DFPT. Wannier work lives under *work_dir* (typically a sibling
    ``wannier/`` subdirectory).
    """
    from siscforge.calculators.qe.wannier import run_wannier_workflow

    return run_wannier_workflow(
        structure,
        config,
        work_dir,
        prefix=prefix,
        fermi_eV=fermi_eV,
        qe_env=qe_env,
        scf_work_dir=scf_work_dir,
        step_log=step_log,
    )


def run_dmft_after_wannier(
    config: DFTConfig,
    work_dir: Path | str,
    *,
    wannier: WannierResult | None = None,
    formula: str = "",
    material_family: str = "other",
    seed: str = "dmft",
    step_log: list[str] | None = None,
) -> DMFTResult:
    """Run (or mock / skip) DMFT after a finished Wannier step.

    Sacred-upstream contract: finished SCF / DFT+U / Wannier artifacts are
    never deleted on remediable DMFT failures. DMFT work lives under
    *work_dir* (typically a sibling ``dmft/`` subdirectory).
    """
    from siscforge.calculators.qe.dmft import run_dmft_workflow

    return run_dmft_workflow(
        config,
        work_dir,
        wannier=wannier,
        formula=formula,
        material_family=material_family,
        seed=seed,
        step_log=step_log,
    )


def recipe_info() -> dict[str, Any]:
    """Metadata for documentation / CLI help."""
    return {
        "steps": [
            "vc-relax (optional)",
            "scf",
            "ph.x DFPT (optional)",
            "dftu SCF+U (optional, P3.1)",
            "wannierization (optional, P3.2)",
            "dmft (optional, P3.3)",
        ],
        "jobflow": detect_qe_environment().jobflow,
        "qe": detect_qe_environment().available,
        "engine": "quantum-espresso",
        "models": [
            "SCFResult",
            "PhononResult",
            "DFTUResult",
            "WannierResult",
            "DMFTResult",
        ],
        "extension_points": {
            "p3_2": (
                "Wannierization after SCF/DFT+U (P3.2 + P3.2.1 nscf/pw2wannier90)"
            ),
            "p3_3": (
                "TRIQS/solid_dmft → DMFTResult "
                "(scaffold + controlled launcher: toml/run package + optional "
                "invoke; drop-in parser retained; production U/J/β residual)"
            ),
            "p3_4": "pairing eigenvalue → performance_score",
        },
    }
