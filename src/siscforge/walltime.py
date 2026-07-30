"""Heuristic desktop walltime bands for QE / EPW campaigns.

Estimates are **order-of-magnitude guidance only**. Machine load, soft-mode
convergence, and I/O dominate real walltime; bands are intentionally wide.
Exact ETAs are impossible — use these to plan overnight/weekend runs, not to
schedule HPC allocations.

Reference anchors (desktop, single-node MPI):
- Screening shortlist, 8-atom Nb–Ti–N, ~16 cores: full candidate often ~1–6 h
- workstation_dense refine DFPT on 8-atom cells has been observed >37 h with
  healthy heartbeats; plan multi-day for a 2-candidate refine campaign

No Folding@home-style mid-iteration checkpoints here — only messaging.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import DFTConfig, RunConfig

TierName = Literal["screening", "workstation_dense", "production"]

# Reference cell / MPI for base bands
_REF_N_ATOMS = 8
_REF_NPROC = 16

# Base walltime bands (hours) at ref n_atoms / nproc / grids.
# dfpt: multi-q ph.x only; full: relax → SCF → DFPT → (EPW when enabled).
_TIER_BASE_H: dict[str, dict[str, Any]] = {
    "screening": {
        "dfpt": (0.5, 4.0),
        "full": (1.0, 6.0),
        "ref_q": 8,  # 2×2×2
        "ref_nkf": 6 * 6 * 6,
    },
    "workstation_dense": {
        "dfpt": (12.0, 48.0),
        "full": (24.0, 72.0),  # ~1–3 days
        "ref_q": 4 * 4 * 4,
        "ref_nkf": 12 * 12 * 12,
    },
    "production": {
        "dfpt": (24.0, 96.0),
        "full": (48.0, 168.0),  # multi-day
        "ref_q": 6 * 6 * 6,
        "ref_nkf": 18 * 18 * 18,
    },
}


def _grid_product(grid: list[int] | tuple[int, ...] | None, default: int = 8) -> int:
    if not grid:
        return default
    p = 1
    for g in list(grid)[:3]:
        p *= max(1, int(g))
    return p


def resolve_walltime_tier(
    dft: DFTConfig,
    *,
    explicit: str | None = None,
) -> TierName:
    """Map DFTConfig (+ optional explicit tier) to an estimation tier.

    Screening quality_tag → screening. Production-labeled configs are split by
    q-mesh product into workstation_dense vs production (matches refine presets).
    """
    if explicit:
        key = explicit.strip().lower().replace("-", "_")
        if key in _TIER_BASE_H:
            return key  # type: ignore[return-value]
    # Campaign extras often store refine tier
    qtag = (dft.quality_tag or "screening").lower()
    nqc = list(dft.epw.nqc) if dft.epw.nqc else list(dft.qpoints)
    qprod = _grid_product(nqc, default=_grid_product(dft.qpoints, 8))
    if qtag == "screening":
        return "screening"
    # production label from refine: 4³ → workstation_dense, 6³+ → production
    if qprod >= 125:  # 5³+
        return "production"
    if qprod >= 27:  # 3³+
        return "workstation_dense"
    # production tag but coarse mesh — still treat as denser than screening
    return "workstation_dense"


def n_atoms_from_candidate(candidate: StructureCandidate | None) -> int:
    """Best-effort atom count (CIF → metadata → conservative default 8)."""
    if candidate is None:
        return _REF_N_ATOMS
    meta = candidate.metadata or {}
    if "n_atoms" in meta:
        try:
            return max(1, int(meta["n_atoms"]))
        except (TypeError, ValueError):
            pass
    if candidate.structure_cif:
        try:
            from pymatgen.core import Structure

            return max(1, len(Structure.from_str(candidate.structure_cif, fmt="cif")))
        except Exception:  # noqa: BLE001
            pass
    # Binary rocksalt often 2 sites; ternaries / shortlists default to supercell 8
    formula = (candidate.formula or "").lower()
    if formula in {"nbn", "tin", "zrn", "hfn", "vn", "mgb2"}:
        return 2
    return _REF_N_ATOMS


def _scale_factor(
    *,
    n_atoms: int,
    nproc: int,
    q_product: int,
    nkf_product: int,
    tier: TierName,
    scale: float,
) -> float:
    """Multiply base band by atoms / q-mesh / MPI / mild EPW-grid factors."""
    base = _TIER_BASE_H[tier]
    atoms = max(1, int(n_atoms))
    # DFPT cost grows faster than linear with atoms (modes ~ 3N)
    atoms_f = max(0.2, (atoms / _REF_N_ATOMS) ** 1.4)
    q_ref = max(1, int(base["ref_q"]))
    q_f = max(0.25, q_product / q_ref)
    # Imperfect strong scaling on desktop
    nproc_f = max(0.15, (_REF_NPROC / max(1, int(nproc))) ** 0.65)
    nkf_ref = max(1, int(base["ref_nkf"]))
    # Fine grids add mild cost beyond DFPT
    ratio = max(1.0, nkf_product / nkf_ref)
    epw_f = 1.0 + 0.12 * math.log2(ratio)
    return max(0.05, float(scale) * atoms_f * q_f * nproc_f * epw_f)


def format_duration_band(lo_h: float, hi_h: float) -> str:
    """Human-readable order-of-magnitude band from hour bounds."""
    lo = max(0.05, float(lo_h))
    hi = max(lo * 1.2, float(hi_h))

    def _unit(h: float) -> tuple[float, str]:
        if h < 1.0:
            return h * 60.0, "min"
        if h < 36.0:
            return h, "h"
        return h / 24.0, "d"

    lo_v, lo_u = _unit(lo)
    hi_v, hi_u = _unit(hi)

    def _fmt(v: float, u: str) -> str:
        if u == "min":
            return f"{max(5, int(round(v)))} min"
        if u == "h":
            if v < 10:
                # one decimal for small hours
                s = f"{v:.1f}".rstrip("0").rstrip(".")
                return f"{s} h"
            return f"{int(round(v))} h"
        # days
        if v < 10:
            s = f"{v:.1f}".rstrip("0").rstrip(".")
            return f"{s} d"
        return f"{int(round(v))} d"

    if lo_u == hi_u:
        lo_s = (
            _fmt(lo_v, lo_u)
            .replace(" min", "")
            .replace(" h", "")
            .replace(" d", "")
        )
        return f"~{lo_s}–{_fmt(hi_v, hi_u)}"
    return f"~{_fmt(lo_v, lo_u)} – {_fmt(hi_v, hi_u)}"


@dataclass(frozen=True)
class WalltimeEstimate:
    """Order-of-magnitude walltime band for one candidate or a campaign."""

    tier: TierName
    n_atoms: int
    nproc: int
    q_product: int
    nkf_product: int
    do_epw: bool
    dfpt_lo_h: float
    dfpt_hi_h: float
    full_lo_h: float
    full_hi_h: float
    n_candidates: int = 1
    scale: float = 1.0
    observed_adjustment: float | None = None
    notes: str = ""

    @property
    def campaign_lo_h(self) -> float:
        return self.full_lo_h * max(1, self.n_candidates)

    @property
    def campaign_hi_h(self) -> float:
        return self.full_hi_h * max(1, self.n_candidates)

    def dfpt_band(self) -> str:
        return format_duration_band(self.dfpt_lo_h, self.dfpt_hi_h)

    def full_band(self) -> str:
        return format_duration_band(self.full_lo_h, self.full_hi_h)

    def campaign_band(self) -> str:
        return format_duration_band(self.campaign_lo_h, self.campaign_hi_h)

    def per_candidate_line(self) -> str:
        nproc = self.nproc
        if self.do_epw:
            return (
                f"DFPT {self.dfpt_band()}; "
                f"full candidate (relax→EPW) {self.full_band()} "
                f"on ~{nproc} cores (order-of-magnitude)"
            )
        return (
            f"DFPT {self.dfpt_band()}; "
            f"full candidate (relax→phonon) {self.full_band()} "
            f"on ~{nproc} cores (order-of-magnitude)"
        )

    def campaign_line(self) -> str:
        n = max(1, self.n_candidates)
        return (
            f"this campaign (~{n} candidate{'s' if n != 1 else ''}, sequential): "
            f"{self.campaign_band()}"
        )


def estimate_candidate_walltime(
    dft: DFTConfig,
    *,
    n_atoms: int | None = None,
    candidate: StructureCandidate | None = None,
    n_candidates: int = 1,
    scale: float = 1.0,
    tier: str | None = None,
    observed_scale: float | None = None,
) -> WalltimeEstimate:
    """Estimate order-of-magnitude walltime bands for one expensive candidate.

    Parameters
    ----------
    dft
        Active DFT/EPW config (quality_tag, grids, nproc, do_epw).
    n_atoms
        Explicit atom count; else derived from *candidate* or default 8.
    n_candidates
        Campaign size for sequential total band (still stored on the estimate).
    scale
        User/run knob to stretch or shrink bands (``run.walltime_scale``).
    tier
        Force ``screening`` / ``workstation_dense`` / ``production``.
    observed_scale
        Optional in-run refinement (median observed / predicted so far).
    """
    atoms = int(n_atoms) if n_atoms is not None else n_atoms_from_candidate(candidate)
    tier_name = resolve_walltime_tier(dft, explicit=tier)
    base = _TIER_BASE_H[tier_name]
    nqc = list(dft.epw.nqc) if dft.epw.nqc else list(dft.qpoints)
    q_prod = _grid_product(nqc, default=_grid_product(dft.qpoints, 8))
    nkf_prod = _grid_product(dft.epw.nkf, default=6 * 6 * 6)
    nproc = max(1, int(dft.nproc))
    do_epw = bool(dft.do_epw or dft.epw.enabled)
    eff_scale = float(scale) * (float(observed_scale) if observed_scale else 1.0)
    fac = _scale_factor(
        n_atoms=atoms,
        nproc=nproc,
        q_product=q_prod,
        nkf_product=nkf_prod if do_epw else base["ref_nkf"],
        tier=tier_name,
        scale=eff_scale,
    )
    dfpt_lo, dfpt_hi = base["dfpt"]
    full_lo, full_hi = base["full"]
    if not do_epw:
        # Without EPW, full path is closer to DFPT + light SCF/relax overhead
        full_lo = dfpt_lo * 1.15
        full_hi = dfpt_hi * 1.35
    return WalltimeEstimate(
        tier=tier_name,
        n_atoms=atoms,
        nproc=nproc,
        q_product=q_prod,
        nkf_product=nkf_prod,
        do_epw=do_epw,
        dfpt_lo_h=dfpt_lo * fac,
        dfpt_hi_h=dfpt_hi * fac,
        full_lo_h=full_lo * fac,
        full_hi_h=full_hi * fac,
        n_candidates=max(1, int(n_candidates)),
        scale=eff_scale,
        observed_adjustment=observed_scale,
        notes=(
            "heuristic only — machine load and convergence dominate; "
            "not a guarantee"
        ),
    )


def estimate_campaign_walltime(
    dft: DFTConfig,
    *,
    n_candidates: int,
    candidates: list[StructureCandidate] | None = None,
    scale: float = 1.0,
    tier: str | None = None,
    observed_scale: float | None = None,
) -> WalltimeEstimate:
    """Campaign band: sequential sum of per-candidate bands (desktop default).

    When *candidates* is provided, uses median atom count; otherwise n_atoms=8.
    """
    n_atoms: int | None = None
    if candidates:
        counts = [n_atoms_from_candidate(c) for c in candidates]
        counts.sort()
        n_atoms = counts[len(counts) // 2]
    return estimate_candidate_walltime(
        dft,
        n_atoms=n_atoms,
        n_candidates=n_candidates,
        scale=scale,
        tier=tier,
        observed_scale=observed_scale,
    )


def format_campaign_estimate_lines(
    estimate: WalltimeEstimate,
    *,
    remaining_candidates: int | None = None,
) -> list[str]:
    """CLI lines printed at campaign start (qe / qe-epw, not mock)."""
    n = (
        remaining_candidates
        if remaining_candidates is not None
        else estimate.n_candidates
    )
    n = max(1, int(n))
    # Rebuild campaign band if remaining differs from estimate.n_candidates
    if n != estimate.n_candidates:
        est = WalltimeEstimate(
            tier=estimate.tier,
            n_atoms=estimate.n_atoms,
            nproc=estimate.nproc,
            q_product=estimate.q_product,
            nkf_product=estimate.nkf_product,
            do_epw=estimate.do_epw,
            dfpt_lo_h=estimate.dfpt_lo_h,
            dfpt_hi_h=estimate.dfpt_hi_h,
            full_lo_h=estimate.full_lo_h,
            full_hi_h=estimate.full_hi_h,
            n_candidates=n,
            scale=estimate.scale,
            observed_adjustment=estimate.observed_adjustment,
            notes=estimate.notes,
        )
    else:
        est = estimate

    lines = [
        "Estimated walltime (heuristic, not a guarantee):",
        f"  per candidate: {est.per_candidate_line()}",
        f"  {est.campaign_line()}",
        "  Tip: safe to interrupt; re-run the same command to resume "
        "finished steps/candidates.",
        f"  tier={est.tier}, n_atoms≈{est.n_atoms}, q-mesh={est.q_product} pts, "
        f"nproc={est.nproc}"
        + (f", nkf={est.nkf_product}" if est.do_epw else ""),
    ]
    if est.observed_adjustment is not None:
        lines.append(
            f"  (adjusted ×{est.observed_adjustment:.2f} from earlier "
            f"candidates in this run)"
        )
    return lines


# ---------------------------------------------------------------------------
# In-run observed walltime (simple, in-memory)
# ---------------------------------------------------------------------------


@dataclass
class WalltimeTracker:
    """Record finished-candidate walltimes to refine messaging mid-campaign."""

    predictions_h: list[float] = field(default_factory=list)
    observed_h: list[float] = field(default_factory=list)
    _t0: dict[str, float] = field(default_factory=dict)

    def start(self, candidate_id: str) -> None:
        self._t0[candidate_id] = time.monotonic()

    def finish(
        self,
        candidate_id: str,
        *,
        predicted_mid_h: float | None = None,
    ) -> float | None:
        """Return observed hours if start was recorded."""
        t0 = self._t0.pop(candidate_id, None)
        if t0 is None:
            return None
        hours = max(0.0, (time.monotonic() - t0) / 3600.0)
        self.observed_h.append(hours)
        if predicted_mid_h is not None and predicted_mid_h > 0:
            self.predictions_h.append(float(predicted_mid_h))
        return hours

    def observed_scale(self) -> float | None:
        """Median(observed/predicted) when both lists non-empty; else None."""
        if not self.observed_h or not self.predictions_h:
            return None
        n = min(len(self.observed_h), len(self.predictions_h))
        if n < 1:
            return None
        ratios = [
            self.observed_h[i] / self.predictions_h[i]
            for i in range(n)
            if self.predictions_h[i] > 1e-6
        ]
        if not ratios:
            return None
        ratios.sort()
        med = ratios[len(ratios) // 2]
        # Clamp so one outlier cannot collapse messaging to zero or explode
        return max(0.35, min(3.0, med))

    def summary_line(self) -> str | None:
        if not self.observed_h:
            return None
        last = self.observed_h[-1]
        mid = sum(self.observed_h) / len(self.observed_h)
        return (
            f"observed walltime last candidate ~{format_duration_band(last, last)}; "
            f"mean so far ~{format_duration_band(mid, mid)} "
            f"({len(self.observed_h)} finished)"
        )


# ---------------------------------------------------------------------------
# Heartbeat progress → remaining-time hint
# ---------------------------------------------------------------------------

_Q_TOTAL_RE = re.compile(r"\(\s*(\d+)\s*q-points\s*\)", re.I)
_Q_CALC_RE = re.compile(r"Calculation of q\s*=", re.I)
# Some QE builds: "q-point #   3  of   8" or "Computing dynamical matrix for q # 2"
_Q_OF_RE = re.compile(
    r"(?:q[- ]?point|q)\s*#\s*(\d+)\s*(?:of|/)\s*(\d+)",
    re.I,
)
_REP_RE = re.compile(r"Representation\s*#\s*(\d+)", re.I)


def parse_ph_progress(text: str | None) -> tuple[float, str] | None:
    """Infer progress fraction from ph.x (or similar) log text.

    Returns ``(fraction, label)`` where fraction ∈ (0, 1], or ``None`` if
    progress cannot be inferred reliably. Does **not** invent precision.
    """
    if not text or not str(text).strip():
        return None
    body = str(text)

    # Explicit "q # i of N"
    matches = list(_Q_OF_RE.finditer(body))
    if matches:
        m = matches[-1]
        i, n = int(m.group(1)), int(m.group(2))
        if n > 0 and 1 <= i <= n:
            return i / n, f"q {i}/{n}"

    # Header total + count of "Calculation of q ="
    totals = _Q_TOTAL_RE.findall(body)
    if totals:
        n = int(totals[-1])
        if n > 0:
            calcs = _Q_CALC_RE.findall(body)
            i = len(calcs)
            if i > 0:
                i = min(i, n)
                return i / n, f"q {i}/{n}"

    # Representation index alone is weak (unknown total modes) — skip
    return None


def remaining_time_hint(
    elapsed_s: float,
    fraction: float,
    *,
    min_fraction: float = 0.08,
    max_fraction: float = 0.97,
) -> str | None:
    """Wide remaining-time band from elapsed / progress fraction.

    Returns None when fraction is too early/late for a meaningful hint.
    """
    if fraction < min_fraction or fraction > max_fraction:
        return None
    if elapsed_s <= 0 or fraction <= 0:
        return None
    total = elapsed_s / fraction
    rem = max(0.0, total - elapsed_s)
    # Wide band: avoid fake precision
    lo_h = (rem * 0.65) / 3600.0
    hi_h = (rem * 1.9) / 3600.0
    if hi_h < 0.05:
        return None
    return f"~{format_duration_band(lo_h, hi_h)} remaining (rough)"


def heartbeat_eta_suffix(
    log_text: str | None,
    elapsed_s: float,
    *,
    enabled: bool = True,
) -> str:
    """Optional '· ~X–Y remaining' fragment for heartbeat lines."""
    if not enabled:
        return ""
    prog = parse_ph_progress(log_text)
    if prog is None:
        return ""
    frac, label = prog
    hint = remaining_time_hint(elapsed_s, frac)
    if not hint:
        return f"; progress {label}"
    return f"; progress {label}; {hint}"


def should_print_walltime_estimate(
    calc_name: str,
    run: RunConfig | None = None,
) -> bool:
    """True for real QE/EPW paths when estimates are not disabled."""
    if calc_name not in {"qe", "qe-epw"}:
        return False
    if run is None:
        return True
    return bool(getattr(run, "estimate_walltime", True))
