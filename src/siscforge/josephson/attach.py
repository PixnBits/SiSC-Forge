"""Attach Tier-1 Josephson metrics + P4.2 fabrication hints (inert unless enabled)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from siscforge import __version__
from siscforge.josephson.fabrication import (
    apply_secondary_ranking,
    infer_fabrication_hints,
)
from siscforge.josephson.tier1 import RANKING_ONLY_CAVEAT, estimate_tier1
from siscforge.models.provenance import Provenance
from siscforge.models.results import JosephsonMetrics

if TYPE_CHECKING:
    from siscforge.models.candidate import CandidateEvaluation
    from siscforge.models.config import JosephsonConfig

logger = logging.getLogger(__name__)


def josephson_is_enabled(config: JosephsonConfig | None) -> bool:
    """True only when the campaign explicitly turns the module on."""
    return bool(config is not None and getattr(config, "enabled", False))


def _skipped_on_error(exc: BaseException) -> JosephsonMetrics:
    """Best-effort skipped payload so a rare crash is still inspectable."""
    return JosephsonMetrics(
        approximate=True,
        status="skipped",
        method="tier1_analytic_ab",
        notes=f"{RANKING_ONLY_CAVEAT}; attach failed: {exc}",
        raw={"reason": "attach_failed", "error": str(exc), "error_type": type(exc).__name__},
        provenance=Provenance(
            source="siscforge.josephson.attach",
            software={"siscforge": __version__},
            notes=RANKING_ONLY_CAVEAT,
        ),
    )


def _with_fabrication(
    evaluation: CandidateEvaluation,
    metrics: JosephsonMetrics,
    config: JosephsonConfig,
) -> JosephsonMetrics:
    """Attach P4.2 hints when the sub-flag is on. Never raises."""
    try:
        if not bool(getattr(config, "fabrication_hints", True)):
            return metrics
        hints = infer_fabrication_hints(evaluation, metrics, config)
        return metrics.model_copy(update={"fabrication": hints})
    except Exception as exc:  # noqa: BLE001
        logger.warning("P4.2 fabrication attach failed: %s", exc, exc_info=True)
        return metrics


def attach_josephson_metrics(
    evaluations: list[CandidateEvaluation],
    config: JosephsonConfig | None = None,
) -> list[CandidateEvaluation]:
    """Return evaluations with optional ``josephson`` attached.

    * Disabled (default) — identity: no copies, no field writes.
    * Enabled — compute Tier-1 metrics for rows that are in the top-N
      shortlist (when ``shortlist_only`` and ``shortlist_size`` apply)
      and have enough gap / Tc input. Missing inputs become a skipped
      :class:`~siscforge.models.results.JosephsonMetrics` (never a crash).
      Rows outside the top-N keep ``josephson=None``.
    * When enabled, P4.2 fabrication hints are attached by default
      (``fabrication_hints``, opt-out). Optional ``secondary_ranking``
      re-orders only the Josephson-annotated shortlist for presentation;
      ``rank`` / ``composite_score`` are unchanged.

    Does **not** change ranking maths, Pareto, Si-feasibility, or pairing.
    """
    if not josephson_is_enabled(config):
        return evaluations

    assert config is not None
    shortlist_only = bool(getattr(config, "shortlist_only", True))
    shortlist_size = int(getattr(config, "shortlist_size", 20) or 0)

    out: list[CandidateEvaluation] = []
    missing_rank = 0
    for ev in evaluations:
        try:
            if shortlist_only and shortlist_size > 0:
                rank = getattr(ev, "rank", None)
                if rank is None or int(rank) > shortlist_size:
                    if rank is None:
                        missing_rank += 1
                    out.append(ev)
                    continue
            metrics = estimate_tier1(ev, config)
            metrics = _with_fabrication(ev, metrics, config)
            out.append(ev.model_copy(update={"josephson": metrics}))
        except Exception as exc:
            cid = getattr(getattr(ev, "candidate", None), "candidate_id", "?")
            logger.warning(
                "P4.1 Josephson attach failed for %s: %s",
                cid,
                exc,
                exc_info=True,
            )
            try:
                skipped = _skipped_on_error(exc)
                skipped = _with_fabrication(ev, skipped, config)
                out.append(ev.model_copy(update={"josephson": skipped}))
            except Exception:
                logger.exception(
                    "P4.1 Josephson could not record skipped metrics for %s; "
                    "leaving josephson unset",
                    cid,
                )
                out.append(ev)

    if missing_rank:
        logger.warning(
            "P4.1 Josephson: %d evaluation(s) left with josephson=None "
            "(shortlist_only=True but rank is missing)",
            missing_rank,
        )

    try:
        out = apply_secondary_ranking(out, config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "P4.2 Josephson secondary ranking skipped: %s", exc, exc_info=True
        )
    return out
