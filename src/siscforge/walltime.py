"""Heuristic desktop walltime bands for QE / EPW campaigns.

Estimates are **order-of-magnitude guidance only**. Machine load, soft-mode
convergence, and I/O dominate real walltime; bands are intentionally wide.
Exact ETAs are impossible — use these to plan overnight/weekend runs, not to
schedule HPC allocations.

Dense-k / dense-q jobs that still carry ``quality_tag: screening`` (phonon-only
maps, k-mesh diagnostics) can still be **multi-hour** on a workstation. The
printed ``q-mesh=`` is the mesh actually used for DFPT.

Reference anchors (desktop, single-node MPI):
- Screening shortlist, 8-atom Nb–Ti–N, ~16 cores: full candidate often ~1–6 h
- workstation_dense refine DFPT on 8-atom cells has been observed >37 h with
  healthy heartbeats; plan multi-day for a 2-candidate refine campaign
- Phonon-only 2-atom ZrN at q=4³ / k=12³ / 16 cores has been observed ~6 h
  (do not advertise a ~1 h band for that class)
- Supercell screening DFPT (N-vacancy maps): 15-atom Nb8N7 at k=8³ / q=2³ /
  16 cores realized ~115 h (~4–5 wall-days); Zr8N7 partial ~91 h. The
  heuristic previously advertised ~9 h mid for that class — far too low.
  Estimates for n_atoms≥12 with k≥8³ now target ~80–120 h mid (wide bands).

Realized walltimes append to ``{output_dir}/walltime_realized.jsonl`` and a
summary JSON; packaged seed under ``data/walltime/`` calibrates
``observed_scale`` / anchored bands across runs. Clamp max scale is 5.0.

No Folding@home-style mid-iteration checkpoints here — only messaging.
"""

from __future__ import annotations

import json
import math
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import DFTConfig, RunConfig

TierName = Literal["screening", "workstation_dense", "production"]

# Reference cell / MPI for base bands
_REF_N_ATOMS = 8
_REF_NPROC = 16
# Typical screening SCF mesh; denser k is a sub-linear extra cost
_REF_K_PRODUCT = 4 * 4 * 4

# observed_scale clamp (raised from 3.0 so supercell under-prediction can correct)
_OBSERVED_SCALE_LO = 0.35
_OBSERVED_SCALE_HI = 5.0

REALIZED_SCHEMA = "siscforge.walltime_realized.v1"
SUMMARY_SCHEMA = "siscforge.walltime_realized_summary.v1"
REALIZED_JSONL_NAME = "walltime_realized.jsonl"
REALIZED_SUMMARY_NAME = "walltime_realized_summary.json"

# Dense-k supercell screening: replace screening DFPT base so mid ~80–120 h
# for 15-atom / k=8³ / q=2³ / 16 cores after the usual scale factors (~4×).
_SUPERCELL_SCREENING_DFPT_H = (8.0, 40.0)  # mid base 24 → ~100 h after fac
_SUPERCELL_MIN_ATOMS = 12
_SUPERCELL_MIN_K_PRODUCT = 8 * 8 * 8


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


def _epw_enabled(dft: DFTConfig) -> bool:
    return bool(dft.do_epw or getattr(dft.epw, "enabled", False))


def dfpt_q_grid(dft: DFTConfig) -> list[int]:
    """q-mesh actually used for DFPT (``ph.x``), not unused EPW defaults.

    When EPW is off, ``dft.qpoints`` is what the calculator runs. Preferring
    ``epw.nqc`` (default 2×2×2) in that case under-counts a 4³ map by 8×.
    When EPW is on, coarse ``nqc`` is the DFPT mesh EPW interpolates from.
    """
    if _epw_enabled(dft) and dft.epw.nqc:
        return list(dft.epw.nqc)
    return list(dft.qpoints) if dft.qpoints else [2, 2, 2]


