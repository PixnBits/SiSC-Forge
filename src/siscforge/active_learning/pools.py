"""P3.6 — conventional / unconventional acquisition pools.

A *pool* is the physics pathway that should drive (or did drive) a
candidate's performance signal:

- ``conventional`` — EPW / surrogate λ/Tc
- ``unconventional`` — DMFT pairing / correlated pathway
- ``unknown`` — no recognized signal; never silently assigned

Derivation is **explicit and ordered**. Conflicting pathway attachments
without a recognized ``performance_score_source`` resolve to ``unknown``
rather than guessing. See ``docs/phase3-p36-mixed-al.md``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

AcquisitionPool = Literal["conventional", "unconventional", "unknown"]
AcquisitionMode = Literal["off", "joint", "separate"]

POOLS: tuple[AcquisitionPool, ...] = ("conventional", "unconventional", "unknown")

CONVENTIONAL_FAMILIES: frozenset[str] = frozenset(
    {"tm_nitride", "mgb2_boride", "b_doped_si"}
)
UNCONVENTIONAL_FAMILIES: frozenset[str] = frozenset({"nickelate", "cuprate"})

# Headline / label sources that *unambiguously* name a pathway.
# Unrecognized strings fall through — they never silently bucket.
CONVENTIONAL_SOURCES: frozenset[str] = frozenset(
    {
        "epw",
        "epw_eliashberg",
        "epw_allen_dynes",
        "mock",  # conventional mock EPW (P3.4)
        "surrogate",
    }
)
UNCONVENTIONAL_SOURCES: frozenset[str] = frozenset(
    {
        "dmft_pairing",
        "dmft_pairing_mock",
    }
)

_REFUSE_STATUS: frozenset[str] = frozenset(
    {"failed", "skipped", "refused", "pending"}
)


@dataclass(frozen=True)
class PoolDecision:
    """Documented outcome of :func:`derive_pool`."""

    pool: AcquisitionPool
    reason: str
    """Machine-readable precedence winner, e.g. ``source:dmft_pairing``."""

    evidence: dict[str, Any]


def normalize_pool_mode(value: str | None) -> AcquisitionMode:
    """Coerce a config / CLI string to a known mode. Default ``off``."""
    if value is None:
        return "off"
    v = str(value).strip().lower()
    if v in {"off", "joint", "separate"}:
        return v  # type: ignore[return-value]
    raise ValueError(
        f"Unknown acquisition pool_mode={value!r}; expected off|joint|separate"
    )


def _finite_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _has_epw_signal(electron_phonon: Any | None) -> bool:
    """True when an e-ph result indicates the conventional pathway ran."""
    if electron_phonon is None:
        return False
    status = str(getattr(electron_phonon, "status", "") or "")
    if status in _REFUSE_STATUS:
        return False
    if _finite_number(getattr(electron_phonon, "lambda_total", None)) is not None:
        return True
    best = getattr(electron_phonon, "best_tc_K", None)
    if callable(best):
        if _finite_number(best()) is not None:
            return True
    for key in ("Tc_eliashberg", "Tc_allen_dynes"):
        if _finite_number(getattr(electron_phonon, key, None)) is not None:
            return True
    return status in {"ok", "mock"}


def _has_dmft_pairing_signal(dmft: Any | None) -> bool:
    """True when a DMFT result carries a usable pairing eigenvalue."""
    if dmft is None:
        return False
    status = str(getattr(dmft, "status", "") or "")
    if status in _REFUSE_STATUS:
        return False
    return _finite_number(getattr(dmft, "leading_pairing_eigenvalue", None)) is not None


def _family_pool(family: str | None) -> AcquisitionPool | None:
    if not family:
        return None
    if family in CONVENTIONAL_FAMILIES:
        return "conventional"
    if family in UNCONVENTIONAL_FAMILIES:
        return "unconventional"
    return None


def _source_pool(source: str | None) -> AcquisitionPool | None:
    if not source:
        return None
    if source in CONVENTIONAL_SOURCES:
        return "conventional"
    if source in UNCONVENTIONAL_SOURCES:
        return "unconventional"
    return None


def derive_pool(
    *,
    candidate: Any | None = None,
    evaluation: Any | None = None,
    material_family: str | None = None,
    performance_score_source: str | None = None,
    electron_phonon: Any | None = None,
    dmft: Any | None = None,
) -> PoolDecision:
    """Assign a pool using documented precedence (no silent mis-bucketing).

    Precedence
    ----------
    1. Recognized ``performance_score_source`` (what actually drove the score).
    2. Pathway attachments on the evaluation:
       - only DMFT pairing → unconventional
       - only electron-phonon → conventional
       - **both** without a recognized source → ``unknown`` (conflict)
    3. Recognized ``material_family`` (prior for unevaluated candidates).
    4. ``unknown``.

    Unrecognized source strings and families fall through instead of
    guessing. Pass explicit kwargs to override attributes pulled from
    *candidate* / *evaluation*.
    """
    ev = evaluation
    cand = candidate if candidate is not None else getattr(ev, "candidate", None)

    family = material_family
    if family is None:
        family = getattr(cand, "material_family", None)

    source = performance_score_source
    if source is None and ev is not None:
        source = getattr(ev, "performance_score_source", None)

    eph = electron_phonon
    if eph is None and ev is not None:
        eph = getattr(ev, "electron_phonon", None)

    dm = dmft
    if dm is None and ev is not None:
        dm = getattr(ev, "dmft", None)

    has_epw = _has_epw_signal(eph)
    has_pair = _has_dmft_pairing_signal(dm)
    fam_pool = _family_pool(family)
    src_pool = _source_pool(source)

    evidence = {
        "performance_score_source": source,
        "material_family": family,
        "has_electron_phonon": has_epw,
        "has_dmft_pairing": has_pair,
    }

    if src_pool is not None:
        return PoolDecision(
            pool=src_pool,
            reason=f"source:{source}",
            evidence=evidence,
        )

    if has_epw and has_pair:
        return PoolDecision(
            pool="unknown",
            reason="conflict:electron_phonon+dmft_pairing",
            evidence=evidence,
        )
    if has_pair:
        return PoolDecision(
            pool="unconventional",
            reason="signal:dmft_pairing",
            evidence=evidence,
        )
    if has_epw:
        return PoolDecision(
            pool="conventional",
            reason="signal:electron_phonon",
            evidence=evidence,
        )
    if fam_pool is not None:
        return PoolDecision(
            pool=fam_pool,
            reason=f"family:{family}",
            evidence=evidence,
        )
    return PoolDecision(pool="unknown", reason="no_recognized_signal", evidence=evidence)


def empty_pool_counts() -> dict[str, int]:
    return {p: 0 for p in POOLS}


def count_pools(pools: Iterable[str]) -> dict[str, int]:
    counts = empty_pool_counts()
    for raw in pools:
        key = raw if raw in counts else "unknown"
        counts[key] += 1
    return counts


def quota_slots(fraction: float, k: int) -> int:
    """Reserved slots for one pool: ``floor(fraction * k)``."""
    if k <= 0 or fraction <= 0:
        return 0
    return int(float(fraction) * int(k))


def select_with_quotas(
    records: Sequence[Any],
    *,
    k: int,
    quotas: Mapping[str, float] | None = None,
    pool_attr: str = "pool",
    score_attr: str = "acquisition_score",
    id_attr: str = "candidate_id",
) -> list[str]:
    """Pick up to *k* candidate ids with per-pool reserved fractions.

    Algorithm
    ---------
    1. Reserve ``floor(quota[pool] * k)`` slots per pool that still has
       candidates, taking the highest-scoring rows in that pool.
    2. Fill leftover slots from the remaining rows by global score so an
       empty (or absent) pool cannot starve a present one.

    *quotas* maps pool name → max fraction of the batch. Missing pools
    default to 0 reserved slots (leftover-only).
    """
    if k <= 0 or not records:
        return []
    q = {p: 0.0 for p in POOLS}
    if quotas:
        for name, frac in quotas.items():
            q[str(name)] = float(frac)

    ranked = sorted(
        records,
        key=lambda r: (
            float(getattr(r, score_attr) or 0.0),
            float(getattr(r, "predicted_tc", None) or -1.0),
        ),
        reverse=True,
    )
    buckets: dict[str, list[Any]] = {p: [] for p in POOLS}
    for rec in ranked:
        pool = str(getattr(rec, pool_attr, None) or "unknown")
        if pool not in buckets:
            pool = "unknown"
        buckets[pool].append(rec)

    selected: list[Any] = []
    selected_ids: set[str] = set()
    remaining: dict[str, list[Any]] = {}
    for pool, items in buckets.items():
        n_reserved = min(len(items), quota_slots(q.get(pool, 0.0), k))
        take = items[:n_reserved]
        remaining[pool] = items[n_reserved:]
        for rec in take:
            cid = str(getattr(rec, id_attr))
            if cid in selected_ids:
                continue
            selected.append(rec)
            selected_ids.add(cid)

    leftover = max(0, int(k) - len(selected))
    if leftover:
        rest = sorted(
            (rec for items in remaining.values() for rec in items),
            key=lambda r: (
                float(getattr(r, score_attr) or 0.0),
                float(getattr(r, "predicted_tc", None) or -1.0),
            ),
            reverse=True,
        )
        for rec in rest:
            if leftover <= 0:
                break
            cid = str(getattr(rec, id_attr))
            if cid in selected_ids:
                continue
            selected.append(rec)
            selected_ids.add(cid)
            leftover -= 1

    # Preserve global-score order among the selected set (matches top-k feel).
    selected.sort(
        key=lambda r: (
            float(getattr(r, score_attr) or 0.0),
            float(getattr(r, "predicted_tc", None) or -1.0),
        ),
        reverse=True,
    )
    return [str(getattr(r, id_attr)) for r in selected[:k]]
