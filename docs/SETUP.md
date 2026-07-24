# SiSC-Forge setup guide

This document is the **canonical install and run guide** for local workstations.  
It covers three tiers:

| Tier | What you get | External binaries |
|------|----------------|-------------------|
| **A — Python only** | Full dry-run / mock path, structure gen, Si-score, ranking, tests | None |
| **B — QE phonon** | Real relax / SCF / phonon (`qe` calculator) | `pw.x`, `ph.x` + UPFs |
| **C — QE + EPW** | Real electron-phonon + Tc (`qe-epw` calculator) — **Phase 1 scientific gate** | `pw.x`, `ph.x`, `epw.x`, `wannier90.x` + UPFs |

On a machine **without** Quantum ESPRESSO (the default for many laptops/CI), stop after **Tier A**.  
The Phase 1 scientific gate (bulk NbN λ and Tc) requires **Tier C**.

---

## Prerequisites (all tiers)

- **Python ≥ 3.11**
- Git
- Network access to install PyPI packages
- Recommended: [`uv`](https://github.com/astral-sh/uv) or a standard `venv` + `pip`

Clone (or use your existing checkout):

```bash
git clone https://github.com/PixnBits/SiSC-Forge.git
cd SiSC-Forge
# or: cd /path/to/your/checkout   e.g. .../projects/SiSC-Forge/main
```

---

## Tier A — Python environment (required on every machine)

### A.1 Create a virtualenv and install SiSC-Forge

**With uv (recommended):**

```bash
# Install uv if needed: https://docs.astral.sh/uv/getting-started/installation/
uv venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

**With pip:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Optional extras:

```bash
# jobflow (optional; QE recipes also run without a job store)
uv pip install -e ".[qe]"
# or: pip install -e ".[qe]"

# phonopy finite-displacement path (optional)
uv pip install -e ".[phonopy]"
```

### A.2 Verify the install

```bash
siscforge --version
pytest -q
```

Expect tests to pass without QE (real QE/EPW tests are skipped unless env vars are set).

### A.3 Dry-run campaigns (no DFT)

```bash
# Nb–Ti–N strain series (Phase 0 end-to-end)
siscforge run --dry-run examples/nbti_n_strain.yaml

# NbN EPW campaign with mock λ / Tc (Phase 1 fields, no epw.x)
siscforge run --dry-run examples/nbn_epw.yaml

# MgB₂ skeleton (mock)
siscforge run --dry-run examples/mgb2_epw_skeleton.yaml

# Structure enumeration only
siscforge enumerate -c examples/nbti_n_strain.yaml
```

Outputs land under `outputs/<campaign_name>/` (JSON, CSV, synthesis cards).

**This is all you need for development, CI, and ranking logic.**  
`--dry-run` always forces the mock calculator and never requires QE/EPW.

---

## Tier B — Quantum ESPRESSO (phonon / SCF only)

### B.1 Install Quantum ESPRESSO

You need at least:

| Binary | Role |
|--------|------|
| `pw.x` | SCF / relax |
| `ph.x` | DFPT phonon |

**Options (pick one):**

1. **Distribution package** (if available): e.g. Ubuntu `quantum-espresso` (version may be older).
2. **Source / official build** — [Quantum ESPRESSO](https://www.quantum-espresso.org/) ≥ 7.2 recommended.
3. **Conda-forge** (when available for your platform):

   ```bash
   conda install -c conda-forge qe
   ```

4. **Module system** on HPC: `module load quantum-espresso` (site-specific).

Confirm:

```bash
which pw.x ph.x
pw.x -v    # or check --help
```

If binaries are not on `PATH`, point SiSC-Forge at them:

```bash
export QE_BIN=/path/to/qe/bin          # directory containing pw.x, ph.x
# optional alias also accepted:
# export QUANTUM_ESPRESSO_BIN=/path/to/qe/bin
```

### B.2 Pseudopotentials (UPF)

SiSC-Forge does **not** vendor UPFs. Recommended: [SSSP efficiency (PBE)](https://www.materialscloud.org/discover/sssp/table/efficiency).

```bash
mkdir -p $HOME/pseudos/sssp_efficiency
# Download Nb and N (and any other elements you need) UPFs into that directory
export SISCFORGE_PSEUDO_DIR=$HOME/pseudos/sssp_efficiency
```

In campaign YAML (`examples/nbn_phonon_qe.yaml`):

```yaml
dft:
  pseudo_dir: /home/YOU/pseudos/sssp_efficiency
  # optional if auto-match fails:
  # pseudopotentials:
  #   Nb: Nb.pbe-....UPF
  #   N:  N.pbe-....UPF
```

### B.3 Run real phonon (no EPW)

```bash
source .venv/bin/activate
# edit dft.pseudo_dir in examples/nbn_phonon_qe.yaml first
siscforge run --calculator qe examples/nbn_phonon_qe.yaml
```

Details: [docs/examples/nbN_phonon_qe.md](examples/nbN_phonon_qe.md).

---

## Tier C — EPW + Wannier90 (Phase 1 scientific gate)

### C.1 Extra binaries

| Binary | Role |
|--------|------|
| `epw.x` | Electron-phonon Wannier (QE EPW package) |
| `wannier90.x` | Wannierization (usually required with EPW) |

QE must be **built with EPW** (not all minimal packages include `epw.x`).  
Wannier90 is often installed separately: [wannier90.org](http://www.wannier.org/).

```bash
which epw.x wannier90.x
export QE_BIN=/path/to/qe/bin   # if needed
```

### C.2 Configure the NbN EPW campaign

Edit [examples/nbn_epw.yaml](../examples/nbn_epw.yaml):

```yaml
dft:
  pseudo_dir: /home/YOU/pseudos/sssp_efficiency   # REQUIRED
  do_epw: true
  epw:
    enabled: true
    nkf: [6, 6, 6]    # screening; raise for production
    nqf: [6, 6, 6]
    mu_star: 0.10
```

### C.3 Run the Phase 1 scientific gate (bulk NbN)

```bash
source .venv/bin/activate
export QE_BIN=/path/to/qe/bin              # if not on PATH
export SISCFORGE_PSEUDO_DIR=$HOME/pseudos/sssp_efficiency

siscforge run --calculator qe-epw examples/nbn_epw.yaml
```

**Order-of-magnitude success (screening):**

| Quantity | Expected range |
|----------|----------------|
| λ | ~0.5 – 2.0 |
| ω_log | ~150 – 500 K |
| Tc | ~8 – 25 K (exp. bulk NbN ~16 K) |

Results: `outputs/nbn_epw_screening/evaluations.json` (fields `electron_phonon.lambda_total`, `Tc_allen_dynes` / `Tc_eliashberg`, `performance_score`).

Optional pytest gate (same stack):

```bash
export SISCFORGE_RUN_EPW=1
export SISCFORGE_PSEUDO_DIR=$HOME/pseudos/sssp_efficiency
pytest tests/test_epw.py -k real_epw -v
```

Details: [docs/examples/nbN_epw.md](examples/nbN_epw.md).

### C.4 If EPW is requested but missing

```text
QE not available:
Quantum ESPRESSO is not available (pw.x not found).
...
epw.x not found; EPW / Eliashberg steps require Quantum ESPRESSO EPW.
```

Exit code **3**. There is **no** silent fallback to mock when you pass `--calculator qe-epw`.  
Use `--dry-run` for mock.

---

## Environment variables (summary)

| Variable | Purpose |
|----------|---------|
| `QE_BIN` / `QUANTUM_ESPRESSO_BIN` | Directory containing `pw.x`, `ph.x`, `epw.x` |
| `SISCFORGE_PSEUDO_DIR` | UPF directory (pytest real-QE/EPW helpers) |
| `SISCFORGE_RUN_QE=1` | Enable optional real QE phonon pytest |
| `SISCFORGE_RUN_EPW=1` | Enable optional real EPW pytest |
| `SISCFORGE_QE_NPROC` | MPI ranks for optional real QE tests |

---

## Quick reference — which command when?

| Goal | Command |
|------|---------|
| Unit tests | `pytest -q` |
| Dry-run nitride campaign | `siscforge run --dry-run examples/nbti_n_strain.yaml` |
| Dry-run NbN e-ph fields | `siscforge run --dry-run examples/nbn_epw.yaml` |
| Real phonon only | `siscforge run --calculator qe examples/nbn_phonon_qe.yaml` |
| Real EPW + Tc (NbN gate) | `siscforge run --calculator qe-epw examples/nbn_epw.yaml` |
| List calculators | `python -c "from siscforge.calculators import list_calculators; print(list_calculators())"` |

---

## This machine (typical laptop / agent host without QE)

If `which pw.x epw.x` prints nothing (as on many developer hosts):

1. **Do Tier A only** — install Python deps, run dry-run and tests.
2. Install QE+EPW+Wannier90+SSSP (Tier B/C) on a workstation or HPC node that has them.
3. Re-run the NbN gate there with `pseudo_dir` set.

Offline checks that still work without QE:

```bash
# Parser + Allen–Dynes on fixture “EPW-like” output
pytest tests/test_epw.py -q

# Mock NbN with populated λ / Tc
siscforge run --dry-run examples/nbn_epw.yaml
```

---

## Related docs

- [Phase 0 exit criteria](phase0-exit.md)
- [Implementation notes](implementation-notes.md)
- [NbN phonon (QE)](examples/nbN_phonon_qe.md)
- [NbN EPW + Tc](examples/nbN_epw.md)
- [Roadmap](ROADMAP.md)
