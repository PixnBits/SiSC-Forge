"""Active-learning prioritization and Phase 1.5 bootstrap flywheel.

Phase 1: queue prioritization for expensive EPW jobs.
Phase 1.5: seed set, promotion gate, lightweight retrain, bootstrap status.
"""

from siscforge.active_learning.acquisition import (
    AcquisitionPlan,
    AcquisitionRecord,
    acquisition_score,
    prioritize_candidates,
)
from siscforge.active_learning.bootstrap import (
    SurrogateRegistry,
    al_status,
    build_prioritization_record,
    is_bootstrap,
    retrain_from_snapshot,
    retrain_from_store,
)
from siscforge.active_learning.training_set import (
    DEFAULT_GOLDEN_SEEDS,
    PromotionError,
    TrainingSetStore,
    literature_example,
    promote_evaluation,
    promotion_eligibility,
    seed_default_goldens,
)

__all__ = [
    "AcquisitionPlan",
    "AcquisitionRecord",
    "DEFAULT_GOLDEN_SEEDS",
    "PromotionError",
    "SurrogateRegistry",
    "TrainingSetStore",
    "acquisition_score",
    "al_status",
    "build_prioritization_record",
    "is_bootstrap",
    "literature_example",
    "prioritize_candidates",
    "promote_evaluation",
    "promotion_eligibility",
    "retrain_from_snapshot",
    "retrain_from_store",
    "seed_default_goldens",
]
