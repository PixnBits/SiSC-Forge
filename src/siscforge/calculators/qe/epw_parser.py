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


def _last_float(patterns: list[str], text: str) -> float | None:
    """Prefer the *last* match (EPW summary sections come after mode dumps)."""
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            try:
                return float(matches[-1])
            except (ValueError, IndexError):
                continue
    return None


def _mev_to_K(x: float) -> float:
    return float(x) * 11.6045


def parse_epw_text(
    text: str,
    *,
    mu_star: float = 0.1,
    quality_tag: str = "screening",
    extra_raw: dict[str, Any] | None = None,
) -> ElectronPhononResult:
    """Parse EPW stdout / summary for λ, ω_log, Tc.

    Handles common EPW printouts (versions vary), including EPW 5.x lines like::

        lambda :   1.048
        Estimated Allen-Dynes Tc =    12.3 K for muc =    0.10000
        Estimated w_log in Allen-Dynes Tc =     24.15 meV

    Always recomputes Allen–Dynes from λ and ω_log when both are present so
    results stay consistent even if EPW's printed Tc line is missing.
    """
    # Prefer the a2F / Allen–Dynes summary block near the end of the run.
    summary = text
    m_sum = re.search(
        r"Eliashberg Spectral Function.*?\Z",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m_sum:
        summary = m_sum.group(0)

    # λ — prefer explicit "coupling strength" (EPW 5.x Eliashberg block), then
    # bare ``lambda :`` / ``lambda =`` lines (never ``lambda___(i)=`` mode dumps).
    lambda_total = _last_float(
        [r"Electron-phonon coupling strength\s*=\s*([-\d.eE+]+)"],
        text,
    )
    if lambda_total is None or lambda_total == 0.0:
        cand = _last_float(
            [
                r"^\s*lambda\s*[:=]\s*([-\d.eE+]+)",
                r"lambda_tot\s*=\s*([-\d.eE+]+)",
            ],
            summary,
        )
        if cand is not None and cand != 0.0:
            lambda_total = cand
        elif lambda_total is None:
            lambda_total = cand

    # ω_log — EPW 5.x: "Estimated w_log in Allen-Dynes Tc = 8.16 meV"
    omega_log_K: float | None = None
    omega_unit = "unknown"
    wlog_mev = _last_float(
        [
            r"Estimated\s+w_log\s+in\s+Allen-Dynes\s+Tc\s*=\s*([-\d.eE+]+)\s*meV",
            r"w_log\s+in\s+Allen-Dynes[^\n]*=\s*([-\d.eE+]+)\s*meV",
            r"logavg\s*=\s*([-\d.eE+]+)",  # sometimes Ry; handled below
        ],
        text,
    )
    if wlog_mev is not None:
        # logavg in some EPW versions is tiny (Ry-like); w_log line is meV.
        if re.search(
            r"Estimated\s+w_log\s+in\s+Allen-Dynes\s+Tc\s*=\s*"
            + re.escape(f"{wlog_mev}".rstrip("0").rstrip("."))
            + r"?\d*\s*meV",
            text,
            re.IGNORECASE,
        ) or re.search(
            r"Estimated\s+w_log\s+in\s+Allen-Dynes\s+Tc\s*=\s*([-\d.eE+]+)\s*meV",
            text,
            re.IGNORECASE,
        ):
            # Re-extract with explicit meV pattern only
            mev = _last_float(
                [r"Estimated\s+w_log\s+in\s+Allen-Dynes\s+Tc\s*=\s*([-\d.eE+]+)\s*meV"],
                text,
            )
            if mev is not None:
                omega_log_K = _mev_to_K(mev)
                omega_unit = "meV"
            elif wlog_mev < 1e-3:
                # logavg in Ry → K: * 13.6057 * 11604.5 / 1000? Actually Ry to K for
                # phonon freq: 1 Ry = 13.6057 eV = 136057 meV → *11.6045 for K is wrong.
                # Skip unusable logavg.
                omega_log_K = None
            else:
                omega_log_K = _mev_to_K(wlog_mev) if wlog_mev < 80 else wlog_mev
                omega_unit = "meV_assumed" if wlog_mev < 80 else "K_assumed"

    if omega_log_K is None:
        omega_raw = _last_float(
            [
                r"omega_log\s*[:=]\s*([-\d.eE+]+)",
                r"logarithmically\s+averaged\s+phonon\s+frequency\s*[:=]\s*([-\d.eE+]+)",
                r"w_log\s*[:=]\s*([-\d.eE+]+)",
            ],
            text,
        )
        if omega_raw is not None:
            if re.search(r"(omega_log|w_log)[^\n]{0,60}meV", text, re.IGNORECASE):
                omega_log_K = _mev_to_K(omega_raw)
                omega_unit = "meV"
            elif re.search(r"(omega_log|w_log)[^\n]{0,60}\bK\b", text, re.IGNORECASE):
                omega_log_K = omega_raw
                omega_unit = "K"
            elif omega_raw < 80.0:
                omega_log_K = _mev_to_K(omega_raw)
                omega_unit = "meV_assumed"
            else:
                omega_log_K = omega_raw
                omega_unit = "K_assumed"

    omega_2 = _last_float(
        [
            r"omega_2\s*[:=]\s*([-\d.eE+]+)",
            r"\bomega2\b\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )
    if omega_2 is not None and omega_2 < 80.0:
        omega_2 = _mev_to_K(omega_2)

    # Printed Tc from EPW (K) — EPW 5.x Allen–Dynes line
    tc_printed = _last_float(
        [
            r"Estimated\s+Allen-Dynes\s+Tc\s*=\s*([-\d.eE+]+)\s*K",
            r"Estimated\s+Tc\s*[:=]\s*([-\d.eE+]+)",
            r"Tc\s+Allen-Dynes\s*[:=]\s*([-\d.eE+]+)",
            r"Tc_AD\s*[:=]\s*([-\d.eE+]+)",
            r"Tc\s*\(Allen-Dynes\)\s*[:=]\s*([-\d.eE+]+)",
            r"Critical\s+temperature\s*[:=]\s*([-\d.eE+]+)",
            r"mu\s*=\s*0\.10\s+Tc\s*=\s*([-\d.eE+]+)\s*K",
        ],
        text,
    )

    tc_eliashberg = _last_float(
        [
            r"Tc\s+Eliashberg\s*[:=]\s*([-\d.eE+]+)",
            r"Tc_eliashberg\s*[:=]\s*([-\d.eE+]+)",
            r"Eliashberg\s+Tc\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )

    mu_parsed = _first_float(
        [
            r"for\s+muc\s*=\s*([-\d.eE+]+)",
            r"mu\s*\*\s*[:=]\s*([-\d.eE+]+)",
            r"mustar\s*[:=]\s*([-\d.eE+]+)",
        ],
        text,
    )
    mu = mu_parsed if mu_parsed is not None else mu_star

    tc_ad: float | None = None
    if lambda_total is not None and omega_log_K is not None and omega_log_K > 0:
        tc_ad = allen_dynes_tc(lambda_total, omega_log_K, mu)
    if tc_printed is not None:
        # Prefer EPW's printed AD Tc when available (matches their a2F moments).
        tc_ad = tc_printed

    tc_el: float | None = tc_eliashberg
    if tc_el is None and lambda_total is not None and omega_log_K is not None and omega_log_K > 0:
        tc_el = isotropic_eliashberg_tc_from_moments(
            lambda_total, omega_log_K, mu, omega_2_K=omega_2
        )

    finished = bool(
        re.search(r"Total program execution", text, re.IGNORECASE)
        or re.search(r"JOB DONE", text, re.IGNORECASE)
        or re.search(r"Estimated\s+Allen-Dynes\s+Tc", text, re.IGNORECASE)
    )
    job_done = finished or (
        lambda_total is not None and omega_log_K is not None
    )
    converged = bool(
        job_done and lambda_total is not None and lambda_total > 0 and (
            omega_log_K is not None or tc_ad is not None
        )
    )

    # Very basic Wannier diagnostics
    wannier_ok: bool | None = None
    if re.search(r"Wannier", text, re.IGNORECASE):
        if re.search(r"Wannier[^\n]{0,40}(failed|not converged)", text, re.IGNORECASE):
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
        alpha2F_summary={
            "source": "epw_text",
            "has_spectrum": "a2f" in text.lower() or "alpha2f" in text.lower(),
        },
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
    from siscforge.calculators.qe.parser import resolve_text_or_path

    text, source = resolve_text_or_path(path_or_text)
    extra = {"source": source}
    return parse_epw_text(
        text, mu_star=mu_star, quality_tag=quality_tag, extra_raw=extra
    )
