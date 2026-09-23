"""JSONL append / summary for realized walltimes."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siscforge.walltime_store_core import (
    REALIZED_JSONL_NAME,
    REALIZED_SCHEMA,
    REALIZED_SUMMARY_NAME,
    SUMMARY_SCHEMA,
    class_key_from_record,
    load_realized_jsonl,
    median_sorted,
)

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
