"""Soft-mode characterisation for phonon-map stores (Slice 29).

Coarse q=2³ maps are an intentional discovery gate, not production
dynamical-stability proof. When a map finishes with zero
``dynamically_stable`` survivors — including known-stable binaries such as
NbN / TiN / ZrN — the operator needs a campaign-level summary and a next
action, not a bare “none stable” dead-end.

This module is **heuristic and non-blocking**:

* Missing frequency lists → conservative class (never auto-promote to
  “stable” or to EPW).
* When a Gamma ``.dyn`` (or an in-memory Gamma force-constant matrix) is
  attached, crystal ASR replaces the raw Gamma frequencies **before**
  acoustic/optical classification. No dyn → behaviour is unchanged.
  Stored phonon records are not rewritten.
* Labels such as ``likely_mesh_artefact`` are *suspects*, not proof.
* The report must never be treated as a go-ahead to launch EPW on
  imaginary-mode cells.

``soft_mode_class`` is a first-class input to shortlist / acquisition
(#45). Critical campaign signals auto-emit a denser-q phonon-only pilot
(``do_epw`` stays false). Never treat this report as a go-ahead to
launch EPW on imaginary-mode cells.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal, TypedDict

from siscforge.calculators.qe.asr import (
    discover_dyn_files,
    frequencies_cm1,
    gamma_asr_frequencies,
    gamma_asr_from_dyn,
    parse_qe_dyn,
)
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
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
#
# How to extend: add a reduced formula only when a published RS phase is
# widely treated as dynamically stable (same bar as NbN/TiN/ZrN/HfN).
# Do not add ternaries, hexagonal/WC phases, or controversial cubics
# (e.g. δ-TaN). Operators can also set
# ``candidate.metadata["known_stable_binary"] = True`` for a one-off.
# VN: NaCl-type, literature-stable metal (Tc ~8–9 K).
KNOWN_STABLE_RS_NITRIDES = frozenset({"NbN", "TiN", "ZrN", "HfN", "VN"})

# Classes that must not go to EPW for a known-stable binary until a
# denser-q phonon (still do_epw=false) has confirmed stability.
SOFT_CLASSES_NEED_DENSER_Q = frozenset(
    {
        "likely_mesh_artefact",
        "optical_soft",
        "genuinely_soft",
        "setup_failed",
        "unknown",
    }
)
AUTO_PILOT_YAML = "denser_q_pilot.yaml"

# Same threshold as the DFPT parser: ignore tiny numeric acoustic noise.
_IMAG_THRESHOLD_CM1 = 5.0
# |Γ| below this is ordinary acoustic numerical noise, not the campaign story.
_GAMMA_MILD_CM1 = 50.0
# Finite-q is the campaign min when it sits this far below Γ.
_LOCUS_GAP_CM1 = 15.0

# Campaign-level signals (not physical verdicts).
SIGNAL_NONE_STABLE = "none_stable"
SIGNAL_NONE_STABLE_BINARIES_SOFT = "none_stable_known_binaries_also_soft"
SIGNAL_HAS_STABLE = "has_stable_survivors"
SIGNAL_NO_PHONON = "no_phonon_results"
SIGNAL_SKIPPED = "skipped"
AUTO_PILOT_SIGNALS = frozenset(
    {SIGNAL_NONE_STABLE, SIGNAL_NONE_STABLE_BINARIES_SOFT}
)


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
    softness_locus: str
    gamma_min_frequency_cm1: float | None
    finite_q_min_frequency_cm1: float | None
    n_q_imaginary: int | None
    gamma_raw_frequencies_cm1: list[float] | None
    gamma_asr_frequencies_cm1: list[float] | None
    asr_applied: str | None


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


def is_known_stable_binary(formula: str, metadata: dict[str, Any] | None = None) -> bool:
    if metadata and metadata.get("known_stable_binary") is True:
        return True
    return reduced_formula(formula) in KNOWN_STABLE_RS_NITRIDES


def denser_q_confirmed(ev: CandidateEvaluation) -> bool:
    """True when metadata records a denser-q phonon confirmation.

    Used so a known-stable binary that was soft on q=2³ can enter an
    EPW shortlist *after* a do_epw=false denser-q pilot came back stable.
    """
    meta = ev.candidate.metadata or {}
    if meta.get("denser_q_confirmed") is True:
        return True
    qpts = meta.get("pilot_target_qpoints") or meta.get("qpoints")
    if isinstance(qpts, (list, tuple)) and len(qpts) >= 3:
        try:
            if min(int(x) for x in qpts[:3]) >= 3:
                ph = ev.phonon
                if (
                    ph is not None
                    and ph.dynamically_stable
                    and not ph.has_imaginary_modes
                ):
                    return True
        except (TypeError, ValueError):
            return False
    return False


def needs_denser_q_before_epw(ev: CandidateEvaluation) -> bool:
    """Known-stable binary that looks soft on a coarse mesh, unconfirmed."""
    row = classify_soft_mode(ev)
    if not row["is_known_stable_binary"]:
        return False
    if row["soft_mode_class"] == "stable":
        return False
    if denser_q_confirmed(ev):
        return False
    return row["soft_mode_class"] in SOFT_CLASSES_NEED_DENSER_Q


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


def n_atoms(ev: CandidateEvaluation) -> int | None:
    """Atom count from candidate metadata or CIF, when available."""
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


# Back-compat alias.
_n_atoms = n_atoms


def _acoustic_vs_optical(
    freqs: list[float],
    *,
    n_atoms: int | None,
    n_modes: int | None,
    imag_threshold_cm1: float = _IMAG_THRESHOLD_CM1,
    asr_corrected: bool = False,
) -> tuple[str, str | None]:
    """Return (label, asr_signal) when a single-q / Gamma list is detectable.

    A flat mesh dump (n_modes ≫ 3 N_at) is **undetermined** — first-three
    slicing would be wrong. Missing data is also undetermined.

    After crystal ASR the three acoustic eigenvalues sit at ~0 and are not
    necessarily the lowest three (an optical instability can lie below
    them). ``asr_corrected=True`` treats the three modes nearest zero as
    that acoustic triplet.
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
    if asr_corrected:
        order = sorted(range(len(freqs)), key=lambda i: (abs(float(freqs[i])), i))
        acoustic_idx = set(order[:3])
        acoustic = [float(freqs[i]) for i in order[:3]]
        optical = [float(freqs[i]) for i in range(len(freqs)) if i not in acoustic_idx]
    else:
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


