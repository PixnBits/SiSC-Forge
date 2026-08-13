# P3.4 — DMFT pairing eigenvalue → `performance_score`

**Status**: in review — deterministic mapping + evaluation wiring  
**Prerequisite**: P3.3 `DMFTResult` (`docs/phase3-p33-dmft.md`)  
**Next**: **P3.6** — mixed AL acquisition (P3.5 oxygen-vacancy enumeration is shipped; see `docs/phase3-p35-oxygen-vacancy.md`)

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
| `kelvin_per_unit` | `25.0` | See “Why 25 / 40” below |
| `ceiling_K` | `40.0` | Independent clamp; default matches ranking ceiling |
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

### Why 25 K / unit and a 40 K ceiling

These numbers are an **engineering ranking convenience**, not a physical
map from pairing eigenvalue to Tc:

- Linearized pairing eigenvalues approach ~1 near an instability. Mapping
  λ = 1 → **25 K** puts a “just unstable” signal in the middle of the
  existing conventional ranking band (NbN-like screening Tcs).
- The **40 K** clamp matches the default `ranking.performance_ceiling_K`
  so a pairing proxy cannot saturate the 0–100 performance axis harder
  than a conventional Eliashberg Tc. The two ceilings are **independent
  knobs** — if you retune one, set the other too.
- PRD intermediate scientific targets are 40–80 K. This map does **not**
  try to reach that range; it only shares the Phase-0/1 ranker axis.

Mixed-family lists (a nitride with real EPW Tc next to a nickelate with
a pairing proxy) are for **prioritization only**. Absolute comparability
of the two origins is not claimed. Source-aware or family-normalized
acquisition / axes are residual **P3.6**.

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

- `mass_enhancement` above `mass_enhancement_soft_cap` (**8.0**) multiplies Q
  by `max(0.70, 1 − 0.10 × (m*/cap − 1))`. 8 is a loose screening fence
  (typical nickelate m* is ~2–5), not a literature cutoff.
- `filling` (or occupancy sum) outside `[occupancy_soft_min, occupancy_soft_max]`
  (default **1–12**) multiplies Q by 0.90. Loose fence around a few-electron
  d / impurity filling; 12 ≈ a full d-shell plus leftover.

Missing occupancy / m* → no demotion. Floor is 0.70.
`occupancy_soft_min` must be `<= occupancy_soft_max`.

### Mock eigenvalues and dry-run ranking

The mock solver **fills** an illustrative `leading_pairing_eigenvalue`
when `material_family == "nickelate"` (≈ 0.55–1.25, `d_x2-y2`). Other
families get a weaker placeholder. Family is the only switch — a `"Ni"`
substring in the formula is **not** used (brittle for alloys).

These numbers are **seeded hashes, not literature-validated**. Source is
always `dmft_pairing_mock`. `raw["pairing_label"]` and provenance notes
repeat the disclaimer.

`allow_mock` defaults to **true** so the dry-run path (the only working
DMFT path until residual real launch) exercises ranking. That is
intentional: **illustrative mock λ participates in rank/Pareto**.
Mitigations:

- source tag `dmft_pairing_mock` (never `epw`)
- quality tier stays screening; `do_not_cite_tc` stays true
- synthesis-card origin line + mock disclaimer
- `siscforge run` / `siscforge rank` print a yellow banner when any
  ranked row has this source

Set `dft.dmft.scoring.allow_mock: false` for a stricter production
posture (no mock headline score).

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
They only see `performance_score`. Quality assessment runs **after**
the headline source is set, so flags/tier reflect pairing vs EPW.

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
      allow_mock: true         # dry-run ranks on illustrative λ (see above)
      quality_demotion: true
      mass_enhancement_soft_cap: 8.0
      occupancy_soft_min: 1.0
      occupancy_soft_max: 12.0

ranking:
  performance_precedence: epw_then_dmft   # default
```

Omitting these keys preserves P3.3 / conventional behaviour when DMFT
is off.

## Wiring (double-apply contract)

| Site | Behaviour |
|------|-----------|
| `MockCalculator` / `QECalculator` | `apply_performance_score(scoring=…)` only — **default** `epw_then_dmft`. Campaign precedence is not available here. |
| `siscforge run` `_finalize_eval` | Re-apply with campaign `ranking.performance_precedence`; surrogate only if still unset |
| `siscforge rank --config` | Re-apply so older stores pick up P3.4 + campaign precedence |

Inspecting a raw calculator result can therefore differ from the
final campaign row when precedence is not the default. The finalize /
rank step is the contract.

Quality (`apply_quality_assessment`) is invoked inside `rank_evaluations`
after the headline source is set.

## Trust / export

- Sources: `dmft_pairing`, `dmft_pairing_mock`
- Quality flags: `dmft_pairing`, `dmft_pairing_mock`
- Synthesis cards: headline origin line + DMFT section show eigenvalue,
  symmetry (metadata), mapped score, and the mock disclaimer
- CSV already has `performance_score_source` and `dmft_leading_pairing_*`
- `do_not_cite_tc` stays true unless `result_quality=production` **and**
  the headline is not a pairing proxy

## Residual (not this package)

- Non-mock `observables.json` → tagged `dmft_pairing` is exercised by a
  unit test of parse + apply. End-to-end real CTHYB launch remains
  `p3_x_real_launch`.
- Source-aware / family-normalized AL acquisition (**P3.6**).
- Oxygen-vacancy structure generation (**P3.5** — shipped; see `docs/phase3-p35-oxygen-vacancy.md`).
- Changing Si-feasibility science or the P2.5 process-recommendation schema.
- Inventing a full pairing / Tc physics model.
