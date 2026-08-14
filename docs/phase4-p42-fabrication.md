# P4.2 — Fabrication-compatibility heuristics + optional JJ secondary sort

**Status**: **Done** — rule-based SIS / SNS / ramp-edge labels, BEOL /
thermal flags, stack notes, optional soft shortlist sort  
**Prerequisite**: P4.1 (`docs/phase4-p41-josephson-tier1.md`) and Phase 2
Si-feasibility (process-temp ceiling, chemical flags, stacks, membrane)  
**Next**: Tier-2 Usadel / Tier-3 BdG (later Phase 4). No foundry PDK.

This package does **not** implement Usadel, BdG, a process PDK, or any
change to Ambegaokar–Baratoff / BCS / Si-score / `composite_score`
formulae. Fabrication labels **reuse** existing evaluation signals.

## Goal

1. Attach machine-readable flags + short human notes beside Tier-1
   `JosephsonMetrics` when Josephson is enabled.
2. Suggest a junction class (`SIS` / `SNS` / `ramp_edge` / `unknown`).
3. Flag CMOS-ish BEOL conflict from the Si process-temperature ceiling.
4. Optionally re-order **only** the Josephson-annotated shortlist by
   IcRn or Jc for presentation / export.
5. Stay completely inert when `josephson.enabled` is false.

## Inputs (reused, not re-derived)

| Signal | Source | Used for |
|--------|--------|----------|
| `process_temp_ceiling_c` | `SiFeasibilityScore` (P2.2) | BEOL-friendly / thermal caution |
| `chemical_flags` | same | N/O windows, `high_thermal_budget`, interdiffusion, direct-on-Si |
| `recommended_buffers` | same | stack notes for JJ growth |
| `thermal_window_note` | same | copied into notes |
| `membrane_transfer_candidate` / note | P2.3 | ramp-edge alternative + membrane note |
| `material_family` | `StructureCandidate` | class table |
| `josephson.assume_SIS` | campaign config | nitride SIS vs SNS override |
| Tier-1 status / IcRn / Jc | P4.1 `JosephsonMetrics` | `tier1_missing` flag; secondary sort keys |

Missing Si-feasibility or skipped Tier-1 metrics **degrade** to
`unknown` + a note. Attach never crashes.

## Junction-class rules

Implemented by `siscforge.josephson.fabrication.suggest_junction_class`.

| Condition | Primary | Alternatives | Rationale |
|-----------|---------|--------------|-----------|
| `tm_nitride` + `assume_SIS` (default) | `SIS` | `ramp_edge` if membrane | NbN/AlN/NbN-style tunnel is the nitride screening default |
| `tm_nitride` + `assume_SIS: false` | `SNS` | `ramp_edge` if membrane | Operator override |
| `mgb2_boride` | `SNS` | `ramp_edge` | MgB₂ native oxide is a poor SIS barrier |
| `nickelate` / `cuprate` / `other` / missing family | `unknown` | — | No class table; do not invent |

`assume_SIS` is **not** a model switch for MgB₂ — it is recorded, and
the class stays `SNS`. These labels are **heuristics**, not process
qualification.

## Thermal / BEOL

```
beol_friendly          = process_temp_ceiling_c ≤ beol_temp_ceiling_c
thermal_budget_caution = ceiling > beol_temp_ceiling_c
                         OR chemical flag high_thermal_budget
```

Default `beol_temp_ceiling_c = 400` °C (CMOS-ish BEOL). This is a
**screening comparison**, not a foundry spec. Missing ceiling →
`beol_friendly=null`, `thermal_unknown`, plus caution if
`high_thermal_budget` is already set.

## Config (inert default)

```yaml
josephson:
  enabled: false              # must flip to true
  fabrication_hints: true     # P4.2; only runs when enabled
  beol_temp_ceiling_c: 400    # CMOS-ish comparison (°C)
  secondary_ranking: none     # none | icrn | jc
```

`secondary_ranking: false` (P4.1 reserved bool) still loads as `none`.
`true` coerces to `icrn`.

When `enabled: false`, `attach_josephson_metrics` is an identity —
no fabrication object, no secondary sort, no CSV/card content.

## Secondary sort (opt-in, presentation only)

If `secondary_ranking` is `icrn` or `jc`:

1. Rows **without** `josephson` stay in place.
2. Rows **with** `josephson` are re-ordered among those same slots,
   descending on `icrn_mV` or `jc_A_per_cm2` (missing last).
3. `rank` and `composite_score` are **not** rewritten.
4. `josephson.secondary_order` (1-based) and
   `josephson.secondary_ranking` are stamped for export.

This is **not** a ranker fork. Primary P2.4 composite maths is
untouched.

## Export

Additive CSV columns (empty when absent):

- `josephson_junction_class`
- `josephson_beol_friendly`
- `josephson_thermal_caution`
- `josephson_fab_flags`
- `josephson_fab_notes`
- `josephson_secondary_ranking`
- `josephson_secondary_order`

Synthesis cards gain a **Fabrication compatibility (P4.2)** subsection
under the existing Josephson block, repeating **approximate / ranking
only** and stating that labels are heuristics, not process
qualification. Process-recommendation schema `1.0` is **unchanged**.

## Limits (honest)

- No Usadel, no BdG, no circuit simulation.
- No foundry / PDK rule decks. No new materials-science derivation.
- No change to Ambegaokar–Baratoff, BCS constants, Si-feasibility
  scores, or `composite_score`.
- Junction class is a **label**, not a barrier-growth recipe.
- Secondary sort is presentation-only and off by default.

## Enable

```yaml
josephson:
  enabled: true
  fabrication_hints: true
  secondary_ranking: none     # or icrn / jc
```

```bash
siscforge run --dry-run examples/nbn_mgb2_josephson_tier1.yaml
```