def _raw_qpoints(ph: PhononResult) -> list[dict[str, Any]]:
    raw = ph.raw or {}
    qps = raw.get("qpoints")
    if not isinstance(qps, list):
        return []
    out: list[dict[str, Any]] = []
    for qp in qps:
        if not isinstance(qp, dict):
            continue
        freqs = qp.get("frequencies_cm1")
        if not isinstance(freqs, list) or not freqs:
            continue
        nums: list[float] = []
        ok = True
        for item in freqs:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                nums.append(float(item))
            else:
                ok = False
                break
        if not ok or not nums:
            continue
        qvec = qp.get("q")
        is_gamma = qp.get("is_gamma")
        if is_gamma is None and isinstance(qvec, (list, tuple)) and len(qvec) == 3:
            try:
                is_gamma = all(abs(float(x)) < 1e-6 for x in qvec)
            except (TypeError, ValueError):
                is_gamma = False
        out.append(
            {
                "q": list(qvec) if isinstance(qvec, (list, tuple)) else None,
                "is_gamma": bool(is_gamma),
                "frequencies_cm1": nums,
                "min_frequency_cm1": min(nums),
            }
        )
    return out


def _chunked_q_spectra(
    freqs: list[float],
    *,
    n_atoms: int | None,
) -> list[dict[str, Any]]:
    """Treat a flat ldisp dump as successive 3 N_at q-blocks.

    QE prints Γ first for ``ldisp=.true.``. The first block is tagged
    ``is_gamma`` as an assumption, not a parsed q-vector.
    """
    if not freqs or n_atoms is None or int(n_atoms) <= 0:
        return []
    n = 3 * int(n_atoms)
    if n < 3 or len(freqs) < 2 * n or len(freqs) % n != 0:
        return []
    chunks: list[dict[str, Any]] = []
    for i in range(0, len(freqs), n):
        block = freqs[i : i + n]
        chunks.append(
            {
                "q": None,
                "is_gamma": i == 0,
                "frequencies_cm1": list(block),
                "min_frequency_cm1": min(block),
            }
        )
    return chunks


def _spectra_for_locus(
    ph: PhononResult,
    freqs: list[float],
    *,
    n_atoms: int | None,
) -> list[dict[str, Any]]:
    parsed = _raw_qpoints(ph)
    if len(parsed) >= 2:
        return parsed
    return _chunked_q_spectra(freqs, n_atoms=n_atoms)


