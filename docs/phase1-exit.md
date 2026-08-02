# Phase 1 Exit Criteria

Aligned with [ROADMAP.md](ROADMAP.md) Phase 1 and Technical Specifications v0.3.

**Status date**: 2026-07-25  
**Tag**: `v0.1.0-phase1`  
**Verdict**: Phase 1 **core exit criteria met** for workstation-validatable conventional screening (mock path always; real EPW optional).

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | EPW + isotropic Eliashberg path | **Met** | `qe-epw` calculator, multi-q DFPT → pp → NSCF → epw, Allen–Dynes / isotropic Tc |
| 2 | `ElectronPhononResult` + `performance_score` in ranking/export | **Met** | CSV / synthesis cards include λ, ω_log, Tc |
| 3 | NbN golden (mock + optional real) | **Met** | `examples/nbn_epw.yaml`; real screening run ~18–22 K Tc on workstation |
| 4 | MgB₂ golden (mock + optional real) | **Met** | `examples/mgb2_epw.yaml`, fixture + docs |
| 5 | λ/Tc surrogate stub for pre-filter | **Met** | `surrogates/tc_lambda.py`; campaign `surrogate.tc_lambda` |
| 6 | Minimal AL prioritization | **Met** | `active_learning/` acquisition + top-k expensive path |
| 7 | Dry-run / mock path intact | **Met** | All example dry-runs green; 124+ tests |
| 8 | Quality tags screening vs production | **Met** | `DFTConfig.quality_tag`; EPW screening templates |

## Shipped after Phase 1 freeze (not deferred)

| Item | Status | Notes |
|------|--------|--------|
| 45° epitaxy + buffer library (Si v0.2) | **Shipped** | Slice 10 — `silicon.buffers`, scorer auto cube/45°/buffer |
| Light EPW diagnostics / grid ladders | **Shipped** | Slice 11 — `diagnose_epw_failure`, `recommended_grids` |
| Broader AL campaign example | **Shipped** | Slice 12 — `examples/nbti_n_al_broad.yaml` |
| Resume / checkpoint on re-run | **Shipped** | Slice 13 — skip finished, continue-on-error |
| Desktop shortlist → real EPW | **In progress** | Slice 14 — `siscforge shortlist` |

## Explicitly deferred (not Phase 1 blockers)

| Item | Notes |
|------|--------|
| Production Wannier automation | Tuned projections, exclude_bands, windows beyond screening template |
| Anisotropic / multi-band Eliashberg | MgB₂ σ–π; SCDFT |
| Trained λ/Tc GNN (ALIGNN/MatGL) | Family-heuristic stub only |
| Full AL retrain loop | Prioritization only; no retrain on EPW labels |
| Multi-layer buffer stacks / critical thickness | Remaining **Phase 2** |
| Membrane mechanics / interface slabs | Phase 2+ |
| DMFT / Josephson | Phases 3–4 |

## Smoke commands

```bash
pip install -e ".[dev]"
pytest -q
siscforge run --dry-run examples/nbti_n_strain.yaml
siscforge run --dry-run examples/nbti_n_al.yaml
siscforge run --dry-run examples/nbti_n_al_broad.yaml
siscforge run --dry-run examples/nbn_epw.yaml
siscforge run --dry-run examples/mgb2_epw.yaml
siscforge run --dry-run examples/nbn_si_45deg.yaml
siscforge run --dry-run examples/nbti_n_phonon_map.yaml
```

Validation record: [validation-phase1.md](validation-phase1.md).

## Configuration highlights

```yaml
dft:
  do_epw: true
  quality_tag: screening
  epw: { enabled: true, nkf: [6,6,6], nqf: [6,6,6], mu_star: 0.10 }

surrogate:
  tc_lambda: { enabled: true, keep_top_n: 10 }

active_learning:
  enabled: true
  max_epw_jobs: 5
  strategy: uncertainty_si_tc

run:
  resume: true
  continue_on_error: true
```
