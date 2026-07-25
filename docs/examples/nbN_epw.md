# NbN EPW + isotropic Tc (Phase 1)

Workstation path for bulk rocksalt NbN electron-phonon coupling and Tc.

**Install guide (Python + QE + EPW + pseudos):** see **[docs/SETUP.md](../SETUP.md)** (Tier C).

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

## Tightening grids (screening → literature recovery)

Screening NbN on a workstation often yields **~18–22 K** Allen–Dynes Tc with
inflated λ from soft modes. To move toward literature bulk ~16 K with more
stable λ:

| Tier | `quality_tag` | DFPT `qpoints` / `epw.nqc` | `epw.nkf` / `nqf` | Notes |
|------|---------------|----------------------------|-------------------|--------|
| Screening (example) | `screening` | 2×2×2 | 6×6×6 | Order-of-magnitude; soft modes common |
| Workstation denser | `production` | 4×4×4 | 12×12×12 | Multi-hour on 16–32 cores; lower `eps_acustic` |
| Production-oriented | `production` | 6×6×6 | 18×18×18 | Needs **tuned Wannier projections** (not `proj=random`) |

Programmatic suggestions:

```python
from siscforge.calculators.qe.epw_inputs import recommended_grids
recommended_grids("tm_nitride", "workstation_dense")
```

YAML sketch (override `examples/nbn_epw.yaml`):

```yaml
dft:
  quality_tag: production
  kpoints: [8, 8, 8]
  qpoints: [4, 4, 4]
  epw:
    nkc: [6, 6, 6]
    nqc: [4, 4, 4]   # must match DFPT q-grid
    nkf: [12, 12, 12]
    nqf: [12, 12, 12]
    eps_acustic: 5.0
    degaussw: 0.05
```

**`quality_tag` is a label** propagated to SCF / phonon / e-ph results and
exports. Changing it alone does not densify grids — edit the knobs above.

## Failure diagnostics

On failed `pp.py` / NSCF / `epw.x` steps, SiSC-Forge appends a short diagnostic
block: which files exist under the work directory, and hints for common
patterns (`cannot bracket` Ef, missing `save/`, PAW `d_matrix`, soft modes).
Inspect `epw.out` tails under `{output_dir}/qe_work/...`.

## Limitations

- Isotropic Allen–Dynes / simplified Eliashberg only (no anisotropic Eliashberg)
- EPW input is a **screening template**; production Wannier projections must be tuned
- Full automated Wannier projection discovery is out of scope (Phase 1 freeze)
