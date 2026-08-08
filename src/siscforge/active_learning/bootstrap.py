"""Bootstrap regime, lightweight retrain, and AL status (Phase 1.5).

Implements design-note failure modes and Specs AC15–AC17:

- Bootstrap mode visible when label count is low
- Retrain that produces NaNs / absurd metrics keeps the previous model
- Model metadata records training-set hash and size
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from siscforge.models.active_learning import (
    PrioritizationRecord,
    SurrogateModelMetadata,
    TrainingExample,
    TrainingSetSnapshot,
)
from siscforge.active_learning.training_set import TrainingSetStore, hash_examples

# Bootstrap thresholds (design note §6 / Specs §2.2.4)
DEFAULT_BOOTSTRAP_MAX_LABELS: int = 150
DEFAULT_BOOTSTRAP_MIN_LABELS_FOR_TRAINED: int = 5


@dataclass
class RetrainResult:
    """Outcome of a lightweight retrain attempt."""

    success: bool
    metadata: SurrogateModelMetadata | None = None
    previous_version: str | None = None
    refused_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    model_path: Path | None = None


class SurrogateRegistry:
    """Filesystem registry of surrogate model versions under ``root``.

    Layout::

        root/
          models/
            <version>.json       # SurrogateModelMetadata + fit payload
          current.json           # pointer to active version
          prioritization/        # PrioritizationRecord audit log
            <record_id>.json
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "models").mkdir(parents=True, exist_ok=True)
        (self.root / "prioritization").mkdir(parents=True, exist_ok=True)

    def current(self) -> SurrogateModelMetadata | None:
        path = self.root / "current.json"
        if not path.is_file():
            return None
        meta = json.loads(path.read_text(encoding="utf-8"))
        version = meta.get("model_version")
        if not version:
            return None
        return self.load(version)

    def load(self, version: str) -> SurrogateModelMetadata | None:
        path = self.root / "models" / f"{version}.json"
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SurrogateModelMetadata.model_validate(raw.get("metadata", raw))

    def load_payload(self, version: str) -> dict[str, Any]:
        path = self.root / "models" / f"{version}.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def install(
        self,
        metadata: SurrogateModelMetadata,
        *,
        payload: dict[str, Any] | None = None,
        make_current: bool = True,
    ) -> Path:
        path = self.root / "models" / f"{metadata.model_version}.json"
        doc = {
            "metadata": metadata.model_dump(mode="json"),
            "payload": payload or {},
        }
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        if make_current:
            (self.root / "current.json").write_text(
                json.dumps(
                    {
                        "model_version": metadata.model_version,
                        "training_set_size": metadata.training_set_size,
                        "bootstrap": metadata.bootstrap,
                        "method": metadata.method,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return path

    def record_prioritization(self, record: PrioritizationRecord) -> Path:
        path = self.root / "prioritization" / f"{record.record_id}.json"
        path.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def list_versions(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "models").glob("*.json"))


def is_bootstrap(
    n_labels: int,
    *,
    mean_uncertainty: float | None = None,
    max_labels: int = DEFAULT_BOOTSTRAP_MAX_LABELS,
) -> bool:
    """True while still in the low-data / high-uncertainty regime."""
    if n_labels < max_labels:
        return True
    if mean_uncertainty is not None and mean_uncertainty > 0.35:
        return True
    return False


def _family_means(examples: Sequence[TrainingExample]) -> dict[str, dict[str, float]]:
    """Compute per-family mean λ, ω_log, Tc from labels (lightweight fit)."""
    buckets: dict[str, list[TrainingExample]] = {}
    for e in examples:
        buckets.setdefault(e.material_family, []).append(e)

    out: dict[str, dict[str, float]] = {}
    for fam, items in buckets.items():
        lams = [x.lambda_total for x in items if x.lambda_total is not None]
        wlogs = [x.omega_log for x in items if x.omega_log is not None]
        tcs = [x.tc_K for x in items if x.tc_K is not None]
        stats: dict[str, float] = {"n": float(len(items))}
        if lams:
            stats["lambda_mean"] = sum(lams) / len(lams)
        if wlogs:
            stats["omega_log_mean"] = sum(wlogs) / len(wlogs)
        if tcs:
            stats["tc_mean"] = sum(tcs) / len(tcs)
            stats["tc_std"] = (
                (sum((t - stats["tc_mean"]) ** 2 for t in tcs) / len(tcs)) ** 0.5
                if len(tcs) > 1
                else 0.0
            )
        out[fam] = stats
    return out


def _validate_fit(family_stats: dict[str, dict[str, float]]) -> tuple[bool, str, dict[str, Any]]:
    """Refuse absurd / NaN fits (AC17)."""
    diag: dict[str, Any] = {"families": family_stats}
    if not family_stats:
        return False, "no family statistics (empty training set)", diag
    for fam, st in family_stats.items():
        for key in ("lambda_mean", "omega_log_mean", "tc_mean"):
            if key not in st:
                continue
            val = st[key]
            if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                return False, f"NaN/Inf in {fam}.{key}", diag
            if key == "lambda_mean" and (val < 0 or val > 5.0):
                return False, f"absurd lambda_mean={val} for {fam}", diag
            if key == "tc_mean" and (val < 0 or val > 300.0):
                return False, f"absurd tc_mean={val} K for {fam}", diag
            if key == "omega_log_mean" and (val < 1.0 or val > 2000.0):
                return False, f"absurd omega_log_mean={val} for {fam}", diag
    return True, "ok", diag


def retrain_from_snapshot(
    snapshot: TrainingSetSnapshot,
    registry: SurrogateRegistry,
    *,
    parent: SurrogateModelMetadata | None = None,
    version_suffix: str | None = None,
    force: bool = False,
) -> RetrainResult:
    """Lightweight family-mean fit on a training-set snapshot.

    On failure, keeps the previous model and returns diagnostics (AC17).
    Refuses empty sets and mock-only payloads (caller must filter).
    """
    previous = parent or registry.current()
    prev_version = previous.model_version if previous else None

    if snapshot.n_examples == 0 or not snapshot.examples:
        return RetrainResult(
            success=False,
            previous_version=prev_version,
            refused_reason="empty training set",
            diagnostics={"n_examples": 0},
        )

    # Hard refusal if any mock slipped through
    mockish = [e for e in snapshot.examples if e.quality_tag == "mock" or e.status == "mock"]
    if mockish and not force:
        return RetrainResult(
            success=False,
            previous_version=prev_version,
            refused_reason="training set contains mock labels (AC18)",
            diagnostics={"n_mock": len(mockish)},
        )

    family_stats = _family_means(snapshot.examples)
    ok, reason, diag = _validate_fit(family_stats)
    if not ok:
        return RetrainResult(
            success=False,
            previous_version=prev_version,
            refused_reason=reason,
            diagnostics=diag,
        )

    n = snapshot.n_examples
    bootstrap = is_bootstrap(n)
    ver = version_suffix or snapshot.content_hash[:8]
    model_version = f"0.2-fit-{ver}"

    # Mean uncertainty proxy: inverse of label density (simple)
    mean_unc = max(0.15, min(0.85, 1.0 - (n / float(DEFAULT_BOOTSTRAP_MAX_LABELS))))

    metadata = SurrogateModelMetadata(
        model_version=model_version,
        method="family_mean_fit",
        training_set_size=n,
        training_set_hash=snapshot.content_hash or hash_examples(snapshot.examples),
        bootstrap=bootstrap,
        mean_uncertainty=round(mean_unc, 4),
        calibration_summary={
            "n_families": len(family_stats),
            "family_stats": family_stats,
        },
        parent_version=prev_version,
        notes=(
            f"Lightweight family-mean fit on {n} labels. "
            + ("Bootstrap regime." if bootstrap else "Mature regime.")
        ),
    )
    path = registry.install(
        metadata,
        payload={"family_stats": family_stats, "snapshot_id": snapshot.snapshot_id},
        make_current=True,
    )
    return RetrainResult(
        success=True,
        metadata=metadata,
        previous_version=prev_version,
        diagnostics=diag,
        model_path=path,
    )


def retrain_from_store(
    training_store: TrainingSetStore,
    registry: SurrogateRegistry,
    *,
    snapshot_notes: str = "",
) -> RetrainResult:
    """Snapshot the working set, then retrain."""
    snap = training_store.snapshot(notes=snapshot_notes)
    return retrain_from_snapshot(snap, registry)


def al_status(
    training_store: TrainingSetStore,
    registry: SurrogateRegistry,
) -> dict[str, Any]:
    """Operator-facing status: label count, model version, bootstrap flag (AC15)."""
    ts = training_store.summary()
    current = registry.current()
    n = int(ts.get("n_examples") or 0)
    bootstrap = is_bootstrap(
        n,
        mean_uncertainty=current.mean_uncertainty if current else None,
    )
    return {
        "training_set": ts,
        "model": (
            {
                "model_version": current.model_version,
                "method": current.method,
                "training_set_size": current.training_set_size,
                "training_set_hash": current.training_set_hash,
                "bootstrap": current.bootstrap,
                "mean_uncertainty": current.mean_uncertainty,
                "parent_version": current.parent_version,
            }
            if current
            else None
        ),
        "bootstrap": bootstrap or (current.bootstrap if current else True),
        "n_labels": n,
        "versions": registry.list_versions(),
        "message": (
            "BOOTSTRAP MODE — rankings are prioritization aids, not quantitative "
            "predictions. Promote clean EPW results and retrain to improve."
            if bootstrap or not current
            else f"Active model {current.model_version} ({current.training_set_size} labels)."
        ),
    }


def build_prioritization_record(
    *,
    model: SurrogateModelMetadata | None,
    strategy: str,
    weights: dict[str, float],
    ranked: Sequence[Any],
    selected_ids: Sequence[str],
    deferred_ids: Sequence[str],
    notes: str = "",
) -> PrioritizationRecord:
    """Attach provenance to a prioritization decision (AC14)."""
    scores = []
    for r in ranked:
        if hasattr(r, "model_dump"):
            d = r.model_dump(mode="json")
            scores.append(
                {
                    "candidate_id": d.get("candidate_id"),
                    "acquisition_score": d.get("acquisition_score"),
                    "selected": d.get("selected_for_expensive"),
                    "predicted_tc": d.get("predicted_tc"),
                    "uncertainty": d.get("uncertainty"),
                }
            )
        elif isinstance(r, dict):
            scores.append(r)
    return PrioritizationRecord(
        model_version=model.model_version if model else "heuristic",
        method=model.method if model else "family_heuristic",
        training_set_size=model.training_set_size if model else 0,
        bootstrap=model.bootstrap if model else True,
        strategy=strategy,
        acquisition_weights=dict(weights),
        n_candidates=len(ranked),
        n_selected=len(selected_ids),
        selected_ids=list(selected_ids),
        deferred_ids=list(deferred_ids),
        ranked_scores=scores,
        notes=notes,
    )
