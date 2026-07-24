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

        try:
            structure = candidate_to_structure(candidate)
        except ValueError as exc:
            raise ValueError(
                f"Cannot build QE structure for {candidate.formula} "
                f"({candidate.candidate_id}): {exc}"
            ) from exc

        work_root = Path(kwargs.get("work_dir") or dft.work_dir or self.work_root)
        cand_dir = work_root / f"{candidate.formula}_{candidate.candidate_id[:8]}"
        cand_dir.mkdir(parents=True, exist_ok=True)

        # Validate pseudos early with a clear error
        from siscforge.calculators.qe.pseudos import (
            PseudoResolutionError,
            resolve_pseudopotentials,
        )

        try:
            resolved_pseudos = resolve_pseudopotentials(structure, dft)
        except PseudoResolutionError as exc:
            raise FileNotFoundError(str(exc)) from exc

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

        # Attach relaxed geometry to the candidate when available
        out_candidate = candidate
        if wf.relaxed_structure is not None:
            try:
                relaxed_cif = wf.relaxed_structure.to(fmt="cif")
                lat = wf.relaxed_structure.lattice
                out_candidate = candidate.model_copy(
                    update={
                        "relaxed_structure_cif": relaxed_cif,
                        "structure_cif": relaxed_cif,
                        "lattice_abc": (float(lat.a), float(lat.b), float(lat.c)),
                        "lattice_angles": (
                            float(lat.alpha),
                            float(lat.beta),
                            float(lat.gamma),
                        ),
                        "quality_tag": dft.quality_tag,
                        "metadata": {
                            **candidate.metadata,
                            "relaxed": True,
                            "pseudos": resolved_pseudos,
                            "phonon_method": dft.phonon_method,
                        },
                    }
                )
            except Exception:  # noqa: BLE001
                out_candidate = candidate.model_copy(
                    update={
                        "metadata": {
                            **candidate.metadata,
                            "pseudos": resolved_pseudos,
                            "phonon_method": dft.phonon_method,
                        },
                        "quality_tag": dft.quality_tag,
                    }
                )
        else:
            out_candidate = candidate.model_copy(
                update={
                    "quality_tag": dft.quality_tag,
                    "metadata": {
                        **candidate.metadata,
                        "pseudos": resolved_pseudos,
                        "phonon_method": dft.phonon_method,
                    },
                }
            )

        status = "ok" if wf.success else "failed"
        notes_parts = [wf.message]
        if not wf.success:
            notes_parts.append(
                "QE workflow did not fully succeed; see step logs under " + str(cand_dir)
            )
        notes_parts.append(f"quality_tag={dft.quality_tag}")
        notes_parts.append(f"phonon_method={dft.phonon_method}")

        # Ensure quality tags on results match campaign setting
        scf = wf.scf
        phonon = wf.phonon
        if scf is not None and scf.quality_tag != dft.quality_tag:
            scf = scf.model_copy(update={"quality_tag": dft.quality_tag})
        if phonon is not None and phonon.quality_tag != dft.quality_tag:
            phonon = phonon.model_copy(update={"quality_tag": dft.quality_tag})

        return CandidateEvaluation(
            candidate=out_candidate,
            scf=scf,
            phonon=phonon,
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
                    "pseudos": resolved_pseudos,
                    "relaxed": wf.relaxed_structure is not None,
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
