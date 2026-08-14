# P3.6 — Mixed conventional / unconventional AL acquisition

**Status**: **Done**  
**Prerequisite**: P3.4 common `performance_score` (`docs/phase3-p34-pairing-score.md`)  
**Next residuals**: production CTHYB calibration, production GNN heads, golden NdNiO₂ science campaign (not this package)

This package makes prioritization work when a campaign mixes **conventional**
(nitride / MgB₂ / EPW) and **unconventional** (nickelate / DMFT-pairing)
candidates. It does **not** launch CTHYB, train a GNN, or change pairing /
Si-feasibility maths.

## Goal

1. Acquisition can operate on a **joint** pool or **separate** pools.
2. Provenance records which pool / pathway drove the score.
3. Conventional-only campaigns stay behaviour-compatible when mixed mode is off.
4. Promotion still refuses mock / unknown labels (Phase 1.5 rules unchanged).

## Pools

| Pool | Meaning |
|------|---------|
| `conventional` | EPW / surrogate λ/Tc pathway |
| `unconventional` | DMFT pairing / correlated pathway |
| `unknown` | Neutral fallback — **never** assigned by guessing |

### Derivation precedence

Implemented by `siscforge.active_learning.pools.derive_pool`. First match wins.

| # | Signal | Result |
|---|--------|--------|
| 1 | Recognized `performance_score_source` | `epw` / `epw_eliashberg` / `epw_allen_dynes` / `mock` / `surrogate` → conventional; `dmft_pairing` / `dmft_pairing_mock` → unconventional |
| 2 | Pathway attachments | Only a usable DMFT pairing eigenvalue → unconventional. Only electron-phonon → conventional. **Both**, with no recognized source → `unknown` (`conflict:electron_phonon+dmft_pairing`) |
| 3 | Recognized `material_family` | `tm_nitride` / `mgb2_boride` / `b_doped_si` → conventional; `nickelate` / `cuprate` → unconventional |
| 4 | Nothing recognized | `unknown` |

Unrecognized source strings (including `literature`) **fall through** rather
than silently bucket. Unevaluated candidates (first-pass `siscforge run`)
typically resolve at step 3 from family.

## Acquisition modes

YAML under `active_learning` (defaults preserve pre-P3.6 behaviour):

```yaml
active_learning:
  enabled: true
  pool_mode: off          # off | joint | separate   (default: off)
  pool_quotas:            # used only when pool_mode: separate
    conventional: 0.5
    unconventional: 0.5
    unknown: 0.0
```

| Mode | Selection | Tc-like input |
|------|-----------|---------------|
| `off` (default) | Global top-`max_epw_jobs` | Family-mean surrogate Tc only. Evaluations' `performance_score` is **ignored** so conventional campaigns cannot drift. |
| `joint` | Same global top-k | Common `performance_score` + uncertainty when an evaluation is supplied; else surrogate Tc. One ordered list across pools. |
| `separate` | Per-pool reserved quotas, then leftover by global score | Same signal as `joint`. |

The score formula is unchanged (uncertainty + Tc-like + Si − hull). Mixed
mode only chooses the Tc-like input and (in `separate`) who gets the slots.
No new trained GNN is required.

### Quotas (`separate`)

Reserved slots = `floor(fraction × max_epw_jobs)` per pool that still has
candidates. Leftover slots fill by global acquisition score so an **empty**
pool cannot starve a present one (a nitride-only campaign with
`pool_mode: separate` still fills the batch from conventional).

Quota fractions are each clamped to `[0, 1]` independently. They **may
sum to more than 1**. In that case reservation over-subscribes the batch
and the selected set is truncated to `k` by global score — a high-scoring
pool can still take every slot. Prefer fractions that sum to ≤ 1 when you
want reserved representation.

## Wiring

- `siscforge run --al-root` reads `active_learning.pool_mode` and writes
  pool + mode onto `AcquisitionRecord`, `PrioritizationRecord`, and
  `CandidateEvaluation` (`acquisition_pool`, `acquisition_mode`,
  `acquisition_pool_reason`).
- When the campaign store already contains evaluations (resume, a previous
  cycle in the same `output_dir`, or seeded results), `run` loads them and
  passes them to `prioritize_candidates`. In `joint` / `separate` those
  `performance_score`s become the Tc-like input, and pool derivation can
  use source / pathway signals instead of family alone.
- First-pass (empty store) still buckets by family and scores with the
  surrogate. The common `performance_score` axis is therefore an
  **operator workflow** once some EPW/DMFT results exist — not only a
  public-API / unit-test path.
- Promotion gate and mock-refusal hygiene from Phase 1.5 are **unchanged**.
- Ranker / Pareto still see only `performance_score` (P3.4) — no family forks.

## Scale / bias (P3.4 proxy)

P3.4 maps a pairing eigenvalue λ = 1 → **25 K** (ceiling **40 K**) so a
“just-unstable” signal sits in the middle of the conventional screening
band. See `docs/phase3-p34-pairing-score.md` (“Why 25 / 40”). Mixed lists
remain **prioritization only** — absolute comparability of EPW Tc and a
pairing proxy is not claimed.

**Recommendation:** start mixed campaigns in `separate` mode until the
relative scales of real EPW Tcs vs pairing proxies have been observed in
*this* campaign. Switch to `joint` only after those bands look
commensurate enough for a single ranked list.

## Uncertainty

Even when `performance_score` is substituted as the Tc-like term,
**uncertainty still comes from the conventional `TcLambdaPrediction`**
(family-mean heuristic or trained nitride-style model). For
unconventional candidates that value is poorly calibrated relative to
DMFT epistemic uncertainty and should not be read as a pairing-error
bar. Pathway-aware uncertainty is a later residual once real DMFT labels
exist.

## Export / observability

When AL is active, synthesis cards and CSV grow two additive columns:

- `acquisition_pool`
- `acquisition_mode`

`siscforge al-status` prints per-pool label counts. When mixed mode has
been used (`joint` / `separate` on the latest prioritization, or both
pools have labels) it also prints `last_mode`.

## Limits

- Mixed lists are for **prioritization only**. Absolute comparability of
  EPW Tc and a DMFT pairing proxy is not claimed (same caveat as P3.4).
- First-pass acquisition without prior evaluations buckets by family and
  uses the surrogate Tc. Resume / later cycles in the same store pick up
  EPW/DMFT `performance_score`s automatically.
- Uncertainty is always the conventional surrogate’s (see above).
- `unknown` has no reserved quota by default (leftover-only).
- Quota fractions summing to > 1 over-reserve then truncate (see Quotas).
- No TRIQS hard dependency. No production ALIGNN/MatGL heads. No Josephson.

## Residual (not this package)

- Production CTHYB calibration / solid_dmft version matrix (`p3_x_real_launch` launcher is shipped)
- Production GNN λ/Tc heads
- Pathway-aware (DMFT) uncertainty
- Full NdNiO₂ literature-golden recovery campaign (science + compute)
- Josephson (Phase 4)
