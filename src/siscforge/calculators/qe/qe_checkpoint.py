"""Mid-step QE / EPW checkpoint probes for candidate work directories.

When a desktop run is killed during DFPT or EPW, the candidate workdir often
already has valid upstream artifacts (relaxed geometry, SCF charge density,
dyn files). These helpers **conservatively** detect completed steps by
parsing outputs — missing or unparseable files mean the step is incomplete.

Phonon / DFPT interrupt recovery
--------------------------------
Incomplete ``ph.x`` is **not** always wiped. When on-disk state looks
recoverable (partial dyn mesh, ``_ph0/``, dvscf files, DFPT progress in
``ph.out`` without hard recover errors), recipes re-launch ``ph.x`` with
QE ``recover=.true.`` instead of discarding multi-hour progress.

Conservative recoverable criteria (prefer full restart when unsure)
-------------------------------------------------------------------
All of the following:

1. Phonon step incomplete (no ``JOB DONE`` in ``ph.out``, or no parseable finish).
2. At least one **promising** DFPT artifact under ``02_scf/``:

   - non-empty ``{prefix}.dyn*`` file(s), or
   - non-empty ``_ph0/`` tree, or
   - non-empty ``{prefix}.dvscf*`` / ``dvscf*`` file(s)

3. No **hard recover-unsafe** markers in ``ph.out`` (e.g. ``cannot recover``,
   ``error reading recover``).

Otherwise → clean partial phonon outputs and full step restart.

Limitations
-----------
- Not Folding@home-style mid-iteration pause; granularity is **QE step** +
  QE-native ``recover`` for DFPT only.
- Partial ``ph.out`` with **no** dyn/_ph0/dvscf ⇒ full phonon restart.
- ``force_rerun`` / ``force_rerun_qe_steps`` disables all step skips and recover.
- EPW (``epw.x``) incomplete steps still clean + re-run (no fragile recover path).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pymatgen.core import Structure

from siscforge.calculators.qe.epw_parser import parse_epw_output
from siscforge.calculators.qe.parser import (
    parse_ph_output,
    parse_pw_output,
    parse_relaxed_structure,
)
from siscforge.models.config import DFTConfig
from siscforge.models.results import ElectronPhononResult, PhononResult, SCFResult

StepName = Literal[
    "vc-relax",
    "scf",
    "phonon",
    "epw_pp",
    "nscf",
    "epw",
]

PIPELINE_PHONON: tuple[StepName, ...] = ("vc-relax", "scf", "phonon")
PIPELINE_EPW: tuple[StepName, ...] = (
    "vc-relax",
    "scf",
    "phonon",
    "epw_pp",
    "nscf",
    "epw",
)

# Hard evidence that QE restart state is corrupt — prefer full restart.
_RECOVER_UNSAFE_MARKERS: tuple[str, ...] = (
    "cannot recover",
    "error reading recover",
    "error in routine  read_file_ph",
    "problems reading recover",
)


@dataclass
class StepProbe:
    """Result of probing one pipeline step in a candidate workdir."""

    name: StepName
    complete: bool
    message: str = ""
    scf: SCFResult | None = None
    phonon: PhononResult | None = None
    electron_phonon: ElectronPhononResult | None = None
    relaxed_structure: Structure | None = None
    stdout_path: Path | None = None


@dataclass
class WorkdirCheckpoint:
    """Aggregated step-completion state for one candidate workdir."""

    work_dir: Path
    prefix: str
    steps: dict[StepName, StepProbe] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def is_complete(self, name: StepName) -> bool:
        probe = self.steps.get(name)
        return bool(probe and probe.complete)

    def first_incomplete(
        self,
        pipeline: tuple[StepName, ...],
        *,
        do_relax: bool = True,
        do_phonon: bool = True,
        want_epw: bool = False,
    ) -> StepName | None:
        for name in pipeline:
            if name == "vc-relax" and not do_relax:
                continue
            if name == "phonon" and not do_phonon:
                continue
            if name in {"epw_pp", "nscf", "epw"} and not want_epw:
                continue
            if not self.is_complete(name):
                return name
        return None


@dataclass(frozen=True)
class PhononRecoverability:
    """Whether an incomplete DFPT/phonon step should try QE ``recover=.true.``."""

    recoverable: bool
    reason: str
    artifacts: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        art = f" [{', '.join(self.artifacts)}]" if self.artifacts else ""
        return f"{self.reason}{art}"


def _job_done(text: str) -> bool:
    return "JOB DONE" in text.upper()


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _nonempty_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        try:
            if p.is_file() and p.stat().st_size > 0:
                out.append(p)
        except OSError:
            continue
    return out


def _dir_has_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(p.is_file() for p in path.rglob("*"))
    except OSError:
        return False


def _ph_out_has_unsafe_recover_markers(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _RECOVER_UNSAFE_MARKERS)


def assess_phonon_recoverability(
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
) -> PhononRecoverability:
    """Conservative probe: incomplete DFPT that QE may resume with ``recover=.true.``.

    Prefer **false** (full restart) over trusting a thin or corrupt dyn mesh.
    See module docstring for criteria.
    """
    work_dir = Path(work_dir)
    scf_dir = work_dir / "02_scf"
    if not scf_dir.is_dir():
        return PhononRecoverability(
            recoverable=False,
            reason="no 02_scf workdir — full phonon restart",
        )

    out_path = scf_dir / "ph.out"
    text = _read_text(out_path)

    if text is not None and _job_done(text):
        return PhononRecoverability(
            recoverable=False,
            reason="ph.out already JOB DONE (use step skip, not recover)",
        )

    if text is not None and _ph_out_has_unsafe_recover_markers(text):
        return PhononRecoverability(
            recoverable=False,
            reason="ph.out has recover-unsafe markers — full phonon restart",
        )

    dyn_files = _nonempty_files(sorted(scf_dir.glob(f"{prefix}.dyn*")))
    dvscf_files = _nonempty_files(
        sorted(scf_dir.glob(f"{prefix}.dvscf*")) + sorted(scf_dir.glob("dvscf*"))
    )
    ph0 = scf_dir / "_ph0"
    has_ph0 = _dir_has_files(ph0)

    artifacts: list[str] = []
    if dyn_files:
        artifacts.append(f"dyn×{len(dyn_files)}")
    if has_ph0:
        artifacts.append("_ph0/")
    if dvscf_files:
        artifacts.append(f"dvscf×{len(dvscf_files)}")

    if not artifacts:
        return PhononRecoverability(
            recoverable=False,
            reason=(
                "incomplete phonon without dyn/_ph0/dvscf artifacts "
                "— full phonon restart"
            ),
        )

    return PhononRecoverability(
        recoverable=True,
        reason="incomplete DFPT with promising on-disk artifacts",
        artifacts=tuple(artifacts),
    )


def probe_vc_relax(
    work_dir: Path,
    *,
    quality_tag: str = "screening",
    fallback: Structure | None = None,
) -> StepProbe:
    """Probe optional vc-relax under ``work_dir/01_relax``."""
    relax_dir = work_dir / "01_relax"
    out_path = relax_dir / "vc-relax.out"
    if not out_path.is_file():
        out_path = relax_dir / "relax.out"
    text = _read_text(out_path)
    if text is None:
        return StepProbe(
            name="vc-relax",
            complete=False,
            message="no vc-relax.out",
            stdout_path=out_path if out_path.parent.is_dir() else None,
        )
    if not _job_done(text):
        return StepProbe(
            name="vc-relax",
            complete=False,
            message="vc-relax.out incomplete (no JOB DONE)",
            stdout_path=out_path,
        )
    relaxed = parse_relaxed_structure(out_path, fallback=None)
    if relaxed is None:
        scf = parse_pw_output(out_path, quality_tag=quality_tag)
        if scf.status == "ok" and fallback is not None:
            return StepProbe(
                name="vc-relax",
                complete=True,
                message="vc-relax JOB DONE (geometry parse fallback)",
                scf=scf,
                relaxed_structure=fallback,
                stdout_path=out_path,
            )
        return StepProbe(
            name="vc-relax",
            complete=False,
            message="vc-relax JOB DONE but geometry unparseable",
            stdout_path=out_path,
        )
    scf = parse_pw_output(out_path, quality_tag=quality_tag)
    return StepProbe(
        name="vc-relax",
        complete=True,
        message="vc-relax checkpoint ok",
        scf=scf if scf.status == "ok" else None,
        relaxed_structure=relaxed,
        stdout_path=out_path,
    )


def probe_scf(
    work_dir: Path,
    *,
    quality_tag: str = "screening",
    require_save: bool = True,
) -> StepProbe:
    """Probe SCF under ``work_dir/02_scf/scf.out``.

    When *require_save* is True (phonon/EPW paths), a ``*.save`` directory must
    exist so downstream steps can load the charge density.
    """
    scf_dir = work_dir / "02_scf"
    out_path = scf_dir / "scf.out"
    text = _read_text(out_path)
    if text is None:
        return StepProbe(
            name="scf",
            complete=False,
            message="no scf.out",
            stdout_path=out_path if scf_dir.is_dir() else None,
        )
    if not _job_done(text):
        return StepProbe(
            name="scf",
            complete=False,
            message="scf.out incomplete (no JOB DONE)",
            stdout_path=out_path,
        )
    scf = parse_pw_output(out_path, quality_tag=quality_tag)
    if scf.status != "ok":
        return StepProbe(
            name="scf",
            complete=False,
            message="scf.out JOB DONE but energy unparseable",
            scf=scf,
            stdout_path=out_path,
        )
    if require_save:
        has_save = any(scf_dir.glob("*.save")) or any(
            (scf_dir / "out").glob("*.save") if (scf_dir / "out").is_dir() else []
        )
        if not has_save:
            return StepProbe(
                name="scf",
                complete=False,
                message="scf.out JOB DONE but *.save charge directory missing",
                scf=scf,
                stdout_path=out_path,
            )
    return StepProbe(
        name="scf",
        complete=True,
        message="scf checkpoint ok",
        scf=scf,
        stdout_path=out_path,
    )


def probe_phonon(
    work_dir: Path,
    *,
    prefix: str,
    quality_tag: str = "screening",
    for_epw: bool = False,
    config: DFTConfig | None = None,
) -> StepProbe:
    """Probe phonon completion under ``work_dir/02_scf`` (or phonopy dir)."""
    scf_dir = work_dir / "02_scf"
    use_phonopy = bool(
        config is not None
        and config.do_phonon
        and config.phonon_method == "phonopy_fd"
    )
    if use_phonopy:
        ph_dir = work_dir / "03_phonopy_fd"
        band = ph_dir / "band.yaml"
        text = _read_text(band)
        if text is None:
            return StepProbe(
                name="phonon",
                complete=False,
                message="no phonopy band.yaml",
            )
        ph = parse_ph_output(text, quality_tag=quality_tag)
        ok = ph.status == "ok" and bool(ph.raw.get("frequencies_cm1"))
        return StepProbe(
            name="phonon",
            complete=ok,
            message="phonopy checkpoint ok" if ok else "phonopy incomplete",
            phonon=ph if ok else None,
            stdout_path=band,
        )

    out_path = scf_dir / "ph.out"
    text = _read_text(out_path)
    if text is None:
        return StepProbe(
            name="phonon",
            complete=False,
            message="no ph.out",
            stdout_path=out_path if scf_dir.is_dir() else None,
        )

    # Conservative: require JOB DONE for a completed ph.x run
    if not _job_done(text):
        rec = assess_phonon_recoverability(work_dir, prefix=prefix)
        msg = "ph.out incomplete (no JOB DONE)"
        if rec.recoverable:
            msg = f"{msg} — recoverable DFPT ({rec.message})"
        else:
            msg = f"{msg} — {rec.reason}"
        return StepProbe(
            name="phonon",
            complete=False,
            message=msg,
            stdout_path=out_path,
        )

    texts = [text]
    dyn_files = sorted(scf_dir.glob(f"{prefix}.dyn*"))
    if for_epw:
        dyn_points = [d for d in dyn_files if d.name != f"{prefix}.dyn0"]
        if not dyn_points:
            return StepProbe(
                name="phonon",
                complete=False,
                message="ph JOB DONE but missing dyn mesh files for EPW",
                stdout_path=out_path,
            )
        for dyn in dyn_files:
            try:
                texts.append(dyn.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue

    combined = "\n".join(texts)
    ph = parse_ph_output(combined, quality_tag=quality_tag)
    # For EPW multi-q, JOB DONE + dyn files is enough even if freq parse is thin
    if for_epw and dyn_files:
        return StepProbe(
            name="phonon",
            complete=True,
            message="phonon checkpoint ok (JOB DONE + dyn files)",
            phonon=ph if ph.status == "ok" else ph,
            stdout_path=out_path,
        )
    if ph.status != "ok":
        return StepProbe(
            name="phonon",
            complete=False,
            message="ph.out JOB DONE but frequencies unparseable",
            phonon=ph,
            stdout_path=out_path,
        )
    return StepProbe(
        name="phonon",
        complete=True,
        message="phonon checkpoint ok",
        phonon=ph,
        stdout_path=out_path,
    )


def probe_epw_pp(work_dir: Path, *, prefix: str) -> StepProbe:
    """Probe EPW ``pp.py`` ``save/`` directory under flat scf workdir."""
    scf_dir = work_dir / "02_scf"
    save_dir = scf_dir / "save"
    if not save_dir.is_dir():
        return StepProbe(
            name="epw_pp",
            complete=False,
            message="no save/ directory (pp.py not done)",
        )
    n_files = sum(1 for p in save_dir.rglob("*") if p.is_file())
    if n_files < 1:
        return StepProbe(
            name="epw_pp",
            complete=False,
            message="save/ exists but is empty",
        )
    return StepProbe(
        name="epw_pp",
        complete=True,
        message=f"epw_pp checkpoint ok ({n_files} files in save/)",
    )


def probe_nscf(
    work_dir: Path,
    *,
    quality_tag: str = "screening",
) -> StepProbe:
    """Probe EPW NSCF under ``work_dir/02_scf/nscf.out``."""
    scf_dir = work_dir / "02_scf"
    out_path = scf_dir / "nscf.out"
    text = _read_text(out_path)
    if text is None:
        return StepProbe(
            name="nscf",
            complete=False,
            message="no nscf.out",
            stdout_path=out_path if scf_dir.is_dir() else None,
        )
    if not _job_done(text):
        return StepProbe(
            name="nscf",
            complete=False,
            message="nscf.out incomplete (no JOB DONE)",
            stdout_path=out_path,
        )
    scf = parse_pw_output(out_path, quality_tag=quality_tag)
    if scf.status != "ok":
        return StepProbe(
            name="nscf",
            complete=False,
            message="nscf.out JOB DONE but energy unparseable",
            scf=scf,
            stdout_path=out_path,
        )
    return StepProbe(
        name="nscf",
        complete=True,
        message="nscf checkpoint ok",
        scf=scf,
        stdout_path=out_path,
    )


def probe_epw(
    work_dir: Path,
    *,
    quality_tag: str = "screening",
    mu_star: float = 0.1,
) -> StepProbe:
    """Probe EPW completion under ``work_dir/02_scf/epw.out``."""
    scf_dir = work_dir / "02_scf"
    out_path = scf_dir / "epw.out"
    text = _read_text(out_path)
    if text is None:
        return StepProbe(
            name="epw",
            complete=False,
            message="no epw.out",
            stdout_path=out_path if scf_dir.is_dir() else None,
        )
    if not _job_done(text):
        return StepProbe(
            name="epw",
            complete=False,
            message="epw.out incomplete (no JOB DONE) — will re-run EPW step",
            stdout_path=out_path,
        )
    eph = parse_epw_output(out_path, mu_star=mu_star, quality_tag=quality_tag)
    ok = eph.status == "ok" and (
        eph.lambda_total is not None or eph.best_tc_K() is not None
    )
    if not ok:
        return StepProbe(
            name="epw",
            complete=False,
            message="epw.out JOB DONE but λ/Tc unparseable",
            electron_phonon=eph,
            stdout_path=out_path,
        )
    return StepProbe(
        name="epw",
        complete=True,
        message="epw checkpoint ok",
        electron_phonon=eph,
        stdout_path=out_path,
    )


def probe_workdir(
    work_dir: Path | str,
    config: DFTConfig,
    *,
    prefix: str = "siscforge",
    structure: Structure | None = None,
    want_epw: bool | None = None,
    force: bool = False,
) -> WorkdirCheckpoint:
    """Probe a candidate workdir and return completed-step state.

    Conservative: incomplete or unparseable outputs ⇒ step not complete.
    When *force* is True, all steps are reported incomplete (no skips).
    """
    work_dir = Path(work_dir).resolve()
    want = (
        bool(want_epw)
        if want_epw is not None
        else bool(config.do_epw or config.epw.enabled)
    )
    qtag = config.quality_tag
    ckpt = WorkdirCheckpoint(work_dir=work_dir, prefix=prefix)
    log_lines: list[str] = []

    pipeline = PIPELINE_EPW if want else PIPELINE_PHONON
    active: list[StepName] = []
    for name in pipeline:
        if name == "vc-relax" and not config.do_relax:
            continue
        if name == "phonon" and not config.do_phonon:
            continue
        if name in {"epw_pp", "nscf", "epw"} and not want:
            continue
        active.append(name)

    if force:
        for name in active:
            ckpt.steps[name] = StepProbe(
                name=name, complete=False, message="force_rerun_qe_steps"
            )
            log_lines.append(f"force: no checkpoint skip for {name}")
        ckpt.log = log_lines
        return ckpt

    if config.do_relax:
        probe = probe_vc_relax(work_dir, quality_tag=qtag, fallback=structure)
        ckpt.steps["vc-relax"] = probe
        log_lines.append(
            f"skip vc-relax (checkpoint): {probe.message}"
            if probe.complete
            else f"incomplete vc-relax: {probe.message}"
        )
    else:
        log_lines.append("vc-relax disabled in config")

    need_save = bool(config.do_phonon or want)
    probe = probe_scf(work_dir, quality_tag=qtag, require_save=need_save)
    ckpt.steps["scf"] = probe
    log_lines.append(
        f"skip SCF (checkpoint): {probe.message}"
        if probe.complete
        else f"incomplete SCF: {probe.message}"
    )

    if config.do_phonon:
        probe = probe_phonon(
            work_dir,
            prefix=prefix,
            quality_tag=qtag,
            for_epw=want,
            config=config,
        )
        ckpt.steps["phonon"] = probe
        log_lines.append(
            f"skip phonon (checkpoint): {probe.message}"
            if probe.complete
            else f"incomplete phonon: {probe.message}"
        )
    else:
        log_lines.append("phonon disabled in config")

    if want:
        probe = probe_epw_pp(work_dir, prefix=prefix)
        ckpt.steps["epw_pp"] = probe
        log_lines.append(
            f"skip epw_pp (checkpoint): {probe.message}"
            if probe.complete
            else f"incomplete epw_pp: {probe.message}"
        )

        probe = probe_nscf(work_dir, quality_tag=qtag)
        ckpt.steps["nscf"] = probe
        log_lines.append(
            f"skip nscf (checkpoint): {probe.message}"
            if probe.complete
            else f"incomplete nscf: {probe.message}"
        )

        probe = probe_epw(
            work_dir,
            quality_tag=qtag,
            mu_star=float(config.epw.mu_star),
        )
        ckpt.steps["epw"] = probe
        log_lines.append(
            f"skip epw (checkpoint): {probe.message}"
            if probe.complete
            else f"incomplete epw: {probe.message}"
        )

    ckpt.log = log_lines
    return ckpt


def ph_recover_hard_failure(stdout_path: Path | None, *, returncode: int) -> bool:
    """True when a recover=.true. ph.x run should fall back to clean + full restart.

    Incomplete-but-interrupted (no JOB DONE, no hard error) is **not** a hard
    failure — leave artifacts so the next re-run can try recover again.
    """
    text = _read_text(stdout_path) if stdout_path is not None else None
    if text is not None and _ph_out_has_unsafe_recover_markers(text):
        return True
    if text is not None and _job_done(text):
        return False
    # Non-zero without JOB DONE: only hard-fail if QE reported an error-like exit
    # and there is no remaining promising state (caller also re-checks recoverability).
    if int(returncode) != 0 and text is not None:
        low = text.lower()
        if "error" in low or "abort" in low or "stopped" in low:
            # Still prefer re-probe; hard only when clearly not recoverable next time
            return True
    return False


def clean_step_outputs(
    work_dir: Path,
    step: StepName,
    *,
    prefix: str = "siscforge",
) -> list[Path]:
    """Remove partial outputs for a single incomplete step (not upstream)."""
    work_dir = Path(work_dir)
    removed: list[Path] = []

    def _rm(path: Path) -> None:
        if path.is_file():
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass

    if step == "vc-relax":
        for name in ("vc-relax.out", "relax.out"):
            _rm(work_dir / "01_relax" / name)
    elif step == "scf":
        _rm(work_dir / "02_scf" / "scf.out")
    elif step == "phonon":
        scf_dir = work_dir / "02_scf"
        _rm(scf_dir / "ph.out")
        for dyn in scf_dir.glob(f"{prefix}.dyn*"):
            _rm(dyn)
        # Partial multi-q DFPT for EPW: remove _ph0 and dvscf leftovers
        ph0 = scf_dir / "_ph0"
        if ph0.is_dir():
            try:
                shutil.rmtree(ph0)
                removed.append(ph0)
            except OSError:
                pass
        for dv in scf_dir.glob(f"{prefix}.dvscf*"):
            _rm(dv)
        for dv in scf_dir.glob("dvscf*"):
            _rm(dv)
        ph_dir = work_dir / "03_phonopy_fd"
        if ph_dir.is_dir():
            for p in ph_dir.glob("*"):
                if p.is_file():
                    _rm(p)
    elif step == "epw_pp":
        save = work_dir / "02_scf" / "save"
        if save.is_dir():
            try:
                shutil.rmtree(save)
                removed.append(save)
            except OSError:
                pass
    elif step == "nscf":
        _rm(work_dir / "02_scf" / "nscf.out")
    elif step == "epw":
        _rm(work_dir / "02_scf" / "epw.out")
    return removed