def resolve_walltime_tier(
    dft: DFTConfig,
    *,
    explicit: str | None = None,
) -> TierName:
    """Map DFTConfig (+ optional explicit tier) to an estimation tier.

    Dense q-meshes upgrade the tier even when ``quality_tag`` is still
    ``screening`` (phonon-only maps often keep that tag while using 4³).
    Production-labeled configs are split by q-mesh product into
    workstation_dense vs production (matches refine presets).
    """
    if explicit:
        key = explicit.strip().lower().replace("-", "_")
        if key in _TIER_BASE_H:
            return key  # type: ignore[return-value]
    qtag = (dft.quality_tag or "screening").lower()
    qprod = _grid_product(dfpt_q_grid(dft), default=_grid_product(dft.qpoints, 8))
    # Mesh density wins over the quality_tag floor so 4³ DFPT is not priced
    # as a 2³ screening shortlist.
    if qprod >= 125:  # 5³+
        return "production"
    if qprod >= 27:  # 3³+
        return "workstation_dense"
    if qtag == "screening":
        return "screening"
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


def _k_factor(k_product: int) -> float:
    """Sub-linear SCF/DFPT cost vs k-mesh, relative to a 4³ screening mesh.

    k=4³ → 1.0; k=8³ → ~1.7; k=12³ → ~2.3. Floor at 1.0 so a coarse k-mesh
    does not shrink a dense-q band below the q/atom model.
    """
    ratio = max(1, int(k_product)) / _REF_K_PRODUCT
    return max(1.0, ratio**0.25)


def _scale_factor(
    *,
    n_atoms: int,
    nproc: int,
    q_product: int,
    nkf_product: int,
    k_product: int,
    tier: TierName,
    scale: float,
) -> float:
    """Multiply base band by atoms / q-mesh / k-mesh / MPI / mild EPW-grid factors."""
    base = _TIER_BASE_H[tier]
    atoms = max(1, int(n_atoms))
    # DFPT cost grows faster than linear with atoms (modes ~ 3N).
    # Floor keeps 2-atom binaries from collapsing ~7× vs the 8-atom ref —
    # each q-point still does a full metallic DFPT cycle.
    atoms_f = max(0.2, (atoms / _REF_N_ATOMS) ** 1.4)
    q_ref = max(1, int(base["ref_q"]))
    q_f = max(0.25, q_product / q_ref)
    # Imperfect strong scaling on desktop
    nproc_f = max(0.15, (_REF_NPROC / max(1, int(nproc))) ** 0.65)
    nkf_ref = max(1, int(base["ref_nkf"]))
    # Fine grids add mild cost beyond DFPT
    ratio = max(1.0, nkf_product / nkf_ref)
    epw_f = 1.0 + 0.12 * math.log2(ratio)
    k_f = _k_factor(k_product)
    return max(0.05, float(scale) * atoms_f * q_f * nproc_f * epw_f * k_f)


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
    k_product: int
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


def atoms_band(n_atoms: int) -> str:
    """Bucket atom counts for realized-class matching."""
    n = max(1, int(n_atoms))
    if n <= 4:
        return "le4"
    if n <= 10:
        return "5to10"
    if n <= 20:
        return "11to20"
    return "gt20"


def k_band(k_product: int) -> str:
    """Bucket k-mesh products for realized-class matching."""
    p = max(1, int(k_product))
    if p < 64:  # <4³
        return "lt64"
    if p < 512:  # 4³ .. <8³
        return "64to511"
    if p < 1728:  # 8³ .. <12³
        return "512to1727"
    return "ge1728"


def walltime_class_key(
    *,
    tier: str,
    do_epw: bool,
    q_product: int,
    k_product: int,
    n_atoms: int,
    nproc: int,
) -> tuple[Any, ...]:
    """Stable class key for matching prior realized walltimes."""
    return (
        str(tier),
        bool(do_epw),
        int(q_product),
        k_band(k_product),
        atoms_band(n_atoms),
        int(nproc),
    )


def class_key_from_record(rec: dict[str, Any]) -> tuple[Any, ...] | None:
    try:
        kpts = rec.get("kpoints") or []
        qpts = rec.get("qpoints") or []
        k_prod = _grid_product(kpts, default=_REF_K_PRODUCT)
        q_prod = _grid_product(qpts, default=8)
        return walltime_class_key(
            tier=str(rec.get("tier") or "screening"),
            do_epw=bool(rec.get("do_epw", False)),
            q_product=q_prod,
            k_product=k_prod,
            n_atoms=int(rec.get("n_atoms") or _REF_N_ATOMS),
            nproc=int(rec.get("nproc") or _REF_NPROC),
        )
    except (TypeError, ValueError):
        return None


