# Active-Learning Flywheel & Bootstrap Strategy

**Status**: Design note (authoritative for implementation intent)  
**Date**: 2026-08-08  
**Related**: PRD v0.3.1, Technical Specifications v0.4.1, ROADMAP.md

This note captures the practical active-learning (AL) strategy for SiSC-Forge so that future implementation and readers share the same mental model. It deliberately emphasizes workstation realism, data efficiency, and operator experience.

---

## 1. Goal of the Flywheel

Convert scarce, expensive EPW (and later DMFT) labels into progressively better prioritization of the next calculations, while keeping Silicon Feasibility and trust-layer constraints first-class.

Near-term scientific target: rank silicon-compatible candidates with predicted Tc ≳ 40–80 K (above LN₂) that survive a realistic process window. Ambient-pressure room-temperature operation remains the long-term aspiration, not a near-term deliverable.

---

## 2. Core Loop (Interleaved, Not Batch-First)

```
Seed set (literature + goldens + early project labels)
        │
   Train / fine-tune first surrogate (or keep heuristic)
        │
   Acquisition function → shortlist (5–15 candidates)
        │
   Phonon-first gate → EPW (screening or production quality)
        │
   Promote clean results into training set
        │
   Retrain / update surrogate
        │
   Repeat
```

**Key principle**: a useful prioritization model exists long before a large static dataset. Do **not** wait for 100–150 production EPW results before the first retrain.

---

## 3. Seed-Set Guidance

| Component | Role | Rough size |
|-----------|------|------------|
| Goldens | NbN family, MgB₂, hyperdoped-Si / silicide examples | 10–30 |
| Literature EPW | Clean, citable results that can be ingested or reproduced | as available |
| Early project labels | Deliberately diverse (composition, modest strain, simple ternaries) | 20–100 |
| **Total for first useful surrogate** | Diversity > raw count | **~50–150 high-quality labels** |

- Formation-energy / stability surrogates can start earlier by fine-tuning pre-trained GNNs (ALIGNN / MatGL) on far fewer points.
- λ / ω_log / Tc proxies are data-hungrier; the numbers above are realistic starting points observed in recent conventional-superconductor ML work.

---

## 4. Quality & Promotion Rules

Only results that pass the trust layer and an explicit quality allow-list may become permanent training examples.

- Prefer `quality_tag ∈ {production, screening}` with no disqualifying flags (imaginary modes that invalidate λ, failed Wannier, mock data, etc.).
- Promotion is an explicit step (CLI or API), never silent.
- Each training-set snapshot used for a model version is immutable and hashed.
- Literature data carries provenance (source, settings, notes) so domain shift is visible.

---

## 5. Acquisition Function

Configurable combination of:

- Predicted performance (Tc proxy, λ, or later pairing eigenvalue)
- Model uncertainty (exploration)
- Silicon Feasibility Score
- Optional diversity / novelty terms

In early (bootstrap) mode the uncertainty weight should be higher to avoid premature collapse onto a small region of chemical space. Weights are YAML-configurable and recorded with every prioritization decision.

---

## 6. Operator Experience & Robustness Requirements

These must be designed in with the feature, not added later.

### Observability
- Every ranking / shortlist records which surrogate version (or heuristic) produced it, training-set size, and acquisition weights.
- CLI status surfaces: last retrain time, label count, bootstrap vs mature indication.
- Synthesis cards carry a one-line surrogate provenance note.

### Training-set hygiene
- Explicit promotion gate.
- Hard filters against mock, failed, or heavily flagged results.
- Audit command that lists every training example with origin and quality flags.

### Bootstrap mode
- Distinct operating regime (or continuous confidence score).
- Higher exploration weight by default.
- Easy human overrides: pin candidates, inject seeds, exclude subspaces, roll back model version.

### Failure modes specific to the learning loop
| Situation | Required behaviour |
|-----------|--------------------|
| Retrain produces NaNs / absurd metrics | Keep previous model; surface diagnostics; do not install |
| New model systematically over-confident on held-out points | Flag for review; optional automatic rejection |
| Acquisition returns empty / near-empty shortlist | Fall back to heuristic or diversity sampling; tell the user |
| Mode collapse (all top-k nearly identical scores) | Warn about insufficient training-set diversity |
| Retrain requested while EPW jobs still running | Queue or refuse with clear message |
| Attempt to train on mock data | Hard refusal |

### Dry-run / mock
- Full prioritize → shortlist → (mock) calculate → promote → retrain → re-prioritize cycle must be exercisable without any QE binary.
- Same CLI and status reporting work in mock mode.

### Escape hatches
- Pin / force candidates into the next shortlist.
- Temporary chemical-space exclusions.
- Roll back to a previous surrogate version.
- Export the exact training set that produced a given model.

---

## 7. Workstation Cadence

Aim for a steady trickle of a few high-quality labels per week rather than giant serial batches.

- Most candidates die at the phonon-first gate.
- Screening-quality EPW is acceptable for early labels; production-quality is reserved for shortlist winners.
- Parallelism (multiple candidates at different stages) + resume/checkpoint already present in the codebase make multi-day wall-clock feasible.

A first useful prioritization model should appear after a few months of steady work, not after 1½ years of serial full-production EPW.

---

## 8. Implementation Phasing Hint

1. Seed-set management + literature ingestion path + promotion gate.
2. First trained (or fine-tuned) surrogate with uncertainty + model metadata.
3. Acquisition function that records its decisions.
4. Lightweight retrain/update trigger after each shortlist cycle.
5. Full observability, bootstrap-mode messaging, and the failure-mode table above.
6. Later: mature mixed conventional/unconventional acquisition (Phase 3 **P3.6** — shipped; see `docs/phase3-p36-mixed-al.md`).

The existing phonon-first, trust-layer, resume, and shortlist machinery already supplies most of the scaffolding. The work above is largely state management, provenance, and operator-facing contracts.

---

## 9. References inside the repo

- PRD §2 Goals, §4 Success Metrics, §5 P1 features (updated language)
- Technical Specifications §2.2 (ML Surrogate Layer) and new robustness acceptance criteria
- ROADMAP Phase 1 residual / bootstrap milestone

*This design note is the detailed rationale. Acceptance criteria live in the Technical Specifications; product intent lives in the PRD.*
