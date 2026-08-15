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

NSCF / EPW coarse-k fingerprint (Slice 26)
-----------------------------------------
EPW coarse k (``nkc`` / ``nk1–3``) can change via preflight auto-raise or
post-DFPT remediation while finished DFPT is kept. A successful ``nscf.out``
from a **previous** mesh must **not** be skipped: compare the mesh used by
existing NSCF inputs against the campaign's current ``nkc``. Prefer the
**requested** mesh from ``nscf.in`` / sidecar over symmetry-reduced counts
in ``nscf.out`` alone. On mismatch, invalidate NSCF + EPW electronic outputs
only — never ``ph.out``, ``*.dyn*``, ``_ph0``, or dvscf.

Limitations
-----------
- Not Folding@home-style mid-iteration pause; granularity is **QE step** +
  QE-native ``recover`` for DFPT only.
- Partial ``ph.out`` with **no** dyn/_ph0/dvscf ⇒ full phonon restart.
- ``force_rerun`` / ``force_rerun_qe_steps`` disables all step skips and recover.
- EPW (``epw.x``) incomplete steps still clean + re-run (no fragile recover path).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

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
# d_matrix / phq_setup / MPI_ABORT are fatal setup aborts: recover=.true.
# cannot continue a star that already failed a symmetry representation.
_RECOVER_UNSAFE_MARKERS: tuple[str, ...] = (
    "cannot recover",
    "error reading recover",
    "error in routine  read_file_ph",
    "problems reading recover",
    "d_matrix",
    "not orthogonal",
    "error in routine phq_setup",
    "fft grid incompatible with symmetry",
    "mpi_abort",
)

# Sidecar written when NSCF-for-EPW completes (requested mesh fingerprint).
NSCF_KMESH_SIDECAR = "siscforge_nscf_kmesh.json"

