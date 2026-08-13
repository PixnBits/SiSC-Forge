"""Standalone Wannier prep + quality metrics — Phase 3.2.

Provides:
- enablement helper from :class:`~siscforge.models.config.WannierConfig`
- screening ``.win`` builder (``proj=random`` or explicit orbital strings)
- Wannier90 log parse → spreads / failure classes
- DMFT readiness gate for **P3.3** (TRIQS / solid_dmft)
- mock :class:`~siscforge.models.results.WannierResult` for dry-run
- gated ``wannier90.x`` when ``.amn``/``.mmn`` are already staged
- sequential recipe glue after SCF / DFT+U (sacred upstream artifacts)

This is **prep + metrics + gated wannier90.x**, not a turnkey nscf +
pw2wannier90 orchestration. Residual **P3.2.1** (or under P3.3) covers
minimal automated nscf + ``pw2wannier90`` when binaries are present.

**Out of scope (later packages):** TRIQS/solid_dmft (P3.3), pairing
eigenvalue (P3.4), oxygen-vacancy enumeration (P3.5), material-specific
production projection libraries, spinor / collinear-spin Wannier manifolds.

The conventional EPW pathway still owns its own internal Wannier90 step
(``proj=random``, coarse grids, remediation). This module does **not**
replace or weaken that path.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from siscforge import __version__
from siscforge.models.config import DFTConfig, WannierConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import WannierResult

# ---------------------------------------------------------------------------
# Failure classes (step-aware; never reuse phonon-only labels)
# ---------------------------------------------------------------------------

WANNIER_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "frozen_window",
        "kmesh_bvector",
        "disentanglement",
        "spread_divergence",
        "missing_files",
        "binary_missing",
        "projection",
        "nscf_failed",
        "pw2wannier_failed",
        "convergence",
        "other",
    }
)

_EXTENSION_HOOKS: dict[str, str] = {
    "p3_3_dmft": (
        "TRIQS/solid_dmft consumes WannierResult.work_dir / .chk / spreads; "
        "P3.3 refuses launch when ready_for_dmft is False "
        "(unless mock bypass / allow_without_wannier_gate)"
    ),
    "p3_4_pairing": "map leading eigenvalue → performance_score",
    "limits": (
        "screening defaults use proj=random + coarse k; material-specific "
        "production projections are a later residual"
    ),
}


def wannier_is_enabled(dft: DFTConfig | None, *, force: bool = False) -> bool:
    """Return True when standalone Wannierization should run."""
    if force:
        return True
    if dft is None:
        return False
    if bool(getattr(dft, "do_wannier", False)):
        return True
    w = getattr(dft, "wannier", None)
    return bool(w is not None and getattr(w, "enabled", False))


# ---------------------------------------------------------------------------
# Failure classification (reuses EPW Wannier fingerprints; own labels)
# ---------------------------------------------------------------------------


def classify_wannier_failure(text: str | None) -> str:
    """Classify a Wannier90 / pw2wannier90 failure for step-aware diagnostics.

    Returns one of :data:`WANNIER_FAILURE_CLASSES`. Never returns phonon-only
    or EPW-orchestration-only labels (e.g. soft_modes, parallel pool).
    """
    if not text or not str(text).strip():
        return "other"
    blob = text.lower()

    if (
        "more states in the frozen window than target" in blob
        or ("dis_windows" in blob and "frozen" in blob)
        or ("frozen window" in blob and "target" in blob and "wf" in blob)
    ):
        return "frozen_window"
    if (
        "kmesh_get_bvector" in blob
        or "not enough bvectors" in blob
        or ("bvector" in blob and "not enough" in blob)
    ):
        return "kmesh_bvector"
    if "wannier90.x" in blob and (
        "not found" in blob or "no such file" in blob or "cannot execute" in blob
    ):
        return "binary_missing"
    if "pw2wannier90" in blob and (
        "error" in blob or "abort" in blob or "failed" in blob
    ):
        return "pw2wannier_failed"
    if "error opening" in blob or "dafopen" in blob or "file not found" in blob:
        return "missing_files"
    if "projection" in blob and ("error" in blob or "invalid" in blob):
        return "projection"
    if "disentang" in blob and ("error" in blob or "failed" in blob or "abort" in blob):
        return "disentanglement"
    if "spread" in blob and (
        "diverg" in blob or "nan" in blob or "infinity" in blob or "too large" in blob
    ):
        return "spread_divergence"
    if "did not converge" in blob or "not converged" in blob:
        return "convergence"
    if "nscf" in blob and ("error" in blob or "failed" in blob):
        return "nscf_failed"
    return "other"


def primary_wannier_failure_reason(
    text: str | None,
    *,
    max_len: int = 120,
) -> str:
    """One-line primary reason for CLI / notes (always labeled as wannier:)."""
    cls = classify_wannier_failure(text)
    labels = {
        "frozen_window": "wannier: frozen window has more states than target WFs",
        "kmesh_bvector": "wannier: kmesh_get_bvector / not enough bvectors",
        "disentanglement": "wannier: disentanglement failure",
        "spread_divergence": "wannier: Wannier spreads diverged / unusable",
        "missing_files": "wannier: missing .amn/.mmn — stage nscf+pw2wannier90 into work_dir",
        "binary_missing": "wannier: wannier90.x not found",
        "projection": "wannier: projection specification error",
        "nscf_failed": "wannier: nscf prerequisite failed",
        "pw2wannier_failed": "wannier: pw2wannier90 failed",
        "convergence": "wannier: MLWF minimisation did not converge",
        "other": "wannier: failed",
    }
    msg = labels.get(cls, "wannier: failed")
    if text and cls == "other":
        for line in str(text).splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if "error" in low or "abort" in low or "fatal" in low:
                msg = f"wannier: {s}"
                break
    if len(msg) > max_len:
        msg = msg[: max_len - 1] + "…"
    return msg


# ---------------------------------------------------------------------------
# Spreads / quality parse
# ---------------------------------------------------------------------------

_SPREAD_LINE = re.compile(
    r"WF centre and spread\s+\d+\s+\(\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*,"
    r"\s*([-\d.Ee+]+)\s*\)\s*([-\d.Ee+]+)",
    re.I,
)
_FINAL_OMEGA = re.compile(
    r"Sum of centres and spreads\s+\(\s*[-\d.Ee+]+\s*,\s*[-\d.Ee+]+\s*,"
    r"\s*[-\d.Ee+]+\s*\)\s*([-\d.Ee+]+)",
    re.I,
)
_OMEGA_I = re.compile(r"Omega I\s*=\s*([-\d.Ee+]+)", re.I)
_NUM_WANN = re.compile(r"Number of Wannier Functions\s*:\s*(\d+)", re.I)
_NUM_BANDS = re.compile(r"Number of bands\s*:\s*(\d+)", re.I)


def parse_wannier_spreads(text: str) -> dict[str, Any]:
    """Extract spread metrics from a ``.wout`` (or similar) log body."""
    spreads: list[float] = []
    for m in _SPREAD_LINE.finditer(text):
        try:
            spreads.append(float(m.group(4)))
        except (TypeError, ValueError):
            continue
    spread_sum: float | None = None
    m_sum = _FINAL_OMEGA.search(text)
    if m_sum:
        try:
            spread_sum = float(m_sum.group(1))
        except (TypeError, ValueError):
            spread_sum = None
    if spread_sum is None and spreads:
        spread_sum = float(sum(spreads))
    avg = float(sum(spreads) / len(spreads)) if spreads else None
    mx = float(max(spreads)) if spreads else None
    num_wann = None
    m_nw = _NUM_WANN.search(text)
    if m_nw:
        num_wann = int(m_nw.group(1))
    num_bands = None
    m_nb = _NUM_BANDS.search(text)
    if m_nb:
        num_bands = int(m_nb.group(1))
    omega_i = None
    m_oi = _OMEGA_I.search(text)
    if m_oi:
        try:
            omega_i = float(m_oi.group(1))
        except (TypeError, ValueError):
            omega_i = None
    return {
        "spreads_ang2": spreads,
        "spread_sum_ang2": spread_sum,
        "avg_spread_ang2": avg,
        "max_spread_ang2": mx,
        "num_wann": num_wann,
        "num_bands": num_bands,
        "omega_i": omega_i,
    }


def discover_wannier_artifacts(
    work_dir: Path | str,
    seedname: str = "siscforge",
) -> dict[str, str | None]:
    """Locate standard Wannier90 artifacts under *work_dir*."""
    wd = Path(work_dir)
    out: dict[str, str | None] = {
        "work_dir": str(wd),
        "win_path": None,
        "amn_path": None,
        "mmn_path": None,
        "chk_path": None,
        "wout_path": None,
    }
    mapping = {
        "win_path": f"{seedname}.win",
        "amn_path": f"{seedname}.amn",
        "mmn_path": f"{seedname}.mmn",
        "chk_path": f"{seedname}.chk",
        "wout_path": f"{seedname}.wout",
    }
    for key, name in mapping.items():
        p = wd / name
        if p.is_file():
            out[key] = str(p)
    # Also accept any *.wout / *.chk if seedname differs
    if out["wout_path"] is None:
        for p in sorted(wd.glob("*.wout")):
            out["wout_path"] = str(p)
            break
    if out["chk_path"] is None:
        for p in sorted(wd.glob("*.chk")):
            out["chk_path"] = str(p)
            break
    return out


def assess_dmft_readiness(
    *,
    wannier_ok: bool,
    status: str,
    avg_spread: float | None,
    max_spread: float | None,
    chk_path: str | None,
    cfg: WannierConfig,
    failure_class: str | None = None,
) -> tuple[bool, str]:
    """Return ``(ready_for_dmft, notes)`` for the P3.3 gate."""
    if status not in {"ok", "mock"}:
        return False, f"not ready for DMFT: status={status}"
    if not wannier_ok:
        cls = failure_class or "unknown"
        return False, f"not ready for DMFT: wannier_ok=False (class={cls})"
    if cfg.require_chk and not chk_path:
        return False, "not ready for DMFT: missing .chk artifact"
    if avg_spread is not None and avg_spread > float(cfg.max_avg_spread_ang2):
        return (
            False,
            f"not ready for DMFT: avg spread {avg_spread:.3f} Å² "
            f"> {cfg.max_avg_spread_ang2:g}",
        )
    if max_spread is not None and max_spread > float(cfg.max_spread_ang2):
        return (
            False,
            f"not ready for DMFT: max spread {max_spread:.3f} Å² "
            f"> {cfg.max_spread_ang2:g}",
        )
    return True, ""


# ---------------------------------------------------------------------------
# num_wann / windows / .win builder
# ---------------------------------------------------------------------------


def default_num_wann_screening(
    *,
    num_bands: int | None,
    structure: Structure | None = None,
    explicit: int | None = None,
    auto: bool = True,
) -> int:
    """Screening floor for ``num_wann`` (same *spirit* as EPW ``auto_nbndsub``).

    Intentionally **not identical** to EPW: uses ``max(16 if n_at>=4 else 8,
    2*n_at, nbnd//2)`` rather than EPW's ``max(16, 4*n_atoms, nbnd//2)``.
    Standalone correlated screening often wants a smaller manifold than the
    conventional EPW interpolation subspace.
    """
    n_bands = int(num_bands) if num_bands and int(num_bands) > 0 else 20
    n_at = len(structure) if structure is not None else 2
    auto_val = max(8, min(n_bands, max(16 if n_at >= 4 else 8, 2 * n_at, n_bands // 2)))
    if explicit is not None and int(explicit) > 0:
        exp = int(explicit)
        if not auto:
            return min(exp, n_bands)
        return min(n_bands, max(exp, auto_val if auto_val <= n_bands else exp))
    return min(n_bands, auto_val) if auto else min(10, n_bands)


def resolve_kmesh(dft: DFTConfig, structure: Structure | None = None) -> list[int]:
    """Wannier-safe coarse k from config (EPW policy; does not mutate EPW)."""
    from siscforge.calculators.qe.epw_inputs import ensure_wannier_safe_nkc

    w = dft.wannier
    n_at = len(structure) if structure is not None else 2
    nkc = list(w.kmesh) if w.kmesh else [4, 4, 4]
    safe, _msg = ensure_wannier_safe_nkc(
        nkc,
        quality_tag=dft.quality_tag,
        n_atoms=n_at,
        auto_raise=bool(w.auto_raise_coarse_k) and not bool(w.strict_coarse_k),
    )
    if w.strict_coarse_k:
        floor_msg = ensure_wannier_safe_nkc(
            nkc,
            quality_tag=dft.quality_tag,
            n_atoms=n_at,
            auto_raise=False,
        )[1]
        if floor_msg:
            raise ValueError(
                f"Wannier strict_coarse_k refused undersized kmesh {nkc}: {floor_msg}"
            )
    return [int(x) for x in safe]


def _window_block(
    cfg: WannierConfig,
    fermi_eV: float | None,
) -> list[str]:
    """Disentanglement / frozen window lines for ``.win``."""
    # Explicit absolute overrides win when provided and not fermi-relative
    if (
        not cfg.use_fermi_relative_windows
        or fermi_eV is None
        or any(
            v is not None
            for v in (cfg.dis_win_min, cfg.dis_win_max, cfg.dis_froz_min, cfg.dis_froz_max)
        )
    ):
        # Mix: if relative requested and Ef known, only fill missing slots from
        # EPW-style defaults; absolute overrides take precedence.
        from siscforge.calculators.qe.epw_inputs import wannier_window_lines

        defaults = wannier_window_lines(
            fermi_eV if cfg.use_fermi_relative_windows else None,
            screening_tight_froz=cfg.screening_tight_froz,
        )
        # Parse defaults into a map
        parsed: dict[str, float] = {}
        for line in defaults:
            if "=" in line:
                k, v = line.split("=", 1)
                parsed[k.strip()] = float(v.strip())
        if cfg.dis_win_min is not None:
            parsed["dis_win_min"] = float(cfg.dis_win_min)
        if cfg.dis_win_max is not None:
            parsed["dis_win_max"] = float(cfg.dis_win_max)
        if cfg.dis_froz_min is not None:
            parsed["dis_froz_min"] = float(cfg.dis_froz_min)
        if cfg.dis_froz_max is not None:
            parsed["dis_froz_max"] = float(cfg.dis_froz_max)
        return [f"  {k} = {v:.4f}" for k, v in parsed.items()]

    from siscforge.calculators.qe.epw_inputs import wannier_window_lines

    return wannier_window_lines(
        fermi_eV, screening_tight_froz=cfg.screening_tight_froz
    )


def projection_block(cfg: WannierConfig) -> tuple[str, list[str], str]:
    """Return ``(mode, .win projection lines, summary)``."""
    mode = (cfg.projection_mode or "random").lower()
    if mode == "explicit" and cfg.projections:
        lines = ["begin projections"]
        for p in cfg.projections:
            lines.append(f"  {p}")
        lines.append("end projections")
        summary = ";".join(cfg.projections)
        return "explicit", lines, summary
    # random (default / fallback)
    note = ""
    if mode == "explicit" and not cfg.projections:
        note = " (explicit requested but projections empty → random fallback)"
    lines = [
        "begin projections",
        "  random",
        "end projections",
    ]
    return "random", lines, "random" + note


def build_win_input(
    structure: Structure,
    dft: DFTConfig,
    *,
    fermi_eV: float | None = None,
    num_wann: int | None = None,
    num_bands: int | None = None,
    kmesh: list[int] | None = None,
    seedname: str | None = None,
) -> str:
    """Build a screening-oriented Wannier90 ``.win`` body.

    Does **not** auto-discover material-specific production projections.
    """
    cfg = dft.wannier
    seed = seedname or cfg.seedname
    n_bands = num_bands or dft.nbnd or 20
    n_wann = num_wann or default_num_wann_screening(
        num_bands=n_bands,
        structure=structure,
        explicit=cfg.num_wann,
        auto=cfg.auto_num_wann,
    )
    mesh = kmesh or resolve_kmesh(dft, structure)
    mode, proj_lines, _summary = projection_block(cfg)
    lat = structure.lattice
    lines: list[str] = [
        f"! SiSC-Forge P3.2 Wannier90 input (seedname={seed})",
        f"! projection_mode={mode}; screening defaults may be coarse",
        f"num_wann = {int(n_wann)}",
        f"num_bands = {int(n_bands)}",
        "",
        "mp_grid = {} {} {}".format(int(mesh[0]), int(mesh[1]), int(mesh[2])),
        "",
        "begin unit_cell_cart",
        "ang",
    ]
    for vec in lat.matrix:
        lines.append(f"  {vec[0]:16.10f} {vec[1]:16.10f} {vec[2]:16.10f}")
    lines.append("end unit_cell_cart")
    lines.append("")
    lines.append("begin atoms_frac")
    for site in structure:
        c = site.frac_coords
        lines.append(
            f"  {site.specie.symbol:2s}  {c[0]:14.10f} {c[1]:14.10f} {c[2]:14.10f}"
        )
    lines.append("end atoms_frac")
    lines.append("")
    lines.extend(proj_lines)
    lines.append("")
    lines.append("dis_num_iter = 1000")
    lines.append("num_iter = 1000")
    lines.append("guiding_centres = .true.")
    lines.append("")
    lines.extend(_window_block(cfg, fermi_eV))
    lines.append("")
    lines.append("write_xyz = .true.")
    lines.append("write_hr = .true.")
    lines.append("")
    # Gamma-centred k-mesh listing is required by Wannier90 when not using
    # postw90-only modes; generate a simple grid.
    lines.append("begin kpoints")
    nx, ny, nz = int(mesh[0]), int(mesh[1]), int(mesh[2])
    for ik in range(nx):
        for jk in range(ny):
            for kk in range(nz):
                lines.append(
                    f"  {ik / nx:12.8f} {jk / ny:12.8f} {kk / nz:12.8f}"
                )
    lines.append("end kpoints")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_win_input(
    structure: Structure,
    dft: DFTConfig,
    work_dir: Path | str,
    *,
    fermi_eV: float | None = None,
    num_wann: int | None = None,
    num_bands: int | None = None,
    kmesh: list[int] | None = None,
) -> Path:
    """Write ``{seedname}.win`` under *work_dir* and return its path."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    seed = dft.wannier.seedname
    text = build_win_input(
        structure,
        dft,
        fermi_eV=fermi_eV,
        num_wann=num_wann,
        num_bands=num_bands,
        kmesh=kmesh,
        seedname=seed,
    )
    path = work_dir / f"{seed}.win"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parse full WannierResult from logs + artifacts
# ---------------------------------------------------------------------------


def parse_wannier_result(
    source: str | Path,
    *,
    dft: DFTConfig | None = None,
    work_dir: Path | str | None = None,
    quality_tag: str = "screening",
    extra_raw: dict[str, Any] | None = None,
    returncode: int | None = None,
) -> WannierResult:
    """Parse Wannier90 output text or file into :class:`WannierResult`."""
    cfg = dft.wannier if dft is not None else WannierConfig()
    path: Path | None = None
    if isinstance(source, Path):
        path = source
        text = path.read_text(encoding="utf-8", errors="replace")
        source_name = str(path)
    elif (
        isinstance(source, str)
        and len(source) < 4096
        and "\n" not in source
        and Path(source).is_file()
    ):
        path = Path(source)
        text = path.read_text(encoding="utf-8", errors="replace")
        source_name = str(path)
    else:
        text = str(source)
        source_name = "inline"

    metrics = parse_wannier_spreads(text)
    job_done = (
        "all done" in text.lower()
        or "final state" in text.lower()
        or "wannierisation finished" in text.lower()
        or "wannierization finished" in text.lower()
    )
    fail_cls: str | None = None
    if returncode not in (None, 0) or not job_done:
        fail_cls = classify_wannier_failure(text)
        if returncode not in (None, 0) and fail_cls == "other" and not job_done:
            fail_cls = "other"

    # Success: finished + spreads present + no hard failure class.
    # ``convergence`` is classified from logs but is **not** a hard_fail
    # fingerprint on its own — returncode / job_done checks below still mark
    # wannier_ok=False. Hard-fail classes force failure even when spread-like
    # text is present.
    hard_fail = fail_cls in {
        "frozen_window",
        "kmesh_bvector",
        "binary_missing",
        "missing_files",
        "projection",
        "disentanglement",
        "spread_divergence",
        "pw2wannier_failed",
        "nscf_failed",
    }
    has_spreads = bool(metrics["spreads_ang2"]) or metrics["spread_sum_ang2"] is not None
    wannier_ok = bool(job_done and has_spreads and not hard_fail and returncode in (None, 0))
    status = "ok" if wannier_ok else "failed"

    wd = Path(work_dir) if work_dir is not None else (path.parent if path else None)
    arts = (
        discover_wannier_artifacts(wd, seedname=cfg.seedname)
        if wd is not None
        else {
            "work_dir": None,
            "win_path": None,
            "amn_path": None,
            "mmn_path": None,
            "chk_path": None,
            "wout_path": source_name if path else None,
        }
    )
    if path is not None and arts.get("wout_path") is None:
        arts["wout_path"] = str(path)

    mode, _, proj_summary = projection_block(cfg)
    ready, gate_notes = assess_dmft_readiness(
        wannier_ok=wannier_ok,
        status=status,
        avg_spread=metrics["avg_spread_ang2"],
        max_spread=metrics["max_spread_ang2"],
        chk_path=arts.get("chk_path"),  # type: ignore[arg-type]
        cfg=cfg,
        failure_class=fail_cls,
    )

    qtag = (
        quality_tag
        if quality_tag in {"screening", "production", "mock", "unknown"}
        else "screening"
    )
    raw: dict[str, Any] = {
        "source": source_name,
        "job_done": job_done,
        "pathway": "wannier",
        "returncode": returncode,
        "extension_hooks": dict(_EXTENSION_HOOKS),
        "omega_i": metrics.get("omega_i"),
        "limits": _EXTENSION_HOOKS["limits"],
    }
    if extra_raw:
        raw.update(extra_raw)

    frozen_notes = ""
    dis_notes = ""
    if fail_cls == "frozen_window":
        frozen_notes = primary_wannier_failure_reason(text)
    elif cfg.screening_tight_froz:
        frozen_notes = "screening tight frozen window (EPW-aligned defaults)"
    if fail_cls == "disentanglement":
        dis_notes = primary_wannier_failure_reason(text)
    elif metrics.get("omega_i") is not None:
        dis_notes = f"Omega I = {metrics['omega_i']}"

    n_wann = metrics["num_wann"] or cfg.num_wann
    n_bands = metrics["num_bands"] or cfg.num_bands or (dft.nbnd if dft else None)

    return WannierResult(
        wannier_ok=wannier_ok,
        ready_for_dmft=ready,
        dmft_gate_notes=gate_notes,
        status=status,
        quality_tag=qtag,  # type: ignore[arg-type]
        failure_class=None if wannier_ok else fail_cls,
        num_wann=n_wann,
        num_bands=n_bands,
        projection_mode=mode,
        projection_summary=proj_summary,
        spread_sum_ang2=metrics["spread_sum_ang2"],
        avg_spread_ang2=metrics["avg_spread_ang2"],
        max_spread_ang2=metrics["max_spread_ang2"],
        spreads_ang2=list(metrics["spreads_ang2"] or []),
        disentanglement_notes=dis_notes,
        frozen_window_notes=frozen_notes,
        kmesh=list(
            (extra_raw or {}).get("actual_kmesh")
            or cfg.kmesh
            or []
        ),
        work_dir=arts.get("work_dir"),  # type: ignore[arg-type]
        win_path=arts.get("win_path"),  # type: ignore[arg-type]
        amn_path=arts.get("amn_path"),  # type: ignore[arg-type]
        mmn_path=arts.get("mmn_path"),  # type: ignore[arg-type]
        chk_path=arts.get("chk_path"),  # type: ignore[arg-type]
        wout_path=arts.get("wout_path"),  # type: ignore[arg-type]
        raw=raw,
        provenance=Provenance(
            source="qe_wannier",
            software={"siscforge": __version__},
            parameters={
                "projection_mode": mode,
                "num_wann": n_wann,
                "seedname": cfg.seedname,
                "kmesh": list(
                    (extra_raw or {}).get("actual_kmesh") or cfg.kmesh or []
                ),
            },
            notes="Wannier90 parse (P3.2)",
        ),
    )


# ---------------------------------------------------------------------------
# Mock results
# ---------------------------------------------------------------------------


def mock_wannier_result(
    *,
    seed: str,
    wannier: WannierConfig | None = None,
    formula: str = "",
    material_family: str = "other",
    quality_tag: str = "mock",
    work_dir: str | Path | None = None,
    force_failure: bool | None = None,
    failure_class: str | None = None,
) -> WannierResult:
    """Deterministic placeholder WannierResult for dry-run / mock calculator.

    When ``force_failure`` (or ``wannier.mock_force_failure``) is True, returns
    a failed result with a step-aware ``failure_class`` so dry-run tests cover
    both success and failure without a Wannier90 binary.
    """
    cfg = wannier or WannierConfig(enabled=True)
    digest = hashlib.sha256(f"{seed}:wannier".encode()).hexdigest()
    r = int(digest[:8], 16) / 0xFFFFFFFF

    fail = (
        bool(force_failure)
        if force_failure is not None
        else bool(cfg.mock_force_failure)
    )
    fcls = failure_class or cfg.mock_failure_class or "frozen_window"
    if fcls not in WANNIER_FAILURE_CLASSES:
        fcls = "other"

    n_wann = int(cfg.num_wann) if cfg.num_wann else (10 if material_family == "nickelate" else 8)
    n_bands = int(cfg.num_bands) if cfg.num_bands else max(n_wann + 4, 16)
    mode, _, proj_summary = projection_block(cfg)

    wd = Path(work_dir) if work_dir is not None else None
    seedname = cfg.seedname
    # Optionally materialise mock artifact handles under work_dir
    arts: dict[str, str | None] = {
        "work_dir": str(wd) if wd else f"mock://wannier/{seed[:12]}",
        "win_path": None,
        "amn_path": None,
        "mmn_path": None,
        "chk_path": None,
        "wout_path": None,
    }
    if wd is not None:
        wd.mkdir(parents=True, exist_ok=True)
        for key, ext in (
            ("win_path", "win"),
            ("amn_path", "amn"),
            ("mmn_path", "mmn"),
            ("chk_path", "chk"),
            ("wout_path", "wout"),
        ):
            p = wd / f"{seedname}.{ext}"
            if fail and ext in {"chk", "amn", "mmn"}:
                # Remediable Wannier failure: leave upstream SCF untouched;
                # omit incomplete artifacts for realism.
                continue
            if not p.is_file():
                if ext == "wout" and fail:
                    p.write_text(
                        "Program Wannier90\n"
                        "Error in routine dis_windows (1):\n"
                        "More states in the frozen window than target WFs\n"
                        "stopping ...\n",
                        encoding="utf-8",
                    )
                elif ext == "wout":
                    spreads = [0.8 + 0.4 * r + 0.05 * i for i in range(n_wann)]
                    body = ["Program Wannier90", "Final State"]
                    for i, sp in enumerate(spreads, 1):
                        body.append(
                            f"WF centre and spread  {i}  "
                            f"( 0.000000,  0.000000,  0.000000 )   {sp:.6f}"
                        )
                    body.append(
                        "Sum of centres and spreads "
                        f"( 0.000000,  0.000000,  0.000000 )   {sum(spreads):.6f}"
                    )
                    body.append("All done.")
                    p.write_text("\n".join(body) + "\n", encoding="utf-8")
                else:
                    p.write_text(f"mock {ext} for {seed}\n", encoding="utf-8")
            arts[key] = str(p)

    qtag = (
        quality_tag
        if quality_tag in {"screening", "production", "mock", "unknown"}
        else "mock"
    )

    if fail:
        ready, gate = assess_dmft_readiness(
            wannier_ok=False,
            status="failed",
            avg_spread=None,
            max_spread=None,
            chk_path=arts.get("chk_path"),  # type: ignore[arg-type]
            cfg=cfg,
            failure_class=fcls,
        )
        return WannierResult(
            wannier_ok=False,
            ready_for_dmft=ready,
            dmft_gate_notes=gate or f"not ready for DMFT: {fcls}",
            status="failed",
            quality_tag=qtag,  # type: ignore[arg-type]
            failure_class=fcls,
            num_wann=n_wann,
            num_bands=n_bands,
            projection_mode=mode,
            projection_summary=proj_summary,
            frozen_window_notes=(
                primary_wannier_failure_reason(
                    "More states in the frozen window than target WFs"
                )
                if fcls == "frozen_window"
                else ""
            ),
            kmesh=list(cfg.kmesh or [4, 4, 4]),
            work_dir=arts.get("work_dir"),  # type: ignore[arg-type]
            win_path=arts.get("win_path"),  # type: ignore[arg-type]
            amn_path=arts.get("amn_path"),  # type: ignore[arg-type]
            mmn_path=arts.get("mmn_path"),  # type: ignore[arg-type]
            chk_path=arts.get("chk_path"),  # type: ignore[arg-type]
            wout_path=arts.get("wout_path"),  # type: ignore[arg-type]
            raw={
                "method": "mock_wannier",
                "pathway": "wannier",
                "mock_force_failure": True,
                "extension_hooks": dict(_EXTENSION_HOOKS),
                "upstream_sacred": (
                    "SCF / DFT+U artifacts must not be deleted on Wannier failure"
                ),
            },
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                parameters={"num_wann": n_wann, "failure_class": fcls},
                notes="dry-run Wannier failure placeholder (P3.2)",
            ),
        )

    spreads = [round(0.7 + 0.5 * r + 0.08 * i, 4) for i in range(n_wann)]
    # Nickelates: slightly tighter mock spreads (more "correlated orbital" like)
    if material_family == "nickelate" or "Ni" in formula:
        spreads = [round(0.5 + 0.3 * r + 0.05 * i, 4) for i in range(n_wann)]
    avg = float(sum(spreads) / len(spreads))
    mx = float(max(spreads))
    ssum = float(sum(spreads))
    ready, gate = assess_dmft_readiness(
        wannier_ok=True,
        status="mock",
        avg_spread=avg,
        max_spread=mx,
        chk_path=arts.get("chk_path") or "mock://chk",  # type: ignore[arg-type]
        cfg=cfg,
    )
    # Mock always has a virtual chk handle for DMFT gate when not force-failed
    if cfg.require_chk and not arts.get("chk_path"):
        arts["chk_path"] = f"mock://wannier/{seed[:12]}/{seedname}.chk"
        ready, gate = assess_dmft_readiness(
            wannier_ok=True,
            status="mock",
            avg_spread=avg,
            max_spread=mx,
            chk_path=arts["chk_path"],
            cfg=cfg,
        )

    return WannierResult(
        wannier_ok=True,
        ready_for_dmft=ready,
        dmft_gate_notes=gate,
        status="mock",
        quality_tag=qtag,  # type: ignore[arg-type]
        failure_class=None,
        num_wann=n_wann,
        num_bands=n_bands,
        projection_mode=mode,
        projection_summary=proj_summary,
        spread_sum_ang2=round(ssum, 4),
        avg_spread_ang2=round(avg, 4),
        max_spread_ang2=round(mx, 4),
        spreads_ang2=spreads,
        disentanglement_notes="mock screening disentanglement (placeholder)",
        frozen_window_notes=(
            "screening tight frozen window (mock)" if cfg.screening_tight_froz else ""
        ),
        kmesh=list(cfg.kmesh or [4, 4, 4]),
        work_dir=arts.get("work_dir"),  # type: ignore[arg-type]
        win_path=arts.get("win_path"),  # type: ignore[arg-type]
        amn_path=arts.get("amn_path"),  # type: ignore[arg-type]
        mmn_path=arts.get("mmn_path"),  # type: ignore[arg-type]
        chk_path=arts.get("chk_path"),  # type: ignore[arg-type]
        wout_path=arts.get("wout_path"),  # type: ignore[arg-type]
        raw={
            "method": "mock_wannier",
            "pathway": "wannier",
            "extension_hooks": dict(_EXTENSION_HOOKS),
            "formula": formula,
            "material_family": material_family,
        },
        provenance=Provenance(
            source="mock_calculator",
            software={"siscforge": __version__},
            parameters={"num_wann": n_wann, "projection_mode": mode},
            notes="dry-run Wannier placeholder (P3.2)",
        ),
    )


