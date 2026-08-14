# P3.6 — Mixed conventional / unconventional AL acquisition

**Status**: **Done**  
**Prerequisite**: P3.4 common `performance_score` (`docs/phase3-p34-pairing-score.md`)  
**Next residuals**: real solid_dmft/CTHYB launch, production GNN heads, golden NdNiO₂ science campaign (not this package)

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

## Wiring

- `siscforge run --al-root` reads `active_learning.pool_mode` and writes
  pool + mode onto `AcquisitionRecord`, `PrioritizationRecord`, and
  `CandidateEvaluation` (`acquisition_pool`, `acquisition_mode`,
  `acquisition_pool_reason`).
- Promotion gate and mock-refusal hygiene from Phase 1.5 are **unchanged**.
- Ranker / Pareto still see only `performance_score` (P3.4) — no family forks.

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
- First-pass acquisition without prior evaluations buckets by family.
- `unknown` has no reserved quota by default (leftover-only).
- No TRIQS hard dependency. No production ALIGNN/MatGL heads. No Josephson.

## Residual (not this package)

- Automated solid_dmft / CTHYB launch (`p3_x_real_launch`)
- Production GNN λ/Tc heads
- Full NdNiO₂ literature-golden recovery campaign (science + compute)
- Josephson (Phase 4)
