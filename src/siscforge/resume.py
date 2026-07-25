"""Resume / checkpoint helpers for multi-candidate campaign runs.

Workstation EPW shortlists are long and often interrupted. Re-running the same
``output_dir`` should **skip finished successes**, **continue past failures**,
and flush partial results after each candidate.

Matching policy
---------------
1. **candidate_id** — exact hit when the same store / regenerated candidates
   reuse IDs (or the evaluation was written in this process).
2. **resume fingerprint** — ``material_family | formula | substrate | strain``
   when IDs are re-enumerated (default ``structure_to_candidate`` uses UUIDs).

Success criteria (``is_successful_evaluation``)
-----------------------------------------------
An evaluation is treated as finished-and-skippable when:

- ``status`` ∈ {``ok``, ``mock``}, and
- at least one of:
  - ``electron_phonon`` with status ∈ {ok, mock} and λ or Tc present, or
  - ``phonon`` with status ∈ {ok, mock} (phonon-only campaigns), or
  - ``scf`` with status ∈ {ok, mock} (SCF-only / no phonon path).

``failed``, ``pending``, and ``surrogate_only`` are **not** successes for the
expensive path (surrogate-only may be refreshed each run for deferred AL rows).
"""

from __future__ import annotations

from typing import Iterable

from siscforge.models.candidate import CandidateEvaluation, StructureCandidate

# Statuses that mean the expensive (or mock stand-in) path finished cleanly.
SUCCESS_STATUSES = frozenset({"ok", "mock"})
RESULT_OK_STATUSES = frozenset({"ok", "mock"})


def resume_fingerprint(candidate: StructureCandidate) -> str:
    """Stable structural identity for resume matching across re-enumeration.

    Uses formula, family, substrate, and in-plane strain (6 decimal places).
    Does **not** include lattice floats or CIF so small numeric noise does not
    miss a hit; intentional composition/strain changes miss as expected.
    """
    strain = candidate.in_plane_strain
    if strain is None and "requested_strain" in candidate.metadata:
        try:
            strain = float(candidate.metadata["requested_strain"])
        except (TypeError, ValueError):
            strain = None
    if strain is None:
        strain_s = "none"
    else:
        strain_s = f"{float(strain):+.6f}"
    sub = (candidate.substrate or "").strip() or "none"
    family = candidate.material_family or "other"
    formula = (candidate.formula or "").strip()
    return f"{family}|{formula}|{sub}|{strain_s}"


def is_successful_evaluation(
    evaluation: CandidateEvaluation,
    *,
    require_real: bool = False,
) -> bool:
    """Return True if *evaluation* should be skipped on resume (not re-run).

    Parameters
    ----------
    require_real:
        When True (``qe`` / ``qe-epw`` runs), ``status=mock`` is **not** treated
        as finished — so a dry-run store does not block real EPW on the same
        fingerprint. Real ``status=ok`` successes still skip.
    """
    if evaluation.status not in SUCCESS_STATUSES:
        return False
    if require_real and evaluation.status == "mock":
        return False
    if require_real and (evaluation.calculator_name or "").lower() in {
        "mock",
        "surrogate",
    }:
        return False

    eph = evaluation.electron_phonon
    if eph is not None and eph.status in RESULT_OK_STATUSES:
        if require_real and eph.status == "mock":
            pass  # fall through to phonon/scf or fail
        else:
            if eph.lambda_total is not None:
                return True
            try:
                if eph.best_tc_K() is not None:
                    return True
            except Exception:  # noqa: BLE001
                pass

    ph = evaluation.phonon
    if ph is not None and ph.status in RESULT_OK_STATUSES:
        if not (require_real and ph.status == "mock"):
            return True

    scf = evaluation.scf
    if scf is not None and scf.status in RESULT_OK_STATUSES:
        if not (require_real and scf.status == "mock"):
            return True

    return False


def index_evaluations(
    evaluations: Iterable[CandidateEvaluation],
    *,
    require_real: bool = False,
) -> tuple[dict[str, CandidateEvaluation], dict[str, CandidateEvaluation]]:
    """Build lookup maps: candidate_id → eval, fingerprint → eval.

    Later entries win for a given key (matches store append semantics).
    Only **successful** evaluations are indexed for resume skip.
    """
    by_id: dict[str, CandidateEvaluation] = {}
    by_fp: dict[str, CandidateEvaluation] = {}
    for ev in evaluations:
        if not is_successful_evaluation(ev, require_real=require_real):
            continue
        by_id[ev.candidate.candidate_id] = ev
        by_fp[resume_fingerprint(ev.candidate)] = ev
    return by_id, by_fp


def find_resumable_evaluation(
    candidate: StructureCandidate,
    *,
    by_id: dict[str, CandidateEvaluation],
    by_fp: dict[str, CandidateEvaluation],
    force_rerun: bool = False,
) -> CandidateEvaluation | None:
    """Locate a prior successful evaluation for *candidate*, if any."""
    if force_rerun:
        return None
    hit = by_id.get(candidate.candidate_id)
    if hit is not None:
        return hit
    return by_fp.get(resume_fingerprint(candidate))


def evaluation_matches_candidate(
    evaluation: CandidateEvaluation,
    candidate: StructureCandidate,
) -> bool:
    """True if *evaluation* is the same logical structure as *candidate*."""
    if evaluation.candidate.candidate_id == candidate.candidate_id:
        return True
    return resume_fingerprint(evaluation.candidate) == resume_fingerprint(candidate)
