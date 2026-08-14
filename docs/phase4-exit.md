# Phase 4 Exit Criteria — Device-Level (Josephson) Modeling

Aligned with [ROADMAP.md](ROADMAP.md) Phase 4 and Technical Specifications §2.8.
Covers work packages **P4.1–P4.2** only — the **analytic (Tier-1)** path.

**Status date**: 2026-08-14
**Verdict**: Phase 4 **Tier-1 exit criteria met**. Top-ranked Si-compatible
candidates can carry caveated Ambegaokar–Baratoff / BCS-from-Tc metrics **and**
fabrication-class / thermal hints when `josephson.enabled` is set. The module
stays completely inert when disabled. **Usadel / BdG remain residual** — this
is **not** a full Phase 4 close-out.

Package notes: [phase4-p41-josephson-tier1.md](phase4-p41-josephson-tier1.md)
(PR [#24](https://github.com/PixnBits/SiSC-Forge/pull/24)),
[phase4-p42-fabrication.md](phase4-p42-fabrication.md)
(PR [#26](https://github.com/PixnBits/SiSC-Forge/pull/26)).

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Typed `JosephsonMetrics` on `CandidateEvaluation` | **Met** | P4.1 — optional `josephson` (default `None`); `approximate` forced True |
| 2 | Tier-1 formulas (AB IcRn, Jc proxy, EJ-style switching) | **Met** | P4.1 — `siscforge.josephson.tier1` |
| 3 | Gap helpers (Eliashberg / explicit → else BCS-from-Tc) | **Met** | P4.1 — Δ = 1.764 k_B Tc fallback; DMFT pairing is **not** a gap |
| 4 | Shortlist enable (`enabled` + top-N) | **Met** | P4.1 — `josephson.enabled` / `shortlist_only` / `shortlist_size` |
| 5 | Fabrication-compatibility hints | **Met** | P4.2 — SIS / SNS / ramp-edge / unknown; BEOL / thermal; stack notes |
| 6 | Secondary sort is presentation-only | **Met** | P4.2 — `secondary_ranking: none \| icrn \| jc`; `rank` / `composite_score` unchanged |
| 7 | Caveats on cards / CSV | **Met** | P4.1 **approximate / ranking only**; P4.2 repeats + “heuristics, not process qualification” |
| 8 | Inert when disabled | **Met** | Dummy / existing dry-runs keep `josephson=None` |
| 9 | Nb / NbN / MgB₂ order-of-magnitude | **Met** | `tests/test_josephson_p41.py` — loose factor ~2–3 |
| 10 | Missing inputs never crash rank / export | **Met** | skipped / `unknown` + note |

## Work packages

| WP | Focus | Status |
|----|-------|--------|
| P4.1 | `JosephsonMetrics` + Tier-1 AB / BCS-from-Tc + shortlist attach + caveated export | **Shipped** |
| P4.2 | Fabrication-compatibility heuristics + optional presentation-only secondary sort | **Shipped** |
| P4.3+ | Tier-2 Usadel / Tier-3 BdG backends | **Residual** |

## Deliverables (Tier-1)

| Item | Location |
|------|----------|
| Model | `JosephsonMetrics` + nested `JosephsonFabricationHints` |
| Analytics | `siscforge.josephson.tier1` |
| Fabrication rules | `siscforge.josephson.fabrication` |
| Attach | `attach_josephson_metrics` after `rank_evaluations` (`run` / `rank --config`) |
| Config | `CampaignConfig.josephson` — **disabled by default** |
| Export | Additive `josephson_*` CSV + synthesis-card section / P4.2 subsection |
| Tests | `tests/test_josephson_p41.py`, `tests/test_josephson_p42.py` |
| Example | `examples/nbn_mgb2_josephson_tier1.yaml` |
| Package notes | [phase4-p41-josephson-tier1.md](phase4-p41-josephson-tier1.md), [phase4-p42-fabrication.md](phase4-p42-fabrication.md) |

## Explicit non-claims

| Claim we do **not** make | Why |
|--------------------------|-----|
| Process / foundry qualification | Labels reuse Phase-2 Si-feasibility signals; no PDK rule decks |
| Usadel (Tier-2) or BdG (Tier-3) transport | Not implemented; AB remains an SIS-tunnel proxy even when class is SNS / ramp-edge |
| DMFT `performance_score` is a gap or Tc | Sources `dmft_pairing` / `dmft_pairing_mock` are never used as Δ or Tc |
| Ranker family forks or composite-score change | Secondary sort reorders Josephson-annotated shortlist slots only |
| Device-design / circuit values | Forced `approximate: true`; ranking geometry (RnA, area) are knobs |

## How to enable

```yaml
josephson:
  enabled: true                 # must flip; default false
  shortlist_only: true
  shortlist_size: 20
  fabrication_hints: true       # P4.2; only runs when enabled
  beol_temp_ceiling_c: 400
  secondary_ranking: none       # or icrn / jc — presentation only
```

```bash
siscforge run --dry-run examples/nbn_mgb2_josephson_tier1.yaml
```

When `enabled: false`, attach is an identity — no metrics, no fabrication
object, no secondary sort, no CSV / card content.

## Residual (not Tier-1 blockers)

| Item | Notes |
|------|--------|
| Tier-2 Usadel | SNS / proximity / high-transparency transport |
| Tier-3 BdG | Geometry-aware microscopic backend |
| Full PDK / foundry rule decks | Heuristic class + thermal flags only |
| Real CTHYB launch, production GNN, NdNiO₂ science golden | Phase 3 residuals — unchanged |
| GPU QE | Not claimed |

## Smoke commands

```bash
pip install -e ".[dev]"
pytest -q tests/test_josephson_p41.py tests/test_josephson_p42.py
siscforge run --dry-run examples/dummy_campaign.yaml
# expect: no Josephson section
siscforge run --dry-run examples/nbn_mgb2_josephson_tier1.yaml
# expect: caveated Tier-1 metrics + fabrication hints
```

## Exit criterion mapping (ROADMAP)

> Top-ranked Si-compatible candidates can carry useful, clearly caveated JJ
> metrics **and** fabrication-class / thermal hints when `josephson.enabled`
> is set; the module remains completely inert when disabled. Usadel/BdG
> remain later.

| Phrase | How met |
|--------|---------|
| Useful, caveated JJ metrics | P4.1 AB / BCS-from-Tc on cards + CSV; **approximate / ranking only** |
| Fabrication-class / thermal hints | P4.2 SIS/SNS/ramp-edge + BEOL / thermal flags (heuristics, not qualification) |
| Inert when disabled | `enabled: false` → `josephson=None` everywhere |
| Usadel/BdG later | Explicit residual; no calculator plugin |
