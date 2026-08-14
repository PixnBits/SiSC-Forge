# P4.1 — JosephsonMetrics + Tier-1 analytic estimates

**Status**: **Done** — typed model, Ambegaokar–Baratoff / BCS-from-Tc
analytics, inert-by-default attachment, caveated export  
**Prerequisite**: Phase 1 conventional Tc (`ElectronPhononResult`) and
Phase 2 ranking / export  
**Next**: P4.2 fabrication-compatibility heuristics; later Tier-2 Usadel
/ Tier-3 BdG

This package does **not** implement Usadel, BdG, a fabrication rule
engine, or any ranker fork. DMFT `performance_score` is **not** a gap.

## Goal

1. Typed `JosephsonMetrics` on `CandidateEvaluation.josephson` (default `None`).
2. Pure-function Tier-1 estimates: gap, IcRn, Jc proxy, EJ-style switching
   energy.
3. Gap from Eliashberg / explicit fields when present; otherwise
   BCS-like Δ ≈ 1.764 k_B Tc.
4. Campaign flag `josephson.enabled: false` (default) — zero behaviour
   change when off.
5. Export CSV + synthesis-card section with a bold
   **approximate / ranking only** caveat.

## Units (fixed)

| Quantity | Unit | Field |
|----------|------|-------|
| Gap Δ | **meV** | `gap_meV` |
| Tc used | K | `tc_used_K` |
| IcRn | **mV** | `icrn_mV` |
| Jc proxy | **A/cm²** | `jc_A_per_cm2` |
| Switching / EJ | **eV** (and EJ/k_B in K) | `switching_energy_eV`, `ej_K` |
| Reference area | μm² | `reference_area_um2` |
| Specific resistance RnA | Ω·μm² | `rna_ohm_um2` |

`approximate` is **forced True** on every Tier-1 object.

## Formulas

Pure functions live in `siscforge.josephson.tier1`.

### Gap

Precedence:

1. `ElectronPhononResult.gap_meV`
2. `alpha2F_summary` / `raw` keys: `gap_meV`, `delta_meV`, `eliashberg_gap_meV`
   (or the `_eV` variants, converted ×1000)
3. BCS fallback when a **conventional** Tc exists:

```
Δ [meV] = r × k_B × Tc
k_B = 0.08617333262145 meV/K
r   = 1.764          # 2Δ / k_B Tc = 3.528 (weak-coupling BCS)
```

`r` is `josephson.bcs_gap_ratio` (default 1.764). Optional
`family_gap_ratios` may override per `material_family`. Empty by
default — **no hidden family forks**. Typical literature 2Δ/k_B Tc
values for the families this platform actually screens:

| `material_family` | Suggested `family_gap_ratios` entry | Notes |
|-------------------|--------------------------------------|-------|
| *(default)* | `1.764` (`bcs_gap_ratio`) | Weak-coupling BCS |
| `tm_nitride` | `2.05` | NbN tunnel data typically ~2.0–2.2 |
| `mgb2_boride` | `2.1` | Isotropic / σ-gap effective; two-gap — ranking only |

These are **operator knobs**, not hidden defaults. See the commented
block in `examples/nbn_mgb2_josephson_tier1.yaml`.

Tc itself comes from `ElectronPhononResult.best_tc_K()` (Eliashberg,
else Allen–Dynes). `performance_score` is used **only** when
`performance_score_source` is `epw` / `mock` / `surrogate`. Sources
`dmft_pairing` and `dmft_pairing_mock` are **never** treated as Δ or Tc.

### Ambegaokar–Baratoff IcRn

```
IcRn [mV] = (π/2) × Δ[meV] × tanh(Δ / 2 k_B T)
```

`josephson.temperature_K: null` (default) is the T = 0 limit
(`tanh → 1`), so `IcRn = (π/2) Δ`.

Δ itself is **temperature-independent** (explicit or BCS-from-Tc).
The tanh factor is *not* a Δ(T) closing law. When `temperature_K` ≥
`tc_used_K`, transport proxies (`IcRn`, `Jc`, `EJ`) are forced to **0**
and a note / `t_ge_tc_transport_zeroed` tag is recorded.

This is the classic **Ambegaokar–Baratoff SIS tunnel** result.
`assume_SIS` is recorded in `assumptions` but does **not** change the
computation. SNS / proximity / high-transparency junctions are **not**
covered (Tier-2 Usadel).

