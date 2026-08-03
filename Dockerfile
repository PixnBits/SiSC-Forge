# syntax=docker/dockerfile:1.6
# =============================================================================
# SiSC-Forge — multi-stage image (Python + QE ≥ 7.2 source build + EPW)
#
# Stage qe-builder  : compile Quantum ESPRESSO 7.3.1 (pw/ph/pp/epw) + Wannier90
# Stage runtime     : lean Ubuntu 24.04 with QE bins, SSSP UPFs, Wannier90,
#                     and SiSC-Forge (pip editable install)
#
# Build:
#   docker build -t siscforge:latest .
#
# See docker/BUILD.md for verification commands.
# =============================================================================

ARG QE_VERSION=7.3.1
ARG QE_TAG=qe-7.3.1
ARG WANNIER90_VERSION=3.1.0
ARG UBUNTU_VERSION=24.04

# ---------------------------------------------------------------------------
# Stage 1 — build Quantum ESPRESSO + Wannier90 from source
# ---------------------------------------------------------------------------
FROM ubuntu:${UBUNTU_VERSION} AS qe-builder

ARG QE_VERSION
ARG QE_TAG
ARG WANNIER90_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gfortran \
        libblas-dev \
        liblapack-dev \
        libfftw3-dev \
        libopenmpi-dev \
        openmpi-bin \
        wget \
        tar \
        ca-certificates \
        make \
        file \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

# --- Wannier90 (not packaged on Ubuntu 24.04 noble; required for Tier C / EPW) ---
RUN wget -q -O "wannier90-${WANNIER90_VERSION}.tar.gz" \
        "https://github.com/wannier-developers/wannier90/archive/refs/tags/v${WANNIER90_VERSION}.tar.gz" \
    && tar xf "wannier90-${WANNIER90_VERSION}.tar.gz" \
    && cd "wannier90-${WANNIER90_VERSION}" \
    && printf '%s\n' \
        'F90 = gfortran' \
        'FCOPTS = -O3 -fallow-argument-mismatch' \
        'LDOPTS = -O3' \
        'LIBS = -llapack -lblas' \
        > make.inc \
    && make -j"$(nproc)" wannier \
    && mkdir -p /opt/qe/bin \
    && cp -f wannier90.x /opt/qe/bin/wannier90.x \
    && chmod 755 /opt/qe/bin/wannier90.x \
    && cd /tmp \
    && rm -rf "wannier90-${WANNIER90_VERSION}" "wannier90-${WANNIER90_VERSION}.tar.gz"

# --- Quantum ESPRESSO (configure-based; known-good for Ubuntu 24.04 / gfortran 13+) ---
# GitLab archive tarballs omit git submodules (mbd, fox, …). Clone pinned commits.
RUN wget -q -O "q-e-${QE_TAG}.tar.bz2" \
        "https://gitlab.com/QEF/q-e/-/archive/${QE_TAG}/q-e-${QE_TAG}.tar.bz2" \
    && tar xf "q-e-${QE_TAG}.tar.bz2" \
    && mv "q-e-${QE_TAG}" /opt/qe-src \
    && rm -f "q-e-${QE_TAG}.tar.bz2" \
    && cd /opt/qe-src/external \
    && for component in mbd fox; do \
         hash=$(awk -v c="$component" '$2==c {print $1}' submodule_commit_hash_records); \
         url=$(git config --file /opt/qe-src/.gitmodules --get "submodule.external/${component}.url"); \
         echo "Cloning ${component} @ ${hash} from ${url}"; \
         rm -rf "${component}"; \
         git clone --filter=blob:none "${url}" "${component}"; \
         git -C "${component}" checkout -q "${hash}"; \
         test -d "${component}/src" -o -f "${component}/configure" -o -f "${component}/Makefile"; \
       done

WORKDIR /opt/qe-src

# Parallel build without ScaLAPACK (desktop / single-node friendly)
RUN ./configure --enable-parallel --with-scalapack=no

