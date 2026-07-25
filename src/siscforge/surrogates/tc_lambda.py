"""Lightweight λ / ω_log / Tc surrogate stub (Phase 1).

**Not** a trained production GNN. Family-aware heuristics anchored on known
references (NbN, MgB₂, TiN, …) provide:

- predicted_lambda
- predicted_omega_log (K)
- predicted_Tc (Allen–Dynes from those moments)
- uncertainty (relative, 0–1 scale; higher = less trusted)

Real EPW remains the source of truth. This module only pre-filters and, when
no ElectronPhononResult exists, may supply a weak ranking signal.

Future ALIGNN/MatGL heads should implement the same :class:`TcLambdaPrediction`
shape without changing campaign YAML keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

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
    quality_tag: Literal["stub", "screening", "production"] = "stub"
    method: str = "family_heuristic"
    """Implementation tag; future models use e.g. ``alignn_head``."""

    features: dict[str, Any] = Field(default_factory=dict)
    """Cheap features used for the prediction (for audit / debugging)."""

    notes: str = (
        "Phase-1 stub surrogate — not trained on large EPW data. "
        "Real EPW ElectronPhononResult overrides this when present."
    )

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


def predict_tc_lambda(
    candidate: StructureCandidate,
    *,
    mu_star: float = 0.10,
) -> TcLambdaPrediction:
    """Predict λ, ω_log, Tc and uncertainty for *candidate*.

    Deterministic, pure-Python, no network / GPU. Safe for unit tests and CI.
    """
    family = candidate.material_family
    formula = _normalize_formula(candidate.formula)
    base_lam, base_wlog, base_unc = _FAMILY_BASE.get(family, _FAMILY_BASE["other"])

    features: dict[str, Any] = {
        "family": family,
        "formula": formula,
        "strain": _strain_magnitude(candidate),
        "n_elements": len(candidate.composition) or 1,
    }

    if formula in _FORMULA_ANCHOR:
        lam, wlog, unc = _FORMULA_ANCHOR[formula]
        features["anchor"] = formula
    else:
        lam, wlog, unc = base_lam, base_wlog, base_unc
        # Soft ternary / multi-metal demotion for nitrides
        if family == "tm_nitride":
            n_met = _n_metal_like(candidate)
            features["n_metals"] = n_met
            if n_met >= 2:
                # Alloy: slightly lower λ, higher unc
                lam *= 0.92
                unc = min(1.0, unc + 0.08)
            # Prefer Nb-rich among common TM nitrides (very soft heuristic)
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
        # ~20% Tc drop at |ε|=0.05 (quadratic-ish via λ, wlog)
        factor = max(0.4, 1.0 - 8.0 * (strain**2))
        lam *= factor
        wlog *= max(0.7, 1.0 - 4.0 * (strain**2))
        unc = min(1.0, unc + 3.0 * strain)
        features["strain_factor"] = round(factor, 4)

    # Known unstable families for conventional e-ph: high unc
    if family in {"nickelate", "cuprate"}:
        features["conventional_proxy_only"] = True

    lam = max(0.05, float(lam))
    wlog = max(50.0, float(wlog))
    unc = float(min(1.0, max(0.05, unc)))
    tc = allen_dynes_tc(lam, wlog, mu_star)

    notes = (
        "Phase-1 family-heuristic stub (not a trained GNN). "
        "Use only for pre-filtering; real EPW overrides ranking when present."
    )
    if family == "mgb2_boride":
        notes += " MgB2: isotropic average of two-gap character."

    return TcLambdaPrediction(
        predicted_lambda=round(lam, 4),
        predicted_omega_log=round(wlog, 2),
        predicted_Tc=round(tc, 2),
        uncertainty=round(unc, 3),
        model_version=MODEL_VERSION,
        quality_tag="stub",
        method="family_heuristic",
        features=features,
        notes=notes,
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

    def __init__(self, config: TcLambdaSurrogateConfig | None = None) -> None:
        self.config = config or TcLambdaSurrogateConfig()

    def predict(self, candidate: StructureCandidate) -> TcLambdaPrediction:
        return predict_tc_lambda(candidate, mu_star=self.config.mu_star)

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
        result = TcLambdaFilterResult(config_version=cfg.version or MODEL_VERSION)

        annotated: list[tuple[StructureCandidate, TcLambdaPrediction]] = []
        for cand in candidates:
            c, pred = self.annotate(cand)
            result.predictions[c.candidate_id] = pred
            annotated.append((c, pred))

        if not cfg.enabled:
            result.kept = [c for c, _ in annotated]
            return result

        kept_pairs: list[tuple[StructureCandidate, TcLambdaPrediction]] = []
        for c, pred in annotated:
            if cfg.min_predicted_tc_K is not None and pred.predicted_Tc < cfg.min_predicted_tc_K:
                result.rejected.append(c)
                result.reasons[c.candidate_id] = (
                    f"surrogate_Tc {pred.predicted_Tc:.2f} K < "
                    f"min {cfg.min_predicted_tc_K}"
                )
                continue
            if (
                cfg.max_uncertainty is not None
                and pred.uncertainty > cfg.max_uncertainty
            ):
                result.rejected.append(c)
                result.reasons[c.candidate_id] = (
                    f"surrogate_uncertainty {pred.uncertainty:.3f} > "
                    f"max {cfg.max_uncertainty}"
                )
                continue
            kept_pairs.append((c, pred))

        # Prefer higher score_for_ranking (Tc demoted by unc)
        kept_pairs.sort(key=lambda pair: pair[1].score_for_ranking(), reverse=True)

        if cfg.keep_top_n is not None and len(kept_pairs) > cfg.keep_top_n:
            for drop_c, drop_p in kept_pairs[cfg.keep_top_n :]:
                result.rejected.append(drop_c)
                result.reasons[drop_c.candidate_id] = (
                    f"outside surrogate keep_top_n={cfg.keep_top_n} "
                    f"(score={drop_p.score_for_ranking():.2f})"
                )
            kept_pairs = kept_pairs[: cfg.keep_top_n]

        result.kept = [c for c, _ in kept_pairs]
        return result


def filter_by_tc_lambda(
    candidates: list[StructureCandidate],
    config: TcLambdaSurrogateConfig | None = None,
) -> TcLambdaFilterResult:
    """Convenience wrapper."""
    return TcLambdaSurrogate(config).filter(candidates)
