# Phase 2 Exit Criteria — Silicon Integration Maturity + Ranking Polish

Aligned with [ROADMAP.md](ROADMAP.md) Phase 2 and Technical Specifications Si /
ranking sections. Closes work packages **P2.1–P2.5**.

**Status date**: 2026-08-11  
**Verdict**: Phase 2 **exit criteria met** for workstation-validatable Si-integration
maturity and transparent multi-objective ranking / export. Experimental collaborators
can act on exported cards and `process_recommendations.json` without further
translation of field names.

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Full Si-feasibility component breakdown + YAML weights | **Met** | P2.1 — `SiFeasibilityComponents`, `weights` on score + CSV/JSON export |
| 2 | Multi-layer buffer stacks + chemical/thermal window flags | **Met** | P2.2 — `silicon.buffers`, `chemical_flags`, `thermal_window_note`, `process_temp_ceiling_c` |
| 3 | Critical thickness + membrane-transfer heuristics | **Met** | P2.3 — Matthews–Blakeslee / People–Bean, `membrane_transfer_*` |
| 4 | Multi-objective ranking + Pareto + provenance | **Met** | P2.4 — `RankingConfig` weights, `on_pareto_front`, composite breakdown |
| 5 | Scannable synthesis cards with Process recommendation | **Met** | P2.5 — Identity → Headline → Process → Supporting detail |
| 6 | Machine-readable process-recommendation schema freeze | **Met** | P2.5 — schema `1.0`; cards + `process_recommendations.json` |
| 7 | Known successful systems score sensibly | **Met** | NbN / buffer paths; dry-run campaigns |
| 8 | YAML ranking weights reorder a test set | **Met** | `tests/test_ranking_p24.py` |
| 9 | Cards contain fields for a first growth attempt | **Met** | Stack, thickness/h_c, temp ceiling, chemical flags, membrane, trust |
| 10 | Ranking + export of ~100 candidates is laptop-fast | **Met** | Pure Python ranking/export path |

## Work packages

| WP | Focus | Status |
|----|-------|--------|
| P2.1 | First-class Si component weights + export | **Shipped** |
| P2.2 | Multi-layer buffer stacks + chemical/thermal windows | **Shipped** |
| P2.3 | Critical thickness + membrane heuristics | **Shipped** |
| P2.4 | Multi-objective weights, Pareto, ranking provenance | **Shipped** |
| P2.5 | Richer cards + process-recommendation schema freeze | **Shipped** |

## P2.5 deliverables

| Item | Location |
|------|----------|
| Process recommendation helper | `siscforge.export.process_recommendation` |
| Schema constant | `PROCESS_RECOMMENDATION_SCHEMA_VERSION = "1.0"` |
| Campaign JSON list | `process_recommendations.json` via `export_campaign_bundle` |
| Card layout | `_card_markdown` — Identity / Headline / Process / Supporting detail |
| Schema doc | [process-recommendation-schema.md](process-recommendation-schema.md) |
| Tests | `tests/test_process_recommendation_p25.py` |

## Explicitly deferred (not Phase 2 blockers)

| Item | Notes |
|------|--------|
| Interface-slab DFT for selected shortlist | Optional Phase 2+ / later |
| FEM membrane mechanics | Heuristics only in P2.3 |
| CALPHAD interlayer thermodynamics | Rule-based chemical flags only |
| Production Wannier / anisotropic Eliashberg | Phase 1 residual / later |
| Trained ALIGNN/MatGL GNN heads | Phase 1.5 family-mean only |
| DMFT / nickelates | **Phase 3** |
| Josephson device metrics | **Phase 4** |

## Smoke commands

```bash
pip install -e ".[dev]"
pytest -q tests/test_process_recommendation_p25.py tests/test_ranking_p24.py tests/test_store_export.py
siscforge run --dry-run examples/nbn_si_45deg.yaml
siscforge run --dry-run examples/nbti_n_al_broad.yaml
# After a run, inspect:
#   outputs/.../synthesis_cards.md
#   outputs/.../process_recommendations.json
```

## Exit criterion mapping (ROADMAP)

> Si-feasibility scores are trusted enough to be the primary filter before expensive
> EPW; experimental collaborators can act on the exported cards without further
> translation.

| Phrase | How met |
|--------|---------|
| Primary Si filter before EPW | Component scores + stacks + h_c + chemical/thermal flags on every evaluation |
| Act without further translation | Process recommendation section + frozen JSON keys (schema 1.0) |
| Transparent ranking | P2.4 weights / Pareto / composite breakdown on cards and CSV |