# libmbd: required by pw.x; skip C API (breaks on gfortran 13–15). See
# docs/build-qe-from-source.md §3a.
RUN set -eux; \
    rm -rf MBD; \
    mkdir -p MBD; \
    test -d external/mbd/src; \
    ( \
      cd external/mbd/src && \
      export FXX=gfortran && \
      export FXXOPT="-O3 -g -fallow-argument-mismatch -cpp -D__FFTW3 -D__MPI \
        -I../../devxlib/src -I. -I../../../include" && \
      make -f ../../mbd.make LIBMBD_C_API=0 && \
      cp -f libmbd.a *.mod ../../../MBD/ \
    ); \
    test -f MBD/libmbd.a || (echo "ERROR: MBD/libmbd.a missing after libmbd build" && ls -la MBD external/mbd 2>/dev/null; exit 1)

# Build only what SiSC-Forge needs (plain `make` prints help and may leave bin/ empty)
RUN make -j"$(nproc)" pw ph pp epw \
    && ls -la bin/pw.x bin/ph.x bin/pp.x bin/epw.x

# Stage install: copy real binaries (resolve symlinks into PW/PHonon/EPW trees)
RUN mkdir -p /opt/qe/bin \
    && for x in pw.x ph.x pp.x epw.x; do \
         if [ -e "bin/$x" ]; then cp -L "bin/$x" "/opt/qe/bin/$x"; fi; \
       done \
    && chmod 755 /opt/qe/bin/* \
    && /opt/qe/bin/pw.x -v 2>&1 | head -5 || true \
    && test -x /opt/qe/bin/wannier90.x \
    && ls -la /opt/qe/bin

# ---------------------------------------------------------------------------
# Stage 2 — runtime image
# ---------------------------------------------------------------------------
FROM ubuntu:${UBUNTU_VERSION} AS runtime

ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="SiSC-Forge" \
      org.opencontainers.image.description="Silicon-compatible superconductor discovery (Python + QE≥7.2 + EPW)" \
      org.opencontainers.image.source="https://github.com/PixnBits/SiSC-Forge" \
      org.opencontainers.image.licenses="MIT"

# Runtime system packages: MPI libs, SSSP UPFs, Python 3.12 (Ubuntu 24.04).
# openmpi-bin pulls the correct libopenmpi soname (3 / 3t64 / 40 depending on suite).
# wannier90.x is built in qe-builder (package absent on noble).
# Do NOT install quantum-espresso (6.7 ph.x is broken).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        git \
        wget \
        tar \
        curl \
        ca-certificates \
        openmpi-bin \
        libblas3 \
        liblapack3 \
        libfftw3-double3 \
        libgfortran5 \
        quantum-espresso-data-sssp \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && python3 --version

# Private QE ≥ 7.2 + Wannier90 (NOT Ubuntu 6.7 packaged binaries)
COPY --from=qe-builder /opt/qe /opt/qe

# Environment (Tier B/C from docs/SETUP.md)
ENV QE_BIN=/opt/qe/bin \
    PATH=/opt/qe/bin:/opt/siscforge-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SISCFORGE_PSEUDO_DIR=/usr/share/espresso/pseudo \
    OMPI_MCA_btl=^openib \
    OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Application tree
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY examples ./examples
COPY docs ./docs
COPY pseudos ./pseudos

# Virtualenv + package (dev + qe + phonopy extras)
RUN python3 -m venv /opt/siscforge-venv \
    && /opt/siscforge-venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/siscforge-venv/bin/pip install -e ".[dev,qe,phonopy]" \
    && /opt/siscforge-venv/bin/siscforge --version \
    && which pw.x ph.x epw.x wannier90.x \
    && which siscforge

# Lightweight verification script (also used by docker/BUILD.md)
COPY docker/verify.sh /usr/local/bin/siscforge-verify
RUN chmod +x /usr/local/bin/siscforge-verify

WORKDIR /workspace
# Mount host data here: -v "$PWD/outputs:/workspace/outputs"
VOLUME ["/workspace"]

# Default: interactive shell. Override with `docker run ... siscforge ...`
CMD ["bash"]
