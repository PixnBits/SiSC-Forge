"""QECalculator — Calculator protocol implementation for Quantum ESPRESSO."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from siscforge import __version__
from siscforge.calculators.base import BaseCalculator
from siscforge.calculators.qe.env import QENotAvailableError, require_qe
from siscforge.calculators.qe.inputs import candidate_to_structure
from siscforge.calculators.qe.recipes import run_relax_scf_phonon
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility

# Re-export for callers that import from calculator
__all__ = ["QECalculator", "QENotAvailableError"]


def _merge_dft_config(
    base: DFTConfig | None,
    kwargs: dict[str, Any],
) -> DFTConfig:
    """Build a DFTConfig from an optional base plus calculator kwargs."""
    data: dict[str, Any] = base.model_dump() if base is not None else {}
    # Allow nested dft= or flat DFTConfig fields in kwargs
    if "dft" in kwargs and isinstance(kwargs["dft"], DFTConfig):
        data.update(kwargs["dft"].model_dump())
    elif "dft" in kwargs and isinstance(kwargs["dft"], dict):
        data.update(kwargs["dft"])
    for key in DFTConfig.model_fields:
        if key in kwargs and key != "dft":
            data[key] = kwargs[key]
    return DFTConfig.model_validate(data)


class QECalculator(BaseCalculator):
    """Run relax → SCF → phonon via Quantum ESPRESSO and return CandidateEvaluation.

    Registration names: ``qe`` and ``quantum-espresso``.

    Parameters
    ----------
    dft:
        Default DFT settings (overridden per-call via kwargs / campaign ``dft``).
    work_root:
        Root directory for per-candidate scratch folders.
    """

    name = "qe"

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        self.dft = dft or DFTConfig(engine="qe")
        self.work_root = Path(work_root) if work_root else Path("qe_work")

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        """Execute the QE workflow for *candidate*.

        Raises
        ------
        QENotAvailableError
            If ``pw.x`` (and ``ph.x`` when phonons are requested) is not found.
        FileNotFoundError
            If pseudopotentials cannot be resolved.
        ValueError
            If the candidate lacks a usable structure (CIF).
        """
        dft = _merge_dft_config(self.dft, kwargs)
        dft = dft.model_copy(update={"engine": "qe"})
        need_ph = dft.do_phonon
        qe_env = require_qe(need_phonon=need_ph)

        structure = candidate_to_structure(candidate)
        work_root = Path(kwargs.get("work_dir") or dft.work_dir or self.work_root)
        cand_dir = work_root / f"{candidate.formula}_{candidate.candidate_id[:8]}"
        cand_dir.mkdir(parents=True, exist_ok=True)

        prefix = f"sf_{candidate.candidate_id[:8]}"
        wf = run_relax_scf_phonon(
            structure,
            dft,
            cand_dir,
            prefix=prefix,
            qe_env=qe_env,
        )

        precomputed = kwargs.get("si_feasibility")
        if isinstance(precomputed, SiFeasibilityScore):
            si = precomputed
        else:
            si = score_si_feasibility(candidate)

        status = "ok" if wf.success else "failed"
        notes_parts = [wf.message]
        if not wf.success:
            notes_parts.append(
                "QE workflow did not fully succeed; see step logs under " + str(cand_dir)
            )

        # Performance proxy: not available from phonon alone in Phase 0 —
        # leave None (ranking falls back to neutral performance).
        return CandidateEvaluation(
            candidate=candidate,
            scf=wf.scf,
            phonon=wf.phonon,
            si_feasibility=si,
            performance_score=None,
            composite_score=None,
            status=status,
            calculator_name=self.name,
            errors=[] if wf.success else [wf.message],
            notes="; ".join(notes_parts),
            provenance=Provenance(
                source="qe_calculator",
                software={
                    "siscforge": __version__,
                    "pw.x": qe_env.pw or "",
                    "ph.x": qe_env.ph or "",
                },
                parameters={
                    "dft": dft.model_dump(mode="json"),
                    "work_dir": str(cand_dir),
                    "steps": [s.name for s in wf.steps],
                },
                parent_ids=[candidate.candidate_id],
                notes="QE relax/SCF/phonon evaluation",
            ),
        )


def register_qe_calculators() -> None:
    """Register ``qe`` and ``quantum-espresso`` aliases in the global registry."""
    from siscforge.calculators import registry

    calc = QECalculator()
    registry.register(calc, name="qe", overwrite=True)
    registry.register(calc, name="quantum-espresso", overwrite=True)