# ---------------------------------------------------------------------------
# Real-path sequential recipe (optional; gated on binaries)
# ---------------------------------------------------------------------------


def require_wannier90():
    """Return QE env with wannier90.x or raise.

    Single source of truth lives in :mod:`siscforge.calculators.qe.env`;
    this is a thin re-export for callers already importing from wannier.
    """
    from siscforge.calculators.qe.env import require_wannier90 as _require

    return _require()


def run_wannier90_on_artifacts(
    work_dir: Path | str,
    dft: DFTConfig,
    *,
    structure: Structure | None = None,
    fermi_eV: float | None = None,
    qe_env=None,
) -> WannierResult:
    """Run ``wannier90.x`` on an existing prep directory (``.win`` + ``.amn``/``.mmn``).

    Sacred-upstream contract: this function never deletes SCF / DFT+U outputs
    outside *work_dir*'s Wannier seedname files. Callers should place Wannier
    work under a subdirectory (e.g. ``cand_dir / "wannier"``).
    """
    import shutil
    import subprocess

    from siscforge.calculators.qe.env import detect_qe_environment
    from siscforge.calculators.qe.recipes import QEStepResult

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = dft.wannier
    seed = cfg.seedname

    # Ensure .win exists
    win_path = work_dir / f"{seed}.win"
    if not win_path.is_file():
        if structure is None:
            return WannierResult(
                wannier_ok=False,
                ready_for_dmft=False,
                dmft_gate_notes="not ready for DMFT: missing .win and structure",
                status="failed",
                quality_tag=dft.quality_tag,
                failure_class="missing_files",
                work_dir=str(work_dir),
                raw={
                    "pathway": "wannier",
                    "extension_hooks": dict(_EXTENSION_HOOKS),
                },
            )
        write_win_input(structure, dft, work_dir, fermi_eV=fermi_eV)

    env = qe_env or detect_qe_environment()
    w90 = getattr(env, "wannier90", None)
    if not w90:
        w90 = shutil.which("wannier90.x")
    if not w90:
        return WannierResult(
            wannier_ok=False,
            ready_for_dmft=False,
            dmft_gate_notes="not ready for DMFT: wannier90.x missing",
            status="failed",
            quality_tag=dft.quality_tag,
            failure_class="binary_missing",
            work_dir=str(work_dir),
            win_path=str(win_path) if win_path.is_file() else None,
            raw={
                "pathway": "wannier",
                "extension_hooks": dict(_EXTENSION_HOOKS),
            },
            provenance=Provenance(
                source="qe_wannier",
                software={"siscforge": __version__},
                notes="wannier90.x not available",
            ),
        )

    # Pre-check amn/mmn (pw2wannier90 must have been run by caller for real path)
    amn = work_dir / f"{seed}.amn"
    mmn = work_dir / f"{seed}.mmn"
    if not amn.is_file() or not mmn.is_file():
        # Still allow wannier90 -pp (preprocess) only when missing; full MLWF needs them
        # For a complete run we classify as missing_files.
        result = WannierResult(
            wannier_ok=False,
            ready_for_dmft=False,
            dmft_gate_notes="not ready for DMFT: missing .amn/.mmn (stage nscf+pw2wannier90 into work_dir)",
            status="failed",
            quality_tag=dft.quality_tag,
            failure_class="missing_files",
            projection_mode=cfg.projection_mode,
            work_dir=str(work_dir),
            win_path=str(win_path),
            amn_path=str(amn) if amn.is_file() else None,
            mmn_path=str(mmn) if mmn.is_file() else None,
            raw={
                "pathway": "wannier",
                "note": "prep-only or incomplete pw2wannier90",
                "extension_hooks": dict(_EXTENSION_HOOKS),
            },
        )
        return result

    wout = work_dir / f"{seed}.wout"
    cmd = [str(w90), seed]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        rc = int(proc.returncode)
        # wannier90 writes .wout itself; capture merge if empty
        if not wout.is_file() or wout.stat().st_size == 0:
            wout.write_text(
                (proc.stdout or "") + "\n" + (proc.stderr or ""),
                encoding="utf-8",
            )
    except OSError as exc:
        return WannierResult(
            wannier_ok=False,
            ready_for_dmft=False,
            dmft_gate_notes=f"not ready for DMFT: launch error ({exc})",
            status="failed",
            quality_tag=dft.quality_tag,
            failure_class="binary_missing",
            work_dir=str(work_dir),
            win_path=str(win_path),
            raw={"pathway": "wannier", "error": str(exc)},
        )

    # Keep a step-shaped breadcrumb for workflow logs (unused return ok)
    _ = QEStepResult(
        name="wannier90",
        work_dir=work_dir,
        returncode=rc,
        stdout_path=wout,
        input_path=win_path,
        success=rc == 0,
        message=f"wannier90.x rc={rc}",
    )

    actual_kmesh = resolve_kmesh(dft, structure) if structure is not None else list(
        dft.wannier.kmesh or []
    )
    return parse_wannier_result(
        wout if wout.is_file() else (proc.stdout or ""),
        dft=dft,
        work_dir=work_dir,
        quality_tag=dft.quality_tag,
        returncode=rc,
        extra_raw={"cmd": cmd, "rc": rc, "actual_kmesh": actual_kmesh},
    )


