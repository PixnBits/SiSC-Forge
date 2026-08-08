# Phase 1.5 exit criteria — AL Bootstrap

Aligned with PRD v0.3.1, Technical Specs v0.4.1 (AC13–AC18), and
`docs/design/active-learning-flywheel.md`.

## Delivered

| Item | Location |
|------|----------|
| TrainingExample / SurrogateModelMetadata / PrioritizationRecord / SurrogatePrediction | `src/siscforge/models/active_learning.py` |
| Promotion gate (explicit; mock refused) | `src/siscforge/active_learning/training_set.py` |
| Literature / golden seed ingestion | `literature_example`, `seed_default_goldens` |
| Immutable hashed snapshots | `TrainingSetStore.snapshot` |
| Lightweight retrain + AC17 refusal | `src/siscforge/active_learning/bootstrap.py` |
| Bootstrap status | `al_status`, CLI `al-status` |
| Acquisition provenance | `AcquisitionRecord.model_version`, `PrioritizationRecord` |
| CLI | `al-status`, `al-seed`, `al-promote`, `al-train`, `al-audit` |
| Tests | `tests/test_al_bootstrap.py` (AC13–AC18 + full mock cycle) |

## Acceptance criteria map

| AC | Covered by |
|----|------------|
| AC13 | `test_promote_clean_evaluation`, `test_refuse_failed_status_ac13` |
| AC14 | `test_prioritization_record_provenance` |
| AC15 | `test_al_status_bootstrap_message` |
| AC16 | `test_full_mock_al_cycle` |
| AC17 | `test_retrain_refuses_empty`, `test_retrain_refuses_absurd_tc` |
| AC18 | `test_refuse_mock_promotion_ac18`, `test_retrain_refuses_mock_in_set` |

## Operator quick start

```bash
# Seed goldens (NbN, MgB2, TiN)
siscforge al-seed --al-root ./al_state

# After a campaign store has real EPW evaluations:
siscforge al-promote path/to/campaign_store --al-root ./al_state

# Snapshot + lightweight retrain
siscforge al-train --al-root ./al_state

# Inspect
siscforge al-status --al-root ./al_state
siscforge al-audit --al-root ./al_state
```

## Not in this phase

- Trained GNN heads (ALIGNN/MatGL) — still family-heuristic + family-mean fit
- Mixed conventional/unconventional acquisition (Phase 3)
- Automatic promotion without CLI/API call
