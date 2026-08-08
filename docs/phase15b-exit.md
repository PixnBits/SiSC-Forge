# Phase 1.5b exit criteria — AL prediction wiring & operator UX

Follows Phase 1.5a (data hygiene on main via PR #2 squash). Closes the Product
Owner gaps called out after that review.

## Goals

| Goal | Done when |
|------|-----------|
| Retrain changes rankings | Family-mean fit payload is loaded into `predict_tc_lambda` / `ActiveSurrogateContext`; acquisition scores move after `al-train` |
| Run loop uses registry | `siscforge run --al-root` loads training set + models, stamps provenance, writes `PrioritizationRecord` |
| Bootstrap on cards (AC15) | Synthesis cards carry BOOTSTRAP banner + model version + label count |
| Dry-run promote | `al-promote --dry-run` previews eligible/refused without writes |
| Literature bulk seed | `al-seed --from-file lit.json` (JSON / JSONL / CSV) |
| Rollback | `al-rollback <version>` points `current.json` at a prior install |
| Unknown quality gate | `quality_tag=unknown` refused unless `--allow-unknown` |

## Delivered

| Item | Location |
|------|----------|
| Trained family-mean predictions | `src/siscforge/surrogates/tc_lambda.py`, `ActiveSurrogateContext` |
| Registry → prediction context | `SurrogateRegistry.active_context`, `resolve_al_context` |
| Run wiring | `cli/main.py` `run --al-root` |
| Prioritization records on every run | `registry.record_prioritization` + `active_learning.json` |
| Bootstrap banner on cards | `export.write_synthesis_cards` |
| CLI UX | `al-promote --dry-run`, `al-seed --from-file`, `al-rollback` |
| Tests | `tests/test_al_bootstrap_phase15b.py` |

## Operator loop (workstation)

```bash
siscforge al-seed --al-root ./al_state
siscforge al-seed --al-root ./al_state --from-file docs/examples/literature_seeds.json --no-goldens
siscforge run --dry-run campaign.yaml -o ./out --al-root ./al_state
siscforge al-promote ./out --al-root ./al_state --dry-run
siscforge al-promote ./out --al-root ./al_state
siscforge al-train --al-root ./al_state
siscforge al-status --al-root ./al_state
# next run uses the trained model for prioritization scores
siscforge run campaign.yaml -o ./out2 --al-root ./al_state
siscforge al-rollback 0.2-fit-<hash> --al-root ./al_state   # if needed
```

## Still not in this phase

- ALIGNN/MatGL production GNN heads
- Auto-promote without CLI
- Mixed conventional/unconventional acquisition (Phase 3)
- Calibration plots / wall-time cost accounting (P2)
