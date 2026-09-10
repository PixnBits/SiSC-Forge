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

### QE 7.3.1 symmetry crash (divide_class)

Hexagonal MgB₂ (and some other cells) can segfault in
`divide_class` / `prepare_sym_analysis` during `ph.x` mode-symmetry
analysis, then again in EPW `epw_setup`. QE's `search_sym` flag only
classifies mode irreps — disabling it does **not** change the q-grid
or skip physical perturbations, so dyn + dvscf for EPW stay valid.

The documented golden YAML sets:

```yaml
dft:
  nosym: true            # SCF + PH + EPW NSCF (also forces search_sym off)
  ph_search_sym: false   # explicit; redundant with nosym but documents intent
```

Campaigns that omit those flags get **one** automatic `ph.x` retry with
`search_sym=.false.` when the log/CRASH matches `divide_class`
(`dft.phonon_retry_on_search_sym`, default true). That does not re-SCF.

EPW can still hit the same class after a successful phonon; `dft.nosym`
is the pipeline-wide fix (#89/#90). If EPW then dies in `gmap_sym` with
nosym already on, try `nproc=1` / `epw.npool=1`.

Workstation validation sibling: `examples/mgb2_epw_validation.yaml`
(same flags, explicit SSSP Mg/B pins, Wannier projections from the QE
MgB₂ example). Prefer a **consistent SSSP PBE efficiency pair** — do
not mix PAW Mg with USPP B; that mix was implicated in the crash.

### Screening grids in the example

| Step | Setting |
|------|---------|
| SCF k | 6×6×4 |
| DFPT q | 2×2×2 (`epw.nqc`) |
| EPW coarse k/q | 4×4×2 / 2×2×2 |
| EPW fine k/q | 8×8×6 |
| μ* | 0.10 |

Raise grids and tune Wannier projections for production (see table below).

## Tightening grids (screening → denser workstation)

| Tier | `quality_tag` | DFPT `qpoints` / `nqc` | `epw.nkf` / `nqf` | Notes |
|------|---------------|------------------------|-------------------|--------|
| Screening (example) | `screening` | 2×2×2 | 8×8×6 | Isotropic average; order-of-magnitude Tc |
| Workstation denser | `production` | 4×4×2 | 16×16×12 | Better λ/ω_log; still isotropic |
| Production-oriented | `production` | 6×6×4 | 24×24×16 | Tuned B-p / Mg-s Wannier; anisotropic Eliashberg still out of scope |

```python
from siscforge.calculators.qe.epw_inputs import recommended_grids
recommended_grids("mgb2_boride", "workstation_dense")
```

```yaml
dft:
  quality_tag: production
  kpoints: [8, 8, 6]
  qpoints: [4, 4, 2]
  epw:
    nkc: [6, 6, 4]
    nqc: [4, 4, 2]
    nkf: [16, 16, 12]
    nqf: [16, 16, 12]
```

Failed Wannier/EPW steps print diagnostics (workdir inventory + common-error
hints). See also docs/examples/nbN_epw.md.

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
- pw.x `&SYSTEM`: **`ibrav=4`** with `celldm(1)` / `celldm(3)` derived from
  the lattice (alat in Bohr, c/a) — same layout as QE
  `EPW/examples/mgb2`. Generic / non-hexagonal cells keep `ibrav=0` +
  `CELL_PARAMETERS`; set `dft.ibrav: 0` to force that escape hatch on MgB₂.

## Golden status (phonon vs EPW)

| Path | Status |
|------|--------|
| Phonon (USPP, screening) | **Accepted** — real frequencies (ω > 0) on the workstation golden |
| EPW on QE 7.3.1 | Was **blocked on the symmetry path** while SiSC emitted `ibrav=0` + `CELL_PARAMETERS` (fork vs upstream `EPW/examples/mgb2`). This tree now emits `ibrav=4` + celldm — re-probe with `nosym=false` / `B:pz` (`divide_class`) before relying on nosym / `gmap_sym` remediation |

Prefer a symmetry-on probe after the ibrav fix; keep nosym remediation as fallback only.

## QE example control grids

Sibling YAML ``examples/mgb2_epw_validation_qe_control.yaml`` mirrors
QE 7.3.1 ``EPW/examples/mgb2`` coarse/fine intent (`nk = nq = 6`,
`mp_mesh_k = .true.`, fine ~20³) while keeping SiSC-Forge
``dft.nosym`` from #90. It does **not** replace
``examples/mgb2_epw_validation.yaml`` (screening golden).

After pipeline nosym, if EPW still dies in ``gmap_sym`` /
``free(): invalid pointer`` (`rotate.f90`) with
``Symmetries of crystal: 24`` printed, try ``nproc=1`` /
``epw.npool=1`` — ops have seen this under ``npool>1`` (possible
QE 7.3.1 heap corruption; not an nbndsub issue). Diagnostics suggest
that path when ``dft.nosym`` is already on.

## Limitations

- Isotropic average only (no two-gap anisotropic Eliashberg)
- EPW input remains a **screening template** (`proj=random` unless you customize)
- Real EPW optional for CI; dry-run is the CI path
- Si-feasibility for MgB₂ reflects high process temperature / buffer needs (heuristic)