def clamp_observed_scale(value: float) -> float:
    return max(_OBSERVED_SCALE_LO, min(_OBSERVED_SCALE_HI, float(value)))


def _is_supercell_dense_k_screening(
    *,
    tier: str,
    n_atoms: int,
    k_product: int,
) -> bool:
    return (
        tier == "screening"
        and int(n_atoms) >= _SUPERCELL_MIN_ATOMS
        and int(k_product) >= _SUPERCELL_MIN_K_PRODUCT
    )


def packaged_walltime_data_dirs() -> list[Path]:
    """Candidate dirs for packaged / repo seed JSONL (first existing wins)."""
    dirs: list[Path] = []
    here = Path(__file__).resolve()
    # src/siscforge/walltime.py → repo root data/walltime
    repo_data = here.parents[2] / "data" / "walltime"
    dirs.append(repo_data)
    # Editable / installed package sibling
    pkg_data = here.parent / "data" / "walltime"
    dirs.append(pkg_data)
    # importlib.resources when packaged under siscforge.data.walltime
    try:
        from importlib import resources

        try:
            root = resources.files("siscforge.data.walltime")
            dirs.append(Path(str(root)))
        except (ModuleNotFoundError, TypeError, AttributeError):
            pass
        try:
            root = resources.files("siscforge")
            dirs.append(Path(str(root)) / "data" / "walltime")
        except (ModuleNotFoundError, TypeError, AttributeError):
            pass
    except Exception:  # noqa: BLE001
        pass
    # Dedup while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def load_realized_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load realized records from a JSONL file (skip bad lines)."""
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("realized_dfpt_h") is not None:
            rows.append(obj)
    return rows


def load_prior_realized(
    *,
    output_dir: Path | str | None = None,
    include_packaged_seed: bool = True,
) -> list[dict[str, Any]]:
    """Load campaign store JSONL + packaged seed (Nb8N7 etc.)."""
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(batch: list[dict[str, Any]]) -> None:
        for r in batch:
            cid = str(r.get("candidate_id") or "")
            key = cid or json.dumps(r, sort_keys=True)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            rows.append(r)

    if output_dir is not None:
        od = Path(output_dir)
        _add(load_realized_jsonl(od / REALIZED_JSONL_NAME))

    if include_packaged_seed:
        for d in packaged_walltime_data_dirs():
            seed = d / "seed_nbn_nvac.jsonl"
            if seed.is_file():
                _add(load_realized_jsonl(seed))
                break
    return rows


