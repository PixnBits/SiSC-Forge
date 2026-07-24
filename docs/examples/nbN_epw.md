# NbN EPW + isotropic Tc (Phase 1)

Workstation path for bulk rocksalt NbN electron-phonon coupling and Tc.

## Prerequisites

1. Quantum ESPRESSO with **EPW** (`epw.x`) and **ph.x** / **pw.x**
2. **Wannier90** (`wannier90.x`) on `PATH`
3. UPF for Nb and N (`dft.pseudo_dir`)
4. Optional: `pip install -e ".[qe]"`

## Dry-run (no EPW binaries)

```bash
siscforge run --dry-run examples/nbn_epw.yaml
```

Mock calculator fills `ElectronPhononResult` (λ, ω_log, Allen–Dynes Tc) and
`performance_score` so ranking/export exercise the Phase 1 fields.

## Real EPW

```yaml
# examples/nbn_epw.yaml
dft:
  pseudo_dir: /path/to/sssp
  do_epw: true
  epw:
    enabled: true
    nkf: [6, 6, 6]   # screening; raise for production
    nqf: [6, 6, 6]
    mu_star: 0.10
```

```bash
siscforge run --calculator qe-epw examples/nbn_epw.yaml
```

Aliases: `--calculator epw` → `qe-epw`.  
Without `--calculator`, set `calculators: [{name: qe-epw}]` and `dft.do_epw: true`.

If `epw.x` is missing, the CLI exits with a clear `QENotAvailableError` (no silent mock).

## Order-of-magnitude NbN targets

| Quantity | Screening acceptance |
|----------|----------------------|
| λ | ~0.5 – 2.0 |
| ω_log | ~150 – 500 K |
| Tc | ~8 – 25 K (exp. bulk ~16 K) |

See `siscforge.calculators.qe.epw_references`.

## Optional pytest gate

```bash
export SISCFORGE_RUN_EPW=1
export SISCFORGE_PSEUDO_DIR=/path/to/upf
pytest tests/test_epw.py -k real_epw -v
```

## Limitations

- Isotropic Allen–Dynes / simplified Eliashberg only (no anisotropic Eliashberg)
- EPW input is a **screening template**; production Wannier projections must be tuned
- Full NSCF + Wannier prep automation is minimal — expect to stage wavefunctions manually for production
