"""Active-learning data models (Phase 1.5 bootstrap).

Implements the contracts from Technical Specs §3.6 and
``docs/design/active-learning-flywheel.md``:

- SurrogatePrediction
- TrainingExample
- SurrogateModelMetadata
- PrioritizationRecord
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class SurrogatePrediction(BaseModel):
    """Optional block on CandidateEvaluation: λ / ω_log / Tc proxy + provenance.

    Aligns with :class:`~siscforge.surrogates.tc_lambda.TcLambdaPrediction` but
    is the stable AL-facing schema (Specs §3.6).
    """

    predicted_lambda: float | None = None
    predicted_omega_log: float | None = None
    predicted_tc: float | None = None
    uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)
    model_version: str = "unknown"
    method: str = "unknown"
    quality_tag: Literal["stub", "screening", "production", "trained"] = "stub"
    features: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_tc_lambda(cls, pred: Any) -> SurrogatePrediction:
        """Build from a TcLambdaPrediction-like object or dict."""
        if isinstance(pred, dict):
            return cls(
                predicted_lambda=pred.get("predicted_lambda"),
                predicted_omega_log=pred.get("predicted_omega_log"),
                predicted_tc=pred.get("predicted_Tc", pred.get("predicted_tc")),
                uncertainty=pred.get("uncertainty"),
                model_version=str(pred.get("model_version", "unknown")),
                method=str(pred.get("method", "unknown")),
                quality_tag=pred.get("quality_tag", "stub"),
                features=dict(pred.get("features") or {}),
                notes=str(pred.get("notes") or ""),
            )
        return cls(
            predicted_lambda=getattr(pred, "predicted_lambda", None),
            predicted_omega_log=getattr(pred, "predicted_omega_log", None),
            predicted_tc=getattr(pred, "predicted_Tc", None)
            or getattr(pred, "predicted_tc", None),
            uncertainty=getattr(pred, "uncertainty", None),
            model_version=str(getattr(pred, "model_version", "unknown")),
            method=str(getattr(pred, "method", "unknown")),
            quality_tag=getattr(pred, "quality_tag", "stub"),
            features=dict(getattr(pred, "features", None) or {}),
            notes=str(getattr(pred, "notes", "") or ""),
        )


class TrainingExample(BaseModel):
    """A permanently promoted label for surrogate training.

    Only results that pass the promotion gate may become TrainingExamples.
    Mock / dry-run labels are refused (AC13, AC18).
    """

    example_id: str = Field(default_factory=_new_id)
    candidate_id: str
    formula: str
    material_family: str = "other"
    source: Literal["project", "literature", "golden"] = "project"
    """Origin of the label."""

    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    status: str = "ok"
    """Evaluation status at promotion time."""

    # Labels (EPW / literature)
    lambda_total: float | None = None
    omega_log: float | None = None
    tc_K: float | None = None
    tc_source: str | None = None
    """e.g. epw_eliashberg, epw_allen_dynes, literature."""

    energy_above_hull: float | None = None
    si_feasibility_total: float | None = None
    quality_flags: list[str] = Field(default_factory=list)

    # Structure / context for domain awareness
    in_plane_strain: float | None = None
    composition: dict[str, float] = Field(default_factory=dict)
    structure_cif: str | None = None

    # Provenance
    campaign_store: str | None = None
    literature_ref: str | None = None
    literature_notes: str = ""
    promoted_at: datetime = Field(default_factory=_utcnow)
    quality_snapshot: dict[str, Any] = Field(default_factory=dict)
    """Frozen quality/trust fields at promotion time."""

    notes: str = ""

    @field_validator("quality_tag")
    @classmethod
    def _reject_mock_tag(cls, v: str) -> str:
        # Soft check at model level; hard refusal is in the promotion gate.
        return v


class SurrogateModelMetadata(BaseModel):
    """Immutable metadata for one surrogate model version."""

    model_version: str
    method: str = "family_heuristic"
    """e.g. family_heuristic, ridge_on_labels, alignn_head."""

    training_set_size: int = 0
    training_set_hash: str = ""
    """Hash of the immutable training-set snapshot used to build this model."""

    created_at: datetime = Field(default_factory=_utcnow)
    bootstrap: bool = True
    """True while label count / uncertainty still indicate bootstrap regime."""

    mean_uncertainty: float | None = None
    calibration_summary: dict[str, Any] = Field(default_factory=dict)
    parent_version: str | None = None
    """Previous model version this was trained from (rollback chain)."""

    notes: str = ""
    weights: dict[str, float] = Field(default_factory=dict)
    """Acquisition or model hyper-parameters recorded with the version."""


class PrioritizationRecord(BaseModel):
    """Provenance for one prioritization / shortlist decision (AC14)."""

    record_id: str = Field(default_factory=_new_id)
    timestamp: datetime = Field(default_factory=_utcnow)
    model_version: str = "heuristic"
    method: str = "family_heuristic"
    training_set_size: int = 0
    bootstrap: bool = True
    strategy: str = "uncertainty_si_tc"
    acquisition_weights: dict[str, float] = Field(default_factory=dict)
    n_candidates: int = 0
    n_selected: int = 0
    selected_ids: list[str] = Field(default_factory=list)
    deferred_ids: list[str] = Field(default_factory=list)
    ranked_scores: list[dict[str, Any]] = Field(default_factory=list)
    """Compact list of {candidate_id, score, selected} for audit."""

    notes: str = ""
    # --- P3.6 mixed-pool provenance (additive) ---
    acquisition_mode: str = "off"
    """``off`` | ``joint`` | ``separate``."""
    pool_counts: dict[str, int] = Field(default_factory=dict)
    selected_by_pool: dict[str, int] = Field(default_factory=dict)


class TrainingSetSnapshot(BaseModel):
    """Immutable snapshot of a training set (hashed, versioned)."""

    snapshot_id: str = Field(default_factory=_new_id)
    created_at: datetime = Field(default_factory=_utcnow)
    content_hash: str = ""
    n_examples: int = 0
    examples: list[TrainingExample] = Field(default_factory=list)
    notes: str = ""
