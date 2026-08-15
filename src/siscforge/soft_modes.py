"""Soft-mode characterisation for phonon-map stores (Slice 29).

Coarse q=2³ maps are an intentional discovery gate, not production
dynamical-stability proof. When a map finishes with zero
``dynamically_stable`` survivors — including known-stable binaries such as
NbN / TiN / ZrN — the operator needs a campaign-level summary and a next
action, not a bare “none stable” dead-end.

This module is **heuristic and non-blocking**:

* Missing frequency lists → conservative class (never auto-promote to
  “stable” or to EPW).
* Labels such as ``likely_mesh_artefact`` are *suspects*, not proof.
* The report must never be treated as a go-ahead to launch EPW on
  imaginary-mode cells.

AL acquisition may later consume ``soft_mode_class`` / pilot provenance;
that wiring is intentionally out of scope (TODO).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal, TypedDict

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.results import PhononResult
from siscforge.store import EvaluationStore

SoftModeClass = Literal[
    "stable",
    "likely_mesh_artefact",
    "optical_soft",
    "setup_failed",
    "genuinely_soft",
    "unknown",
]

REPORT_JSON = "soft_mode_report.json"
REPORT_MD = "soft_mode_report.md"
REPORT_SCHEMA = "siscforge.soft_mode_report"
REPORT_VERSION = 1

# Literature-stable rock-salt binaries that still go soft on coarse q=2³
# screening meshes in practice. Used only as a *heuristic* flag.
KNOWN_STABLE_RS_NITRIDES = frozenset({"NbN", "TiN", "ZrN", "HfN"})

# Same threshold as the DFPT parser: ignore tiny numeric acoustic noise.
_IMAG_THRESHOLD_CM1 = 5.0

# Campaign-level signals (not physical verdicts).
SIGNAL_NONE_STABLE = "none_stable"
SIGNAL_NONE_STABLE_BINARIES_SOFT = "none_stable_known_binaries_also_soft"
SIGNAL_HAS_STABLE = "has_stable_survivors"
SIGNAL_NO_PHONON = "no_phonon_results"
SIGNAL_SKIPPED = "skipped"


class SoftModeRow(TypedDict):
    candidate_id: str
    formula: str
    strain: float | None
    min_frequency_cm1: float | None
    has_imaginary_modes: bool | None
    dynamically_stable: bool | None
    n_modes: int | None
    phonon_status: str | None
    eval_status: str
    soft_mode_class: SoftModeClass
    reasons: list[str]
    acoustic_vs_optical: str
    asr_signal: str | None
    is_binary_nitride: bool
    is_known_stable_binary: bool


def reduced_formula(formula: str) -> str:
    """Best-effort reduced formula (NbN, TiN, …)."""
    text = (formula or "").strip()
    if not text:
        return ""
    try:
        from pymatgen.core import Composition

        return str(Composition(text).reduced_formula)
    except Exception:  # noqa: BLE001 — conservative fallback
        return text.replace(" ", "")


def is_binary_nitride(formula: str) -> bool:
    """True for two-element nitride cells (NbN, TiN, ZrN, …)."""
    text = (formula or "").strip()
    if not text:
        return False
    try:
        from pymatgen.core import Composition

        comp = Composition(text)
        els = {el.symbol for el in comp.elements}
        return "N" in els and len(els) == 2
    except Exception:  # noqa: BLE001
        red = reduced_formula(text)
        return red in KNOWN_STABLE_RS_NITRIDES or (
            red.endswith("N") and 2 <= len(red) <= 4
        )


def is_known_stable_binary(formula: str) -> bool:
    return reduced_formula(formula) in KNOWN_STABLE_RS_NITRIDES


def _frequency_list(ph: PhononResult) -> list[float]:
    """Extract a flat cm⁻¹ list when the store actually has one."""
    raw = ph.raw or {}
    for key in ("frequencies_cm1", "frequencies", "omega_cm1"):
        val = raw.get(key)
        if isinstance(val, list) and val:
            out: list[float] = []
            ok = True
            for item in val:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    out.append(float(item))
                else:
                    ok = False
                    break
            if ok and out:
                return out
    return []


def _n_atoms(ev: CandidateEvaluation) -> int | None:
    meta = ev.candidate.metadata or {}
    for key in ("n_atoms", "natoms", "n_sites"):
        if key in meta:
            try:
                return int(meta[key])
            except (TypeError, ValueError):
                pass
    cif = ev.candidate.structure_cif
    if cif:
        try:
            from pymatgen.core import Structure

            return len(Structure.from_str(cif, fmt="cif"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _acoustic_vs_optical(
    freqs: list[float],
    *,
    n_atoms: int | None,
    n_modes: int | None,
    imag_threshold_cm1: float = _IMAG_THRESHOLD_CM1,
) -> tuple[str, str | None]:
    """Return (label, asr_signal) when a single-q / Gamma list is detectable.

    A flat mesh dump (n_modes ≫ 3 N_at) is **undetermined** — first-three
    slicing would be wrong. Missing data is also undetermined.
    """
    if not freqs:
        return "undetermined", None
    expected: int | None = None
    if n_atoms is not None and n_atoms > 0:
        expected = 3 * int(n_atoms)
    elif n_modes is not None and int(n_modes) > 0:
        expected = int(n_modes)
    # Detectable only when the list length matches one q-point (3 N_at).
    if expected is None or len(freqs) != expected or expected < 3:
        return "undetermined", None

    thr = -abs(float(imag_threshold_cm1))
    acoustic = freqs[:3]
    optical = freqs[3:]
    imag_ac = any(f < thr for f in acoustic)
    imag_op = any(f < thr for f in optical)
    asr: str | None = None
    if all(abs(f) > 20.0 for f in acoustic):
        asr = "acoustic_triplet_far_from_zero"
    if imag_op:
        return "optical_imaginary", asr
    if imag_ac:
        return "acoustic_only_imaginary", asr
    return "none_below_threshold", asr


def classify_soft_mode(
    ev: CandidateEvaluation,
    *,
    imag_threshold_cm1: float = _IMAG_THRESHOLD_CM1,
) -> SoftModeRow:
    """Classify one evaluation. Conservative when frequencies are missing."""
    c = ev.candidate
    formula = c.formula
    ph = ev.phonon
    binary = is_binary_nitride(formula)
    known = is_known_stable_binary(formula)
    reasons: list[str] = []

    if ph is None:
        return SoftModeRow(
            candidate_id=c.candidate_id,
            formula=formula,
            strain=c.in_plane_strain,
            min_frequency_cm1=None,
            has_imaginary_modes=None,
            dynamically_stable=None,
            n_modes=None,
            phonon_status=None,
            eval_status=ev.status or "unknown",
            soft_mode_class="unknown",
            reasons=["no_phonon_result"],
            acoustic_vs_optical="undetermined",
            asr_signal=None,
            is_binary_nitride=binary,
            is_known_stable_binary=known,
        )

    status = (ph.status or "unknown").lower()
    freqs = _frequency_list(ph)
    n_modes = ph.n_modes if ph.n_modes is not None else (len(freqs) or None)
    ac_op, asr = _acoustic_vs_optical(
        freqs,
        n_atoms=_n_atoms(ev),
        n_modes=n_modes,
        imag_threshold_cm1=imag_threshold_cm1,
    )

    setup = status not in {"ok", "mock"} or (
        not freqs
        and ph.min_frequency_cm1 is None
        and (n_modes is None or int(n_modes) <= 0)
    )
    if setup:
        cls: SoftModeClass = "setup_failed"
        reasons.append("phonon_setup_or_empty_modes")
        return SoftModeRow(
            candidate_id=c.candidate_id,
            formula=formula,
            strain=c.in_plane_strain,
            min_frequency_cm1=ph.min_frequency_cm1,
            has_imaginary_modes=ph.has_imaginary_modes,
            dynamically_stable=ph.dynamically_stable,
            n_modes=n_modes,
            phonon_status=ph.status,
            eval_status=ev.status or "unknown",
            soft_mode_class=cls,
            reasons=reasons,
            acoustic_vs_optical=ac_op,
            asr_signal=asr,
            is_binary_nitride=binary,
            is_known_stable_binary=known,
        )

    if ph.dynamically_stable and not ph.has_imaginary_modes:
        min_f = ph.min_frequency_cm1
        if min_f is None or float(min_f) >= -abs(imag_threshold_cm1):
            return SoftModeRow(
                candidate_id=c.candidate_id,
                formula=formula,
                strain=c.in_plane_strain,
                min_frequency_cm1=ph.min_frequency_cm1,
                has_imaginary_modes=ph.has_imaginary_modes,
                dynamically_stable=ph.dynamically_stable,
                n_modes=n_modes,
                phonon_status=ph.status,
                eval_status=ev.status or "unknown",
                soft_mode_class="stable",
                reasons=["no_imaginary_modes"],
                acoustic_vs_optical=ac_op,
                asr_signal=asr,
                is_binary_nitride=binary,
                is_known_stable_binary=known,
            )

    # Imaginary / not dynamically stable from here.
    if ac_op == "optical_imaginary":
        cls = "optical_soft"
        reasons.append("imaginary_weight_on_optical_branches")
    elif known:
        cls = "likely_mesh_artefact"
        reasons.append("known_stable_binary_nitride_on_coarse_or_screening_mesh")
        if (ph.quality_tag or "") == "screening":
            reasons.append("screening_quality_tag")
    elif ac_op == "acoustic_only_imaginary":
        cls = "likely_mesh_artefact"
        reasons.append("imaginary_modes_confined_to_lowest_three_branches")
    elif not freqs:
        # Conservative: do not claim a mesh artefact without a spectrum.
        cls = "genuinely_soft"
        reasons.append("missing_frequency_list_conservative")
    else:
        cls = "genuinely_soft"
        reasons.append("imaginary_modes_not_classified_as_artefact")

    if asr:
        reasons.append(asr)

    return SoftModeRow(
        candidate_id=c.candidate_id,
        formula=formula,
        strain=c.in_plane_strain,
        min_frequency_cm1=ph.min_frequency_cm1,
        has_imaginary_modes=ph.has_imaginary_modes,
        dynamically_stable=ph.dynamically_stable,
        n_modes=n_modes,
        phonon_status=ph.status,
        eval_status=ev.status or "unknown",
        soft_mode_class=cls,
        reasons=reasons,
        acoustic_vs_optical=ac_op,
        asr_signal=asr,
        is_binary_nitride=binary,
        is_known_stable_binary=known,
    )


def _campaign_signal(rows: list[SoftModeRow], *, n_with_phonon: int) -> str:
    if n_with_phonon <= 0:
        return SIGNAL_NO_PHONON
    n_stable = sum(1 for r in rows if r["soft_mode_class"] == "stable")
    if n_stable > 0:
        return SIGNAL_HAS_STABLE
    known_soft = [
        r
        for r in rows
        if r["is_known_stable_binary"]
        and r["soft_mode_class"]
        in {"likely_mesh_artefact", "genuinely_soft", "optical_soft"}
    ]
    if known_soft:
        return SIGNAL_NONE_STABLE_BINARIES_SOFT
    return SIGNAL_NONE_STABLE


def _next_actions(store_dir: str | Path, signal: str) -> list[str]:
    store = str(store_dir)
    if signal == SIGNAL_NO_PHONON:
        return [
            "Store has no phonon results — nothing to characterise. "
            "Run a phonon-only campaign (do_epw: false) first.",
        ]
    if signal == SIGNAL_HAS_STABLE:
        return [
            f"siscforge shortlist {store} -o <epw.yaml> --mode stable_only",
            "Launch EPW only on dynamically_stable survivors. "
            "Coarse q remains a gate, not production proof.",
        ]
    return [
        "Do not shortlist imaginary-mode cells for EPW "
        "(stable_only correctly stays empty).",
        f"Read {REPORT_JSON} / {REPORT_MD} in the campaign store.",
        f"siscforge pilot {store} -o <pilot.yaml> --mode binaries --qpoints 3,3,3",
        f"siscforge pilot {store} -o <pilot.yaml> --mode least_soft -n 4 "
        "--qpoints 3,3,3",
        "The denser-q pilot is still a gate (do_epw stays false). "
        "The human decides whether to expand or abandon the family.",
    ]


def build_soft_mode_report(
    evaluations: list[CandidateEvaluation],
    *,
    source_store: str | None = None,
    campaign_name: str | None = None,
) -> dict[str, Any]:
    """Build the campaign-level report dict (never raises on missing freqs)."""
    rows = [classify_soft_mode(ev) for ev in evaluations]
    counts = Counter(r["soft_mode_class"] for r in rows)
    n_with_phonon = sum(1 for ev in evaluations if ev.phonon is not None)
    signal = _campaign_signal(rows, n_with_phonon=n_with_phonon)
    known_soft = sorted(
        {
            r["formula"]
            for r in rows
            if r["is_known_stable_binary"] and r["soft_mode_class"] != "stable"
        }
    )
    store = source_store or ""
    return {
        "version": REPORT_VERSION,
        "schema": REPORT_SCHEMA,
        "source_store": store,
        "campaign": campaign_name,
        "limitation": (
            "Heuristic characterisation only. Coarse q=2³ maps are a discovery "
            "gate, not production dynamical-stability proof. Classes such as "
            "likely_mesh_artefact do not certify that a cell is physically "
            "stable. Never launch EPW on imaginary-mode cells from this report."
        ),
        "n_evaluations": len(evaluations),
        "n_with_phonon": n_with_phonon,
        "n_stable": counts.get("stable", 0),
        "n_setup_failed": counts.get("setup_failed", 0),
        "n_likely_mesh_artefact": counts.get("likely_mesh_artefact", 0),
        "n_optical_soft": counts.get("optical_soft", 0),
        "n_genuinely_soft": counts.get("genuinely_soft", 0),
        "n_unknown": counts.get("unknown", 0),
        "known_stable_binaries_soft": known_soft,
        "campaign_signal": signal,
        "next_actions": _next_actions(store, signal),
        "candidates": rows,
    }


def render_soft_mode_markdown(report: dict[str, Any]) -> str:
    """Short operator-facing Markdown section."""
    skipped = report.get("skipped")
    if skipped:
        reason = report.get("skip_reason") or "unspecified"
        return (
            "# Soft-mode report\n\n"
            f"**Skipped:** {reason}\n\n"
            "This is not a stability conclusion.\n"
        )
    signal = report.get("campaign_signal") or "—"
    lines = [
        "# Soft-mode report",
        "",
        report.get("limitation", ""),
        "",
        f"- campaign: `{report.get('campaign') or '—'}`",
        f"- store: `{report.get('source_store') or '—'}`",
        f"- evaluations: {report.get('n_evaluations', 0)} "
        f"({report.get('n_with_phonon', 0)} with phonon)",
        f"- stable: **{report.get('n_stable', 0)}**",
        f"- likely_mesh_artefact: {report.get('n_likely_mesh_artefact', 0)}",
        f"- optical_soft: {report.get('n_optical_soft', 0)}",
        f"- genuinely_soft: {report.get('n_genuinely_soft', 0)}",
        f"- setup_failed: {report.get('n_setup_failed', 0)}",
        f"- unknown: {report.get('n_unknown', 0)}",
        f"- campaign signal: `{signal}`",
    ]
    known = report.get("known_stable_binaries_soft") or []
    if known:
        lines.append(f"- known-stable binaries also soft: {', '.join(known)}")
    lines.extend(["", "## Next actions", ""])
    for action in report.get("next_actions") or []:
        lines.append(f"- {action}")
    rows = list(report.get("candidates") or [])
    if rows:
        lines.extend(
            [
                "",
                "## Per-candidate",
                "",
                "| formula | strain | min ω (cm⁻¹) | class | acoustic/optical |",
                "|---|---:|---:|---|---|",
            ]
        )
        # Least-soft first (highest min ω) so the operator sees the least
        # imaginary cells at the top of an all-soft map.
        def _sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
            mf = row.get("min_frequency_cm1")
            if mf is None:
                return (1, 0.0, str(row.get("formula") or ""))
            return (0, -float(mf), str(row.get("formula") or ""))

        for row in sorted(rows, key=_sort_key):
            strain = row.get("strain")
            strain_s = f"{float(strain):+.3f}" if strain is not None else "—"
            mf = row.get("min_frequency_cm1")
            mf_s = f"{float(mf):.1f}" if mf is not None else "—"
            lines.append(
                f"| {row.get('formula') or '—'} | {strain_s} | {mf_s} | "
                f"`{row.get('soft_mode_class')}` | "
                f"{row.get('acoustic_vs_optical') or 'undetermined'} |"
            )
    lines.append("")
    return "\n".join(lines)


def skip_report(*, source_store: str | None, reason: str) -> dict[str, Any]:
    return {
        "version": REPORT_VERSION,
        "schema": REPORT_SCHEMA,
        "source_store": source_store or "",
        "skipped": True,
        "skip_reason": reason,
        "campaign_signal": SIGNAL_SKIPPED,
        "n_evaluations": 0,
        "n_with_phonon": 0,
        "n_stable": 0,
        "next_actions": [reason],
        "candidates": [],
    }


def write_soft_mode_report(
    evaluations: list[CandidateEvaluation],
    store_dir: str | Path,
    *,
    campaign_name: str | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Write JSON + Markdown into the campaign store. Always writes something.

    If there is nothing to characterise, writes an explicit skip record
    (clear reason) rather than failing silently.
    """
    store = EvaluationStore(store_dir)
    source = str(Path(store_dir).resolve())
    if not evaluations:
        report = skip_report(
            source_store=source,
            reason="empty store — no evaluations to characterise",
        )
    elif not any(ev.phonon is not None for ev in evaluations):
        report = skip_report(
            source_store=source,
            reason=(
                "no phonon results in store (EPW-only, mocked without phonon, "
                "or failed before DFPT) — skip characterisation"
            ),
        )
    else:
        report = build_soft_mode_report(
            evaluations,
            source_store=source,
            campaign_name=campaign_name,
        )
    json_path = store.save_json(REPORT_JSON, report)
    md_path = Path(store_dir) / REPORT_MD
    md_path.write_text(render_soft_mode_markdown(report), encoding="utf-8")
    return report, json_path, md_path


