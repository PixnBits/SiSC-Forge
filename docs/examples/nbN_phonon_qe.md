# Running a real NbN phonon calculation with Quantum ESPRESSO

This walkthrough runs the Phase-0 **golden system**: bulk rocksalt NbN SCF + Gamma-point DFPT phonon via the `qe` calculator.

**Install guide (Python + QE + pseudos):** see **[docs/SETUP.md](../SETUP.md)** (Tier B).

## Prerequisites

1. **Quantum ESPRESSO ≥ 7.2** with `pw.x` and `ph.x` on your `PATH`  
   (or set `QE_BIN` / `QUANTUM_ESPRESSO_BIN` to the directory containing them).
2. **Pseudopotentials** (UPF) for Nb and N — e.g. [SSSP](https://www.materialscloud.org/discover/sssp/table/efficiency) or PseudoDojo, PBE.
3. Python env with SiSC-Forge:

```bash
pip install -e ".[dev]"
# optional: jobflow (recipes still run sequentially without a job store)
pip install -e ".[qe]"
```

## Dry-run first (no QE)

```bash
siscforge run --dry-run examples/nbn_phonon_qe.yaml
```

This uses `MockCalculator`, structure generation, and the Si-feasibility scorer only.

## Configure pseudopotentials

Recommended: download [SSSP efficiency (PBE)](https://www.materialscloud.org/discover/sssp/table/efficiency) UPFs into a local folder.

Edit `examples/nbn_phonon_qe.yaml`:

```yaml
dft:
  pseudo_dir: /path/to/sssp_efficiency
  # optional explicit filenames if auto-match fails:
  # pseudopotentials:
  #   Nb: Nb.pbe-spn-kjpaw_psl.1.0.0.UPF
  #   N:  N.pbe-n-kjpaw_psl.1.0.0.UPF
  quality_tag: screening   # or production
  phonon_method: gamma     # dfpt | gamma | phonopy_fd
```

Auto-resolution prefers PBE / PAW / SSSP-like filenames. Missing elements raise a clear `PseudoResolutionError` listing files present in `pseudo_dir`.

Or export for the optional golden pytest:

```bash
export SISCFORGE_PSEUDO_DIR=/path/to/your/upf
```

## Run with the QE calculator

```bash
siscforge run --calculator qe examples/nbn_phonon_qe.yaml
```

Campaign-level alternative (without CLI flag):

```yaml
calculators:
  - name: qe
dft:
  engine: qe
  pseudo_dir: /path/to/your/upf
```

If `pw.x` is missing, the CLI exits with a clear error (no silent mock fallback).

## What gets executed

1. **Structure** — rocksalt NbN from the structure generator (`a ≈ 4.392 Å`).
2. **Optional vc-relax** — when `do_relax: true`; final geometry is **re-read** from pw.x output and used for SCF/phonon.
3. **SCF** — `pw.x` (`calculation='scf'`) with campaign cutoffs / k-grid.
4. **Phonon** — default `ph.x` (`gamma` or `dfpt`); optional `phonopy_fd` if phonopy is installed.
5. **Parse** → `SCFResult` + `PhononResult`; relaxed CIF attached to the candidate when available.
6. **Si-score + rank + export** as usual.

Working directories default to `{output_dir}/qe_work/`.

## Expected phonon reference (order of magnitude)

| Quantity | Phase-0 acceptance |
|----------|--------------------|
| Imaginary modes | None (min ω ≳ −5 cm⁻¹) |
| Highest optical mode | ~300–800 cm⁻¹ (typ. ~450–550 cm⁻¹ PBE) |
| Stability flag | `dynamically_stable: true` |

See `siscforge.calculators.qe.references` and `tests/test_nbn_phonon.py`.

## Optional pytest real-QE gate

```bash
export SISCFORGE_RUN_QE=1
export SISCFORGE_PSEUDO_DIR=/path/to/upf
pytest tests/test_nbn_phonon.py -k real_qe -v
```

Without these env vars, the NbN tests still pass using mock + fixtures.

## Switching calculators

| Goal | Command / config |
|------|------------------|
| Always mock | `siscforge run --dry-run …` |
| Explicit mock | `siscforge run --calculator mock …` |
| Real QE | `siscforge run --calculator qe …` with `dft.pseudo_dir` set |
| Campaign default | `calculators: [{name: qe}]` and `dft.engine: qe` |

`--dry-run` **always** forces mock, regardless of campaign calculator settings.