### Jc proxy (ranking geometry)

```
Jc [A/cm²] = IcRn [mV] / RnA [Ω·μm²] × 10⁵
```

Default `rna_ohm_um2 = 20` (SIS-like screening default). This is **not**
a measured specific resistance and **not** a device layout. Jc scales
**linearly with 1/RnA**; EJ and Ic scale **linearly with area and
1/RnA**. A global rescale of the knobs does not change ranking *order*
when every row shares the same RnA / area.

### Switching / EJ proxy

```
Ic = Jc × A
EJ = Φ0 Ic / 2π
```

Default `reference_area_um2 = 1.0`. The area is a ranking knob, not a
fabricated junction.

## Config (inert default)

```yaml
josephson:
  enabled: false           # must flip to true
  shortlist_only: true     # attach only rank ≤ shortlist_size
  shortlist_size: 20       # 0 → all ranked rows
  model_tier: analytic_AB
  reference_area_um2: 1.0
  rna_ohm_um2: 20.0
  assume_SIS: true
  temperature_K: null      # T = 0 AB limit
  bcs_gap_ratio: 1.764
  family_gap_ratios: {}    # optional, e.g. tm_nitride: 2.05
  secondary_ranking: false # reserved; P4.1 does not change the ranker
```

When `enabled: false`, `attach_josephson_metrics` is an identity and
every evaluation keeps `josephson=None`.

## Wiring

After `rank_evaluations` in `siscforge run` and `siscforge rank --config`:

1. If disabled → no-op.
2. If `shortlist_only` and rank > `shortlist_size` → leave `josephson=None`.
3. Else compute `estimate_tier1`. Missing gap/Tc → `status=skipped`
   (never a crash). Exceptions during attach leave the row unchanged.

Analytics-only — no calculator plugin. Pairing, Si-feasibility, and
ranking maths are untouched.

## Export

Additive CSV columns `josephson_*` (empty when absent). Synthesis cards
gain a **Josephson metrics (P4.1) — approximate / ranking only**
section when the object is present. Process-recommendation schema `1.0`
is **unchanged**.

## Sanity bands (factor ~2–3)

Tier-1 BCS-from-literature-Tc vs typical experimental scales. Tests
allow a loose factor of 3.

| Material | Tc (K) | Δ exp (meV) | IcRn exp (mV) | This module (BCS, T=0, RnA=20) |
|----------|--------|-------------|---------------|--------------------------------|
| Nb | 9.25 | ~1.4–1.6 | ~1.5–2.5 | Δ≈1.41 meV, IcRn≈2.21 mV, Jc≈11 kA/cm² |
| NbN | 16 | ~2.3–3.2 | ~2.5–5 | Δ≈2.43 meV, IcRn≈3.82 mV, Jc≈19 kA/cm² |
| MgB₂ | 39 | 2.2 (π) / 7 (σ); iso ~5–7 | wide | Δ≈5.93 meV, IcRn≈9.32 mV (isotropic) |

MgB₂ is two-gap; the estimate uses an **isotropic** Δ and is labelled
as such.

## Limits (honest)

- No Usadel, no BdG, no circuit simulation.
- No fabrication-compatibility engine (SIS/SNS/ramp-edge rules) — **P4.2**.
- AB formula is SIS tunnel only. `assume_SIS` is a recorded assumption,
  not a model switch; SNS / proximity / high-transparency junctions are
  out of scope.
- **Temperature-independent Δ.** Tier-1 uses a fixed gap (explicit or
  BCS-from-Tc). The AB tanh factor is applied, but the gap itself does
  not close. If a user sets `temperature_K` ≥ `tc_used_K`, IcRn / Jc /
  EJ are forced to 0 (otherwise the fixed-Δ formula stays finite above
  Tc). Default `temperature_K: null` is the T = 0 ranking limit.
- No secondary ranking on IcRn / Jc (`secondary_ranking` is reserved).
- No family forks in the ranker. `family_gap_ratios` is an explicit
  operator map (empty by default).
- Default RnA / area are ranking assumptions, not process recipes.
  Jc ∝ 1/RnA; EJ ∝ area / RnA.
- Mock EPW Tc produces `quality_tag=mock` metrics — still approximate.

## Enable

```yaml
josephson:
  enabled: true
  shortlist_size: 20
```

```bash
siscforge run --dry-run examples/nbn_mgb2_josephson_tier1.yaml
```
