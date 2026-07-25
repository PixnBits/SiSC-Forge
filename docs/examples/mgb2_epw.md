# MgB₂ EPW + isotropic Tc (Phase 1 golden)

Workstation path for bulk hexagonal MgB₂ electron-phonon coupling and Tc.

**Install guide (Python + QE + EPW + pseudos):** see **[docs/SETUP.md](../SETUP.md)** (Tier C).

## Physics note (two-gap → isotropic average)

MgB₂ is a **two-gap** superconductor (σ and π bands). SiSC-Forge Phase 1 uses the
same **isotropic** EPW path as NbN:

- total λ and ω_log from α²F
- Allen–Dynes + isotropic Eliashberg Tc
- `performance_score` = best Tc (K)

This recovers **order-of-magnitude** Tc (~30–45 K experimental 39 K) under good
settings, but is **not** a multi-band anisotropic Eliashberg calculation.
Production Wannier projections and denser grids are still required for
literature-quality λ.

## Prerequisites

1. Quantum ESPRESSO with **EPW** (`epw.x`) and **ph.x** / **pw.x**
2. **Wannier90** on `PATH`
3. UPF for **Mg** and **B** (`dft.pseudo_dir`)
4. Optional: `pip install -e ".[qe]"`

## Dry-run (no EPW binaries)

```bash
siscforge run --dry-run examples/mgb2_epw.yaml
```

The mock calculator fills `ElectronPhononResult` with MgB₂-family moments
(higher ω_log, λ ~ 0.6–1.0) and a Tc-based `performance_score`. CSV and
synthesis cards include λ, ω_log, and Tc columns the same way as NbN.

## Real EPW

```bash
export QE_BIN=$HOME/src/q-e-*/bin   # if using a private build
export PATH="$QE_BIN:$PATH"
export SISCFORGE_PSEUDO_DIR=/path/to/upf   # Mg + B UPF

# edit examples/mgb2_epw.yaml dft.pseudo_dir if needed
siscforge run --calculator qe-epw examples/mgb2_epw.yaml
```

Aliases: `--calculator epw` → `qe-epw`.

If `epw.x` is missing, the CLI exits with `QENotAvailableError` (no silent mock).

### Screening grids in the example

| Step | Setting |
|------|---------|
| SCF k | 6×6×4 |
| DFPT q | 2×2×2 (`epw.nqc`) |
| EPW coarse k/q | 4×4×2 / 2×2×2 |
| EPW fine k/q | 8×8×6 |
| μ* | 0.10 |

Raise grids and tune Wannier projections for production.

## Order-of-magnitude MgB₂ targets

| Quantity | Screening acceptance |
|----------|----------------------|
| λ (isotropic avg.) | ~0.5 – 1.2 |
| ω_log | ~500 – 900 K |
| Tc | ~25 – 50 K (exp. bulk ~39 K) |

See `siscforge.calculators.qe.epw_references` (`MGB2_*`).

## Optional pytest gate

```bash
export SISCFORGE_RUN_EPW=1
export SISCFORGE_PSEUDO_DIR=/path/to/upf
pytest tests/test_epw.py -k mgb2_real -v
```

Default `pytest` never requires EPW binaries.

## Structure

- Module: `siscforge.structure.mgb2.build_mgb2`
- Cell: 3-atom hexagonal AlB₂-type (P6/mmm), a = 3.086 Å, c = 3.524 Å
- Enumerated via `material_families: [mgb2_boride]`

## Limitations

- Isotropic average only (no two-gap anisotropic Eliashberg)
- EPW input remains a **screening template** (`proj=random` unless you customize)
- Real EPW optional for CI; dry-run is the CI path
- Si-feasibility for MgB₂ reflects high process temperature / buffer needs (heuristic)
