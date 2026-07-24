"""Parse EPW / Eliashberg text output into ElectronPhononResult."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from siscforge import __version__
from siscforge.calculators.qe.eliashberg import (
    allen_dynes_tc,
    isotropic_eliashberg_tc_from_moments,
)
from siscforge.models.provenance import Provenance
from siscforge.models.results import ElectronPhononResult


def _first_float(patterns: list[str], text: str) -> float | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def parse_epw_text(
    text: str,
    *,
    mu_star: float = 0.1,
    quality_tag: str = "screening",
    extra_raw: dict[str, Any] | None = None,
) -> ElectronPhononResult:
    """Parse EPW stdout / summary for λ, ω_log, Tc.

    Handles common EPW printouts (versions vary). Always recomputes Allen–Dynes
    from λ and ω_log when both are present so results stay consistent even if
    EPW's printed Tc line is missing.
    """
    # λ
    lambda_total = _first_float(
        [
            r"lambda\s*[:=]\s*([-\d.eE+]+)",
            r"Electron-phonon coupling strength\s*=\s*([-\d.eE+]+)",
            r"lambda_tot\s*=\s*([-\d.eE+]+)",
            r"\blambda\b\s+([-\d.eE+]+)",
        ],
        text,
    )

    # ω_log — EPW often prints meV; convert to K if value is small
    omega_raw = _first_float(
        [
            r"omega_log\s*[:=]\s*([-\d.eE+]+)",
            r"logarithmically\s+averaged\s+phonon\s+frequency\s*[:=]\s*([-\d.eE+]+)",
            r"w_log\s*[:=]\s*([-\d.eE+]+)",
            r"ω_log\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )
    omega_log_K: float | None = None
    omega_unit = "unknown"
    if omega_raw is not None:
        # Heuristic: values < 50 are usually meV; literature ω_log ~ 10–40 meV
        # or hundreds of K. If unit markers present, prefer them.
        if re.search(r"omega_log[^\n]{0,40}meV", text, re.IGNORECASE):
            omega_log_K = omega_raw * 11.6045  # meV → K
            omega_unit = "meV"
        elif re.search(r"omega_log[^\n]{0,40}\bK\b", text, re.IGNORECASE):
            omega_log_K = omega_raw
            omega_unit = "K"
        elif omega_raw < 80.0:
            omega_log_K = omega_raw * 11.6045
            omega_unit = "meV_assumed"
        else:
            omega_log_K = omega_raw
            omega_unit = "K_assumed"

    omega_2 = _first_float(
        [
            r"omega_2\s*[:=]\s*([-\d.eE+]+)",
            r"\bomega2\b\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )
    if omega_2 is not None and omega_2 < 80.0:
        omega_2 = omega_2 * 11.6045  # meV → K heuristic

    # Printed Tc from EPW (K)
    tc_printed = _first_float(
        [
            r"Estimated\s+Tc\s*[:=]\s*([-\d.eE+]+)",
            r"Tc\s+Allen-Dynes\s*[:=]\s*([-\d.eE+]+)",
            r"Tc_AD\s*[:=]\s*([-\d.eE+]+)",
            r"Tc\s*\(Allen-Dynes\)\s*[:=]\s*([-\d.eE+]+)",
            r"Critical\s+temperature\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )

    tc_eliashberg = _first_float(
        [
            r"Tc\s+Eliashberg\s*[:=]\s*([-\d.eE+]+)",
            r"Tc_eliashberg\s*[:=]\s*([-\d.eE+]+)",
            r"Eliashberg\s+Tc\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )

    mu_parsed = _first_float(
        [r"mu\s*\*\s*[:=]\s*([-\d.eE+]+)", r"mustar\s*[:=]\s*([-\d.eE+]+)"],
        text,
    )
    mu = mu_parsed if mu_parsed is not None else mu_star

    tc_ad: float | None = None
    if lambda_total is not None and omega_log_K is not None:
        tc_ad = allen_dynes_tc(lambda_total, omega_log_K, mu)
    elif tc_printed is not None:
        tc_ad = tc_printed

    tc_el: float | None = tc_eliashberg
    if tc_el is None and lambda_total is not None and omega_log_K is not None:
        tc_el = isotropic_eliashberg_tc_from_moments(
            lambda_total, omega_log_K, mu, omega_2_K=omega_2
        )

    job_done = "JOB DONE" in text.upper() or (
        lambda_total is not None and omega_log_K is not None
    )
    converged = job_done and lambda_total is not None and lambda_total > 0

    # Very basic Wannier diagnostics
    wannier_ok: bool | None = None
    if re.search(r"Wannier\s+functions", text, re.IGNORECASE):
        if re.search(r"warning|failed|not converged", text, re.IGNORECASE):
            wannier_ok = False
        else:
            wannier_ok = True

    raw: dict[str, Any] = {
        "omega_unit_assumed": omega_unit,
        "tc_printed": tc_printed,
        "job_done": job_done,
    }
    if extra_raw:
        raw.update(extra_raw)

    status = "ok" if converged else ("failed" if text.strip() else "unknown")

    return ElectronPhononResult(
        lambda_total=lambda_total,
        omega_log=omega_log_K,
        omega_2=omega_2,
        mu_star=mu,
        Tc_allen_dynes=tc_ad,
        Tc_eliashberg=tc_el,
        alpha2F_summary={"source": "epw_text", "has_spectrum": "alpha2F" in text.lower()},
        converged=bool(converged),
        wannier_ok=wannier_ok,
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
        raw=raw,
        provenance=Provenance(
            source="epw_parser",
            software={"siscforge": __version__},
            parameters={"mu_star": mu},
        ),
    )


def parse_epw_output(
    path_or_text: Path | str,
    *,
    mu_star: float = 0.1,
    quality_tag: str = "screening",
) -> ElectronPhononResult:
    """Parse an EPW output file or raw text."""
    if isinstance(path_or_text, Path) or (
        isinstance(path_or_text, str) and Path(path_or_text).is_file()
    ):
        path = Path(path_or_text)
        text = path.read_text(encoding="utf-8", errors="replace")
        extra = {"source": str(path)}
    else:
        text = str(path_or_text)
        extra = {"source": "<string>"}
    return parse_epw_text(
        text, mu_star=mu_star, quality_tag=quality_tag, extra_raw=extra
    )
