# Build a working `ph.x` (and EPW) from source

**Why:** On Ubuntu, `apt install quantum-espresso` gives QE **6.7**.  
`pw.x` works, but **`ph.x` often aborts** with:

```text
*** buffer overflow detected ***: terminated
```

while reading `*.save/data-file-schema.xml`. That blocks DFPT phonons and EPW.

**Fix:** compile **Quantum ESPRESSO ≥ 7.2** yourself and put its `bin/` **ahead of** `/usr/bin` on your `PATH`.

You already have: `pw.x` / `epw.x` / SSSP from apt. Keep the SSSP package; only replace the **binaries**.

---

## 0. Prerequisites

- Ubuntu/Debian with `sudo`
- ~5–10 GB free disk under `$HOME`
- 15–40 minutes wall time (32-core machine is fine)

---

## 1. Install build dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential gfortran \
  libblas-dev liblapack-dev libfftw3-dev \
  libopenmpi-dev openmpi-bin \
  wget tar
```

Check the compiler:

```bash
gfortran --version
mpif90 --version
```

---

## 2. Download Quantum ESPRESSO 7.3.1

Classic `./configure` + `make` path (reliable):

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"

wget -O q-e-qe-7.3.1.tar.bz2 \
  https://gitlab.com/QEF/q-e/-/archive/qe-7.3.1/q-e-qe-7.3.1.tar.bz2

tar xf q-e-qe-7.3.1.tar.bz2
cd q-e-qe-7.3.1
```

(Optional: newer tags such as `qe-7.4.x` / `qe-7.6` exist; 7.3.1 is a good known-good configure-based build.)

---

## 3. Configure and compile

```bash
cd "$HOME/src/q-e-qe-7.3.1"

./configure --enable-parallel --with-scalapack=no
```

### 3a. Fix libmbd (required on gfortran 13–15 / Ubuntu 24.04+)

Bare `make pw` often stops with:

```text
No rule to make target '.../MBD/libmbd.a', needed by 'pw.x'
```

or a half-built `MBD/` with only `.mod` files. Also, `mbd_c_api.F90` fails on new gfortran.
QE only needs the **Fortran** libmbd (skip the C API):

```bash
cd "$HOME/src/q-e-qe-7.3.1"
rm -rf MBD
mkdir -p MBD
(
  cd external/mbd/src
  export FXX=gfortran
  export FXXOPT="-O3 -g -fallow-argument-mismatch -cpp -D__FFTW3 -D__MPI \
    -I../../devxlib/src -I. -I../../../include"
  make -f ../../mbd.make LIBMBD_C_API=0
  cp libmbd.a *.mod ../../../MBD/
)
ls -la MBD/libmbd.a   # must exist
```

### 3b. Build packages (name the targets!)

Plain `make` with **no target** only prints help and may leave `bin/` empty.

```bash
cd "$HOME/src/q-e-qe-7.3.1"
make -j"$(nproc)" pw ph pp epw
```

When finished:

```bash
ls -la bin/pw.x bin/ph.x bin/epw.x
# typically symlinks into PW/src, PHonon/PH, EPW/...
```

If `configure` fails, read `install/config.log` and install any missing `-dev` packages.

---

## 4. Prefer the new binaries over `/usr/bin`

**Important:** `which pw.x` must **not** print `/usr/bin/pw.x` first.

```bash
export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"
export PATH="$QE_BIN:$PATH"

which -a pw.x ph.x epw.x
# First line for each should be under $HOME/src/q-e-qe-7.3.1/bin/
```

Make it permanent (optional):

```bash
cat >> ~/.bashrc <<'EOF'

# SiSC-Forge: private Quantum ESPRESSO build (working ph.x)
export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"
export PATH="$QE_BIN:$PATH"
EOF
source ~/.bashrc
```

SiSC-Forge also honors `QE_BIN` when resolving executables.

---

## 5. Smoke-test `ph.x` (do this before SiSC-Forge)

Uses system SSSP from apt (`/usr/share/espresso/pseudo`):