def _classify_acoustic_over_q(
    qpoints: list[dict[str, Any]],
    *,
    n_atoms: int | None,
    imag_threshold_cm1: float,
) -> tuple[str, str | None]:
    """Resolve acoustic/optical when each q is a 3 N_at list."""
    if not qpoints:
        return "undetermined", None
    any_opt = False
    any_ac = False
    any_ok = False
    asr: str | None = None
    for qp in qpoints:
        freqs = qp.get("frequencies_cm1") or []
        label, this_asr = _acoustic_vs_optical(
            freqs,
            n_atoms=n_atoms,
            n_modes=len(freqs) if freqs else None,
            imag_threshold_cm1=imag_threshold_cm1,
            asr_corrected=bool(qp.get("asr_corrected")),
        )
        if label == "undetermined":
            continue
        any_ok = True
        if qp.get("is_gamma") and this_asr:
            asr = this_asr
        if label == "optical_imaginary":
            any_opt = True
        elif label == "acoustic_only_imaginary":
            any_ac = True
    if not any_ok:
        return "undetermined", asr
    if any_opt:
        return "optical_imaginary", asr
    if any_ac:
        return "acoustic_only_imaginary", asr
    return "none_below_threshold", asr


def _softness_locus(
    qpoints: list[dict[str, Any]],
    *,
    imag_threshold_cm1: float = _IMAG_THRESHOLD_CM1,
) -> tuple[str, float | None, float | None, int | None]:
    """Return (locus, gamma_min, finite_q_min, n_q_imaginary)."""
    if len(qpoints) < 2:
        return "undetermined", None, None, None
    thr = -abs(float(imag_threshold_cm1))
    gamma_mins = [
        float(qp["min_frequency_cm1"])
        for qp in qpoints
        if qp.get("is_gamma") and qp.get("min_frequency_cm1") is not None
    ]
    finite_mins = [
        float(qp["min_frequency_cm1"])
        for qp in qpoints
        if not qp.get("is_gamma") and qp.get("min_frequency_cm1") is not None
    ]
    gamma_min = min(gamma_mins) if gamma_mins else None
    finite_min = min(finite_mins) if finite_mins else None
    n_imag_q = sum(
        1
        for qp in qpoints
        if qp.get("min_frequency_cm1") is not None
        and float(qp["min_frequency_cm1"]) < thr
    )
    gamma_imag = gamma_min is not None and gamma_min < thr
    finite_imag = finite_min is not None and finite_min < thr
    if not gamma_imag and not finite_imag:
        return "none", gamma_min, finite_min, n_imag_q
    if finite_imag and finite_min is not None:
        gamma_mild = gamma_min is None or gamma_min > -_GAMMA_MILD_CM1
        finite_softer = gamma_min is None or finite_min < gamma_min - _LOCUS_GAP_CM1
        if (not gamma_imag) or gamma_mild or finite_softer:
            return "finite_q", gamma_min, finite_min, n_imag_q
        return "both", gamma_min, finite_min, n_imag_q
    if gamma_imag:
        return "gamma", gamma_min, finite_min, n_imag_q
    return "undetermined", gamma_min, finite_min, n_imag_q


def _blank_locus() -> dict[str, Any]:
    return {
        "softness_locus": "undetermined",
        "gamma_min_frequency_cm1": None,
        "finite_q_min_frequency_cm1": None,
        "n_q_imaginary": None,
    }


def _asr_row_fields(
    raw: list[float] | None = None,
    corrected: list[float] | None = None,
    applied: str | None = None,
) -> dict[str, Any]:
    """Optional report fields. ``applied`` is ``"crystal"`` or ``None``."""
    return {
        "gamma_raw_frequencies_cm1": raw,
        "gamma_asr_frequencies_cm1": corrected,
        "asr_applied": applied,
    }


class _GammaASRPack:
    """Crystal-ASR Gamma spectrum ready to splice into a classification."""

    __slots__ = ("asr_cm1", "nat", "raw_cm1")

    def __init__(self, raw_cm1: list[float], asr_cm1: list[float], nat: int) -> None:
        self.raw_cm1 = raw_cm1
        self.asr_cm1 = asr_cm1
        self.nat = nat


