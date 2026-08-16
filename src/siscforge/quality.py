"""Result-quality / trust assessment for screening EPW and phonon results.

This is a **trust layer**, not a substitute for denser-grid production
refinement. Pathological screening λ (e.g. 6–13 vs literature ~1–1.5 for NbN)
must not silently dominate ranking.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import QualityConfig

ResultQualityTier = Literal[
    "production",
    "screening",
    "screening_suspect",
    "unreliable",
    "unknown",
]

# Machine-readable flag codes
FLAG_HIGH_LAMBDA = "high_lambda"
FLAG_EXTREME_LAMBDA = "extreme_lambda"
FLAG_IMAGINARY_MODES = "imaginary_modes"
FLAG_DYNAMICALLY_UNSTABLE = "dynamically_unstable"
FLAG_SOFT_MODES = "soft_modes"
FLAG_WANNIER_RANDOM = "wannier_random_proj"
FLAG_WANNIER_LOW_CONF = "wannier_low_confidence"
FLAG_COARSE_GRIDS = "coarse_grids"
FLAG_QUALITY_TAG_SCREENING = "quality_tag_screening"
FLAG_QUALITY_TAG_MOCK = "quality_tag_mock"
FLAG_EPW_FAILED = "epw_failed"
FLAG_SURROGATE_ONLY = "surrogate_only"
FLAG_DMFT_PAIRING = "dmft_pairing"
FLAG_DMFT_PAIRING_MOCK = "dmft_pairing_mock"

# Sort key: higher = more trustworthy (used as secondary sort key)
_QUALITY_RANK: dict[str, int] = {
    "production": 4,
    "screening": 3,
    "screening_suspect": 2,
    "unreliable": 1,
    "unknown": 0,
}


class ResultQualityAssessment(BaseModel):
    """Trust assessment for an evaluation's phonon / e-ph results."""

    result_quality: ResultQualityTier = "unknown"
    quality_flags: list[str] = Field(default_factory=list)
    quality_notes: str = ""
    version: str = "0.1"

    def quality_rank(self) -> int:
        """Higher = more trustworthy (for sorting)."""
        return _QUALITY_RANK.get(self.result_quality, 0)


def quality_tier_rank(tier: str | None) -> int:
    return _QUALITY_RANK.get(tier or "unknown", 0)


