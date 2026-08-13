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
    epw: str | None = None
    wannier90: str | None = None
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

    @property
    def epw_available(self) -> bool:
        """True when epw.x is present (wannier90 recommended but optional for detection)."""
        return self.epw is not None


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
    epw = resolve("epw.x")
    wannier90 = resolve("wannier90.x") or _which("wannier90.x")
    mpirun = _which("mpirun") or _which("mpiexec")

    if not pw:
        notes.append(
            "pw.x not found on PATH. Install Quantum ESPRESSO and/or set QE_BIN."
        )
    if pw and not ph:
        notes.append("ph.x not found; phonon (DFPT) steps will fail.")
    if not epw:
        notes.append(
            "epw.x not found; EPW / Eliashberg steps require Quantum ESPRESSO EPW."
        )
    if epw and not wannier90:
        notes.append(
            "wannier90.x not found; EPW typically needs Wannier90 on PATH."
        )

    return QEEnvironment(
        pw=pw,
        ph=ph,
        q2r=q2r,
        matdyn=matdyn,
        epw=epw,
        wannier90=wannier90,
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


def require_qe(*, need_phonon: bool = True, need_epw: bool = False) -> QEEnvironment:
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
    if need_epw and not env.epw_available:
        raise QENotAvailableError(
            "epw.x not found. Electron-phonon (EPW) requires Quantum ESPRESSO built with EPW.\n"
            "Install QE with EPW support, ensure epw.x is on PATH (or set QE_BIN),\n"
            "and typically install Wannier90 (wannier90.x).\n"
            "For dry-run without EPW: siscforge run --dry-run <campaign.yaml>\n"
            "Or disable EPW: dft.do_epw: false / use --calculator qe without EPW."
        )
    return env


class EPWNotAvailableError(QENotAvailableError):
    """Alias for EPW-specific availability failures."""


def epw_available() -> bool:
    """Return True if ``epw.x`` is on PATH."""
    return detect_qe_environment().epw_available


def require_epw() -> QEEnvironment:
    """Require pw.x, ph.x, and epw.x for a full conventional pathway run."""
    return require_qe(need_phonon=True, need_epw=True)


def wannier90_available() -> bool:
    """Return True if ``wannier90.x`` is on PATH / QE_BIN."""
    return detect_qe_environment().wannier90 is not None


def require_wannier90() -> QEEnvironment:
    """Require ``wannier90.x`` for standalone Wannierization (P3.2)."""
    env = detect_qe_environment()
    if not env.wannier90:
        raise QENotAvailableError(
            "wannier90.x not found. Standalone Wannierization (P3.2) requires "
            "Wannier90 on PATH or under QE_BIN.\n"
            "For dry-run without Wannier90: siscforge run --dry-run <campaign.yaml>\n"
            "Or disable: dft.do_wannier: false / dft.wannier.enabled: false"
        )
    return env