def run_wannier_workflow(
    structure: Structure,
    config: DFTConfig,
    work_dir: Path | str,
    *,
    prefix: str = "siscforge",
    fermi_eV: float | None = None,
    qe_env=None,
    scf_work_dir: Path | str | None = None,
    step_log: list[str] | None = None,
) -> WannierResult:
    """Prep + optional gated ``wannier90.x`` under *work_dir*.

    Writes ``.win`` always. Invokes ``wannier90.x`` only when ``.amn``/``.mmn``
    are already staged (no automated nscf / pw2wannier90 in P3.2 — residual
    **P3.2.1**). Missing prep returns ``failure_class=missing_files`` with an
    operator next-step; upstream SCF/DFT+U is never touched.

    Parameters
    ----------
    scf_work_dir:
        Optional path to finished SCF / DFT+U artifacts. **Never modified or
        deleted** on Wannier failure (sacred-upstream contract, same philosophy
        as EPW-after-DFPT).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    log: list[str] = step_log if step_log is not None else []

    # Always write a .win for audit / P3.3 extension even if binary missing
    win = write_win_input(structure, config, work_dir, fermi_eV=fermi_eV)
    log.append(f"wrote {win.name}")

    if scf_work_dir is not None:
        log.append(f"upstream_scf={scf_work_dir} (sacred — not modified)")

    # Real path requires .amn/.mmn from pw2wannier90 — if absent, classify
    # cleanly without destroying upstream.
    seed = config.wannier.seedname
    amn = work_dir / f"{seed}.amn"
    mmn = work_dir / f"{seed}.mmn"
    if not amn.is_file() or not mmn.is_file():
        # Attempt is still valuable: .win is ready for operators / later steps
        result = WannierResult(
            wannier_ok=False,
            ready_for_dmft=False,
            dmft_gate_notes=(
                "not ready for DMFT: .amn/.mmn not present — "
                "stage nscf + pw2wannier90 outputs into this work_dir, "
                "then re-invoke or call run_wannier90_on_artifacts"
            ),
            status="failed",
            quality_tag=config.quality_tag,
            failure_class="missing_files",
            num_wann=config.wannier.num_wann
            or default_num_wann_screening(
                num_bands=config.nbnd or config.wannier.num_bands,
                structure=structure,
                explicit=config.wannier.num_wann,
                auto=config.wannier.auto_num_wann,
            ),
            num_bands=config.nbnd or config.wannier.num_bands,
            projection_mode=projection_block(config.wannier)[0],
            projection_summary=projection_block(config.wannier)[2],
            frozen_window_notes=(
                "screening tight frozen window" if config.wannier.screening_tight_froz else ""
            ),
            kmesh=resolve_kmesh(config, structure),
            work_dir=str(work_dir),
            win_path=str(win),
            amn_path=str(amn) if amn.is_file() else None,
            mmn_path=str(mmn) if mmn.is_file() else None,
            raw={
                "pathway": "wannier",
                "prefix": prefix,
                "scf_work_dir": str(scf_work_dir) if scf_work_dir else None,
                "extension_hooks": dict(_EXTENSION_HOOKS),
                "actual_kmesh": resolve_kmesh(config, structure),
                "note": (
                    "Real path is prep + gated wannier90.x only (P3.2). "
                    "Stage nscf + pw2wannier90 (.amn/.mmn) into this work_dir, "
                    "then re-invoke or call run_wannier90_on_artifacts. "
                    "Automated nscf/pw2wannier90 orchestration is residual P3.2.1."
                ),
                "operator_next_step": (
                    "stage nscf+pw2wannier90 (.amn/.mmn) into work_dir, "
                    "then re-invoke / run_wannier90_on_artifacts"
                ),
            },
            provenance=Provenance(
                source="qe_wannier",
                software={"siscforge": __version__},
                notes="Wannier prep only — missing .amn/.mmn",
            ),
        )
        log.append("wannier prep incomplete (missing .amn/.mmn)")
        if step_log is not None:
            step_log[:] = log
        return result

    result = run_wannier90_on_artifacts(
        work_dir,
        config,
        structure=structure,
        fermi_eV=fermi_eV,
        qe_env=qe_env,
    )
    log.append(result.summary_line())
    if step_log is not None:
        step_log[:] = log
    return result