def assess_result_quality(
    evaluation: CandidateEvaluation,
    config: QualityConfig | None = None,
) -> ResultQualityAssessment:
    """Assess trustworthiness of phonon / EPW results on *evaluation*.

    Pure function: does not mutate *evaluation*. Defaults are conservative
    (honest about screening inflation).
    """
    config = config or QualityConfig()
    flags: list[str] = []
    notes: list[str] = []

    eph = evaluation.electron_phonon
    phonon = evaluation.phonon
    status = (evaluation.status or "").lower()

    # --- Phonon stability ---
    if phonon is not None:
        if phonon.has_imaginary_modes or phonon.dynamically_stable is False:
            flags.append(FLAG_IMAGINARY_MODES)
            flags.append(FLAG_DYNAMICALLY_UNSTABLE)
            notes.append("imaginary phonon modes / dynamically unstable")
        elif phonon.min_frequency_cm1 is not None:
            min_f = float(phonon.min_frequency_cm1)
            if 0.0 <= min_f < float(config.min_frequency_cm1_soft):
                flags.append(FLAG_SOFT_MODES)
                notes.append(
                    f"soft modes (min ω={min_f:.1f} cm⁻¹ "
                    f"< {config.min_frequency_cm1_soft:g})"
                )

    # --- Lambda magnitude ---
    # Historical QualityConfig default was 8.0 (no mock_unreliable field),
    # which left the documented pathological band (λ≈6) as
    # screening_suspect. Lift that one legacy default to 5.0. Explicit
    # YAML / QualityConfig(lambda_unreliable_above=8.0) on the new model
    # is honoured as-is.
    unreliable_above = float(config.lambda_unreliable_above)
    if (
        unreliable_above == 8.0
        and "mock_unreliable" not in getattr(type(config), "model_fields", {})
    ):
        unreliable_above = 5.0
    lam: float | None = None
    if eph is not None and eph.lambda_total is not None:
        lam = float(eph.lambda_total)
        if lam >= unreliable_above:
            flags.append(FLAG_EXTREME_LAMBDA)
            flags.append(FLAG_HIGH_LAMBDA)
            notes.append(
                f"λ={lam:.2f} ≥ {unreliable_above:g} "
                f"(pathological for conventional NbN-like screening)"
            )
        elif lam >= float(config.lambda_suspect_above):
            flags.append(FLAG_HIGH_LAMBDA)
            notes.append(
                f"λ={lam:.2f} ≥ {config.lambda_suspect_above:g} "
                f"(elevated vs literature ~1–1.5 for NbN/NbTiN; "
                f"screening soft modes / coarse grids often inflate λ)"
            )

    # --- Quality tags / grids / Wannier ---
    qtags: list[str] = []
    if eph is not None:
        qtags.append(eph.quality_tag or "unknown")
        summary = eph.alpha2F_summary or {}
        method = str(summary.get("method", "")).lower()
        mat = str(summary.get("material_notes", "")).lower()
        # Screening EPW path always uses proj=random unless production projs set
        if (
            "random" in method
            or "random" in mat
            or "proj=random" in mat
            or (
                eph.quality_tag == "screening"
                and eph.status in {"ok", "unknown"}
            )
        ):
            flags.append(FLAG_WANNIER_RANDOM)
            notes.append("Wannier screening uses random projections")
        if eph.wannier_ok is False:
            flags.append(FLAG_WANNIER_LOW_CONF)
            notes.append("wannier_ok=False")
        if eph.status not in {"ok", "mock"} and status == "failed":
            flags.append(FLAG_EPW_FAILED)
    if phonon is not None:
        qtags.append(phonon.quality_tag or "unknown")
    if evaluation.candidate.quality_tag:
        qtags.append(evaluation.candidate.quality_tag)
    dmft = getattr(evaluation, "dmft", None)
    if dmft is not None:
        qtags.append(dmft.quality_tag or "unknown")

    if any(t == "screening" for t in qtags):
        flags.append(FLAG_QUALITY_TAG_SCREENING)
        flags.append(FLAG_COARSE_GRIDS)
    src = evaluation.performance_score_source or ""
    if any(t == "mock" for t in qtags) or status == "mock" or src == "mock":
        flags.append(FLAG_QUALITY_TAG_MOCK)

    if status == "surrogate_only" or evaluation.performance_score_source == "surrogate":
        flags.append(FLAG_SURROGATE_ONLY)
        notes.append("performance from λ/Tc surrogate stub (not EPW)")

    if src == "dmft_pairing_mock":
        flags.append(FLAG_DMFT_PAIRING_MOCK)
        notes.append(
            "performance from mock DMFT pairing eigenvalue "
            "(illustrative, not literature-validated; not Eliashberg Tc)"
        )
    elif src == "dmft_pairing":
        flags.append(FLAG_DMFT_PAIRING)
        notes.append(
            "performance from DMFT pairing eigenvalue (Tc-like proxy, not EPW Tc)"
        )

    # Deduplicate flags preserving order
    seen: set[str] = set()
    uniq_flags: list[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            uniq_flags.append(f)

    # --- Assign tier ---
    tier: ResultQualityTier = "unknown"
    has_eph = eph is not None and eph.lambda_total is not None
    has_phonon = phonon is not None

    mock_unreliable = bool(getattr(config, "mock_unreliable", True)) and (
        FLAG_QUALITY_TAG_MOCK in uniq_flags
        or FLAG_DMFT_PAIRING_MOCK in uniq_flags
        or src == "mock"
    )

    if status in {"failed", "pending"} and not has_eph:
        tier = "unknown"
        if FLAG_EPW_FAILED in uniq_flags or status == "failed":
            notes.append("no successful EPW for quality assessment")
    elif mock_unreliable:
        tier = "unreliable"
        notes.append(
            "mock / dry-run result — not comparable to screening or production numbers"
        )
    elif FLAG_EXTREME_LAMBDA in uniq_flags:
        tier = "unreliable"
    elif FLAG_IMAGINARY_MODES in uniq_flags and config.imaginary_modes_unreliable:
        tier = "unreliable"
    elif FLAG_HIGH_LAMBDA in uniq_flags or (
        FLAG_IMAGINARY_MODES in uniq_flags and not config.imaginary_modes_unreliable
    ):
        tier = "screening_suspect"
    elif FLAG_SOFT_MODES in uniq_flags and has_eph:
        tier = "screening_suspect"
        notes.append("soft modes with EPW → treat Tc as order-of-magnitude only")
    elif any(t == "production" for t in qtags) and FLAG_HIGH_LAMBDA not in uniq_flags:
        tier = "production"
    elif any(t in {"screening", "mock"} for t in qtags) or has_eph or has_phonon:
        tier = "screening"
        if not notes and has_eph:
            notes.append("screening-quality EPW/phonon; not production citation-grade")
    elif FLAG_SURROGATE_ONLY in uniq_flags:
        tier = "screening_suspect"
    elif FLAG_DMFT_PAIRING_MOCK in uniq_flags or FLAG_DMFT_PAIRING in uniq_flags:
        # Pairing proxy is never citation-grade Tc
        tier = "screening"
        if FLAG_DMFT_PAIRING_MOCK in uniq_flags and not notes:
            notes.append("mock DMFT pairing — illustrative, not quantitative")
    else:
        tier = "unknown"

    # Soft modes alone without EPW stay screening if stable enough
    if (
        tier == "unknown"
        and has_phonon
        and FLAG_IMAGINARY_MODES not in uniq_flags
    ):
        tier = "screening"

    note_str = "; ".join(notes) if notes else (
        f"result_quality={tier}" if tier != "unknown" else "insufficient data"
    )

    return ResultQualityAssessment(
        result_quality=tier,
        quality_flags=uniq_flags,
        quality_notes=note_str,
        version=config.version,
    )


def apply_quality_assessment(
    evaluation: CandidateEvaluation,
    config: QualityConfig | None = None,
) -> CandidateEvaluation:
    """Return a copy of *evaluation* with quality fields populated."""
    assessment = assess_result_quality(evaluation, config)
    updates: dict = {
        "result_quality": assessment.result_quality,
        "quality_flags": list(assessment.quality_flags),
        "quality_notes": assessment.quality_notes,
    }
    # Mirror onto electron_phonon for export convenience when present
    if evaluation.electron_phonon is not None:
        eph = evaluation.electron_phonon.model_copy(
            update={
                "result_quality": assessment.result_quality,
                "quality_flags": list(assessment.quality_flags),
                "quality_notes": assessment.quality_notes,
            }
        )
        updates["electron_phonon"] = eph
    return evaluation.model_copy(update=updates)
