"""EPW workflow steps on top of relax → SCF → phonon."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pymatgen.core import Structure

from siscforge.calculators.qe.eliashberg import (
    allen_dynes_tc,
    isotropic_eliashberg_tc_from_moments,
    performance_score_from_epw,
)
from siscforge.calculators.qe.env import QEEnvironment, require_epw
from siscforge.calculators.qe.epw_inputs import (
    apply_coarse_k_to_config,
    build_epw_input,
    next_coarse_k_after_bvector_failure,
    preflight_epw_grids,
    write_epw_input,
)
from siscforge.calculators.qe.epw_parser import parse_epw_output
from siscforge.calculators.qe.inputs import build_nscf_epw_input
from siscforge.calculators.qe.parser import parse_ph_output, parse_pw_output
from siscforge.calculators.qe.recipes import (
    QEStepResult,
    QEWorkflowResult,
    run_pw,
)
from siscforge.models.config import DFTConfig
from siscforge.models.results import ElectronPhononResult

# Workdir sidecar: track EPW k-mesh remediation so resume does not infinite-loop.
_EPW_REMEDIATION_JSON = "siscforge_epw_remediation.json"

EPWFailureClass = Literal[
    "kmesh_bvector",
    "kgrid_inconsistency",
    "frozen_window",
    "nbndsub",
    "parallel",
    "missing_files",
    "fermi",
    "d_matrix",
    "soft_modes",
    "other",
]


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
    from siscforge.calculators.qe.recipes import (
        _heartbeat_eta_enabled,
        _heartbeat_seconds_from_config,
        _mpi_prefix,
        _run_cmd,
    )

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
    rc = _run_cmd(
        cmd,
        cwd=work_dir,
        stdout_path=out_path,
        heartbeat_seconds=_heartbeat_seconds_from_config(config),
        step_label="nscf (pw.x, EPW prep)",
        heartbeat_eta=_heartbeat_eta_enabled(config),
    )
    ok = rc == 0 and out_path.is_file()
    msg = f"pw.x nscf (EPW) rc={rc}"
    if not ok:
        try:
            tail = out_path.read_text(encoding="utf-8", errors="replace")[-800:]
            msg += f"\n--- output tail ---\n{tail}"
        except OSError:
            pass
    if ok:
        # Fingerprint requested coarse mesh so resume can detect nkc bumps.
        try:
            from siscforge.calculators.qe.qe_checkpoint import write_nscf_kmesh_sidecar

            nkc = list(dft.epw.nkc or dft.kpoints or [4, 4, 4])
            write_nscf_kmesh_sidecar(work_dir, nkc)
        except Exception:  # noqa: BLE001
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


# Common EPW / Wannier failure fingerprints → (short CLI label, remediation)
# Order matters: more specific patterns first.
_EPW_FAILURE_HINTS: list[tuple[str, str, str]] = [
    (
        "kmesh_get_bvector",
        "EPW Wannier: kmesh_get_bvector — not enough bvectors (coarse k too sparse)",
        "Raise epw.nkc (6→8→12) and re-run NSCF+epw only — do NOT redo DFPT. "
        "SiSC-Forge auto-retries EPW-only when auto_retry_kmesh is true.",
    ),
    (
        "not enough bvectors",
        "EPW Wannier: not enough bvectors on coarse k-mesh",
        "Coarse electronic k (nk1–3 / nkc) is too sparse for Wannier90. "
        "Raise nkc to ≥8³ on ≥8-atom cells; re-NSCF + epw only (keep phonon).",
    ),
    (
        "more states in the frozen window than target",
        "EPW Wannier: frozen window has more states than nbndsub",
        "Raise epw.nbndsub (e.g. min(nbnd, max(16, nbnd//2))) and/or tighten "
        "dis_froz_* around E_F. Screening auto_nbndsub + wannier_retry should "
        "handle this; production needs hand-tuned projections.",
    ),
    (
        "more states in the frozen window than target wfs",
        "EPW Wannier: frozen window has more states than nbndsub",
        "Raise epw.nbndsub and/or tighten dis_froz window (dis_windows error).",
    ),
    (
        "dis_windows",
        "EPW Wannier: dis_windows frozen-window error",
        "Frozen window inconsistent with nbndsub — increase nbndsub or narrow "
        "dis_froz_min/max around E_F.",
    ),
    (
        "cannot bracket",
        "EPW: cannot bracket Fermi level after Wannier",
        "Ensure dis_win_* brackets E_F (SiSC-Forge sets windows from nscf/scf) "
        "and try efermi_read / denser nkf.",
    ),
    (
        "efermig",
        "EPW: fine-mesh Fermi search failed (efermig)",
        "Pin fermi_energy from DFT (efermi_read) or widen fsthick / denser nkf.",
    ),
    (
        "error in routine d_matrix",
        "QE phonon: d_matrix — D_S (l=2) symmetry not orthogonal",
        "PAW + crystal symmetry mismatch after strain/relax. Auto-retry may re-SCF "
        "with nosym/noinv (dft.phonon_retry_on_d_matrix). Else: tighten vc-relax, "
        "try nearby strain, or check lattice noise in CIF.",
    ),
    (
        "d_matrix",
        "QE phonon: d_matrix — D_S symmetry not orthogonal",
        "PAW symmetry representation failed (often D_S l=2 not orthogonal). "
        "Enable dft.phonon_retry_on_d_matrix (default) for one nosym SCF+ph retry; "
        "or re-relax / shift strain slightly.",
    ),
    (
        "not orthogonal",
        "QE phonon: symmetry representation not orthogonal",
        "Usually d_matrix/D_S after strained DFPT. See phonon_retry_on_d_matrix.",
    ),
    (
        "d_s (l=",
        "QE phonon: D_S(l) symmetry matrix not orthogonal",
        "PAW d_matrix failure class — retry with nosym SCF or cleaner geometry.",
    ),
    (
        "error in routine dafopen",
        "EPW: missing phonon/dvscf files (dafopen)",
        "Confirm multi-q DFPT wrote dyn* + fildvscf and pp.py created save/.",
    ),
    (
        "error opening",
        "EPW: missing prerequisite files",
        "Check save/, *.save, and nscf wavefunctions (flat outdir layout).",
    ),
    (
        "not enough bands",
        "EPW/QE: not enough bands",
        "Increase dft.nbnd and epw.nbndsub for metals + Wannier.",
    ),
    (
        "nbndsub",
        "EPW: nbndsub / Wannier band count issue",
        "Set epw.nbndsub consistently with bands in the Wannier window "
        "(screening auto: min(nbnd, max(16, 4*n_at, nbnd//2))).",
    ),
    (
        "proj(1)",
        "EPW: Wannier projection / random proj issue",
        "Screening uses proj=random — production needs material-specific projs.",
    ),
    (
        "number of processes must be equal",
        "EPW parallel: nproc ≠ npool×nimage",
        "Set epw.npool=dft.nproc (nimage=1). SiSC-Forge auto-sets unless "
        "strict_parallel is true.",
    ),
    (
        "number of pools and number of images",
        "EPW parallel: nproc ≠ npool×nimage",
        "Set epw.npool=dft.nproc (nimage=1).",
    ),
    (
        "k-grid",
        "EPW: k-grid inconsistency",
        "nscf crystal mesh must match epw nk1–nk3 (nkc). "
        "SiSC-Forge invalidates stale NSCF when nkc changes and retries EPW-only.",
    ),
    (
        "k-point",
        "EPW: k-grid inconsistency",
        "nscf crystal mesh must match epw nk1–nk3 (nkc).",
    ),
    (
        "error reading xml",
        "EPW: fatal XML read (often stale NSCF after nkc change)",
        "Rebuild NSCF at current nkc then re-run epw — DFPT kept. "
        "Do not manually rm nscf.out; resume auto-invalidates on mesh mismatch.",
    ),
    (
        "reading xml file",
        "EPW: XML/save read failure after k-mesh change",
        "Usually nscf wavefunctions from a different coarse k. Re-NSCF + epw only.",
    ),
    (
        "imaginary",
        "Phonon soft/imaginary modes",
        "Raise eps_acustic, improve relaxation, or denser DFPT q-mesh.",
    ),
    (
        "segmentation",
        "EPW segfault",
        "Try nproc=1/npool=1 or re-relax; check QE/EPW vs Wannier90 versions.",
    ),
    (
        "wannier",
        "EPW Wannierization issue",
        "Screening uses proj=random — raise nbndsub / tighten freeze window, "
        "or set material-specific projections for production.",
    ),
    (
        "%% error",
        "QE fatal error",
        "See output tail and workdir logs.",
    ),
]


def is_frozen_window_overflow(text: str | None) -> bool:
    """True if Wannier90 reports frozen window has more states than target WFs."""
    if not text:
        return False
    blob = text.lower()
    return (
        "more states in the frozen window than target" in blob
        or ("dis_windows" in blob and "frozen" in blob)
        or ("frozen window" in blob and "target" in blob and "wf" in blob)
    )


def is_kmesh_bvector_failure(text: str | None) -> bool:
    """True if Wannier90 failed with kmesh_get_bvector / not enough bvectors."""
    if not text:
        return False
    blob = text.lower()
    return (
        "kmesh_get_bvector" in blob
        or "not enough bvectors" in blob
        or ("bvector" in blob and "not enough" in blob)
    )


def is_kgrid_inconsistency(text: str | None) -> bool:
    """True if EPW reports nscf / epw nk mesh mismatch (or related XML fail).

    Covers explicit k-grid inconsistency messages and the common follow-on
    fatal XML/save read that appears when epw nk ≠ nscf crystal mesh.
    """
    if not text:
        return False
    blob = text.lower()
    if "k-grid" in blob and ("inconsist" in blob or "mismatch" in blob):
        return True
    if "k-point" in blob and (
        "inconsist" in blob or "does not match" in blob or "must match" in blob
    ):
        return True
    if "k point" in blob and (
        "inconsist" in blob or "does not match" in blob or "must match" in blob
    ):
        return True
    if "number of k" in blob and ("nscf" in blob or "inconsist" in blob):
        return True
    if "nk1" in blob and ("match" in blob or "inconsist" in blob):
        return True
    # XML / save read fatals that commonly follow a stale nscf mesh after nkc raise
    xmlish = (
        "error reading xml" in blob
        or "error while reading xml" in blob
        or "fatal error reading xml" in blob
        or "reading xml file" in blob
    )
    if xmlish and (
        "k" in blob or "save" in blob or "nscf" in blob or "wannier" in blob
    ):
        return True
    if "error in routine" in blob and "xml" in blob and (
        "k-grid" in blob or "kmesh" in blob or "nscf" in blob or "save" in blob
    ):
        return True
    return False


def is_d_matrix_failure(text: str | None) -> bool:
    """True if ph.x / QE failed with PAW d_matrix / non-orthogonal D_S."""
    if not text:
        return False
    blob = text.lower()
    if "d_matrix" in blob:
        return True
    if "not orthogonal" in blob and ("d_s" in blob or "symmetry" in blob):
        return True
    if "error in routine d_matrix" in blob:
        return True
    return False


def classify_epw_failure(text: str | None) -> EPWFailureClass:
    """Map failure text to a remediable / non-remediable class."""
    if is_kmesh_bvector_failure(text):
        return "kmesh_bvector"
    if is_kgrid_inconsistency(text):
        return "kgrid_inconsistency"
    if is_frozen_window_overflow(text):
        return "frozen_window"
    if is_d_matrix_failure(text):
        return "d_matrix"
    blob = (text or "").lower()
    if "nbndsub" in blob or "not enough bands" in blob:
        return "nbndsub"
    if "number of processes must be equal" in blob or "number of pools" in blob:
        return "parallel"
    if "dafopen" in blob or "error opening" in blob:
        return "missing_files"
    if "cannot bracket" in blob or "efermig" in blob:
        return "fermi"
    if "imaginary" in blob:
        return "soft_modes"
    return "other"


def is_remediable_kmesh_failure(text: str | None) -> bool:
    """Failures that justify EPW-only coarse-k retry after DFPT."""
    cls = classify_epw_failure(text)
    return cls in {"kmesh_bvector", "kgrid_inconsistency"}


def truncate_for_notes(text: str | None, *, max_chars: int = 1200) -> str:
    """Cap log/exception blobs for evaluation.notes and errors (never path-sized)."""
    if not text:
        return ""
    s = str(text)
    if len(s) <= max_chars:
        return s
    # Prefer keeping the tail (error usually at end)
    head = 200
    tail = max_chars - head - 40
    return s[:head] + "\n…[truncated]…\n" + s[-tail:]


def log_tail_lines(text: str | None, *, n_lines: int = 40) -> str:
    """Return last *n_lines* of a log as a short string."""
    if not text:
        return ""
    lines = str(text).splitlines()
    tail = lines[-n_lines:] if len(lines) > n_lines else lines
    return "\n".join(tail)


def diagnose_qe_step_failure(
    text: str | None,
    *,
    work_dir: Path | str | None = None,
    step_name: str = "phonon",
    include_tail: bool = True,
    tail_lines: int = 40,
) -> str:
    """Diagnostic block for failed pw.x / ph.x / epw.x steps (path-safe)."""
    parts: list[str] = [f"[{step_name}] QE diagnostic"]
    primary = extract_primary_failure_reason(text, step_name=step_name)
    parts.append(f"  primary: {primary}")
    if work_dir is not None:
        wd = Path(work_dir)
        parts.append(f"  work_dir: {wd}")
        for name in (
            "ph.out",
            "ph.in",
            "scf.out",
            "vc-relax.out",
            "epw.out",
            "nscf.out",
        ):
            # Prefer files under work_dir or 02_scf
            candidates = [wd / name, wd / "02_scf" / name, wd / "01_relax" / name]
            for p in candidates:
                if p.is_file():
                    try:
                        size = p.stat().st_size
                    except OSError:
                        size = -1
                    parts.append(f"  present: {p.name} ({size} bytes) @ {p.parent.name}/")
                    break
    blob = (text or "").lower()
    hits: list[str] = []
    if blob:
        for needle, _short, hint in _EPW_FAILURE_HINTS:
            if needle.lower() in blob:
                hits.append(f"  · matched '{needle}': {hint}")
        if not hits:
            hits.append(
                "  · no known fingerprint — inspect ph.out / scf.out tail in work_dir."
            )
    else:
        hits.append("  · no output text available to scan.")
    parts.append("hints:")
    parts.extend(hits)
    if is_d_matrix_failure(text):
        parts.append(
            "  · d_matrix remediation: dft.phonon_retry_on_d_matrix (auto nosym SCF+ph); "
            "or tighten relax / change strain / clean lattice noise."
        )
    if include_tail and text:
        lines = str(text).splitlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        if tail:
            parts.append("  --- output tail ---")
            parts.extend(f"  {ln}" for ln in tail[:tail_lines])
    return "\n".join(parts)



def extract_primary_failure_reason(
    text: str | None,
    *,
    step_name: str = "epw",
    max_len: int = 120,
) -> str:
    """One-line primary reason for CLI progress (no file open required)."""
    if not text or not str(text).strip():
        return f"{step_name}: failed (no output text)"
    blob = text.lower()
    for needle, short, _hint in _EPW_FAILURE_HINTS:
        if needle.lower() in blob:
            msg = short
            if len(msg) > max_len:
                msg = msg[: max_len - 1] + "…"
            return msg
    # Generic: first Error / %%% line
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if "error" in low or "abort" in low or "fatal" in low:
            if len(s) > max_len:
                s = s[: max_len - 1] + "…"
            return f"{step_name}: {s}"
    # Fall back to last non-empty line of a short tail
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    if lines:
        s = lines[-1]
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        return f"{step_name}: {s}"
    return f"{step_name}: failed"


def diagnose_epw_failure(
    text: str | None,
    *,
    work_dir: Path | str | None = None,
    step_name: str = "epw",
    include_tail: bool = True,
    tail_lines: int = 30,
) -> str:
    """Return a multi-line diagnostic string for failed Wannier/EPW steps.

    Scans *text* (typically ``epw.out`` or a step message) for known fingerprints
    and appends workdir / quality_tag guidance. Safe for missing files.
    """
    parts: list[str] = [f"[{step_name}] EPW/Wannier diagnostic"]
    primary = extract_primary_failure_reason(text, step_name=step_name)
    parts.append(f"  primary: {primary}")
    cls = classify_epw_failure(text)
    parts.append(f"  class: {cls}")

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
        for needle, _short, hint in _EPW_FAILURE_HINTS:
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
    if cls == "kmesh_bvector":
        parts.append(
            "  · remediation: EPW-only retry with denser nkc (6→8→12); DFPT reused. "
            "Do not --force-rerun just for this error."
        )
    if cls == "kgrid_inconsistency":
        parts.append(
            "  · remediation: invalidate/rebuild NSCF at current nkc then epw "
            "(phonon kept). No manual rm of nscf.out required."
        )
    parts.append(
        "  · screening: enable auto_nbndsub (default) and "
        "wannier_retry_on_froz_overflow; raise epw.nbndsub if retry fails."
    )
    parts.append(
        "  · denser grids: raise epw.nkf/nqf and dft.qpoints (nqc must match DFPT)."
    )
    parts.append(
        "  · docs: docs/examples/nbN_epw.md, docs/examples/desktop_shortlist_epw.md"
    )

    if include_tail and text:
        lines = str(text).splitlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        if tail:
            parts.append("  --- output tail ---")
            parts.extend(f"  {ln}" for ln in tail)

    return "\n".join(parts)


def _output_tail(path: Path, n_chars: int = 1600) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n_chars:]
    except OSError:
        return ""


def resolve_epw_launch_topology(
    config: DFTConfig,
) -> tuple[DFTConfig, str]:
    """Return config with a valid EPW (nproc, npool) topology and a log line.

    Auto-sets ``epw.npool = nproc`` (nimage=1) when needed unless
    ``epw.strict_parallel`` is True (then raises ``ValueError``).
    """
    from siscforge.calculators.qe.epw_parallel import resolve_epw_parallel

    nproc = max(1, int(config.nproc))
    npool = max(1, int(config.epw.npool))
    strict = bool(getattr(config.epw, "strict_parallel", False))
    plan = resolve_epw_parallel(
        nproc,
        npool,
        nimage=1,
        fine_grid=True,
        auto_fix=not strict,
    )
    if not plan.ok:
        raise ValueError(plan.message)

    cfg = config
    if plan.npool != config.epw.npool:
        cfg = config.model_copy(
            update={"epw": config.epw.model_copy(update={"npool": plan.npool})}
        )
    return cfg, plan.message


# ---------------------------------------------------------------------------
# EPW remediation persistence (resume-safe; never touches phonon artifacts)
# ---------------------------------------------------------------------------


def load_epw_remediation_state(work_dir: Path) -> dict[str, Any]:
    """Load attempt notes from workdir sidecar (empty if missing)."""
    path = Path(work_dir) / _EPW_REMEDIATION_JSON
    if not path.is_file():
        return {"attempts": [], "version": 1}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"attempts": [], "version": 1}
        data.setdefault("attempts", [])
        return data
    except (OSError, json.JSONDecodeError):
        return {"attempts": [], "version": 1}


def save_epw_remediation_state(work_dir: Path, state: dict[str, Any]) -> Path:
    """Persist remediation attempts (for resume / anti-loop)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / _EPW_REMEDIATION_JSON
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def record_epw_remediation_attempt(
    work_dir: Path,
    *,
    reason: str,
    nkc_before: list[int],
    nkc_after: list[int],
    note: str = "",
) -> dict[str, Any]:
    state = load_epw_remediation_state(work_dir)
    attempts = list(state.get("attempts") or [])
    attempts.append(
        {
            "reason": reason,
            "nkc_before": list(nkc_before),
            "nkc_after": list(nkc_after),
            "note": note,
        }
    )
    state["attempts"] = attempts
    save_epw_remediation_state(work_dir, state)
    return state