def median_sorted(values: list[float]) -> float:
    vs = sorted(values)
    return vs[len(vs) // 2]


def observed_scale_from_records(
    records: list[dict[str, Any]],
    *,
    class_key: tuple[Any, ...] | None = None,
) -> float | None:
    """Median(realized/predicted_mid), optionally filtered by class key."""
    ratios: list[float] = []
    for r in records:
        if class_key is not None:
            rk = class_key_from_record(r)
            if rk != class_key:
                continue
        # Prefer explicit ratio; else compute
        ratio = r.get("ratio_realized_over_predicted_mid")
        if ratio is None:
            mid = r.get("predicted_dfpt_mid_h")
            realized = r.get("realized_dfpt_h")
            try:
                if mid is not None and float(mid) > 1e-6 and realized is not None:
                    ratio = float(realized) / float(mid)
            except (TypeError, ValueError):
                ratio = None
        try:
            if ratio is not None and float(ratio) > 0:
                ratios.append(float(ratio))
        except (TypeError, ValueError):
            continue
    if not ratios:
        # Fall back to all records if class filter empty
        if class_key is not None:
            return observed_scale_from_records(records, class_key=None)
        return None
    return clamp_observed_scale(median_sorted(ratios))


def anchored_dfpt_band_h(
    records: list[dict[str, Any]],
    *,
    class_key: tuple[Any, ...] | None,
) -> tuple[float, float, float] | None:
    """Prefer median realized mid with wide lo/hi when sample≥1 for class."""
    realized: list[float] = []
    for r in records:
        if class_key is not None:
            rk = class_key_from_record(r)
            if rk != class_key:
                continue
        try:
            v = float(r["realized_dfpt_h"])
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0:
            realized.append(v)
    if not realized:
        return None
    mid = median_sorted(realized)
    lo = max(0.5, mid * 0.45)
    hi = mid * 2.2
    return lo, mid, hi


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    ratios: list[float] = []
    realized: list[float] = []
    for r in records:
        try:
            rh = float(r["realized_dfpt_h"])
        except (KeyError, TypeError, ValueError):
            continue
        if rh <= 0:
            continue
        realized.append(rh)
        ratio = r.get("ratio_realized_over_predicted_mid")
        if ratio is None:
            mid = r.get("predicted_dfpt_mid_h")
            try:
                if mid is not None and float(mid) > 1e-6:
                    ratio = rh / float(mid)
            except (TypeError, ValueError):
                ratio = None
        if ratio is not None:
            try:
                ratios.append(float(ratio))
            except (TypeError, ValueError):
                pass
    n = len(realized)
    summary: dict[str, Any] = {
        "schema": SUMMARY_SCHEMA,
        "n_samples": n,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if ratios:
        summary["median_ratio"] = median_sorted(ratios)
        summary["mean_ratio"] = sum(ratios) / len(ratios)
    if realized:
        summary["median_realized_dfpt_h"] = median_sorted(realized)
        summary["mean_realized_dfpt_h"] = sum(realized) / len(realized)
    return summary


def append_realized_record(
    output_dir: Path | str,
    record: dict[str, Any],
    *,
    dry_run: bool = False,
) -> Path | None:
    """Append one realized record and refresh summary. No-op on dry_run."""
    if dry_run:
        return None
    od = Path(output_dir)
    od.mkdir(parents=True, exist_ok=True)
    jsonl = od / REALIZED_JSONL_NAME
    payload = dict(record)
    payload.setdefault("schema", REALIZED_SCHEMA)
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    all_rows = load_realized_jsonl(jsonl)
    summary = build_summary(all_rows)
    summary_path = od / REALIZED_SUMMARY_NAME
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return jsonl


def refresh_realized_summary(output_dir: Path | str) -> dict[str, Any]:
    od = Path(output_dir)
    rows = load_realized_jsonl(od / REALIZED_JSONL_NAME)
    summary = build_summary(rows)
    (od / REALIZED_SUMMARY_NAME).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


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
    q_prod = _grid_product(dfpt_q_grid(dft), default=_grid_product(dft.qpoints, 8))
    k_prod = _grid_product(dft.kpoints, default=_REF_K_PRODUCT)
    nkf_prod = _grid_product(dft.epw.nkf, default=6 * 6 * 6)
    nproc = max(1, int(dft.nproc))
    do_epw = _epw_enabled(dft)
    eff_scale = float(scale) * (float(observed_scale) if observed_scale else 1.0)
    fac = _scale_factor(
        n_atoms=atoms,
        nproc=nproc,
        q_product=q_prod,
        nkf_product=nkf_prod if do_epw else base["ref_nkf"],
        k_product=k_prod,
        tier=tier_name,
        scale=eff_scale,
    )
    dfpt_lo, dfpt_hi = base["dfpt"]
    full_lo, full_hi = base["full"]
    notes = (
        "heuristic only — machine load and convergence dominate; "
        "not a guarantee"
    )
    if _is_supercell_dense_k_screening(
        tier=tier_name, n_atoms=atoms, k_product=k_prod
    ):
        # Elevate screening DFPT base so 15-atom / k=8³ mid ~80–120 h
        dfpt_lo, dfpt_hi = _SUPERCELL_SCREENING_DFPT_H
        notes = (
            "supercell dense-k screening calibrated (Nb8N7 ~115 h class); "
            "heuristic only — not a guarantee"
        )
    if not do_epw:
        # Without EPW, full path is closer to DFPT + light SCF/relax overhead
        full_lo = dfpt_lo * 1.15
        full_hi = dfpt_hi * 1.35
    return WalltimeEstimate(
        tier=tier_name,
        n_atoms=atoms,
        nproc=nproc,
        q_product=q_prod,
        k_product=k_prod,
        nkf_product=nkf_prod,
        do_epw=do_epw,
        dfpt_lo_h=dfpt_lo * fac,
        dfpt_hi_h=dfpt_hi * fac,
        full_lo_h=full_lo * fac,
        full_hi_h=full_hi * fac,
        n_candidates=max(1, int(n_candidates)),
        scale=eff_scale,
        observed_adjustment=observed_scale,
        notes=notes,
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
            k_product=estimate.k_product,
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
        f"k-mesh={est.k_product} pts, nproc={est.nproc}"
        + (f", nkf={est.nkf_product}" if est.do_epw else ""),
    ]
    if est.observed_adjustment is not None:
        lines.append(
            f"  (adjusted ×{est.observed_adjustment:.2f} from earlier "
            f"candidates in this run)"
        )
    return lines


# ---------------------------------------------------------------------------
# In-run + persisted observed walltime
# ---------------------------------------------------------------------------


@dataclass
class WalltimeTracker:
    """Record finished-candidate walltimes; optionally persist to output_dir.

    At campaign start, call :meth:`load_priors` so ``observed_scale`` and
    anchored bands incorporate packaged seed + prior campaign JSONL.
    Dry-run must pass ``persist=False`` so stores are never created/wiped.
    """

    predictions_h: list[float] = field(default_factory=list)
    observed_h: list[float] = field(default_factory=list)
    _t0: dict[str, float] = field(default_factory=dict)
    output_dir: Path | None = None
    campaign: str | None = None
    persist: bool = True
    prior_records: list[dict[str, Any]] = field(default_factory=list)
    _meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    _prior_scale: float | None = None

    def load_priors(self, *, include_packaged_seed: bool = True) -> int:
        """Load store + seed; seed in-memory ratios for observed_scale."""
        self.prior_records = load_prior_realized(
            output_dir=self.output_dir,
            include_packaged_seed=include_packaged_seed,
        )
        # Seed in-memory lists from priors so scale is available immediately
        for r in self.prior_records:
            try:
                mid = float(r.get("predicted_dfpt_mid_h") or 0.0)
                realized = float(r.get("realized_dfpt_h") or 0.0)
            except (TypeError, ValueError):
                continue
            if mid > 1e-6 and realized > 0:
                self.predictions_h.append(mid)
                self.observed_h.append(realized)
        self._prior_scale = observed_scale_from_records(self.prior_records)
        return len(self.prior_records)

    def start(
        self,
        candidate_id: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._t0[candidate_id] = time.monotonic()
        if meta:
            self._meta[candidate_id] = dict(meta)

    def finish(
        self,
        candidate_id: str,
        *,
        predicted_mid_h: float | None = None,
        predicted_dfpt_lo_h: float | None = None,
        predicted_dfpt_hi_h: float | None = None,
        predicted_dfpt_mid_h: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> float | None:
        """Return observed hours if start was recorded; append JSONL when set."""
        t0 = self._t0.pop(candidate_id, None)
        if t0 is None:
            return None
        hours = max(0.0, (time.monotonic() - t0) / 3600.0)
        self.observed_h.append(hours)
        dfpt_mid = predicted_dfpt_mid_h
        if dfpt_mid is None:
            dfpt_mid = predicted_mid_h
        if dfpt_mid is not None and dfpt_mid > 0:
            self.predictions_h.append(float(dfpt_mid))

        merged = dict(self._meta.pop(candidate_id, {}) or {})
        if meta:
            merged.update(meta)

        if (
            self.persist
            and self.output_dir is not None
            and not merged.get("dry_run", False)
        ):
            lo = predicted_dfpt_lo_h
            hi = predicted_dfpt_hi_h
            mid = dfpt_mid
            if mid is None and lo is not None and hi is not None:
                mid = 0.5 * (float(lo) + float(hi))
            record: dict[str, Any] = {
                "schema": REALIZED_SCHEMA,
                "candidate_id": candidate_id,
                "formula": merged.get("formula"),
                "n_atoms": merged.get("n_atoms"),
                "nproc": merged.get("nproc"),
                "kpoints": merged.get("kpoints"),
                "qpoints": merged.get("qpoints"),
                "quality_tag": merged.get("quality_tag"),
                "do_epw": merged.get("do_epw"),
                "tier": merged.get("tier"),
                "predicted_dfpt_lo_h": lo,
                "predicted_dfpt_hi_h": hi,
                "predicted_dfpt_mid_h": mid,
                "realized_dfpt_h": hours,
                "host": merged.get("host") or socket.gethostname(),
                "campaign": merged.get("campaign") or self.campaign,
                "finished_at": datetime.now(UTC).isoformat(),
            }
            if mid and float(mid) > 1e-6:
                record["ratio_realized_over_predicted_mid"] = hours / float(mid)
            # Drop Nones for cleaner JSONL
            record = {k: v for k, v in record.items() if v is not None}
            append_realized_record(self.output_dir, record, dry_run=False)
            self.prior_records.append(record)
        return hours

    def observed_scale(
        self,
        *,
        class_key: tuple[Any, ...] | None = None,
    ) -> float | None:
        """Median(observed/predicted); clamp to [0.35, 5.0].

        When *class_key* is set, prefer matching prior/seed records; otherwise
        use in-memory lists (priors + this run).
        """
        if class_key is not None and self.prior_records:
            scale = observed_scale_from_records(
                self.prior_records, class_key=class_key
            )
            if scale is not None:
                return scale
        if not self.observed_h or not self.predictions_h:
            return self._prior_scale
        n = min(len(self.observed_h), len(self.predictions_h))
        if n < 1:
            return self._prior_scale
        ratios = [
            self.observed_h[i] / self.predictions_h[i]
            for i in range(n)
            if self.predictions_h[i] > 1e-6
        ]
        if not ratios:
            return self._prior_scale
        ratios.sort()
        med = ratios[len(ratios) // 2]
        return clamp_observed_scale(med)

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


def calibrate_estimate_with_realized(
    estimate: WalltimeEstimate,
    records: list[dict[str, Any]],
) -> WalltimeEstimate:
    """Apply class observed_scale and/or anchored bands from realized store."""
    key = walltime_class_key(
        tier=estimate.tier,
        do_epw=estimate.do_epw,
        q_product=estimate.q_product,
        k_product=estimate.k_product,
        n_atoms=estimate.n_atoms,
        nproc=estimate.nproc,
    )
    anchored = anchored_dfpt_band_h(records, class_key=key)
    scale = observed_scale_from_records(records, class_key=key)

    if anchored is not None:
        lo, _mid, hi = anchored
        # Prefer anchored realized mid/band over pure heuristic when sample≥1
        full_lo = lo * 1.15 if not estimate.do_epw else lo
        full_hi = hi * 1.35 if not estimate.do_epw else hi * 1.2
        return WalltimeEstimate(
            tier=estimate.tier,
            n_atoms=estimate.n_atoms,
            nproc=estimate.nproc,
            q_product=estimate.q_product,
            k_product=estimate.k_product,
            nkf_product=estimate.nkf_product,
            do_epw=estimate.do_epw,
            dfpt_lo_h=lo,
            dfpt_hi_h=hi,
            full_lo_h=full_lo,
            full_hi_h=full_hi,
            n_candidates=estimate.n_candidates,
            scale=estimate.scale,
            observed_adjustment=scale,
            notes=(
                "anchored to realized DFPT median for this class; "
                "heuristic bands remain wide"
            ),
        )

    if scale is not None and abs(scale - 1.0) > 0.05:
        return WalltimeEstimate(
            tier=estimate.tier,
            n_atoms=estimate.n_atoms,
            nproc=estimate.nproc,
            q_product=estimate.q_product,
            k_product=estimate.k_product,
            nkf_product=estimate.nkf_product,
            do_epw=estimate.do_epw,
            dfpt_lo_h=estimate.dfpt_lo_h * scale,
            dfpt_hi_h=estimate.dfpt_hi_h * scale,
            full_lo_h=estimate.full_lo_h * scale,
            full_hi_h=estimate.full_hi_h * scale,
            n_candidates=estimate.n_candidates,
            scale=estimate.scale * scale,
            observed_adjustment=scale,
            notes=estimate.notes,
        )
    return estimate


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