def ensure_soft_mode_report(
    store_dir: str | Path,
    *,
    campaign_name: str | None = None,
    evaluations: list[CandidateEvaluation] | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Return an existing report or write a fresh one."""
    store_path = Path(store_dir)
    json_path = store_path / REPORT_JSON
    md_path = store_path / REPORT_MD
    if json_path.is_file():
        import json

        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("schema") == REPORT_SCHEMA:
            if not md_path.is_file():
                md_path.write_text(render_soft_mode_markdown(raw), encoding="utf-8")
            return raw, json_path, md_path
    if evaluations is None:
        evaluations = EvaluationStore(store_dir).load_evaluations(ranked=False)
        if not evaluations:
            evaluations = EvaluationStore(store_dir).load_evaluations(ranked=True)
    return write_soft_mode_report(
        evaluations,
        store_dir,
        campaign_name=campaign_name,
    )


def empty_stable_only_message(
    store_dir: str | Path,
    *,
    n_total: int,
    n_with_ph: int,
    n_imag: int,
    soft_min_cm1: float = 0.0,
) -> str:
    """Actionable text when ``stable_only`` finds zero survivors."""
    store = str(store_dir)
    return (
        f"No dynamically stable evaluations for shortlist "
        f"(mode=stable_only). Store has {n_total} evaluations, "
        f"{n_with_ph} with phonon data, {n_imag} with imaginary/"
        f"unstable modes. Refusing to fall back to unstable top-k. "
        f"Next: inspect the campaign soft-mode report "
        f"({REPORT_JSON} / {REPORT_MD}) and emit a denser-q phonon "
        f"pilot — `siscforge pilot {store} -o <pilot.yaml> --mode binaries` "
        f"(or `--mode least_soft`). "
        f"Do not launch EPW on imaginary-mode cells. "
        f"A denser-q pilot is still a gate, not production "
        f"dynamical-stability proof. "
        f"Optional: try --mode stable_or_soft "
        f"(soft_min_cm1={soft_min_cm1:g}) only for numeric noise."
    )
