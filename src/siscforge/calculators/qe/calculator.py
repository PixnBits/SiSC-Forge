"""QECalculator — Calculator protocol for Quantum ESPRESSO (+ optional EPW)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from siscforge import __version__
from siscforge.calculators.base import BaseCalculator
from siscforge.calculators.qe.eliashberg import performance_score_from_epw
from siscforge.calculators.qe.env import QENotAvailableError, require_qe
from siscforge.calculators.qe.epw_recipes import run_relax_scf_phonon_epw
from siscforge.calculators.qe.inputs import candidate_to_structure
from siscforge.calculators.qe.recipes import run_relax_scf_phonon
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility

__all__ = ["QECalculator", "QENotAvailableError", "QEEpwCalculator"]


def _merge_dft_config(
    base: DFTConfig | None,
    kwargs: dict[str, Any],
) -> DFTConfig:
    """Build a DFTConfig from an optional base plus calculator kwargs."""
    data: dict[str, Any] = base.model_dump() if base is not None else {}
    if "dft" in kwargs and isinstance(kwargs["dft"], DFTConfig):
        data.update(kwargs["dft"].model_dump())
    elif "dft" in kwargs and isinstance(kwargs["dft"], dict):
        data.update(kwargs["dft"])
    for key in DFTConfig.model_fields:
        if key in kwargs and key != "dft":
            data[key] = kwargs[key]
    return DFTConfig.model_validate(data)


class QECalculator(BaseCalculator):
    """Run relax → SCF → phonon (+ optional EPW) via Quantum ESPRESSO.

    Registration names: ``qe``, ``quantum-espresso``.
    Enable EPW with ``dft.do_epw: true`` / ``dft.epw.enabled: true``, or use
    :class:`QEEpwCalculator` (``qe-epw``).
    """

    name = "qe"
    force_epw: bool = False

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
        *,
        force_epw: bool = False,
    ) -> None:
        self.dft = dft or DFTConfig(engine="qe")
        self.work_root = Path(work_root) if work_root else Path("qe_work")
        self.force_epw = force_epw

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        """Execute the QE (+ optional EPW) workflow for *candidate*."""
        dft = _merge_dft_config(self.dft, kwargs)
        want_epw = self.force_epw or dft.do_epw or dft.epw.enabled
        if want_epw:
            dft = dft.model_copy(
                update={
                    "engine": "qe-epw",
                    "do_epw": True,
                    "epw": dft.epw.model_copy(update={"enabled": True}),
                }
            )
        else:
            dft = dft.model_copy(update={"engine": "qe"})

        need_ph = dft.do_phonon
        qe_env = require_qe(need_phonon=need_ph, need_epw=want_epw)

        try:
            structure = candidate_to_structure(candidate)
        except ValueError as exc:
            raise ValueError(
                f"Cannot build QE structure for {candidate.formula} "
                f"({candidate.candidate_id}): {exc}"
            ) from exc

        work_root = Path(
            kwargs.get("work_dir") or dft.work_dir or self.work_root
        ).expanduser()
        # Prefer a short absolute work root: long paths + Ubuntu QE 6.7 ph.x are
        # fragile; keep campaign outputs separate from heavy scratch when possible.
        if not work_root.is_absolute():
            work_root = (Path.cwd() / work_root).resolve()
        # Compact candidate directory names (formula + short id)
        short_id = candidate.candidate_id.replace("-", "")[:8]
        safe_formula = "".join(ch if ch.isalnum() else "" for ch in candidate.formula)[
            :12
        ] or "cand"
        cand_dir = work_root / f"{safe_formula}_{short_id}"
        cand_dir.mkdir(parents=True, exist_ok=True)

        from siscforge.calculators.qe.pseudos import (
            PseudoResolutionError,
            resolve_pseudopotentials,
        )

        try:
            resolved_pseudos = resolve_pseudopotentials(structure, dft)
        except PseudoResolutionError as exc:
            raise FileNotFoundError(str(exc)) from exc

        # Short prefix — some QE builds have tight internal path buffers
        prefix = f"s{short_id}"
        # Mid-step QE resume: reuse workdir checkpoints unless force_rerun*
        run_cfg = kwargs.get("run_config")
        resume_qe = kwargs.get("resume_qe_steps")
        force_qe = kwargs.get("force_qe_steps")
        if run_cfg is not None:
            # Attach for recipe helpers that read config._run_config
            dft.__dict__["_run_config"] = run_cfg
            if force_qe is None:
                force_qe = bool(
                    getattr(run_cfg, "force_rerun", False)
                    or getattr(run_cfg, "force_rerun_qe_steps", False)
                )
            if resume_qe is None:
                resume_qe = bool(
                    getattr(run_cfg, "resume_qe_steps", True)
                ) and not bool(force_qe)
        if force_qe is None:
            force_qe = False
        if resume_qe is None:
            resume_qe = not force_qe

        step_log: list[str] = []
        if want_epw:
            wf = run_relax_scf_phonon_epw(
                structure,
                dft,
                cand_dir,
                prefix=prefix,
                qe_env=qe_env,
                resume_qe_steps=resume_qe,
                force_qe_steps=force_qe,
                step_log=step_log,
            )
        else:
            base = run_relax_scf_phonon(
                structure,
                dft,
                cand_dir,
                prefix=prefix,
                qe_env=qe_env,
                resume_qe_steps=resume_qe,
                force_qe_steps=force_qe,
                step_log=step_log,
            )
            from siscforge.calculators.qe.epw_recipes import EPWWorkflowResult

            wf = EPWWorkflowResult(
                work_dir=base.work_dir,
                structure=base.structure,
                scf=base.scf,
                phonon=base.phonon,
                steps=list(base.steps),
                relaxed_structure=base.relaxed_structure,
                success=base.success,
                message=base.message,
            )

        precomputed = kwargs.get("si_feasibility")
        if isinstance(precomputed, SiFeasibilityScore):
            si = precomputed
        else:
            si = score_si_feasibility(candidate)

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
                            "do_epw": want_epw,
                        },
                    }
                )
            except Exception:  # noqa: BLE001
                out_candidate = candidate.model_copy(
                    update={
                        "metadata": {
                            **candidate.metadata,
                            "pseudos": resolved_pseudos,
                            "do_epw": want_epw,
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
                        "do_epw": want_epw,
                    },
                }
            )

        status = "ok" if wf.success else "failed"
        notes_parts = [wf.message]
        if not wf.success:
            notes_parts.append(
                "QE workflow did not fully succeed; see step logs under " + str(cand_dir)
            )
            # Attach EPW/Wannier diagnostic block for shortlist continue-on-error
            try:
                from siscforge.calculators.qe.epw_recipes import diagnose_epw_failure

                diag_src = wf.message or ""
                for step in reversed(list(wf.steps)):
                    if step.stdout_path and Path(step.stdout_path).is_file():
                        try:
                            diag_src = (
                                Path(step.stdout_path)
                                .read_text(encoding="utf-8", errors="replace")[-2000:]
                            )
                            break
                        except OSError:
                            continue
                notes_parts.append(
                    diagnose_epw_failure(
                        diag_src, work_dir=cand_dir, step_name="qe_workflow"
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        notes_parts.append(f"quality_tag={dft.quality_tag}")
        notes_parts.append(f"phonon_method={dft.phonon_method}")
        notes_parts.append(f"do_epw={want_epw}")
        notes_parts.append(f"work_dir={cand_dir}")
        if step_log:
            notes_parts.append("qe_steps: " + "; ".join(step_log))

        scf = wf.scf
        phonon = wf.phonon
        eph = getattr(wf, "electron_phonon", None)
        if scf is not None and scf.quality_tag != dft.quality_tag:
            scf = scf.model_copy(update={"quality_tag": dft.quality_tag})
        if phonon is not None and phonon.quality_tag != dft.quality_tag:
            phonon = phonon.model_copy(update={"quality_tag": dft.quality_tag})
        if eph is not None and eph.quality_tag != dft.quality_tag:
            eph = eph.model_copy(update={"quality_tag": dft.quality_tag})

        performance = getattr(wf, "performance_score", None)
        if performance is None and eph is not None:
            performance = performance_score_from_epw(eph.best_tc_K())

        err_list: list[str] = []
        if not wf.success:
            err_list.append(wf.message)
            err_list.append(f"work_dir={cand_dir}")

        return CandidateEvaluation(
            candidate=out_candidate,
            scf=scf,
            phonon=phonon,
            electron_phonon=eph,
            si_feasibility=si,
            performance_score=performance,
            composite_score=None,
            status=status,
            calculator_name=self.name,
            errors=err_list,
            notes="; ".join(notes_parts),
            provenance=Provenance(
                source="qe_calculator",
                software={
                    "siscforge": __version__,
                    "pw.x": qe_env.pw or "",
                    "ph.x": qe_env.ph or "",
                    "epw.x": qe_env.epw or "",
                },
                parameters={
                    "dft": dft.model_dump(mode="json"),
                    "work_dir": str(cand_dir),
                    "steps": [s.name for s in wf.steps],
                    "pseudos": resolved_pseudos,
                    "relaxed": wf.relaxed_structure is not None,
                    "do_epw": want_epw,
                },
                parent_ids=[candidate.candidate_id],
                notes="QE relax/SCF/phonon" + ("/EPW" if want_epw else "") + " evaluation",
            ),
        )


class QEEpwCalculator(QECalculator):
    """QE calculator that always enables the EPW step."""

    name = "qe-epw"
    force_epw = True

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        super().__init__(dft=dft, work_root=work_root, force_epw=True)
        if self.dft.engine == "mock":
            self.dft = self.dft.model_copy(update={"engine": "qe-epw", "do_epw": True})


def register_qe_calculators() -> None:
    """Register ``qe``, ``quantum-espresso``, and ``qe-epw`` aliases."""
    from siscforge.calculators import registry

    calc = QECalculator()
    registry.register(calc, name="qe", overwrite=True)
    registry.register(calc, name="quantum-espresso", overwrite=True)
    registry.register(QEEpwCalculator(), name="qe-epw", overwrite=True)
    registry.register(QEEpwCalculator(), name="epw", overwrite=True)
