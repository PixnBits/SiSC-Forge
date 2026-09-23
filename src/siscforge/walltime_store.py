"""Persist and calibrate realized DFPT walltimes (plain Python, no shards)."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REALIZED_SCHEMA = "siscforge.walltime_realized.v1"
SUMMARY_SCHEMA = "siscforge.walltime_realized_summary.v1"
REALIZED_JSONL_NAME = "walltime_realized.jsonl"
REALIZED_SUMMARY_NAME = "walltime_realized_summary.json"

_OBSERVED_SCALE_LO = 0.35
_OBSERVED_SCALE_HI = 5.0

_REF_N_ATOMS = 8
_REF_NPROC = 16
_REF_K_PRODUCT = 4 * 4 * 4


def _grid_product(grid: list[int] | tuple[int, ...] | None, default: int = 8) -> int:
    if not grid:
        return default
    p = 1
    for g in list(grid)[:3]:
        p *= max(1, int(g))
    return p


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


def packaged_walltime_data_dirs() -> list[Path]:
    """Candidate dirs for packaged / repo seed JSONL (first existing wins)."""
    dirs: list[Path] = []
    here = Path(__file__).resolve()
    repo_data = here.parents[2] / "data" / "walltime"
    dirs.append(repo_data)
    pkg_data = here.parent / "data" / "walltime"
    dirs.append(pkg_data)
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