def _archive_epw_attempt(work_dir: Path, attempt_idx: int) -> None:
    """Rename epw.out → epw.attemptN.out so the next launch is clean."""
    work_dir = Path(work_dir)
    first_out = work_dir / "epw.out"
    if first_out.is_file():
        dest = work_dir / f"epw.attempt{attempt_idx}.out"
        try:
            if dest.is_file():
                dest.unlink()
            first_out.rename(dest)
        except OSError:
            pass


def _clean_nscf_and_epw_only(work_dir: Path, prefix: str = "siscforge") -> None:
    """Remove NSCF + EPW outputs only — **never** phonon / dyn / _ph0 / save prep
    that depends on DFPT. save/ is kept (from pp.py after DFPT).

    When coarse k changes, nscf wavefunctions must be regenerated; epw.out is
    archived separately. Phonon artifacts are sacred.
    """
    from siscforge.calculators.qe.qe_checkpoint import invalidate_nscf_epw_for_kmesh

    invalidate_nscf_epw_for_kmesh(
        work_dir,
        prefix=prefix,
        reason="cleaning NSCF/EPW for denser or corrected coarse k (phonon reused)",
        clear_wannier=True,
    )


def plan_kmesh_remediation(
    config: DFTConfig,
    failure_text: str | None,
    *,
    work_dir: Path | str | None = None,
) -> tuple[DFTConfig, str] | None:
    """If failure is remediable, return (new_config, cli_log_line) else None.

    Caps retries via ``epw.max_kmesh_retries`` and workdir attempt log.
    Does **not** change nqc / qpoints.
    """
    if not bool(getattr(config.epw, "auto_retry_kmesh", True)):
        return None
    if not is_remediable_kmesh_failure(failure_text):
        return None

    max_retries = int(getattr(config.epw, "max_kmesh_retries", 2) or 2)
    attempts_done = 0
    if work_dir is not None:
        state = load_epw_remediation_state(Path(work_dir))
        attempts_done = len(state.get("attempts") or [])
    if attempts_done >= max_retries:
        return None

    nkc_now = list(config.epw.nkc or [6, 6, 6])
    nkc_next = next_coarse_k_after_bvector_failure(nkc_now, attempt=attempts_done)
    if nkc_next is None:
        return None

    cls = classify_epw_failure(failure_text)
    nk_label = f"{nkc_now[0]}×{nkc_now[1]}×{nkc_now[2]}"
    new_label = f"{nkc_next[0]}×{nkc_next[1]}×{nkc_next[2]}"
    short = "kmesh_get_bvector" if cls == "kmesh_bvector" else cls
    log_line = (
        f"EPW failed ({short} @ nk={nkc_now[0]}) — "
        f"retrying EPW-only with nk={nkc_next[0]} (DFPT reused)"
    )
    new_cfg = apply_coarse_k_to_config(config, nkc_next)
    # Surface detail in a second sentence for notes
    detail = (
        f"{log_line}; coarse k {nk_label}→{new_label}; "
        f"nqc unchanged={list(config.epw.nqc)}; attempt {attempts_done + 1}/{max_retries}"
    )
    return new_cfg, detail


