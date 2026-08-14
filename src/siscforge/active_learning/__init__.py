"""Active-learning prioritization and Phase 1.5 / P3.6 flywheel.

Phase 1: queue prioritization for expensive EPW jobs.
Phase 1.5a: seed set, promotion gate, lightweight retrain, bootstrap status.
Phase 1.5b: trained predictions affect rankings; run-loop provenance; operator UX.
P3.6: mixed conventional / unconventional acquisition pools (joint | separate).
"""

from siscforge.active_learning.acquisition import (
    AcquisitionPlan,
    AcquisitionRecord,
    acquisition_score,
    prioritize_candidates,
)
from siscforge.active_learning.bootstrap import (
    ActiveSurrogateContext,
    SurrogateRegistry,
    al_status,
    build_prioritization_record,
    is_bootstrap,
    resolve_al_context,
    retrain_from_snapshot,
    retrain_from_store,
)
from siscforge.active_learning.paths import (
    DEFAULT_AL_ROOT_NAME,
    ENV_AL_ROOT,
    al_subroots,
    resolve_al_root,
    write_al_pointer,
)
from siscforge.active_learning.pools import (
    POOLS,
    PoolDecision,
    derive_pool,
    select_with_quotas,
)
from siscforge.active_learning.training_set import (
    DEFAULT_GOLDEN_SEEDS,
    PromotionError,
    TrainingSetStore,
    literature_example,
    load_literature_records,
    promote_evaluation,
    promotion_eligibility,
    seed_default_goldens,
    seed_from_literature_file,
)

__all__ = [
    "AcquisitionPlan",
    "AcquisitionRecord",
    "ActiveSurrogateContext",
    "DEFAULT_AL_ROOT_NAME",
    "DEFAULT_GOLDEN_SEEDS",
    "ENV_AL_ROOT",
    "POOLS",
    "PoolDecision",
    "PromotionError",
    "SurrogateRegistry",
    "TrainingSetStore",
    "acquisition_score",
    "al_status",
    "al_subroots",
    "build_prioritization_record",
    "derive_pool",
    "is_bootstrap",
    "literature_example",
    "load_literature_records",
    "prioritize_candidates",
    "promote_evaluation",
    "promotion_eligibility",
    "resolve_al_context",
    "resolve_al_root",
    "retrain_from_snapshot",
    "retrain_from_store",
    "seed_default_goldens",
    "seed_from_literature_file",
    "select_with_quotas",
    "write_al_pointer",
]