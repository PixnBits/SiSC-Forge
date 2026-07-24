"""Environment detection for Quantum ESPRESSO and jobflow."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


class QENotAvailableError(RuntimeError):
    """Raised when a QE run is requested but binaries/pseudos are missing."""


@dataclass(frozen=True)
class QEEnvironment:
    """Resolved paths for QE executables and optional tools."""

    pw: str | None = None
    ph: str | None = None
    q2r: str | None = None
    matdyn: str | None = None
    mpirun: str | None = None
    jobflow: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        """True when at least ``pw.x`` is on PATH."""
        return self.pw is not None

    @property
    def phonon_available(self) -> bool:
        return self.pw is not None and self.ph is not None


def _which(name: str) -> str | None:
    return shutil.which(name)


def detect_qe_environment() -> QEEnvironment:
    """Probe PATH (and ``QE_BIN`` / ``ESPRESSO_PSEUDO`` hints) for QE tools."""
    bin_dir = os.environ.get("QE_BIN") or os.environ.get("QUANTUM_ESPRESSO_BIN")
    notes: list[str] = []

    def resolve(exe: str) -> str | None:
        if bin_dir:
            candidate = Path(bin_dir) / exe
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return _which(exe)

    pw = resolve("pw.x")
    ph = resolve("ph.x")
    q2r = resolve("q2r.x")
    matdyn = resolve("matdyn.x")
    mpirun = _which("mpirun") or _which("mpiexec")

    if not pw:
        notes.append(
            "pw.x not found on PATH. Install Quantum ESPRESSO and/or set QE_BIN."
        )
    if pw and not ph:
        notes.append("ph.x not found; phonon (DFPT) steps will fail.")

    return QEEnvironment(
        pw=pw,
        ph=ph,
        q2r=q2r,
        matdyn=matdyn,
        mpirun=mpirun,
        jobflow=jobflow_available(),
        notes=notes,
    )


def jobflow_available() -> bool:
    """Return True if the optional ``jobflow`` package can be imported."""
    try:
        import jobflow  # noqa: F401

        return True
    except ImportError:
        return False


def qe_available() -> bool:
    """Return True if ``pw.x`` is available."""
    return detect_qe_environment().available


def require_qe(*, need_phonon: bool = True) -> QEEnvironment:
    """Return a validated :class:`QEEnvironment` or raise :class:`QENotAvailableError`."""
    env = detect_qe_environment()
    if not env.available:
        msg = (
            "Quantum ESPRESSO is not available (pw.x not found).\n"
            "Install QE ≥ 7.2, ensure pw.x is on PATH, or set QE_BIN to the bin directory.\n"
            "For dry-run without DFT use: siscforge run --dry-run <campaign.yaml>\n"
            "Or select the mock calculator: --calculator mock"
        )
        if env.notes:
            msg += "\n" + "\n".join(f"  - {n}" for n in env.notes)
        raise QENotAvailableError(msg)
    if need_phonon and not env.phonon_available:
        raise QENotAvailableError(
            "ph.x not found. Phonon DFPT requires ph.x on PATH or under QE_BIN.\n"
            "Install the full Quantum ESPRESSO suite, or set dft.do_phonon: false "
            "for SCF-only runs."
        )
    return env
