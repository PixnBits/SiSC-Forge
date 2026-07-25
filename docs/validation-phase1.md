# Phase 1 Automated Validation Record

**Date**: 2026-07-25  
**Environment**: Python 3.14 venv, mock dry-run path (no EPW required)

## Commands run

| # | Command | Result |
|---|---------|--------|
| 1 | `pytest -q` | **109 passed**, 3 skipped (optional real QE/EPW gates) |
| 2 | `siscforge run --dry-run examples/nbti_n_strain.yaml` | **OK** — 15 candidates, mock ranking |
| 3 | `siscforge run --dry-run examples/nbti_n_al.yaml` | **OK** — AL acquisition table, top-5 selected / 10 deferred |
| 4 | `siscforge run --dry-run examples/nbn_epw.yaml` | **OK** — mock λ/Tc + Si-score |
| 5 | `siscforge run --dry-run examples/mgb2_epw.yaml` | **OK** — mock MgB₂ e-ph fields |

## Confirmations

- All dry-runs succeed and write JSON/CSV/synthesis cards under `outputs/`
- AL example prints acquisition ranking (Acq / Tĉ / Unc / Si / EPW?) and selects `max_epw_jobs`
- NbN / MgB₂ / nitride mock paths remain green

## Real EPW (human / scientific gate — not CI)

| System | Status |
|--------|--------|
| Bulk NbN (screening grids) | Previously recovered **~18–22 K** Allen–Dynes Tc on workstation (soft modes inflate λ) |
| Bulk MgB₂ | Optional; dry-run only in this validation record |

Real EPW remains **optional** for CI (`SISCFORGE_RUN_EPW=1`). Literature-quality λ requires denser grids and production Wannier projections (deferred).