def _run_epw_once(
    config: DFTConfig,
    work_dir: Path,
    *,
    prefix: str,
    qe_env: Any,
    structure: Structure | None,
    outdir_str: str,
    dvscf_str: str,
    fermi_eV: float | None,
    parallel_msg: str,
) -> tuple[QEStepResult, ElectronPhononResult | None, str]:
    """Single epw.x launch; returns (step, eph, full_output_text)."""
    from siscforge.calculators.qe.epw_inputs import (
        default_nbndsub_screening,
        epw_material_notes,
    )
    from siscforge.calculators.qe.epw_parallel import epw_npool_cli_args
    from siscforge.calculators.qe.recipes import (
        _heartbeat_eta_enabled,
        _heartbeat_seconds_from_config,
        _mpi_prefix,
        _run_cmd,
    )

    assert qe_env.epw is not None
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

    npool = max(1, int(config.epw.npool))
    nbndsub = default_nbndsub_screening(
        nbnd=config.nbnd,
        structure=structure,
        explicit=config.epw.nbndsub,
        auto=bool(getattr(config.epw, "auto_nbndsub", True)),
    )
    cmd = [
        *_mpi_prefix(qe_env, config.nproc),
        qe_env.epw,
        *epw_npool_cli_args(npool),
        "-in",
        in_path.name,
    ]

    rc = _run_cmd(
        cmd,
        cwd=work_dir,
        stdout_path=out_path,
        heartbeat_seconds=_heartbeat_seconds_from_config(config),
        step_label="epw.x (Wannier + e-ph)",
        heartbeat_eta=_heartbeat_eta_enabled(config),
    )
    full_text = ""
    if out_path.is_file():
        try:
            full_text = out_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            full_text = ""

    ok = rc == 0 and out_path.is_file()
    qtag = config.quality_tag
    primary = extract_primary_failure_reason(full_text, step_name="epw") if not ok else "ok"
    msg = (
        f"epw.x rc={rc}; quality_tag={qtag}; "
        f"nproc={config.nproc} npool={npool} nbndsub={nbndsub}; {parallel_msg}"
    )
    if not ok:
        msg = f"{primary} | {msg}"
        msg += "\n" + diagnose_epw_failure(
            full_text or msg,
            work_dir=work_dir,
            step_name="epw",
            include_tail=True,
            tail_lines=30,
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

        mat_note = epw_material_notes(structure)
        if mat_note and eph is not None:
            summary = dict(eph.alpha2F_summary or {})
            summary.setdefault("material_notes", mat_note)
            summary.setdefault("tc_model", "isotropic_average")
            summary.setdefault("quality_tag", config.quality_tag)
            summary.setdefault("nbndsub", nbndsub)
            eph = eph.model_copy(update={"alpha2F_summary": summary})

        if eph is not None and not ok and eph.status != "ok":
            summary = dict(eph.alpha2F_summary or {})
            summary["failure_diagnostic"] = diagnose_epw_failure(
                full_text, work_dir=work_dir, step_name="epw"
            )
            summary["primary_failure"] = primary
            summary["failure_class"] = classify_epw_failure(full_text)
            eph = eph.model_copy(update={"alpha2F_summary": summary})

    return step, eph, full_text


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
    """Write and run ``epw.x`` in *work_dir*; parse stdout into ElectronPhononResult.

    Validates MPI topology before launch: ``nproc`` must equal ``npool`` (nimage=1
    for fine-grid). Default desktop policy auto-sets ``npool = nproc`` when
    inconsistent (e.g. nproc=8, npool=1) so epw.x never hits the
    ``epw_readin`` pools/images abort after multi-hour DFPT.

    On Wannier frozen-window overflow (screening), optionally retries **once**
    with a larger nbndsub (reuses save/nscf via same workdir).

    Coarse-k / bvector remediation that needs re-NSCF is handled by
    :func:`run_relax_scf_phonon_epw` (EPW-only path after DFPT).
    """
    from siscforge.calculators.qe.epw_inputs import default_nbndsub_screening

    qe_env = qe_env or require_epw()
    assert qe_env.epw is not None

    # --- Parallel topology: never launch with nproc ≠ npool (fine-grid) ---
    try:
        config, parallel_msg = resolve_epw_launch_topology(config)
    except ValueError as exc:
        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        out_path = work_dir / "epw.out"
        out_path.write_text(
            f"SiSC-Forge refused to launch epw.x:\n{exc}\n",
            encoding="utf-8",
        )
        step = QEStepResult(
            name="epw",
            work_dir=work_dir,
            returncode=1,
            stdout_path=out_path,
            input_path=work_dir / "epw.in",
            success=False,
            message=str(exc),
        )
        return step, None

    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out = Path(outdir).resolve() if outdir is not None else work_dir
    out.mkdir(parents=True, exist_ok=True)
    save_dir = work_dir / "save"

    if out.resolve() == work_dir.resolve():
        outdir_str = "./"
        dvscf_str = "./save"
    else:
        outdir_str = str(out)
        dvscf_str = str(save_dir.resolve())

    if fermi_eV is None:
        fermi_eV = _fermi_from_work_dir(work_dir)

    # Apply auto nbndsub into config so epw.in and retries stay consistent
    nbndsub0 = default_nbndsub_screening(
        nbnd=config.nbnd,
        structure=structure,
        explicit=config.epw.nbndsub,
        auto=bool(getattr(config.epw, "auto_nbndsub", True)),
    )
    if config.epw.nbndsub != nbndsub0:
        config = config.model_copy(
            update={"epw": config.epw.model_copy(update={"nbndsub": nbndsub0})}
        )

    step, eph, full_text = _run_epw_once(
        config,
        work_dir,
        prefix=prefix,
        qe_env=qe_env,
        structure=structure,
        outdir_str=outdir_str,
        dvscf_str=dvscf_str,
        fermi_eV=fermi_eV,
        parallel_msg=parallel_msg,
    )

    # --- One screening retry on frozen-window overflow ---
    retry = bool(getattr(config.epw, "wannier_retry_on_froz_overflow", True))
    is_screening = (config.quality_tag or "screening") == "screening"
    if (
        not step.success
        and retry
        and is_screening
        and is_frozen_window_overflow(full_text)
    ):
        old_sub = int(config.epw.nbndsub or nbndsub0)
        n_bands = int(config.nbnd) if config.nbnd else max(old_sub * 2, 32)
        new_sub = min(n_bands, max(old_sub * 2, old_sub + 8))
        if new_sub > old_sub:
            retry_note = (
                f"EPW Wannier retry: frozen-window overflow — "
                f"nbndsub {old_sub}→{new_sub} (one retry; save/nscf reused)"
            )
            config = config.model_copy(
                update={
                    "epw": config.epw.model_copy(
                        update={"nbndsub": new_sub, "auto_nbndsub": False}
                    )
                }
            )
            # Archive first-attempt log
            first_out = work_dir / "epw.out"
            if first_out.is_file():
                try:
                    first_out.rename(work_dir / "epw.attempt1.out")
                except OSError:
                    pass
            step2, eph2, full_text2 = _run_epw_once(
                config,
                work_dir,
                prefix=prefix,
                qe_env=qe_env,
                structure=structure,
                outdir_str=outdir_str,
                dvscf_str=dvscf_str,
                fermi_eV=fermi_eV,
                parallel_msg=parallel_msg + f"; {retry_note}",
            )
            step2.message = f"{retry_note}\n{step2.message}"
            if eph2 is not None:
                summary = dict(eph2.alpha2F_summary or {})
                summary["wannier_retry"] = {
                    "reason": "frozen_window_overflow",
                    "nbndsub_before": old_sub,
                    "nbndsub_after": new_sub,
                }
                eph2 = eph2.model_copy(update={"alpha2F_summary": summary})
            return step2, eph2

    return step, eph


def _retry_epw_with_denser_k(
    structure: Structure,
    config: DFTConfig,
    scf_dir: Path,
    *,
    prefix: str,
    qe_env: QEEnvironment | None,
    full_text: str,
    log: list[str],
    result: EPWWorkflowResult,
    step: QEStepResult,
    eph: ElectronPhononResult | None,
) -> tuple[DFTConfig, QEStepResult, ElectronPhononResult | None, str]:
    """Post-DFPT EPW-only remediation loop (kmesh bvector / k-grid mismatch).

    Never cleans phonon / DFPT artifacts. Re-runs NSCF when nkc changes, then
    epw.x. Caps at ``epw.max_kmesh_retries``.

    Special case: k-grid inconsistency with *stale* NSCF at the *current*
    campaign nkc rebuilds NSCF at the same nkc first (no ladder bump) so an
    8³ epw is not forced to 12³ solely because resume skipped a 6³ nscf.
    """
    from siscforge.calculators.qe.qe_checkpoint import (
        inspect_nscf_vs_epw_coarse_k,
        nscf_matches_epw_coarse_k,
    )

    attempt = 0

    # One-shot: rebuild NSCF at current nkc when mesh is stale (k-grid path).
    cls0 = classify_epw_failure(full_text or step.message)
    nkc_cur = list(config.epw.nkc or [8, 8, 8])
    if cls0 == "kgrid_inconsistency" and not nscf_matches_epw_coarse_k(
        scf_dir, nkc_cur
    ):
        fp = inspect_nscf_vs_epw_coarse_k(scf_dir, nkc_cur)
        cli_line = (
            "nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF "
            f"(phonon reused); rebuilding NSCF at nk={nkc_cur[0]}"
        )
        log.append(cli_line)
        log.append(fp.message)
        record_epw_remediation_attempt(
            scf_dir,
            reason="kgrid_stale_nscf",
            nkc_before=nkc_cur,
            nkc_after=nkc_cur,
            note=cli_line,
        )
        _archive_epw_attempt(scf_dir, 0)
        _clean_nscf_and_epw_only(scf_dir, prefix=prefix)
        log.append(
            f"running nscf (stale-mesh rebuild nk={nkc_cur[0]}×{nkc_cur[1]}×{nkc_cur[2]})"
        )
        nscf_step = run_nscf_for_epw(
            structure,
            config,
            scf_dir,
            prefix=prefix,
            qe_env=qe_env,
            outdir=scf_dir,
        )
        result.epw_steps.append(nscf_step)
        result.steps.append(nscf_step)
        if nscf_step.success:
            log.append(f"running epw (after stale NSCF rebuild nk={nkc_cur[0]})")
            step, eph = run_epw(
                config,
                scf_dir,
                prefix=prefix,
                qe_env=qe_env,
                structure=structure,
                outdir=scf_dir,
            )
            step.message = f"{cli_line}\n{step.message}"
            result.epw_steps.append(step)
            result.steps.append(step)
            full_text = ""
            if step.stdout_path is not None and step.stdout_path.is_file():
                try:
                    full_text = step.stdout_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    full_text = step.message or ""
            if step.success or (eph is not None and eph.lambda_total is not None):
                return config, step, eph, full_text
            if not is_remediable_kmesh_failure(full_text):
                return config, step, eph, full_text
        else:
            return config, nscf_step, None, nscf_step.message or ""

    while True:
        plan = plan_kmesh_remediation(
            config, full_text or step.message, work_dir=scf_dir
        )
        if plan is None:
            break
        new_cfg, detail = plan
        nkc_before = list(config.epw.nkc)
        nkc_after = list(new_cfg.epw.nkc)
        attempt += 1
        cli_line = (
            f"EPW failed (kmesh_get_bvector @ nk={nkc_before[0]}) — "
            f"retrying EPW-only with nk={nkc_after[0]} (DFPT reused)"
        )
        log.append(cli_line)
        log.append(detail)

        record_epw_remediation_attempt(
            scf_dir,
            reason=classify_epw_failure(full_text),
            nkc_before=nkc_before,
            nkc_after=nkc_after,
            note=cli_line,
        )
        _archive_epw_attempt(scf_dir, attempt)
        _clean_nscf_and_epw_only(scf_dir, prefix=prefix)

        # Re-NSCF on denser coarse k (must match epw nk1–3)
        log.append(
            f"running nscf (remediation nk={nkc_after[0]}×{nkc_after[1]}×{nkc_after[2]})"
        )
        nscf_step = run_nscf_for_epw(
            structure,
            new_cfg,
            scf_dir,
            prefix=prefix,
            qe_env=qe_env,
            outdir=scf_dir,
        )
        result.epw_steps.append(nscf_step)
        result.steps.append(nscf_step)
        if not nscf_step.success:
            step = nscf_step
            eph = None
            full_text = nscf_step.message or ""
            config = new_cfg
            break

        log.append(f"running epw (remediation nk={nkc_after[0]})")
        step, eph = run_epw(
            new_cfg,
            scf_dir,
            prefix=prefix,
            qe_env=qe_env,
            structure=structure,
            outdir=scf_dir,
        )
        step.message = f"{cli_line}\n{step.message}"
        result.epw_steps.append(step)
        result.steps.append(step)
        config = new_cfg
        full_text = ""
        if step.stdout_path is not None and step.stdout_path.is_file():
            try:
                full_text = step.stdout_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                full_text = step.message or ""
        if step.success or (
            eph is not None and eph.lambda_total is not None
        ):
            break
        if not is_remediable_kmesh_failure(full_text):
            break

    return config, step, eph, full_text


def run_relax_scf_phonon_epw(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    qe_env: QEEnvironment | None = None,
    resume_qe_steps: bool | None = None,
    force_qe_steps: bool | None = None,
    step_log: list[str] | None = None,
) -> EPWWorkflowResult:
    """Conventional path tuned for EPW: SCF → multi-q DFPT → pp.py → EPW.

    Uses a flat work directory layout (``outdir = work_dir``) so EPW's ``pp.py``
    can find ``_ph0/``, ``*.save``, and ``*.dyn*`` as in the official examples.

    Mid-step resume: when enabled (default), successful upstream outputs in the
    candidate workdir are re-used. A kill during ``ph.x`` therefore skips
    vc-relax/SCF; incomplete DFPT that looks recoverable is re-launched with
    QE ``recover=.true.``, otherwise phonon restarts from a clean step.
    Incomplete EPW / pp / nscf steps still clean + re-run (no fragile recover).

    **Pre-DFPT EPW preflight** auto-raises Wannier-unsafe coarse k (e.g. 6→8
    on ≥8-atom production cells) and aligns nqc to DFPT qpoints.

    **Post-DFPT EPW remediation** on ``kmesh_get_bvector`` re-runs NSCF+epw
    only with denser nkc — phonon artifacts are never deleted.
    """
    from siscforge.calculators.qe.qe_checkpoint import (
        clean_step_outputs,
        probe_workdir,
    )
    from siscforge.calculators.qe.recipes import (
        _force_qe_steps,
        _run_ph_with_optional_recover,
        _should_resume_qe_steps,
        _skipped_step,
        _try_read_relaxed_structure,
    )

    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    want_epw = config.do_epw or config.epw.enabled
    qe_env = qe_env or (require_epw() if want_epw else None)

    result = EPWWorkflowResult(work_dir=work_dir, structure=structure)
    current = structure
    log: list[str] = step_log if step_log is not None else []

    # --- Pre-DFPT EPW preflight (coarse k + nq consistency) ---
    nkc_changed_by_preflight = False
    if want_epw:
        pre = preflight_epw_grids(config, structure=structure)
        for m in pre.summary_lines:
            log.append(m)
        if not pre.ok:
            result.success = False
            result.message = (
                "EPW preflight failed (strict_coarse_k or invalid grids):\n"
                + "\n".join(pre.summary_lines)
            )
            return result
        if pre.nkc_raised:
            nkc_changed_by_preflight = True
        config = pre.config

    do_resume = _should_resume_qe_steps(config, resume_qe_steps=resume_qe_steps)
    do_force = _force_qe_steps(config, force_qe_steps=force_qe_steps) or not do_resume
    ckpt = probe_workdir(
        work_dir,
        config,
        prefix=prefix,
        structure=structure,
        want_epw=want_epw,
        force=do_force,
    )
    log.extend(ckpt.log)

    # Invalidate NSCF/EPW when coarse k changed (preflight) OR existing NSCF
    # mesh fingerprint ≠ campaign nkc. Never touch phonon / DFPT artifacts.
    # (CLI preflight may already have raised nkc before this function runs, so
    # nkc_raised alone is insufficient — always compare on-disk mesh.)
    if want_epw and not do_force:
        from siscforge.calculators.qe.qe_checkpoint import (
            inspect_nscf_vs_epw_coarse_k,
            invalidate_nscf_epw_for_kmesh,
        )

        nkc_now = list(config.epw.nkc or [4, 4, 4])
        scf_probe_dir = work_dir / "02_scf"
        has_nscf = (scf_probe_dir / "nscf.out").is_file() or (
            scf_probe_dir / "nscf.in"
        ).is_file()
        fp = inspect_nscf_vs_epw_coarse_k(work_dir, nkc_now)
        need_invalidate = False
        inv_reason = ""
        if nkc_changed_by_preflight and (has_nscf or ckpt.is_complete("epw")):
            need_invalidate = True
            inv_reason = (
                "nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF "
                "(phonon reused)"
            )
        elif has_nscf and not fp.matches:
            need_invalidate = True
            inv_reason = (
                "nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF "
                f"(phonon reused); {fp.message}"
            )
        if need_invalidate:
            _, inv_msg = invalidate_nscf_epw_for_kmesh(
                work_dir,
                prefix=prefix,
                reason=inv_reason,
                clear_wannier=True,
            )
            log.append(inv_msg)
            ckpt = probe_workdir(
                work_dir,
                config,
                prefix=prefix,
                structure=structure,
                want_epw=want_epw,
                force=do_force,
            )
            log.extend([x for x in ckpt.log if x not in log])

    # 1. Optional relax
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
                result.message = f"Relaxation failed: {step.message}"
                result.success = False
                return result
            current = _try_read_relaxed_structure(work_dir / "01_relax", current)
            result.relaxed_structure = current
    else:
        prior = _try_read_relaxed_structure(work_dir / "01_relax", current)
        if prior is not current:
            current = prior
            result.relaxed_structure = current

    # 2. SCF (flat outdir for EPW)
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
        if result.relaxed_structure is not None:
            current = result.relaxed_structure
    else:
        # Only clean scf.out — keep prefix.save if partially present from crash
        # after JOB DONE edge cases; incomplete scf.out means re-run.
        if (scf_dir / "scf.out").is_file():
            clean_step_outputs(work_dir, "scf", prefix=prefix)
        log.append("running SCF")
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
            result.scf = parse_pw_output(
                step.stdout_path, quality_tag=config.quality_tag
            )
        if not step.success:
            result.message = f"SCF failed: {step.message}"
            result.success = False
            return result

    # 3. Phonon (multi-q + dvscf when EPW requested)
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
    else:
        step = _run_ph_with_optional_recover(
            config,
            work_dir=work_dir,
            scf_dir=scf_dir,
            prefix=prefix,
            qe_env=qe_env,
            for_epw=want_epw,
            outdir=scf_dir if want_epw else None,
            log=log,
        )
        result.steps.append(step)
        from siscforge.calculators.qe.recipes import _maybe_retry_phonon_d_matrix

        step, ph_body = _maybe_retry_phonon_d_matrix(
            config,
            structure=current,
            work_dir=work_dir,
            scf_dir=scf_dir,
            prefix=prefix,
            qe_env=qe_env,
            for_epw=want_epw,
            outdir=scf_dir if want_epw else None,
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
                    step.stdout_path.read_text(encoding="utf-8", errors="replace")
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
            result.phonon = parse_ph_output(
                combined, quality_tag=config.quality_tag
            )
        if not step.success:
            primary = extract_primary_failure_reason(
                combined or step.message, step_name="phonon"
            )
            diag = diagnose_qe_step_failure(
                combined or step.message,
                work_dir=work_dir,
                step_name="phonon",
            )
            result.message = (
                f"Phonon failed (ph.x): {primary}\n"
                f"work_dir={work_dir}\n{diag}\n"
                f"step_message={truncate_for_notes(step.message, max_chars=600)}"
            )
            result.success = False
            return result

    if not want_epw:
        result.success = True
        result.message = "ok" if not any(
            "skip" in (s.message or "") for s in result.steps
        ) else "ok (mid-step resume)"
        if step_log is not None:
            step_log.extend(log)
        return result

    # 4. EPW pp.py → save/
    if ckpt.is_complete("epw_pp"):
        result.steps.append(
            _skipped_step(
                "epw_pp",
                scf_dir,
                message="skip epw_pp (checkpoint)",
            )
        )
        log.append("skip epw_pp (checkpoint)")
    else:
        if (scf_dir / "save").exists():
            clean_step_outputs(work_dir, "epw_pp", prefix=prefix)
        log.append("running epw_pp")
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
    if ckpt.is_complete("nscf"):
        result.steps.append(
            _skipped_step(
                "nscf",
                scf_dir,
                stdout_path=ckpt.steps["nscf"].stdout_path,
                message="skip nscf (checkpoint)",
            )
        )
        log.append("skip nscf (checkpoint)")
    else:
        if (scf_dir / "nscf.out").is_file():
            clean_step_outputs(work_dir, "nscf", prefix=prefix)
        log.append("running nscf")
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

    # 6. epw.x (+ optional post-DFPT k-mesh remediation)
    if ckpt.is_complete("epw"):
        probe = ckpt.steps["epw"]
        result.electron_phonon = probe.electron_phonon
        result.steps.append(
            _skipped_step(
                "epw",
                scf_dir,
                stdout_path=probe.stdout_path,
                message="skip epw (checkpoint)",
            )
        )
        log.append("skip epw (checkpoint)")
        eph = result.electron_phonon
        step_msg = "skip epw (checkpoint)"
        step = result.steps[-1]
    else:
        if (scf_dir / "epw.out").is_file():
            # If a prior failed epw.out exists with remediable class and retries
            # remaining, plan remediation before replaying the same nk.
            prior_text = ""
            try:
                prior_text = (scf_dir / "epw.out").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                prior_text = ""
            from siscforge.calculators.qe.qe_checkpoint import (
                invalidate_nscf_epw_for_kmesh,
                nscf_matches_epw_coarse_k,
            )

            prior_cls = classify_epw_failure(prior_text)
            nkc_cur = list(config.epw.nkc or [8, 8, 8])
            # Stale NSCF + k-grid/XML class: rebuild at current nkc (no ladder yet)
            if (
                prior_cls in {"kgrid_inconsistency", "kmesh_bvector"}
                and not nscf_matches_epw_coarse_k(work_dir, nkc_cur)
            ):
                log.append(
                    "nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF "
                    f"(phonon reused); prior EPW class={prior_cls}"
                )
                _archive_epw_attempt(
                    scf_dir,
                    len(load_epw_remediation_state(scf_dir).get("attempts") or []),
                )
                invalidate_nscf_epw_for_kmesh(
                    work_dir,
                    prefix=prefix,
                    reason=(
                        "nkc changed or NSCF/EPW k-mesh mismatch — "
                        "invalidating NSCF (phonon reused)"
                    ),
                )
                record_epw_remediation_attempt(
                    scf_dir,
                    reason="kgrid_stale_nscf",
                    nkc_before=nkc_cur,
                    nkc_after=nkc_cur,
                    note="stale NSCF rebuild at current nkc after prior EPW fail",
                )
                log.append(
                    f"running nscf (stale-mesh rebuild nk={nkc_cur[0]}×"
                    f"{nkc_cur[1]}×{nkc_cur[2]})"
                )
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
                        f"EPW NSCF failed during stale-mesh rebuild: {nscf_step.message}"
                    )
                    result.success = False
                    if step_log is not None:
                        step_log.extend(log)
                    return result
            else:
                plan = plan_kmesh_remediation(config, prior_text, work_dir=scf_dir)
                if plan is not None:
                    new_cfg, detail = plan
                    log.append(
                        "prior EPW failure remediable — applying denser coarse k "
                        "before re-launch (DFPT kept)"
                    )
                    log.append(detail)
                    nkc_before = list(config.epw.nkc)
                    nkc_after = list(new_cfg.epw.nkc)
                    record_epw_remediation_attempt(
                        scf_dir,
                        reason=classify_epw_failure(prior_text),
                        nkc_before=nkc_before,
                        nkc_after=nkc_after,
                        note=detail,
                    )
                    _archive_epw_attempt(
                        scf_dir,
                        len(load_epw_remediation_state(scf_dir).get("attempts") or []),
                    )
                    invalidate_nscf_epw_for_kmesh(
                        work_dir,
                        prefix=prefix,
                        reason=(
                            "nkc changed or NSCF/EPW k-mesh mismatch — "
                            "invalidating NSCF (phonon reused)"
                        ),
                    )
                    config = new_cfg
                    log.append("running nscf (remediation after prior kmesh failure)")
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
                            "EPW NSCF failed during k-mesh remediation: "
                            f"{nscf_step.message}"
                        )
                        result.success = False
                        if step_log is not None:
                            step_log.extend(log)
                        return result
                else:
                    clean_step_outputs(work_dir, "epw", prefix=prefix)
        log.append("running epw")
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
        step_msg = step.message
        full_text = ""
        if step.stdout_path is not None and step.stdout_path.is_file():
            try:
                full_text = step.stdout_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                full_text = step.message or ""
        # Post-DFPT auto-remediation (EPW-only)
        if not step.success and is_remediable_kmesh_failure(full_text):
            config, step, eph, full_text = _retry_epw_with_denser_k(
                current,
                config,
                scf_dir,
                prefix=prefix,
                qe_env=qe_env,
                full_text=full_text,
                log=log,
                result=result,
                step=step,
                eph=eph,
            )
            result.electron_phonon = eph
            step_msg = step.message

    if eph is not None and (eph.converged or eph.lambda_total is not None):
        tc = eph.best_tc_K()
        result.performance_score = performance_score_from_epw(tc)
        # Accept partial success if λ was extracted even if rc != 0
        result.success = eph.status == "ok" or eph.lambda_total is not None
        if result.success:
            skipped = any("skip" in (s.message or "") for s in result.steps)
            tag = config.quality_tag
            result.message = (
                f"ok (quality_tag={tag}; mid-step resume)"
                if skipped
                else f"ok (quality_tag={tag})"
            )
        else:
            result.message = step_msg
    else:
        result.success = False
        primary = extract_primary_failure_reason(step_msg, step_name="epw")
        next_step = (
            "Human next step: raise epw.nkc further (e.g. 12³), check NSCF k "
            "matches nk1–3, or set material-specific Wannier projections. "
            "Do not re-run DFPT for kmesh_get_bvector — phonon is intact. "
            "Use --force-rerun only if you intentionally want a full redo."
        )
        if is_kmesh_bvector_failure(step_msg):
            next_step = (
                "Human next step: all automatic coarse-k retries exhausted "
                "(6→8→12). Inspect epw.out / Wannier90 .wout; try nkc=12 with "
                "hand-tuned projections, or a different strain/cell. "
                "DFPT/phonon artifacts were NOT deleted — re-run without "
                "--force-rerun to retry EPW only."
            )
        result.message = (
            f"EPW failed or did not converge (quality_tag={config.quality_tag}):\n"
            f"{primary}\n"
            f"{step_msg}\n"
            f"{next_step}"
        )

    if step_log is not None:
        # log already is step_log when provided
        if log is not step_log:
            step_log.extend(log)
    elif log:
        # surface checkpoint trail in message when useful
        if any(x.startswith("skip ") or x.startswith("running ") or "EPW" in x for x in log):
            trail = "; ".join(log)
            if result.message == "ok" or result.message.startswith("ok "):
                result.message = f"{result.message} [{trail}]"
            elif not result.success:
                result.message = f"{result.message}\n[steps: {trail}]"

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
        "coarse_k_safety": (
            "workstation_dense/production min nkc=8³ for ≥8-atom cells; "
            "post-DFPT kmesh_get_bvector → EPW-only retry 6→8→12"
        ),
    }
