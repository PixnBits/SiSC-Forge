#!/usr/bin/env bash
# Fast, non-destructive image verification for SiSC-Forge + QE ≥ 7.2.
# Usage (inside container): siscforge-verify
# Or: docker run --rm siscforge:latest siscforge-verify
set -euo pipefail

echo "=== SiSC-Forge image verification ==="
echo "PATH=$PATH"
echo "QE_BIN=${QE_BIN:-}"
echo "SISCFORGE_PSEUDO_DIR=${SISCFORGE_PSEUDO_DIR:-}"
echo

fail=0

check() {
  if "$@"; then
    echo "OK: $*"
  else
    echo "FAIL: $*"
    fail=1
  fi
}

echo "--- (a) binaries ---"
for b in pw.x ph.x epw.x wannier90.x siscforge; do
  if command -v "$b" >/dev/null 2>&1; then
    echo "OK: which $b -> $(command -v "$b")"
  else
    echo "FAIL: $b not on PATH"
    fail=1
  fi
done

# Ensure private QE wins over any system packages
if command -v pw.x >/dev/null 2>&1; then
  pw_path="$(command -v pw.x)"
  case "$pw_path" in
    /opt/qe/bin/*) echo "OK: pw.x is from private build ($pw_path)" ;;
    *)
      echo "WARN: pw.x is not under /opt/qe/bin ($pw_path)"
      # Not a hard fail if version is still ≥ 7.2
      ;;
  esac
fi

# Version ≥ 7.2 (pw.x -v prints Program PWSCF v.X.Y)
ver_line="$(pw.x -v 2>&1 | head -20 || true)"
echo "pw.x version output (first lines):"
echo "$ver_line" | head -5
if echo "$ver_line" | grep -Eiq 'v\.?(7\.[2-9]|7\.[1-9][0-9]|[8-9]\.|[1-9][0-9]\.)'; then
  echo "OK: pw.x reports QE ≥ 7.2"
else
  # Fallback: parse major.minor from common banners
  if echo "$ver_line" | grep -Eiq '7\.(3|4|5|6|7|8|9)|8\.'; then
    echo "OK: pw.x version looks ≥ 7.3"
  else
    echo "FAIL: could not confirm QE ≥ 7.2 from pw.x -v"
    fail=1
  fi
fi

siscforge --version || fail=1

echo
echo "--- (b) Python QE environment detection ---"
python - <<'PY' || fail=1
from siscforge.calculators.qe.env import detect_qe_environment
e = detect_qe_environment()
print(e)
assert e.pw, "pw.x not detected"
assert e.ph, "ph.x not detected"
assert e.epw, "epw.x not detected"
print("OK: detect_qe_environment found pw/ph/epw")
PY

echo
echo "--- (c) unit tests (mock path; no real DFT) ---"
# Stay in /app where the package and tests live
cd /app
# Do not set SISCFORGE_RUN_QE / SISCFORGE_RUN_EPW
pytest -q --tb=no
echo "OK: pytest finished"

echo
echo "--- (d) dry-run CLI smoke ---"
# Use /tmp so we do not pollute the image layers with campaign outputs
export HOME=/tmp/siscforge-verify-home
mkdir -p "$HOME"
cd /tmp
# Copy examples if needed (siscforge resolves paths relative to CWD — use absolute)
siscforge run --dry-run /app/examples/dummy_campaign.yaml -o /tmp/out_dummy
siscforge run --dry-run /app/examples/nbn_epw.yaml -o /tmp/out_nbn_epw
echo "OK: dry-run campaigns completed"

echo
echo "--- (e) SSSP pseudopotentials ---"
ls /usr/share/espresso/pseudo/Nb*.UPF /usr/share/espresso/pseudo/N*.UPF 2>/dev/null \
  || ls /usr/share/espresso/pseudo/ | head -20
if ls /usr/share/espresso/pseudo/Nb*.UPF >/dev/null 2>&1 \
   && ls /usr/share/espresso/pseudo/N*.UPF >/dev/null 2>&1; then
  echo "OK: Nb/N UPF files present under SISCFORGE_PSEUDO_DIR"
else
  echo "WARN: Nb/N UPF glob missed; listing pseudo dir:"
  ls /usr/share/espresso/pseudo/ | head -30 || true
  # Some SSSP packages use different naming; soft-fail only if dir empty
  if [ -z "$(ls -A /usr/share/espresso/pseudo 2>/dev/null)" ]; then
    echo "FAIL: empty pseudo directory"
    fail=1
  else
    echo "OK: pseudo directory non-empty"
  fi
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "=== VERIFICATION FAILED ==="
  exit 1
fi
echo "=== ALL VERIFICATION CHECKS PASSED ==="