# Wannier / EPW electronic side products safe to clear on nkc change.
_WANNIER_EPW_GLOBS: tuple[str, ...] = (
    "*.win",
    "*.amn",
    "*.mmn",
    "*.eig",
    "*.nnkp",
    "*.wout",
    "*.chk",
    "*.uk",
    "*.uHu",
    "*.uIu",
    "epwdata.fmt",
    "crystal.fmt",
    "*.epmat*",
    "selecq.fmt",
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


@dataclass(frozen=True)
class NscfKmeshFingerprint:
    """How an existing NSCF relates to the campaign's current EPW coarse k."""

    matches: bool
    expected_nkc: tuple[int, int, int]
    observed_nkc: tuple[int, int, int] | None
    observed_nk_count: int | None
    source: str
    message: str


def _job_done(text: str) -> bool:
    return "JOB DONE" in text.upper()


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_qe_crash_text(
    work_dir: Path | str | None = None,
    stdout_path: Path | str | None = None,
) -> str:
    """Read QE ``CRASH`` sidecars next to a step log.

    ``ph.x`` often writes the real abort (``d_matrix``, ``phq_setup``) to
    ``CRASH`` in the launch cwd and only ``MPI_ABORT`` into ``ph.out``.
    """
    candidates: list[Path] = []
    if stdout_path is not None:
        candidates.append(Path(stdout_path).parent / "CRASH")
    if work_dir is not None:
        wd = Path(work_dir)
        candidates.append(wd / "CRASH")
        candidates.append(wd / "02_scf" / "CRASH")
        candidates.append(wd / "out" / "CRASH")
    seen: set[Path] = set()
    parts: list[str] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        text = _read_text(path)
        if text and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def phonon_diagnostic_text(
    work_dir: Path | str | None = None,
    stdout_path: Path | str | None = None,
) -> str:
    """Concatenate ``CRASH`` + ``ph.out`` for classification / retry."""
    parts: list[str] = []
    crash = read_qe_crash_text(work_dir, stdout_path)
    if crash:
        parts.append(crash)
    if stdout_path is not None:
        body = _read_text(Path(stdout_path))
        if body:
            parts.append(body)
    return "\n".join(parts)


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


def _normalize_nkc3(nkc: Sequence[int] | None) -> tuple[int, int, int] | None:
    if nkc is None:
        return None
    vals = [int(x) for x in list(nkc)[:3]]
    if len(vals) < 3:
        return None
    if min(vals) < 1:
        return None
    return (vals[0], vals[1], vals[2])


def _scf_subdir(work_dir: Path) -> Path:
    """Return the flat EPW scf dir (``work_dir/02_scf`` or ``work_dir`` itself)."""
    work_dir = Path(work_dir)
    if (work_dir / "02_scf").is_dir():
        return work_dir / "02_scf"
    if work_dir.name == "02_scf":
        return work_dir
    # Flat layout: nscf lives directly under work_dir
    if (work_dir / "nscf.out").is_file() or (work_dir / "nscf.in").is_file():
        return work_dir
    return work_dir / "02_scf"


def write_nscf_kmesh_sidecar(
    work_dir: Path | str,
    nkc: Sequence[int],
) -> Path | None:
    """Record the requested NSCF/EPW coarse mesh after a successful NSCF run."""
    scf_dir = _scf_subdir(Path(work_dir))
    mesh = _normalize_nkc3(nkc)
    if mesh is None:
        return None
    scf_dir.mkdir(parents=True, exist_ok=True)
    path = scf_dir / NSCF_KMESH_SIDECAR
    payload = {
        "nkc": list(mesh),
        "nk_product": int(mesh[0] * mesh[1] * mesh[2]),
        "source": "run_nscf_for_epw",
    }
    try:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


def parse_nscf_in_kmesh(path: Path | str) -> tuple[int, int, int] | int | None:
    """Parse requested k-mesh from ``nscf.in``.

    Preference order
    ----------------
    1. Explicit ``nk1/nk2/nk3``-style comments or keys if present
    2. ``K_POINTS crystal`` followed by point count *N* — return *N* as product
       (cube-root factors only when N is a perfect cube; else product-only)

    Returns a 3-tuple when dimensions are known, an int product when only the
    crystal mesh count is known, or None if unparseable.
    """
    text = _read_text(Path(path))
    if text is None:
        return None

    # Optional: nk1 = … nk2 = … nk3 = … (some decks / comments)
    dims: list[int | None] = [None, None, None]
    for i, key in enumerate(("nk1", "nk2", "nk3")):
        m = re.search(rf"\b{key}\s*=\s*(\d+)", text, flags=re.IGNORECASE)
        if m:
            dims[i] = int(m.group(1))
    if all(d is not None for d in dims):
        return (int(dims[0]), int(dims[1]), int(dims[2]))  # type: ignore[arg-type]

    # K_POINTS crystal\n  <N>
    m = re.search(
        r"K_POINTS\s+crystal\s*\n\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        n_pts = int(m.group(1))
        # Prefer exact isotropic cube when possible
        cbrt = round(n_pts ** (1.0 / 3.0))
        if cbrt > 0 and cbrt * cbrt * cbrt == n_pts:
            return (cbrt, cbrt, cbrt)
        return n_pts

    # K_POINTS automatic\n  nk1 nk2 nk3  shift…
    m = re.search(
        r"K_POINTS\s+automatic\s*\n\s*(\d+)\s+(\d+)\s+(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def parse_nscf_out_k_count(path: Path | str) -> int | None:
    """Parse ``number of k points = N`` from pw.x NSCF stdout (may be reduced)."""
    text = _read_text(Path(path))
    if text is None:
        return None
    m = re.search(r"number of k points\s*=\s*(\d+)", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def parse_epw_in_nkc(path: Path | str) -> tuple[int, int, int] | None:
    """Parse ``nk1/nk2/nk3`` from ``epw.in``."""
    text = _read_text(Path(path))
    if text is None:
        return None
    dims: list[int | None] = [None, None, None]
    for i, key in enumerate(("nk1", "nk2", "nk3")):
        m = re.search(rf"\b{key}\s*=\s*(\d+)", text, flags=re.IGNORECASE)
        if m:
            dims[i] = int(m.group(1))
    if all(d is not None for d in dims):
        return (int(dims[0]), int(dims[1]), int(dims[2]))  # type: ignore[arg-type]
    return None


def read_nscf_kmesh_sidecar(work_dir: Path | str) -> tuple[int, int, int] | None:
    """Load requested nkc from ``siscforge_nscf_kmesh.json`` if present."""
    scf_dir = _scf_subdir(Path(work_dir))
    path = scf_dir / NSCF_KMESH_SIDECAR
    if not path.is_file():
        # Also try candidate root
        alt = Path(work_dir) / NSCF_KMESH_SIDECAR
        path = alt if alt.is_file() else path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    nkc = data.get("nkc") if isinstance(data, dict) else None
    return _normalize_nkc3(nkc)


def inspect_nscf_vs_epw_coarse_k(
    work_dir: Path | str,
    nkc: Sequence[int],
) -> NscfKmeshFingerprint:
    """Compare existing NSCF mesh fingerprint to campaign ``nkc``.

    Comparison rule (prefer requested mesh over fragile reduced counts)
    -------------------------------------------------------------------
    1. Sidecar ``siscforge_nscf_kmesh.json`` → exact ``nkc`` triple match
    2. ``nscf.in`` ``K_POINTS crystal`` count / dims → product or triple match
    3. ``nscf.out`` ``number of k points`` → product match only (last resort;
       symmetry-reduced counts can false-mismatch automatic MP meshes, but
       EPW NSCF uses full crystal meshes so product equality is expected)

    Missing NSCF files ⇒ **matches=True** (nothing stale to invalidate; the
    normal incomplete probe handles absence). Callers that need "exists and
    mismatches" should check for ``nscf.out`` first.
    """
    expected = _normalize_nkc3(nkc)
    if expected is None:
        return NscfKmeshFingerprint(
            matches=True,
            expected_nkc=(0, 0, 0),
            observed_nkc=None,
            observed_nk_count=None,
            source="no_expected",
            message="no expected nkc — skip mesh check",
        )
    exp_prod = expected[0] * expected[1] * expected[2]
    work_dir = Path(work_dir)
    scf_dir = _scf_subdir(work_dir)

    nscf_out = scf_dir / "nscf.out"
    nscf_in = scf_dir / "nscf.in"
    if not nscf_out.is_file() and not nscf_in.is_file():
        # Also allow flat work_dir without 02_scf
        if (work_dir / "nscf.out").is_file():
            scf_dir = work_dir
            nscf_out = work_dir / "nscf.out"
            nscf_in = work_dir / "nscf.in"
        else:
            return NscfKmeshFingerprint(
                matches=True,
                expected_nkc=expected,
                observed_nkc=None,
                observed_nk_count=None,
                source="missing",
                message="no nscf artifacts — mesh check N/A",
            )

    # 1. Sidecar (authoritative requested mesh)
    side = read_nscf_kmesh_sidecar(scf_dir)
    if side is not None:
        ok = side == expected
        return NscfKmeshFingerprint(
            matches=ok,
            expected_nkc=expected,
            observed_nkc=side,
            observed_nk_count=side[0] * side[1] * side[2],
            source="sidecar",
            message=(
                f"nscf sidecar nkc={side[0]}×{side[1]}×{side[2]} "
                f"{'matches' if ok else '≠'} campaign "
                f"{expected[0]}×{expected[1]}×{expected[2]}"
            ),
        )

    # 2. nscf.in requested mesh
    if nscf_in.is_file():
        parsed = parse_nscf_in_kmesh(nscf_in)
        if isinstance(parsed, tuple):
            ok = parsed == expected
            return NscfKmeshFingerprint(
                matches=ok,
                expected_nkc=expected,
                observed_nkc=parsed,
                observed_nk_count=parsed[0] * parsed[1] * parsed[2],
                source="nscf.in",
                message=(
                    f"nscf.in nkc={parsed[0]}×{parsed[1]}×{parsed[2]} "
                    f"{'matches' if ok else '≠'} campaign "
                    f"{expected[0]}×{expected[1]}×{expected[2]}"
                ),
            )
        if isinstance(parsed, int):
            ok = parsed == exp_prod
            return NscfKmeshFingerprint(
                matches=ok,
                expected_nkc=expected,
                observed_nkc=None,
                observed_nk_count=parsed,
                source="nscf.in_count",
                message=(
                    f"nscf.in K_POINTS count={parsed} "
                    f"{'matches' if ok else '≠'} product "
                    f"{exp_prod} ({expected[0]}×{expected[1]}×{expected[2]})"
                ),
            )

    # 3. nscf.out number of k points (full crystal mesh expected for EPW)
    if nscf_out.is_file():
        n_k = parse_nscf_out_k_count(nscf_out)
        if n_k is not None:
            ok = n_k == exp_prod
            return NscfKmeshFingerprint(
                matches=ok,
                expected_nkc=expected,
                observed_nkc=None,
                observed_nk_count=n_k,
                source="nscf.out",
                message=(
                    f"nscf.out number of k points={n_k} "
                    f"{'matches' if ok else '≠'} product "
                    f"{exp_prod} ({expected[0]}×{expected[1]}×{expected[2]})"
                ),
            )

    # Artifacts exist but unparseable — conservative mismatch (force re-NSCF)
    return NscfKmeshFingerprint(
        matches=False,
        expected_nkc=expected,
        observed_nkc=None,
        observed_nk_count=None,
        source="unparseable",
        message=(
            f"nscf present but k-mesh unparseable — treating as mismatch vs "
            f"{expected[0]}×{expected[1]}×{expected[2]}"
        ),
    )


def nscf_matches_epw_coarse_k(
    work_dir: Path | str,
    nkc: Sequence[int],
) -> bool:
    """True when existing NSCF is absent or its requested mesh matches *nkc*.

    See :func:`inspect_nscf_vs_epw_coarse_k` for the comparison rule.
    """
    return inspect_nscf_vs_epw_coarse_k(work_dir, nkc).matches


def invalidate_nscf_epw_for_kmesh(
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    reason: str | None = None,
    clear_wannier: bool = True,
) -> tuple[list[Path], str]:
    """Remove NSCF + EPW electronic outputs so resume re-runs electronic steps.

    **Never** deletes phonon / DFPT artifacts (``ph.out``, ``*.dyn*``, ``_ph0``,
    dvscf). Optionally clears Wannier side products (``*.win``, ``*.amn``, …).

    Returns ``(removed_paths, one_line_cli_message)``.
    """
    work_dir = Path(work_dir)
    cand = work_dir
    if work_dir.name == "02_scf":
        cand = work_dir.parent
    scf_dir = _scf_subdir(work_dir)
    removed: list[Path] = []

    def _rm(path: Path) -> None:
        if path.is_file():
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass
        elif path.is_dir():
            try:
                shutil.rmtree(path)
                removed.append(path)
            except OSError:
                pass

    # Prefer clean_step_outputs when candidate root has 02_scf layout
    if (cand / "02_scf").is_dir():
        removed.extend(clean_step_outputs(cand, "nscf", prefix=prefix))
        removed.extend(clean_step_outputs(cand, "epw", prefix=prefix))
    else:
        for name in ("nscf.out", "nscf.in", "epw.out", "epw.in"):
            _rm(scf_dir / name)

    # Always clear inputs + sidecar + archives that pin the old mesh
    for name in (
        "nscf.in",
        "nscf.out",
        "epw.in",
        "epw.out",
        NSCF_KMESH_SIDECAR,
    ):
        _rm(scf_dir / name)
    for archive in scf_dir.glob("epw.attempt*.out"):
        _rm(archive)

    if clear_wannier:
        for pattern in _WANNIER_EPW_GLOBS:
            for p in scf_dir.glob(pattern):
                _rm(p)

    msg = reason or (
        "nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF (phonon reused)"
    )
    return removed, msg


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
    text = phonon_diagnostic_text(work_dir, out_path) or None

    if text is not None and _job_done(text):
        return PhononRecoverability(
            recoverable=False,
            reason="ph.out already JOB DONE (use step skip, not recover)",
        )

    if text is not None and _ph_out_has_unsafe_recover_markers(text):
        return PhononRecoverability(
            recoverable=False,
            reason="ph.out/CRASH has recover-unsafe markers — full phonon restart",
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
    expected_nkc: Sequence[int] | None = None,
) -> StepProbe:
    """Probe EPW NSCF under ``work_dir/02_scf/nscf.out``.

    When *expected_nkc* is set (campaign EPW coarse k), a JOB DONE ``nscf.out``
    whose k-mesh fingerprint does not match is treated as **incomplete** so
    resume never pairs a denser ``epw.in`` with a stale NSCF mesh.
    """
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

    if expected_nkc is not None:
        fp = inspect_nscf_vs_epw_coarse_k(work_dir, expected_nkc)
        if not fp.matches:
            exp = fp.expected_nkc
            detail = fp.message
            return StepProbe(
                name="nscf",
                complete=False,
                message=(
                    f"nscf k-mesh mismatch vs epw nkc "
                    f"{exp[0]}×{exp[1]}×{exp[2]} ({detail})"
                ),
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
    For EPW campaigns, NSCF is complete only when the on-disk k-mesh matches
    ``config.epw.nkc`` (see :func:`nscf_matches_epw_coarse_k`).
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

        expected_nkc = list(config.epw.nkc) if config.epw.nkc else None
        probe = probe_nscf(
            work_dir, quality_tag=qtag, expected_nkc=expected_nkc
        )
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
    text = None
    if stdout_path is not None:
        text = phonon_diagnostic_text(Path(stdout_path).parent, stdout_path) or None
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
        _rm(scf_dir / "CRASH")
        _rm(work_dir / "CRASH")
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
        scf_dir = work_dir / "02_scf"
        _rm(scf_dir / "nscf.out")
        _rm(scf_dir / "nscf.in")
        _rm(scf_dir / NSCF_KMESH_SIDECAR)
    elif step == "epw":
        scf_dir = work_dir / "02_scf"
        _rm(scf_dir / "epw.out")
        _rm(scf_dir / "epw.in")
    return removed
