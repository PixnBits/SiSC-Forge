"""Lightweight λ / ω_log / Tc surrogate (Phase 1 / 1.5).

**Not** a trained production GNN. Two modes:

1. **Family-heuristic stub** (default) — fixed anchors (NbN, MgB₂, TiN, …).
2. **Family-mean fit** (Phase 1.5) — when a trained registry payload supplies
   per-family statistics from promoted EPW / literature labels, those means
   override the hardcoded baselines so prioritization **moves** after retrain.

Real EPW remains the source of truth. This module only pre-filters and, when
no ElectronPhononResult exists, may supply a weak ranking signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

from siscforge.calculators.qe.eliashberg import allen_dynes_tc
from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import TcLambdaSurrogateConfig

MODEL_VERSION: str = "0.1-family-heuristic"

# Family baselines: (λ, ω_log K, relative uncertainty)
_FAMILY_BASE: dict[str, tuple[float, float, float]] = {
    "tm_nitride": (1.00, 280.0, 0.35),
    "mgb2_boride": (0.85, 700.0, 0.28),
    "b_doped_si": (0.25, 450.0, 0.55),
    "nickelate": (0.40, 200.0, 0.70),  # unconventional; weak conventional proxy
    "cuprate": (0.30, 180.0, 0.75),
    "other": (0.50, 300.0, 0.60),
}

# Formula anchors (override/boost toward literature midpoints)
_FORMULA_ANCHOR: dict[str, tuple[float, float, float]] = {
    # λ, ω_log K, unc
    "NbN": (1.05, 280.0, 0.25),
    "TiN": (0.70, 320.0, 0.35),
    "ZrN": (0.80, 300.0, 0.35),
    "HfN": (0.75, 290.0, 0.40),
    "VN": (0.90, 310.0, 0.35),
    "TaN": (0.85, 270.0, 0.40),
    "MgB2": (0.85, 700.0, 0.22),
    "B2Mg": (0.85, 700.0, 0.22),
}


class TcLambdaPrediction(BaseModel):
    """Surrogate prediction for electron-phonon / Tc pre-filtering."""

    predicted_lambda: float
    """Predicted electron-phonon coupling λ."""

    predicted_omega_log: float | None = None
    """Predicted ω_log in kelvin (optional)."""

    predicted_Tc: float
    """Predicted Allen–Dynes Tc (K) from surrogate moments."""

    uncertainty: float = Field(ge=0.0, le=1.0)
    """Relative uncertainty (0 = high confidence, 1 = pure guess)."""

    model_version: str = MODEL_VERSION
    quality_tag: Literal["stub", "screening", "production", "trained"] = "stub"
    method: str = "family_heuristic"
    """Implementation tag; future models use e.g. ``alignn_head``."""

    features: dict[str, Any] = Field(default_factory=dict)
    """Cheap features used for the prediction (for audit / debugging)."""

    notes: str = (
        "Phase-1 stub surrogate — not trained on large EPW data. "
        "Real EPW ElectronPhononResult overrides this when present."
    )
    training_set_size: int = 0
    bootstrap: bool = True

    def score_for_ranking(self) -> float:
        """Tc demoted by uncertainty (higher uncertainty → lower score)."""
        return float(self.predicted_Tc) * (1.0 - 0.5 * float(self.uncertainty))


def _normalize_formula(formula: str) -> str:
    return formula.replace("₂", "2").replace(" ", "").strip()


def _strain_magnitude(candidate: StructureCandidate) -> float:
    if candidate.in_plane_strain is not None:
        return abs(float(candidate.in_plane_strain))
    if candidate.strain_tensor is not None:
        return abs(float(candidate.strain_tensor[0]))
    return 0.0


def _n_metal_like(candidate: StructureCandidate) -> int:
    """Count non-N / non-B anions-ish elements for mixing cost."""
    skip = {"N", "O", "H"}
    return sum(1 for el in candidate.composition if el not in skip)


def _family_stats_baseline(
    family: str,
    family_stats: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[float, float, float, dict[str, Any]] | None:
    """Return (lam, wlog, unc, features) from trained family means, or None.

    Only exact family keys are used. Missing families fall back to the
    heuristic path (high uncertainty), not a silent ``other`` mean that
    could launder the wrong chemistry into rankings.

    Label modes:

    - λ and/or ω_log present → use those moments (fill missing from family base).
    - Only ``tc_mean`` present → use family-base moments for structure, but set
      ``tc_override`` so predicted Tc comes from the labels (not Allen–Dynes on
      hardcoded λ/ω).
    - Neither moments nor Tc → not a trained baseline (return None).
    """
    if not family_stats:
        return None
    st = family_stats.get(family)
    if not st:
        return None
    lam = st.get("lambda_mean")
    wlog = st.get("omega_log_mean")
    tc_mean = st.get("tc_mean")
    has_moments = lam is not None or wlog is not None
    if not has_moments and tc_mean is None:
        return None

    base_lam, base_wlog, _base_unc = _FAMILY_BASE.get(family, _FAMILY_BASE["other"])
    if lam is None:
        lam = base_lam
    if wlog is None:
        wlog = base_wlog

    n = float(st.get("n") or 1.0)
    tc_std = float(st.get("tc_std") or 0.0)
    # More labels → lower floor uncertainty; high within-family scatter → higher
    unc = max(0.12, min(0.85, 0.55 / (n**0.5) + min(0.25, tc_std / 40.0)))
    feats: dict[str, Any] = {
        "trained_family": family,
        "n_labels_family": n,
        "tc_std": tc_std,
        "source": "family_mean_fit",
    }
    if not has_moments and tc_mean is not None:
        # Tc-only labels: do not pretend hardcoded λ/ω produced Tc
        feats["tc_override"] = float(tc_mean)
        feats["source"] = "family_tc_mean"
        feats["moments_from"] = "family_base_placeholder"
        unc = max(unc, 0.40)
    return float(lam), float(wlog), float(unc), feats



def predict_tc_lambda(
    candidate: StructureCandidate,
    *,
    mu_star: float = 0.10,
    family_stats: Mapping[str, Mapping[str, Any]] | None = None,
    model_version: str | None = None,
    method: str | None = None,
    training_set_size: int = 0,
    bootstrap: bool | None = None,
) -> TcLambdaPrediction:
    """Predict λ, ω_log, Tc and uncertainty for *candidate*.

    When *family_stats* is provided (from a Phase 1.5 retrain payload), family
    means override hardcoded baselines so the acquisition queue **changes**
    after ``al-train``. Deterministic, pure-Python, no network / GPU.
    """
    family = candidate.material_family
    formula = _normalize_formula(candidate.formula)
    trained = _family_stats_baseline(family, family_stats)

    features: dict[str, Any] = {
        "family": family,
        "formula": formula,
        "strain": _strain_magnitude(candidate),
        "n_elements": len(candidate.composition) or 1,
    }

    quality_tag: Literal["stub", "screening", "production", "trained"] = "stub"
    # Always start heuristic; only claim a fit method when family stats apply.
    used_method = "family_heuristic"
    used_version = model_version or MODEL_VERSION

    if trained is not None:
        lam, wlog, unc, tfeats = trained
        features.update(tfeats)
        quality_tag = "trained"
        src = str(tfeats.get("source") or "family_mean_fit")
        if src == "family_tc_mean":
            used_method = "family_tc_mean"
        else:
            used_method = (
                method
                if method and method not in {"family_heuristic", "heuristic"}
                else "family_mean_fit"
            )
        # Formula anchors still gently bias toward known compounds when present
        # (skip for tc-only override — labels own the Tc).
        if formula in _FORMULA_ANCHOR and "tc_override" not in tfeats:
            a_lam, a_wlog, a_unc = _FORMULA_ANCHOR[formula]
            # Blend 70% trained family mean, 30% formula anchor
            lam = 0.7 * lam + 0.3 * a_lam
            wlog = 0.7 * wlog + 0.3 * a_wlog
            unc = min(1.0, 0.7 * unc + 0.3 * a_unc)
            features["anchor_blend"] = formula
    elif formula in _FORMULA_ANCHOR:
        lam, wlog, unc = _FORMULA_ANCHOR[formula]
        features["anchor"] = formula
    else:
        lam, wlog, unc = _FAMILY_BASE.get(family, _FAMILY_BASE["other"])
        # Soft ternary / multi-metal demotion for nitrides
        if family == "tm_nitride":
            n_met = _n_metal_like(candidate)
            features["n_metals"] = n_met
            if n_met >= 2:
                lam *= 0.92
                unc = min(1.0, unc + 0.08)
            comp = candidate.composition or {}
            if comp.get("Nb", 0) >= 0.4:
                lam *= 1.05
                features["nb_rich"] = True
            if comp.get("Ti", 0) >= 0.5 and comp.get("Nb", 0) < 0.3:
                lam *= 0.90
                features["ti_rich"] = True

    # Strain softens Tc / increases uncertainty
    strain = features["strain"]
    if strain > 0:
        factor = max(0.4, 1.0 - 8.0 * (strain**2))
        lam *= factor
        wlog *= max(0.7, 1.0 - 4.0 * (strain**2))
        unc = min(1.0, unc + 3.0 * strain)
        features["strain_factor"] = round(factor, 4)

    if family in {"nickelate", "cuprate"}:
        features["conventional_proxy_only"] = True

    lam = max(0.05, float(lam))
    wlog = max(50.0, float(wlog))
    unc = float(min(1.0, max(0.05, unc)))
    if features.get("tc_override") is not None:
        # Label Tc owns the prediction when moments were placeholders only.
        tc = float(features["tc_override"])
        strain = float(features.get("strain") or 0.0)
        if strain > 0:
            tc *= max(0.4, 1.0 - 8.0 * (strain**2))
    else:
        tc = allen_dynes_tc(lam, wlog, mu_star)

    if quality_tag == "trained":
        notes = (
            f"Phase-1.5 {used_method} ({used_version}; "
            f"{training_set_size} labels). Prioritization aid — not experimental Tc."
        )
    else:
        notes = (
            "Phase-1 family-heuristic stub (not a trained GNN). "
            "Use only for pre-filtering; real EPW overrides ranking when present."
        )
    if family == "mgb2_boride":
        notes += " MgB2: isotropic average of two-gap character."

    boot = True if bootstrap is None else bool(bootstrap)
    if bootstrap is None and quality_tag == "trained" and training_set_size >= 150:
        boot = False

    return TcLambdaPrediction(
        predicted_lambda=round(lam, 4),
        predicted_omega_log=round(wlog, 2),
        predicted_Tc=round(tc, 2),
        uncertainty=round(unc, 3),
        model_version=used_version,
        quality_tag=quality_tag,
        method=used_method,
        features=features,
        notes=notes,
        training_set_size=int(training_set_size),
        bootstrap=boot,
    )


@dataclass
class TcLambdaFilterResult:
    """Outcome of surrogate pre-filter."""

    kept: list[StructureCandidate] = field(default_factory=list)
    rejected: list[StructureCandidate] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    predictions: dict[str, TcLambdaPrediction] = field(default_factory=dict)
    """candidate_id → prediction (kept and rejected)."""

    config_version: str = MODEL_VERSION

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)

    def summary(self) -> dict[str, Any]:
        return {
            "n_kept": self.n_kept,
            "n_rejected": self.n_rejected,
            "model_version": self.config_version,
            "reasons": dict(self.reasons),
            "predictions": {
                cid: p.model_dump(mode="json") for cid, p in self.predictions.items()
            },
        }


class TcLambdaSurrogate:
    """Predict + optional pre-filter using :func:`predict_tc_lambda`."""

    def __init__(
        self,
        config: TcLambdaSurrogateConfig | None = None,
        *,
        family_stats: Mapping[str, Mapping[str, Any]] | None = None,
        model_version: str | None = None,
        method: str | None = None,
        training_set_size: int = 0,
        bootstrap: bool = True,
    ) -> None:
        self.config = config or TcLambdaSurrogateConfig()
        self.family_stats = family_stats
        self.model_version = model_version
        self.method = method
        self.training_set_size = training_set_size
        self.bootstrap = bootstrap

    def predict(self, candidate: StructureCandidate) -> TcLambdaPrediction:
        return predict_tc_lambda(
            candidate,
            mu_star=self.config.mu_star,
            family_stats=self.family_stats,
            model_version=self.model_version or self.config.version,
            method=self.method,
            training_set_size=self.training_set_size,
            bootstrap=self.bootstrap,
        )

    def annotate(self, candidate: StructureCandidate) -> tuple[StructureCandidate, TcLambdaPrediction]:
        """Attach surrogate prediction to candidate metadata."""
        pred = self.predict(candidate)
        meta = dict(candidate.metadata)
        meta["tc_lambda_surrogate"] = pred.model_dump(mode="json")
        meta["tc_lambda_surrogate_version"] = pred.model_version
        return (
            candidate.model_copy(update={"metadata": meta}),
            pred,
        )

    def filter(
        self,
        candidates: list[StructureCandidate],
    ) -> TcLambdaFilterResult:
        """Annotate all candidates; optionally drop low-Tc / high-unc / outside top-k."""
        cfg = self.config
        version = self.model_version or cfg.version or MODEL_VERSION
        result = TcLambdaFilterResult(config_version=version)

        annotated: list[tuple[StructureCandidate, TcLambdaPrediction]] = []
        for cand in candidates:
            c, pred = self.annotate(cand)
            result.predictions[c.candidate_id] = pred
            annotated.append((c, pred))

        # Apply filters only when enabled
        if not cfg.enabled:
            result.kept = [c for c, _ in annotated]
            return result

        survivors: list[tuple[StructureCandidate, TcLambdaPrediction]] = []
        for c, pred in annotated:
            if cfg.min_predicted_tc_K is not None and pred.predicted_Tc < cfg.min_predicted_tc_K:
                result.rejected.append(c)
                result.reasons[c.candidate_id] = (
                    f"predicted_Tc={pred.predicted_Tc} < min {cfg.min_predicted_tc_K}"
                )
                continue
            if cfg.max_uncertainty is not None and pred.uncertainty > cfg.max_uncertainty:
                result.rejected.append(c)
                result.reasons[c.candidate_id] = (
                    f"uncertainty={pred.uncertainty} > max {cfg.max_uncertainty}"
                )
                continue
            survivors.append((c, pred))

        # Always rank by surrogate score when the filter is enabled (not only
        # when keep_top_n truncates) so calculator order is score-ordered.
        survivors.sort(key=lambda x: x[1].score_for_ranking(), reverse=True)

        if cfg.keep_top_n is not None and len(survivors) > cfg.keep_top_n:
            for c, drop_p in survivors[cfg.keep_top_n :]:
                result.rejected.append(c)
                result.reasons[c.candidate_id] = (
                    f"outside keep_top_n={cfg.keep_top_n} "
                    f"(score={drop_p.score_for_ranking():.2f})"
                )
            survivors = survivors[: cfg.keep_top_n]

        result.kept = [c for c, _ in survivors]
        return result


def filter_by_tc_lambda(
    candidates: list[StructureCandidate],
    config: TcLambdaSurrogateConfig | None = None,
) -> TcLambdaFilterResult:
    """Convenience wrapper around :class:`TcLambdaSurrogate.filter`."""
    return TcLambdaSurrogate(config).filter(candidates)
