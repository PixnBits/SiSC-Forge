# P3.4 — DMFT pairing eigenvalue → `performance_score`

**Status**: shipped — deterministic mapping + evaluation wiring  
**Prerequisite**: P3.3 `DMFTResult` (`docs/phase3-p33-dmft.md`)  
**Next**: **P3.5** — oxygen-vacancy enumeration (out of scope here)

This package does **not** launch CTHYB, enumerate oxygen vacancies, or
change ranking / Pareto maths. It fills the common `performance_score`
axis so unconventional candidates use the **same** ranker as nitrides.

## Goal

1. Map a usable `DMFTResult.leading_pairing_eigenvalue` to a finite
   Tc-like kelvin proxy on `CandidateEvaluation.performance_score`.
2. Tag the source (`dmft_pairing` / `dmft_pairing_mock`) so mock data
   cannot silently look like production Eliashberg Tc.
3. Prefer trusted EPW Tc when both conventional and DMFT data exist.
4. Leave conventional campaigns (DMFT off) numerically unchanged.

## Formula

Pure function: `siscforge.scoring.pairing.performance_score_from_pairing`.

```
score_K = clamp( (λ − threshold) × kelvin_per_unit × Q,  0,  ceiling_K )
```

| Symbol | Default | Meaning |
|--------|---------|---------|
| `λ` | `DMFTResult.leading_pairing_eigenvalue` | Leading pairing eigenvalue |
| `threshold` | `0.0` | Subtracted before scaling |
| `kelvin_per_unit` | `25.0` | λ = 1.0 → 25 K (mid conventional band) |
| `ceiling_K` | `40.0` | Same default as `ranking.performance_ceiling_K` |
| `Q` | `1.0` (soft ∈ [0.70, 1]) | Optional occupancy / m* demotion |

**Typical bands** (defaults, Q = 1):

| λ | score_K |
|---|---------|
| 0.0 | 0.0 |
| 0.4 | 10.0 |
| 1.0 | 25.0 |
| 1.6 | 40.0 (clamped) |

This is a **ranking proxy**, not a Tc calculation and not a fitted
Eliashberg / FLEX / DΓA model. Do not cite the number as a predicted Tc.

### `pairing_symmetry`

Metadata only (e.g. `d_x2-y2`). Exported on cards / CSV. **Never**
enters the score.

### No-score cases

`usable=False` (score stays unset / existing) when:

- mapping disabled (`dft.dmft.scoring.enabled: false`)
- no `DMFTResult`
- `leading_pairing_eigenvalue` is `None`, non-numeric, or non-finite
- eigenvalue is **negative** (unphysical for this map)
- `status` ∈ {`failed`, `skipped`, `refused`, `pending`}
- `require_converged` (default) and `converged` is false
- mock result and `allow_mock: false`

### Soft quality demotion (not a physics model)

When `quality_demotion: true` (default):

- `mass_enhancement` above `mass_enhancement_soft_cap` (8.0) multiplies Q
  by `max(0.70, 1 − 0.10 × (m*/cap − 1))`
- `filling` (or occupancy sum) outside `[occupancy_soft_min, occupancy_soft_max]`
  (default 1–12) multiplies Q by 0.90

Missing occupancy / m* → no demotion. Floor is 0.70.

### Mock eigenvalues

The mock solver **fills** an illustrative `leading_pairing_eigenvalue`
(nickelate-like ≈ 0.55–1.25, `d_x2-y2`; other families weaker). These
numbers are **seeded hashes, not literature-validated**. Source is
always `dmft_pairing_mock`. `raw["pairing_label"]` and provenance notes
repeat the disclaimer.

## Precedence

Resolved by `siscforge.scoring.pairing.resolve_performance_score`.
YAML: `ranking.performance_precedence` (default `epw_then_dmft`).

| # | Default rule | What counts |
|---|--------------|-------------|
| 1 | Trusted EPW Eliashberg / Allen–Dynes Tc | `electron_phonon.best_tc_K()` with `status`/`quality_tag` **not** `mock`, not failed/skipped, not `result_quality=unreliable` |
| 2 | DMFT pairing proxy | Usable `PairingMapResult` from the formula above |
| 3 | Existing score | Mock EPW, surrogate stub, or unset |

**Mock EPW is not trusted.** A nickelate dry-run that still carries a
conventional mock Allen–Dynes placeholder will take the DMFT pairing
score, tagged `dmft_pairing_mock`.

Overrides: `dmft_then_epw`, `epw_only`, `dmft_only`.

Ranking (`rank_evaluations`) and Pareto **do not** branch on family.
They only see `performance_score`.

## Config (inert defaults)

```yaml
dft:
  dmft:
    scoring:
      enabled: true            # inert unless a pairing signal exists
      kelvin_per_unit: 25.0
      eigenvalue_threshold: 0.0
      score_ceiling_K: 40.0
      require_converged: true
      allow_mock: true
      quality_demotion: true
      mass_enhancement_soft_cap: 8.0
      occupancy_soft_min: 1.0
      occupancy_soft_max: 12.0

ranking:
  performance_precedence: epw_then_dmft   # default
```

Omitting these keys preserves P3.3 / conventional behaviour when DMFT
is off.

## Wiring

| Site | Behaviour |
|------|-----------|
| `MockCalculator` | After mock EPW fill, `apply_performance_score` |
| `QECalculator` | After EPW/DMFT attach, `apply_performance_score` |
| `siscforge run` `_finalize_eval` | Re-apply with campaign ranking precedence; surrogate only if still unset |
| `siscforge rank --config` | Re-apply so older stores pick up P3.4 |

## Trust / export

- Sources: `dmft_pairing`, `dmft_pairing_mock`
- Quality flags: `dmft_pairing`, `dmft_pairing_mock`
- Synthesis cards: headline origin line + DMFT section show eigenvalue,
  symmetry (metadata), mapped score, and the mock disclaimer
- CSV already has `performance_score_source` and `dmft_leading_pairing_*`
- `do_not_cite_tc` stays true unless `result_quality=production` **and**
  the headline is not a pairing proxy

## Hard out of scope

- Oxygen-vacancy structure generation (**P3.5**)
- Mixed conventional/unconventional AL (**P3.6**)
- Real solid_dmft / CTHYB launch (residual `p3_x_real_launch`)
- Changing Si-feasibility science or the P2.5 process-recommendation schema
- Inventing a full pairing / Tc physics model
