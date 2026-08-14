"""Attach Tier-1 Josephson metrics to evaluations (inert unless enabled)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from siscforge.josephson.tier1 import estimate_tier1

if TYPE_CHECKING:
    from siscforge.models.candidate import CandidateEvaluation
    from siscforge.models.config import JosephsonConfig

logger = logging.getLogger(__name__)


def josephson_is_enabled(config: JosephsonConfig | None) -> bool:
    """True only when the campaign explicitly turns the module on."""
    return bool(config is not None and getattr(config, "enabled", False))


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

    Does **not** change ranking, Pareto, Si-feasibility, or pairing.
    """
    if not josephson_is_enabled(config):
        return evaluations

    assert config is not None
    shortlist_only = bool(getattr(config, "shortlist_only", True))
    shortlist_size = int(getattr(config, "shortlist_size", 20) or 0)

    out: list[CandidateEvaluation] = []
    for ev in evaluations:
        try:
            if shortlist_only and shortlist_size > 0:
                rank = getattr(ev, "rank", None)
                if rank is None or int(rank) > shortlist_size:
                    out.append(ev)
                    continue
            metrics = estimate_tier1(ev, config)
            out.append(ev.model_copy(update={"josephson": metrics}))
        except Exception:
            logger.exception(
                "P4.1 Josephson attach failed for %s; leaving josephson unset",
                getattr(getattr(ev, "candidate", None), "candidate_id", "?"),
            )
            out.append(ev)
    return out