def _dyn_search_roots(ph: PhononResult, ev: CandidateEvaluation) -> list[Path]:
    """Directories or dyn paths that might hold this candidate's Gamma matrix."""
    roots: list[Path] = []
    raw = ph.raw or {}
    for key in ("ph_dir", "qe_work_dir", "work_dir"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            roots.append(Path(val.strip()))
    meta = ev.candidate.metadata or {}
    for key in ("ph_dir", "qe_work_dir", "work_dir", "gamma_dyn_path"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            roots.append(Path(val.strip()))
    prov = getattr(ev, "provenance", None)
    params = getattr(prov, "parameters", None) or {}
    work = params.get("work_dir") if isinstance(params, dict) else None
    if isinstance(work, str) and work.strip():
        roots.append(Path(work.strip()))
    notes = ev.notes or ""
    for match in re.finditer(r"work_dir=([^\s;]+)", notes):
        roots.append(Path(match.group(1)))
    return roots


def _lookup_gamma_asr(ph: PhononResult, ev: CandidateEvaluation) -> _GammaASRPack | None:
    """Load a Gamma dyn / matrix when one is attached. Never raises."""
    raw = ph.raw or {}
    explicit = raw.get("gamma_dyn_path") or raw.get("dyn_path")
    if isinstance(explicit, str) and explicit.strip():
        try:
            path = Path(explicit.strip())
            if path.is_file():
                raw_f, asr_f = gamma_asr_from_dyn(path)
                if len(raw_f) >= 3 and len(raw_f) % 3 == 0:
                    return _GammaASRPack(raw_f, asr_f, len(raw_f) // 3)
        except (OSError, ValueError):
            return None
        return None

    matrix = raw.get("gamma_force_constants")
    masses = raw.get("gamma_masses_ry")
    if matrix is not None and masses is not None:
        try:
            raw_f, asr_f = gamma_asr_frequencies(matrix, masses)
            if len(raw_f) >= 3 and len(raw_f) % 3 == 0:
                return _GammaASRPack(raw_f, asr_f, len(raw_f) // 3)
        except (TypeError, ValueError):
            return None
        return None

    seen: set[Path] = set()
    for root in _dyn_search_roots(ph, ev):
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            try:
                candidates = discover_dyn_files(root)
            except OSError:
                continue
        else:
            continue
        for candidate in candidates:
            try:
                parsed = parse_qe_dyn(candidate)
            except (OSError, ValueError):
                continue
            if not parsed.is_gamma:
                continue
            try:
                raw_f, asr_f = gamma_asr_frequencies(
                    parsed.force_constants, parsed.masses_ry
                )
            except (TypeError, ValueError):
                continue
            if len(raw_f) >= 3 and len(raw_f) % 3 == 0:
                return _GammaASRPack(raw_f, asr_f, parsed.nat)
    return None


def _inject_gamma_asr(
    freqs: list[float],
    qpoints: list[dict[str, Any]],
    pack: _GammaASRPack,
) -> tuple[list[float], list[dict[str, Any]], bool, bool]:
    """Replace Gamma blocks with crystal-ASR frequencies.

    Returns ``(freqs, qpoints, injected, flat_is_asr)``. Finite-q blocks
    are left unchanged. A flat list is replaced only when it is exactly
    one q-point (3 N_at), which is the single-q Gamma case.
    """
    asr = [float(value) for value in pack.asr_cm1]
    n = 3 * int(pack.nat)
    if len(asr) != n or n < 3:
        return list(freqs), [dict(qp) for qp in qpoints], False, False
    injected = False
    new_q: list[dict[str, Any]] = []
    for qp in qpoints:
        block = list(qp.get("frequencies_cm1") or [])
        if qp.get("is_gamma") and len(block) == n:
            new_q.append(
                {
                    **qp,
                    "frequencies_cm1": list(asr),
                    "min_frequency_cm1": float(min(asr)),
                    "asr_corrected": True,
                }
            )
            injected = True
        else:
            new_q.append(dict(qp))
    new_freqs = [float(value) for value in freqs]
    flat_is_asr = False
    if len(new_freqs) == n:
        new_freqs = list(asr)
        flat_is_asr = True
        injected = True
    return new_freqs, new_q, injected, flat_is_asr


def _spectrum_has_imaginary(
    freqs: list[float],
    qpoints: list[dict[str, Any]],
    *,
    imag_threshold_cm1: float,
) -> bool:
    thr = -abs(float(imag_threshold_cm1))
    if qpoints:
        for qp in qpoints:
            for value in qp.get("frequencies_cm1") or []:
                if float(value) < thr:
                    return True
        return False
    return any(float(value) < thr for value in freqs)


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
    known = is_known_stable_binary(formula, c.metadata)
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
            **_blank_locus(),
            **_asr_row_fields(),
        )

    status = (ph.status or "unknown").lower()
    freqs = _frequency_list(ph)
    nat = n_atoms(ev)
    n_modes = ph.n_modes if ph.n_modes is not None else (len(freqs) or None)
    qpoints = _spectra_for_locus(ph, freqs, n_atoms=nat)
    gamma_raw: list[float] | None = None
    gamma_asr: list[float] | None = None
    asr_name: str | None = None
    asr_injected = False
    flat_asr = False
    pack: _GammaASRPack | None = None
    if status in {"ok", "mock"}:
        pack = _lookup_gamma_asr(ph, ev)
        if pack is not None:
            freqs, qpoints, asr_injected, flat_asr = _inject_gamma_asr(
                freqs, qpoints, pack
            )
            if asr_injected:
                gamma_raw = list(pack.raw_cm1)
                gamma_asr = list(pack.asr_cm1)
                asr_name = "crystal"
    asr_fields = _asr_row_fields(gamma_raw, gamma_asr, asr_name)
    nat_for_split = nat
    if nat_for_split is None and asr_injected and pack is not None:
        nat_for_split = pack.nat
    if qpoints:
        ac_op, asr = _classify_acoustic_over_q(
            qpoints,
            n_atoms=nat_for_split,
            imag_threshold_cm1=imag_threshold_cm1,
        )
    else:
        ac_op, asr = _acoustic_vs_optical(
            freqs,
            n_atoms=nat_for_split,
            n_modes=len(freqs) if flat_asr else n_modes,
            imag_threshold_cm1=imag_threshold_cm1,
            asr_corrected=flat_asr,
        )
    locus, gamma_min, finite_min, n_q_imag = _softness_locus(
        qpoints, imag_threshold_cm1=imag_threshold_cm1
    )
    locus_fields = {
        "softness_locus": locus,
        "gamma_min_frequency_cm1": gamma_min,
        "finite_q_min_frequency_cm1": finite_min,
        "n_q_imaginary": n_q_imag,
    }

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
            **locus_fields,
            **asr_fields,
        )

    corrected_imag = (
        _spectrum_has_imaginary(
            freqs, qpoints, imag_threshold_cm1=imag_threshold_cm1
        )
        if asr_injected
        else None
    )
    raw_clean = bool(ph.dynamically_stable) and not ph.has_imaginary_modes and (
        ph.min_frequency_cm1 is None
        or float(ph.min_frequency_cm1) >= -abs(imag_threshold_cm1)
    )
    # Crystal ASR can remove a spurious acoustic triplet. Trust the corrected
    # spectrum when a Gamma matrix was actually spliced in; otherwise keep
    # the raw phonon flags exactly as before.
    asr_clean = asr_injected and corrected_imag is False
    asr_still_imag = asr_injected and corrected_imag is True
    if asr_clean or (raw_clean and not asr_still_imag):
        stable_reasons = ["no_imaginary_modes"]
        if asr_injected:
            stable_reasons.append("crystal_asr_applied")
        return SoftModeRow(
            candidate_id=c.candidate_id,
            formula=formula,
            strain=c.in_plane_strain,
            min_frequency_cm1=ph.min_frequency_cm1,
            has_imaginary_modes=False if asr_clean else ph.has_imaginary_modes,
            dynamically_stable=True if asr_clean else ph.dynamically_stable,
            n_modes=n_modes,
            phonon_status=ph.status,
            eval_status=ev.status or "unknown",
            soft_mode_class="stable",
            reasons=stable_reasons,
            acoustic_vs_optical=ac_op,
            asr_signal=asr,
            is_binary_nitride=binary,
            is_known_stable_binary=known,
            **locus_fields,
            **asr_fields,
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
    if asr_injected:
        reasons.append("crystal_asr_applied")
    if locus == "finite_q":
        reasons.append("softest_q_is_finite_q")
        if gamma_min is not None and gamma_min > -_GAMMA_MILD_CM1:
            reasons.append("gamma_only_mildly_imaginary")

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
        **locus_fields,
        **asr_fields,
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


def _next_actions(
    store_dir: str | Path,
    signal: str,
    *,
    finite_q_softest: bool = False,
) -> list[str]:
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
    actions = [
        "Do not shortlist imaginary-mode cells for EPW "
        "(stable_only correctly stays empty).",
        f"Read {REPORT_JSON} / {REPORT_MD} in the campaign store.",
    ]
    if finite_q_softest:
        from siscforge.pilot import NITRIDE_PHONON_K_POLICY

        actions.append(
            "Softest mode is at finite q (Γ only mildly imaginary). "
            "Densify SCF k / ecut on the same q-grid or audit the UPF; "
            "another coarse-q pilot will not discriminate this pattern. "
            f"Nitride phonon recovery k is {NITRIDE_PHONON_K_POLICY}."
        )
    actions.extend(
        [
            f"siscforge pilot {store} -o <pilot.yaml> --mode binaries --qpoints 3,3,3",
            f"siscforge pilot {store} -o <pilot.yaml> --mode least_soft -n 4 "
            "--qpoints 3,3,3",
            "The denser-q pilot is still a gate (do_epw stays false). "
            "The human decides whether to expand or abandon the family.",
        ]
    )
    return actions


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
    finite_q_softest = any(r.get("softness_locus") == "finite_q" for r in rows)
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
        "finite_q_softest": finite_q_softest,
        "next_actions": _next_actions(
            store, signal, finite_q_softest=finite_q_softest
        ),
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
    if report.get("auto_pilot_yaml"):
        lines.append(
            f"- auto denser-q pilot: `{report['auto_pilot_yaml']}` "
            f"(mode={report.get('auto_pilot_mode')}, do_epw=false)"
        )
    if report.get("finite_q_softest"):
        lines.append(
            "- softness locus: **softest q is finite-q; Γ only mildly "
            "imaginary** (acoustic numerical noise). "
            "`likely_mesh_artefact` remains a suspect, not proof."
        )
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
                "| formula | strain | min ω (cm⁻¹) | Γ ω | finite-q min | locus | class | acoustic/optical |",
                "|---|---:|---:|---:|---:|---|---|---|",
            ]
        )
        # Least-soft first (highest min ω) so the operator sees the least
        # imaginary cells at the top of an all-soft map.
        def _sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
            mf = row.get("min_frequency_cm1")
            if mf is None:
                return (1, 0.0, str(row.get("formula") or ""))
            return (0, -float(mf), str(row.get("formula") or ""))

        def _fmt_omega(val: Any) -> str:
            if val is None:
                return "—"
            try:
                return f"{float(val):.1f}"
            except (TypeError, ValueError):
                return "—"

        for row in sorted(rows, key=_sort_key):
            strain = row.get("strain")
            strain_s = f"{float(strain):+.3f}" if strain is not None else "—"
            lines.append(
                f"| {row.get('formula') or '—'} | {strain_s} | "
                f"{_fmt_omega(row.get('min_frequency_cm1'))} | "
                f"{_fmt_omega(row.get('gamma_min_frequency_cm1'))} | "
                f"{_fmt_omega(row.get('finite_q_min_frequency_cm1'))} | "
                f"`{row.get('softness_locus') or 'undetermined'}` | "
                f"`{row.get('soft_mode_class')}` | "
                f"{row.get('acoustic_vs_optical') or 'undetermined'} |"
            )
    asr_rows = [row for row in rows if row.get("asr_applied")]
    if asr_rows:
        lines.extend(["", "## Gamma frequencies: raw vs crystal ASR", ""])

        def _fmt2(val: float) -> str:
            return f"{float(val):.2f}"

        for row in asr_rows:
            raw_f = list(row.get("gamma_raw_frequencies_cm1") or [])
            asr_f = list(row.get("gamma_asr_frequencies_cm1") or [])
            label = row.get("formula") or "—"
            lines.append(
                f"### {label} (`{row.get('asr_applied')}`, "
                f"class `{row.get('soft_mode_class')}`)"
            )
            lines.append("")
            lines.append("| mode | raw (cm⁻¹) | ASR (cm⁻¹) |")
            lines.append("|---:|---:|---:|")
            width = max(len(raw_f), len(asr_f))
            for index in range(width):
                raw_cell = _fmt2(raw_f[index]) if index < len(raw_f) else "—"
                asr_cell = _fmt2(asr_f[index]) if index < len(asr_f) else "—"
                lines.append(f"| {index + 1} | {raw_cell} | {asr_cell} |")
            lines.append("")
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
    if not campaign_name:
        meta_path = Path(store_dir) / "store_meta.json"
        if meta_path.is_file():
            try:
                import json

                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                name = meta.get("campaign") if isinstance(meta, dict) else None
                if isinstance(name, str) and name.strip():
                    campaign_name = name.strip()
            except (OSError, ValueError):
                pass
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
    _maybe_auto_emit_pilot(report, evaluations, store_dir)
    if report.get("auto_pilot_yaml"):
        # Re-save JSON so the auto-pilot path is durable.
        json_path = store.save_json(REPORT_JSON, report)
        md_path.write_text(render_soft_mode_markdown(report), encoding="utf-8")
    return report, json_path, md_path


def _maybe_auto_emit_pilot(
    report: dict[str, Any],
    evaluations: list[CandidateEvaluation],
    store_dir: str | Path,
) -> None:
    """Write a denser-q phonon-only YAML on critical none-stable signals.

    Never enables EPW. Failure is recorded on the report and does not
    raise — the report itself remains the operator-facing artefact.
    """
    signal = report.get("campaign_signal")
    if signal not in AUTO_PILOT_SIGNALS:
        return
    if report.get("skipped"):
        return
    store_path = Path(store_dir)
    yaml_path = store_path / AUTO_PILOT_YAML
    try:
        from siscforge.pilot import (
            build_pilot_campaign,
            load_source_campaign,
            write_pilot_yaml,
        )

        source_campaign = load_source_campaign(store_path)
        mode = "binaries"
        try:
            cfg, _ = build_pilot_campaign(
                evaluations,
                name=f"{(report.get('campaign') or 'phonon_map')}_pilot_q3",
                source_store=str(store_path.resolve()),
                source_campaign=source_campaign,
                max_jobs=4,
                mode=mode,
                qpoints=[3, 3, 3],
                output_dir=str(store_path.resolve()) + "_pilot_q3",
            )
        except ValueError:
            mode = "least_soft"
            cfg, _ = build_pilot_campaign(
                evaluations,
                name=f"{(report.get('campaign') or 'phonon_map')}_pilot_q3",
                source_store=str(store_path.resolve()),
                source_campaign=source_campaign,
                max_jobs=4,
                mode=mode,
                qpoints=[3, 3, 3],
                output_dir=str(store_path.resolve()) + "_pilot_q3",
            )
        write_pilot_yaml(cfg, yaml_path)
        extras = cfg.extras.get("pilot") if cfg.extras else {}
        if extras is None:
            extras = {}
        report["auto_pilot_yaml"] = str(yaml_path)
        report["auto_pilot_mode"] = mode
        report["auto_pilot_do_epw"] = False
        report["auto_pilot_formulas"] = list(extras.get("formulas") or [])
        actions = list(report.get("next_actions") or [])
        actions.insert(
            0,
            f"Auto-emitted denser-q phonon-only pilot: {yaml_path} "
            f"(mode={mode}, do_epw=false). Review then "
            f"`siscforge run --calculator qe {yaml_path}`.",
        )
        report["next_actions"] = actions
    except Exception as exc:  # noqa: BLE001 — report write must not fail
        report["auto_pilot_error"] = str(exc)


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


def evaluation_from_ph_directory(
    ph_dir: str | Path,
    *,
    formula: str = "unknown",
) -> CandidateEvaluation:
    """Build an evaluation from a QE directory of ``*.dyn1`` … ``*.dynN``.

    Phonon frequencies on the result are the **raw** dynamical-matrix
    eigenvalues (what ``ph.x`` prints). :func:`classify_soft_mode` applies
    crystal ASR to the Gamma file before labelling the cell. Finite-q
    blocks are not sum-rule corrected.
    """
    root = Path(ph_dir)
    files = discover_dyn_files(root)
    if not files:
        raise FileNotFoundError(f"no QE *.dyn1..dynN files in {root}")
    qpoints: list[dict[str, Any]] = []
    flat: list[float] = []
    gamma_path: Path | None = None
    nat: int | None = None
    for dyn in files:
        parsed = parse_qe_dyn(dyn)
        spectrum = frequencies_cm1(parsed.force_constants, parsed.masses_ry)
        if nat is None:
            nat = parsed.nat
        if parsed.is_gamma and gamma_path is None:
            gamma_path = dyn
        qpoints.append(
            {
                "q": [float(component) for component in parsed.q],
                "is_gamma": bool(parsed.is_gamma),
                "frequencies_cm1": spectrum,
                "min_frequency_cm1": min(spectrum) if spectrum else None,
            }
        )
        flat.extend(spectrum)
    if gamma_path is None:
        raise ValueError(f"no Gamma dynamical matrix under {root}")
    from siscforge.calculators.qe.parser import summarize_frequencies

    summary = summarize_frequencies(flat)
    work = root if root.is_dir() else root.parent
    phonon = PhononResult(
        min_frequency_cm1=summary["min_frequency_cm1"],
        max_frequency_cm1=summary["max_frequency_cm1"],
        n_modes=summary["n_modes"] or None,
        has_imaginary_modes=summary["has_imaginary_modes"],
        dynamically_stable=summary["dynamically_stable"],
        status="ok",
        quality_tag="screening",
        raw={
            "frequencies_cm1": flat,
            "qpoints": qpoints,
            "n_qpoints": len(qpoints),
            "gamma_dyn_path": str(gamma_path),
            "ph_dir": str(work),
            # Parser convention: the stored list is still the raw ph.x spectrum.
            "asr_applied": False,
        },
    )
    candidate = StructureCandidate(
        formula=(formula or "unknown").strip() or "unknown",
        material_family="other",
        metadata={"n_atoms": nat, "ph_dir": str(work)},
        source="qe_dyn",
    )
    return CandidateEvaluation(
        candidate=candidate,
        phonon=phonon,
        status="ok",
        calculator_name="qe",
        notes=f"work_dir={work}",
    )


def classify_ph_directory(
    ph_dir: str | Path,
    *,
    formula: str = "unknown",
    imag_threshold_cm1: float = _IMAG_THRESHOLD_CM1,
) -> SoftModeRow:
    """Classify ``*.dyn1..dynN`` in *ph_dir* with crystal ASR at Gamma.

    Suitable for a ``qe_work`` phonon directory (for example ``/tmp/v3si_dyn``).
    """
    evaluation = evaluation_from_ph_directory(ph_dir, formula=formula)
    return classify_soft_mode(evaluation, imag_threshold_cm1=imag_threshold_cm1)


def evaluation_from_gamma_matrix(
    force_constants: Any,
    masses_ry: Any,
    *,
    formula: str = "synthetic",
) -> CandidateEvaluation:
    """Evaluation whose Gamma matrix is classified by :func:`classify_soft_mode`.

    The phonon frequency list is the raw diagonalization. Crystal ASR runs
    inside the classifier, on the same path as a ``.dyn`` file.
    """
    raw_freqs, _corrected = gamma_asr_frequencies(force_constants, masses_ry)
    from siscforge.calculators.qe.parser import summarize_frequencies

    summary = summarize_frequencies(raw_freqs)
    nat = len(raw_freqs) // 3
    import numpy as np

    matrix = np.asarray(force_constants)
    if np.iscomplexobj(matrix) and float(np.max(np.abs(matrix.imag))) > 1e-8:
        stored_matrix = matrix.tolist()
    else:
        stored_matrix = np.real(matrix).tolist()
    stored_masses = [float(value) for value in np.asarray(masses_ry, dtype=float).reshape(-1)]
    phonon = PhononResult(
        min_frequency_cm1=summary["min_frequency_cm1"],
        max_frequency_cm1=summary["max_frequency_cm1"],
        n_modes=summary["n_modes"] or None,
        has_imaginary_modes=summary["has_imaginary_modes"],
        dynamically_stable=summary["dynamically_stable"],
        status="ok",
        quality_tag="screening",
        raw={
            "frequencies_cm1": list(raw_freqs),
            "gamma_force_constants": stored_matrix,
            "gamma_masses_ry": stored_masses,
            "qpoints": [
                {
                    "q": [0.0, 0.0, 0.0],
                    "is_gamma": True,
                    "frequencies_cm1": list(raw_freqs),
                    "min_frequency_cm1": summary["min_frequency_cm1"],
                }
            ],
        },
    )
    candidate = StructureCandidate(
        formula=(formula or "synthetic").strip() or "synthetic",
        material_family="other",
        metadata={"n_atoms": nat},
        source="gamma_matrix",
    )
    return CandidateEvaluation(
        candidate=candidate,
        phonon=phonon,
        status="ok",
        calculator_name="qe",
    )


def classify_gamma_force_constants(
    force_constants: Any,
    masses_ry: Any,
    *,
    formula: str = "synthetic",
    imag_threshold_cm1: float = _IMAG_THRESHOLD_CM1,
) -> SoftModeRow:
    """Classify one Gamma force-constant matrix with crystal ASR."""
    evaluation = evaluation_from_gamma_matrix(
        force_constants, masses_ry, formula=formula
    )
    return classify_soft_mode(evaluation, imag_threshold_cm1=imag_threshold_cm1)
