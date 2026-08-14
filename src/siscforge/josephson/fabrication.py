"""P4.2 fabrication-compatibility heuristics for Josephson suggestions.

Pure rules that **reuse** Phase-2 Si-feasibility signals (process-temp
ceiling, chemical flags, recommended stacks, membrane notes) plus
family / pathway tags and the presence of Tier-1 metrics.

Nothing here is process qualification, a foundry PDK, or a new
materials-science derivation. Usadel / BdG are out of scope.

See ``docs/phase4-p42-fabrication.md``.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, Literal

from siscforge.josephson.tier1 import RANKING_ONLY_CAVEAT
from siscforge.models.results import JosephsonFabricationHints, JosephsonMetrics

if TYPE_CHECKING:
    from siscforge.models.candidate import CandidateEvaluation
    from siscforge.models.config import JosephsonConfig

logger = logging.getLogger(__name__)

# CMOS-ish BEOL comparison threshold (°C). Heuristic screening default —
# not a foundry spec. Operators may override via josephson.beol_temp_ceiling_c.
DEFAULT_BEOL_TEMP_C = 400.0

HEURISTIC_CAVEAT = (
    "Fabrication labels are heuristics, not process qualification. "
    "Not a foundry PDK / process sign-off."
)

# Always-on reminder when the suggested class is not SIS: Tier-1 numbers
# remain Ambegaokar–Baratoff SIS-tunnel proxies (P4.1). Do not treat them
# as SNS / ramp-edge device values.
NON_SIS_AB_CAVEAT = (
    "Tier-1 IcRn/Jc still use the SIS Ambegaokar–Baratoff formula "
    "(ranking proxy only). SNS / proximity performance can differ "
    "substantially; treat numbers with extra caution until Tier-2 Usadel."
)

JunctionClass = Literal["SIS", "SNS", "ramp_edge", "unknown"]

_NITRIDE_FAMILIES = frozenset({"tm_nitride"})
_SNS_FAMILIES = frozenset({"mgb2_boride"})

# Chemical flags copied through when present on the Si score.
_PASSTHROUGH_CHEM_FLAGS = (
    "nitrogen_window",
    "oxygen_window",
    "oxide_on_si",
    "interdiffusion_caution",
    "direct_on_si",
    "high_thermal_budget",
)


def _finite_number(value: object) -> float | None:
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _family_of(evaluation: CandidateEvaluation) -> str | None:
    family = getattr(getattr(evaluation, "candidate", None), "material_family", None)
    if family is None:
        return None
    text = str(family).strip()
    return text or None


def _si_of(evaluation: CandidateEvaluation) -> Any | None:
    return getattr(evaluation, "si_feasibility", None)


def suggest_junction_class(
    *,
    family: str | None,
    assume_sis: bool,
    membrane: bool,
) -> tuple[JunctionClass, list[str], list[str]]:
    """Return ``(primary, alternatives, notes)`` from family + flags.

    Rule table (heuristic only):

    * ``tm_nitride`` + ``assume_SIS`` → ``SIS`` (NbN/AlN/NbN-style tunnel).
    * ``tm_nitride`` + not ``assume_SIS`` → ``SNS`` (operator override).
    * ``mgb2_boride`` → ``SNS`` (+ ``ramp_edge`` alternative). MgB₂ native
      oxide is a poor SIS barrier; ``assume_SIS`` is recorded, not used.
    * membrane-transfer on a nitride → keep primary, add ``ramp_edge``.
    * anything else / missing family → ``unknown``.

    Only ``tm_nitride`` and ``mgb2_boride`` have assigned labels. New
    families must stay ``unknown`` until a literature-justified rule is
    added here (do not infer SIS from ``assume_SIS`` alone).
    """
    notes: list[str] = []
    alternatives: list[str] = []

    if family in _NITRIDE_FAMILIES:
        if assume_sis:
            primary: JunctionClass = "SIS"
            notes.append(
                "tm_nitride + assume_SIS → SIS "
                "(NbN/AlN/NbN-style tunnel is the screening default)"
            )
        else:
            primary = "SNS"
            notes.append("tm_nitride with assume_SIS=false → SNS (operator override)")
        if membrane:
            alternatives.append("ramp_edge")
            notes.append(
                "membrane-transfer flag → consider ramp-edge as an integration alternative"
            )
        return primary, alternatives, notes

    if family in _SNS_FAMILIES:
        notes.append(
            "mgb2_boride → SNS (MgB2 native oxide is a poor SIS barrier; "
            "literature junctions are SNS / ramp-edge)"
        )
        if assume_sis:
            notes.append(
                "assume_SIS is recorded but not used for the class label — "
                "SIS on MgB2 is not the heuristic default"
            )
        if membrane:
            notes.append("membrane-transfer flag reinforces ramp-edge as an alternative")
        return "SNS", ["ramp_edge"], notes

    if not family:
        notes.append(
            "material_family missing — junction class unknown "
            "(assume_SIS is not enough to assign SIS/SNS)"
        )
        return "unknown", [], notes

    notes.append(
        f"material_family={family} has no fabrication class table — "
        "junction class unknown"
    )
    if assume_sis:
        notes.append(
            "assume_SIS is recorded as an operator assumption, not a class assignment"
        )
    return "unknown", [], notes


def thermal_compatibility(
    *,
    process_temp_ceiling_c: float | None,
    chemical_flags: list[str],
    beol_temp_c: float = DEFAULT_BEOL_TEMP_C,
) -> tuple[bool | None, bool, list[str], list[str]]:
    """Compare Si process ceiling to a CMOS-ish BEOL limit.

    Returns ``(beol_friendly, thermal_budget_caution, flags, notes)``.
    ``beol_friendly`` is ``None`` when the ceiling is missing.
    """
    flags: list[str] = []
    notes: list[str] = []
    caution = False
    beol: bool | None
    ceiling = _finite_number(process_temp_ceiling_c)
    limit = _finite_number(beol_temp_c) or DEFAULT_BEOL_TEMP_C

    if ceiling is None:
        beol = None
        flags.append("thermal_unknown")
        notes.append("process_temp_ceiling_c missing — BEOL compatibility unknown")
        if "high_thermal_budget" in chemical_flags:
            caution = True
            flags.append("thermal_budget_caution")
            notes.append("high_thermal_budget chemical flag without a numeric ceiling")
        return beol, caution, flags, notes

    if ceiling > limit:
        beol = False
        caution = True
        flags.append("thermal_budget_caution")
        notes.append(
            f"process ceiling {ceiling:g} °C exceeds CMOS-ish BEOL ~{limit:g} °C"
        )
    else:
        beol = True
        flags.append("beol_friendly")
        notes.append(
            f"process ceiling {ceiling:g} °C is within CMOS-ish BEOL ~{limit:g} °C"
        )

    if "high_thermal_budget" in chemical_flags:
        if not caution:
            caution = True
            flags.append("thermal_budget_caution")
            notes.append("high_thermal_budget chemical flag set")
        else:
            notes.append("high_thermal_budget chemical flag agrees with high ceiling")
    return beol, caution, flags, notes


def stack_notes(
    *,
    recommended_buffers: list[str],
    chemical_flags: list[str],
    membrane: bool,
    membrane_note: str,
    thermal_window_note: str,
) -> tuple[list[str], list[str]]:
    """Buffer / chemical caveats relevant to JJ growth. Reuses Si signals."""
    flags: list[str] = []
    notes: list[str] = []

    if recommended_buffers:
        shown = ", ".join(recommended_buffers[:4])
        extra = ", …" if len(recommended_buffers) > 4 else ""
        notes.append(f"JJ growth suggested on recommended Si stack: {shown}{extra}")
        flags.append("stack_note")

    if thermal_window_note:
        notes.append(f"Si thermal window: {thermal_window_note}")

    if "nitrogen_window" in chemical_flags:
        notes.append("N-process window — keep residual O off the barrier / film")
        flags.append("nitrogen_window")
    if "oxygen_window" in chemical_flags:
        notes.append("O-process window — residual O harms subsequent nitride barriers")
        flags.append("oxygen_window")
    if "oxide_on_si" in chemical_flags:
        notes.append("oxide-on-Si stack — watch SiO2 reaction / interdiffusion")
        flags.append("oxide_on_si")
    if "interdiffusion_caution" in chemical_flags:
        notes.append("interdiffusion caution on the recommended stack")
        flags.append("interdiffusion_caution")
    if "direct_on_si" in chemical_flags:
        notes.append("direct-on-Si path — no buffer; JJ stack would sit on bare Si")
        flags.append("direct_on_si")

    if membrane:
        flags.append("membrane_transfer")
        if membrane_note:
            notes.append(f"membrane-transfer heuristic: {membrane_note}")
        else:
            notes.append(
                "membrane-transfer candidate — consider transfer / ramp-edge "
                "integration rather than direct BEOL growth"
            )
    return flags, notes


def _unknown_hints(
    *,
    reason: str,
    extra_notes: list[str] | None = None,
) -> JosephsonFabricationHints:
    notes = [HEURISTIC_CAVEAT, reason]
    if extra_notes:
        notes.extend(extra_notes)
    return JosephsonFabricationHints(
        suggested_junction_class="unknown",
        status="unknown",
        heuristic=True,
        flags=["heuristic", "unknown_class"],
        notes=notes,
    )


def infer_fabrication_hints(
    evaluation: CandidateEvaluation,
    metrics: JosephsonMetrics | None = None,
    config: JosephsonConfig | None = None,
) -> JosephsonFabricationHints:
    """Build caveated fabrication hints. Never raises on missing science inputs."""
    try:
        return _infer_fabrication_hints(evaluation, metrics, config)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash attach
        logger.warning("P4.2 fabrication heuristics failed: %s", exc, exc_info=True)
        return _unknown_hints(
            reason=f"heuristic evaluation failed: {exc}",
            extra_notes=[RANKING_ONLY_CAVEAT],
        )


def _infer_fabrication_hints(
    evaluation: CandidateEvaluation,
    metrics: JosephsonMetrics | None,
    config: JosephsonConfig | None,
) -> JosephsonFabricationHints:
    assume_sis = True
    beol_limit = DEFAULT_BEOL_TEMP_C
    if config is not None:
        assume_sis = bool(getattr(config, "assume_SIS", True))
        raw_limit = _finite_number(getattr(config, "beol_temp_ceiling_c", None))
        if raw_limit is not None and raw_limit > 0.0:
            beol_limit = raw_limit

    si = _si_of(evaluation)
    si_missing = si is None
    family = _family_of(evaluation)
    chem = list(getattr(si, "chemical_flags", None) or []) if si is not None else []
    buffers = list(getattr(si, "recommended_buffers", None) or []) if si is not None else []
    ceiling = (
        _finite_number(getattr(si, "process_temp_ceiling_c", None)) if si is not None else None
    )
    membrane = bool(getattr(si, "membrane_transfer_candidate", False)) if si is not None else False
    membrane_note = (
        str(getattr(si, "membrane_transfer_note", "") or "") if si is not None else ""
    )
    thermal_window = (
        str(getattr(si, "thermal_window_note", "") or "") if si is not None else ""
    )

    primary, alternatives, class_notes = suggest_junction_class(
        family=family,
        assume_sis=assume_sis,
        membrane=membrane,
    )
    beol, caution, thermal_flags, thermal_notes = thermal_compatibility(
        process_temp_ceiling_c=ceiling,
        chemical_flags=chem,
        beol_temp_c=beol_limit,
    )
    stack_flags, s_notes = stack_notes(
        recommended_buffers=buffers,
        chemical_flags=chem,
        membrane=membrane,
        membrane_note=membrane_note,
        thermal_window_note=thermal_window,
    )

    flags: list[str] = ["heuristic"]
    class_flag = {
        "SIS": "sis",
        "SNS": "sns",
        "ramp_edge": "ramp_edge",
        "unknown": "unknown_class",
    }[primary]
    flags.append(class_flag)
    flags.extend(thermal_flags)
    flags.extend(stack_flags)
    for flag in _PASSTHROUGH_CHEM_FLAGS:
        if flag in chem and flag not in flags:
            flags.append(flag)

    notes: list[str] = [HEURISTIC_CAVEAT, RANKING_ONLY_CAVEAT]
    flags.append("ab_sis_formula")
    if primary != "SIS":
        notes.append(NON_SIS_AB_CAVEAT)
        flags.append("ab_sis_proxy_on_nonsis_class")
    notes.extend(class_notes)
    notes.extend(thermal_notes)
    notes.extend(s_notes)

    jj_status = getattr(metrics, "status", None) if metrics is not None else None
    if metrics is None or jj_status not in {"ok"}:
        flags.append("tier1_missing")
        notes.append(
            "Tier-1 Josephson metrics missing or skipped — "
            "fabrication hints still use Si / family signals"
        )
    if si_missing:
        flags.append("si_missing")
        notes.append(
            "Si-feasibility missing — thermal / stack hints unavailable; "
            "junction class from family only"
        )

    # Deduplicate flags while preserving order.
    seen: set[str] = set()
    uniq_flags: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            uniq_flags.append(flag)

    have_signal = (
        primary != "unknown"
        or ceiling is not None
        or bool(chem)
        or bool(buffers)
        or membrane
    )
    status = "ok" if have_signal else "unknown"

    return JosephsonFabricationHints(
        suggested_junction_class=primary,
        alternative_classes=list(alternatives),
        beol_friendly=beol,
        thermal_budget_caution=caution,
        process_temp_ceiling_c=ceiling,
        chemical_flags=list(chem),
        recommended_stacks=list(buffers),
        membrane_transfer_candidate=None if si_missing else membrane,
        flags=uniq_flags,
        notes=notes,
        status=status,
        heuristic=True,
        assume_sis=assume_sis,
        beol_temp_ceiling_c=beol_limit,
        family=family,
    )


def apply_secondary_ranking(
    evaluations: list[CandidateEvaluation],
    config: JosephsonConfig | None = None,
) -> list[CandidateEvaluation]:
    """Soft-reorder the Josephson-annotated shortlist for presentation.

    Only rows that already have ``josephson`` attached are re-ordered
    among themselves (same slots). ``rank`` and ``composite_score`` are
    **not** changed. ``secondary_ranking`` of ``none`` / disabled is identity.

    After this runs, **do not assume list index equals ``.rank``**. Prefer
    ``evaluation.rank`` for campaign identity and
    ``evaluation.josephson.secondary_order`` for the presentation key.
    """
    mode = normalize_secondary_ranking(getattr(config, "secondary_ranking", "none"))
    if mode == "none":
        return evaluations

    attr = "icrn_mV" if mode == "icrn" else "jc_A_per_cm2"
    jj_positions = [
        i for i, ev in enumerate(evaluations) if getattr(ev, "josephson", None) is not None
    ]
    if not jj_positions:
        return evaluations

    def _key(i: int) -> tuple[int, float, int]:
        jj = evaluations[i].josephson
        val = getattr(jj, attr, None) if jj is not None else None
        x = _finite_number(val)
        rank = getattr(evaluations[i], "rank", None)
        rank_i = int(rank) if rank is not None else 10**9
        if x is None:
            return (1, 0.0, rank_i)
        return (0, -x, rank_i)

    ordered_idx = sorted(jj_positions, key=_key)
    stamped: dict[int, CandidateEvaluation] = {}
    for order, src_i in enumerate(ordered_idx, start=1):
        ev = evaluations[src_i]
        jj = ev.josephson
        if jj is None:
            stamped[src_i] = ev
            continue
        new_jj = jj.model_copy(
            update={"secondary_ranking": mode, "secondary_order": order}
        )
        stamped[src_i] = ev.model_copy(update={"josephson": new_jj})

    out = list(evaluations)
    reordered = [stamped[i] for i in ordered_idx]
    for slot, ev in zip(sorted(jj_positions), reordered, strict=True):
        out[slot] = ev
    return out


def normalize_secondary_ranking(value: object) -> Literal["none", "icrn", "jc"]:
    """Coerce YAML bools / strings to the P4.2 secondary-ranking mode.

    Thin wrapper around :meth:`JosephsonConfig.normalize_secondary_ranking`
    so the config validator stays the single source of truth.
    """
    from siscforge.models.config import JosephsonConfig

    return JosephsonConfig.normalize_secondary_ranking(value)


def format_fab_notes_for_csv(
    notes: list[str] | None,
    *,
    max_notes: int = 6,
) -> str:
    """Join notes with the permanent caveats first (CSV cells get long)."""
    raw = list(notes or [])
    priority = (HEURISTIC_CAVEAT, RANKING_ONLY_CAVEAT, NON_SIS_AB_CAVEAT)
    front = [item for item in priority if item in raw]
    rest = [item for item in raw if item not in front]
    return " | ".join((front + rest)[:max_notes])


def secondary_ranking_summary(
    evaluations: list[CandidateEvaluation],
) -> str | None:
    """One-line operator note when a presentation sort actually ran."""
    modes: set[str] = set()
    for ev in evaluations:
        jj = getattr(ev, "josephson", None)
        mode = getattr(jj, "secondary_ranking", None) if jj is not None else None
        if mode and mode not in {"none", ""}:
            modes.add(str(mode))
    if not modes:
        return None
    mode_s = ", ".join(sorted(modes))
    return (
        f"Josephson shortlist reordered by {mode_s} for presentation only; "
        "rank identity unchanged."
    )