```bash
export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"
export PATH="$QE_BIN:$PATH"

WORKDIR=/tmp/qe73_ph_smoke
rm -rf "$WORKDIR" && mkdir -p "$WORKDIR/out"
cd "$WORKDIR"
PSEUDO=/usr/share/espresso/pseudo

cat > scf.in <<EOF
&CONTROL
  calculation='scf'
  outdir='$WORKDIR/out'
  prefix='x'
  pseudo_dir='$PSEUDO'
/
&SYSTEM
  ibrav=0
  nat=2
  ntyp=2
  ecutwfc=40
  ecutrho=320
  occupations='smearing'
  smearing='mv'
  degauss=0.02
/
&ELECTRONS
  conv_thr=1.0d-8
/
ATOMIC_SPECIES
 Nb 92.9 Nb.pbe-spn-kjpaw_psl.0.3.0.UPF
 N  14.0 N.pbe-n-radius_5.UPF
ATOMIC_POSITIONS crystal
 Nb 0.0 0.0 0.0
 N  0.5 0.5 0.5
K_POINTS automatic
  2 2 2 0 0 0
CELL_PARAMETERS angstrom
  4.392 0 0
  0 4.392 0
  0 0 4.392
EOF

# Always use mpirun with OpenMPI-linked QE
mpirun --oversubscribe -np 1 "$QE_BIN/pw.x" -in scf.in < /dev/null > scf.out 2>&1
grep 'JOB DONE' scf.out && echo "SCF OK"

cat > ph.in <<EOF
&inputph
  tr2_ph=1.0d-12
  prefix='x'
  outdir='$WORKDIR/out'
  fildyn='x.dyn'
  ldisp=.false.
/
0.0 0.0 0.0
EOF

mpirun --oversubscribe -np 1 "$QE_BIN/ph.x" -in ph.in < /dev/null > ph.out 2>&1
grep -E 'JOB DONE|buffer overflow' ph.out
```

**Success:** you see `JOB DONE` in `ph.out` and **no** `buffer overflow`.  
**Failure:** paste the last 40 lines of `ph.out` and `install/config.log` from the build.

---

## 6. Run SiSC-Forge with DFPT (not phonopy)

```bash
cd ~/projects/SiSC-Forge/main
source .venv/bin/activate

export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"
export PATH="$QE_BIN:$PATH"
export SISCFORGE_PSEUDO_DIR=/usr/share/espresso/pseudo

# Confirm override
python - <<'PY'
from siscforge.calculators.qe.env import detect_qe_environment
e = detect_qe_environment()
print("pw ", e.pw)
print("ph ", e.ph)
print("epw", e.epw)
assert e.ph and "q-e" in e.ph, "Still using distro ph.x? Check PATH/QE_BIN"
print("OK — using private build")
PY
```

Edit the examples to use DFPT again (if they still say `phonopy_fd`):

```yaml
# examples/nbn_phonon_qe.yaml  and  examples/nbn_epw.yaml
dft:
  pseudo_dir: /usr/share/espresso/pseudo
  phonon_method: gamma    # or dfpt
  work_dir: /tmp/siscforge_qe/nbn
```

Then:

```bash
# Tier B — phonon
siscforge run --calculator qe examples/nbn_phonon_qe.yaml

# Tier C — EPW + Tc (longer)
siscforge run --calculator qe-epw examples/nbn_epw.yaml
```

---

## 7. Troubleshooting

| Symptom | What to do |
|---------|------------|
| `gfortran: command not found` | `sudo apt-get install gfortran` |
| `which ph.x` → `/usr/bin/ph.x` | `export PATH="$QE_BIN:$PATH"` and open a new shell; check `which -a ph.x` |
| Build fails on Scalapack | Keep `--with-scalapack=no` (as above) |
| `ph.x` still buffer-overflows | Confirm binary is from `$QE_BIN` (`ldd $(which ph.x)`); rebuild clean: `make clean` then `make -j$(nproc) pw ph epw` |
| EPW fails after good phonon | Check Wannier window / coarse grids; inspect `**/epw.out` — production Wannierization is still non-trivial |

---

## Time / space expectations

| Item | Rough size / time |
|------|-------------------|
| Download | ~50–80 MB |
| Build tree | ~2–4 GB |
| Compile (`-j32`) | ~10–30 min |
| NbN SCF+Γ phonon (screening) | minutes |
| NbN EPW (screening) | tens of minutes to hours |

---

## Related

- [SETUP.md](SETUP.md) — full install tiers  
- [nbN_phonon_qe.md](examples/nbN_phonon_qe.md)  
- [nbN_epw.md](examples/nbN_epw.md)  
