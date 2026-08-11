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
from siscforge.calculators.qe.recipes import run_dftu_workflow, run_relax_scf_phonon
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility

__all__ = ["QECalculator", "QENotAvailableError", "QEEpwCalculator", "QEDftuCalculator"]


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
    """Run relax → SCF → phonon (+ optional EPW / DFT+U) via Quantum ESPRESSO.

    Registration names: ``qe``, ``quantum-espresso``.
    Enable EPW with ``dft.do_epw: true`` / ``dft.epw.enabled: true``, or use
    :class:`QEEpwCalculator` (``qe-epw``).
    Enable DFT+U with ``dft.do_dftu: true`` / ``dft.dftu.enabled: true``, or
    use :class:`QEDftuCalculator` (``qe-dftu``). DFT+U is inert by default.
    """

    name = "qe"
    force_epw: bool = False
    force_dftu: bool = False

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
        *,
        force_epw: bool = False,
        force_dftu: bool = False,
    ) -> None:
        self.dft = dft or DFTConfig(engine="qe")
        self.work_root = Path(work_root) if work_root else Path("qe_work")
        self.force_epw = force_epw
        self.force_dftu = force_dftu

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        """Execute the QE (+ optional EPW / DFT+U) workflow for *candidate*."""
        from siscforge.calculators.qe.dftu import dftu_is_enabled

        dft = _merge_dft_config(self.dft, kwargs)
        want_epw = self.force_epw or dft.do_epw or dft.epw.enabled
        want_dftu = dftu_is_enabled(dft, force=self.force_dftu)
        if want_dftu:
            dft = dft.model_copy(
                update={
                    "do_dftu": True,
                    "dftu": dft.dftu.model_copy(update={"enabled": True}),
                }
            )
        if want_epw:
            dft = dft.model_copy(
                update={
                    "engine": "qe-epw",
                    "do_epw": True,
                    "epw": dft.epw.model_copy(update={"enabled": True}),
                }
            )
        elif self.force_dftu and want_dftu and not dft.do_phonon:
            # Pure DFT+U only for the forced qe-dftu calculator (not additive qe)
            dft = dft.model_copy(update={"engine": "qe-dftu"})
        else:
            dft = dft.model_copy(update={"engine": "qe" if not want_epw else dft.engine})

        need_ph = bool(dft.do_phonon)
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
        # DFT+U-only workflow is reserved for calculator qe-dftu (force_dftu).
        # Enabling do_dftu on plain qe remains additive (conventional then DFT+U).
        dftu_only = bool(
            want_dftu and not want_epw and self.force_dftu and not dft.do_phonon
        )
        if dftu_only:
            base = run_dftu_workflow(
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
            # stash dftu on a side channel for evaluation assembly
            wf.__dict__["_dftu_result"] = base.dftu
        elif want_epw:
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
            if want_dftu:
                dftu_dir = cand_dir / "dftu"
                # Conventional path already relaxed (if configured). Only re-relax
                # under U when dftu.do_relax_with_u is explicitly enabled.
                dft_u = dft
                if not dft.dftu.do_relax_with_u:
                    dft_u = dft.model_copy(update={"do_relax": False})
                dftu_base = run_dftu_workflow(
                    structure if wf.relaxed_structure is None else wf.relaxed_structure,
                    dft_u,
                    dftu_dir,
                    prefix=prefix,
                    qe_env=qe_env,
                    resume_qe_steps=resume_qe,
                    force_qe_steps=force_qe,
                    step_log=step_log,
                )
                wf.__dict__["_dftu_result"] = dftu_base.dftu
                wf.steps = list(wf.steps) + list(dftu_base.steps)
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
            if want_dftu:
                dftu_dir = cand_dir / "dftu"
                struct_for_u = base.relaxed_structure or structure
                # Conventional path already relaxed (if configured). Only re-relax
                # under U when dftu.do_relax_with_u is explicitly enabled.
                dft_u = dft
                if not dft.dftu.do_relax_with_u:
                    dft_u = dft.model_copy(update={"do_relax": False})
                dftu_base = run_dftu_workflow(
                    struct_for_u,
                    dft_u,
                    dftu_dir,
                    prefix=prefix,
                    qe_env=qe_env,
                    resume_qe_steps=resume_qe,
                    force_qe_steps=force_qe,
                    step_log=step_log,
                )
                wf.__dict__["_dftu_result"] = dftu_base.dftu
                wf.steps = list(wf.steps) + list(dftu_base.steps)
                # Additive DFT+U should not fail the whole eval if conventional ok
                if not dftu_base.success:
                    step_log.append(
                        f"DFT+U failed (conventional path kept): {dftu_base.message}"
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
        from siscforge.calculators.qe.epw_recipes import (
            diagnose_qe_step_failure,
            extract_primary_failure_reason,
            truncate_for_notes,
        )

        notes_parts: list[str] = []
        # Phonon-only campaigns must use phonon diagnose (never EPW k-grid labels)
        fail_step = "phonon" if (not want_epw and dft.do_phonon) else "qe"
        if not wf.success:
            # Short primary first so CLI / notes never bury QE errors under Errno 36
            primary = extract_primary_failure_reason(
                wf.message or "", step_name=fail_step
            )
            notes_parts.append(primary)
            notes_parts.append(
                "QE workflow did not fully succeed; see step logs under "
                + str(cand_dir)
            )
            try:
                diag_src = wf.message or ""
                for step in reversed(list(wf.steps)):
                    sp = getattr(step, "stdout_path", None)
                    if sp is not None and Path(sp).is_file():
                        try:
                            # Fixed path only — never open a log blob as a path
                            diag_src = Path(sp).read_text(
                                encoding="utf-8", errors="replace"
                            )
                            # Prefer last 8 KiB for classification, not the full multi-MB log
                            if len(diag_src) > 8192:
                                diag_src = diag_src[-8192:]
                            break
                        except OSError:
                            continue
                notes_parts.append(
                    diagnose_qe_step_failure(
                        diag_src,
                        work_dir=cand_dir,
                        step_name=fail_step,
                    )
                )
            except Exception:  # noqa: BLE001
                pass
            notes_parts.append(truncate_for_notes(wf.message, max_chars=800))
            # Setup failures are not stability conclusions
            if "fft" in primary.lower() or "phq_setup" in primary.lower():
                notes_parts.append(
                    "phonon setup failure — not a dynamical-stability conclusion "
                    "(stable_only shortlist ignores this candidate)"
                )
        else:
            notes_parts.append(wf.message or "ok")
        notes_parts.append(f"quality_tag={dft.quality_tag}")
        notes_parts.append(f"phonon_method={dft.phonon_method}")
        notes_parts.append(f"do_epw={want_epw}")
        notes_parts.append(f"work_dir={cand_dir}")
        if step_log:
            notes_parts.append("qe_steps: " + "; ".join(step_log))
            if any("fft_symmetry retry" in s or "nosym" in s for s in step_log):
                if any("succeeded" in s for s in step_log):
                    notes_parts.append(
                        "phonon recovered via nosym+noinv SCF/PH retry"
                    )

        scf = wf.scf
        phonon = wf.phonon
        eph = getattr(wf, "electron_phonon", None)
        if scf is not None and scf.quality_tag != dft.quality_tag:
            scf = scf.model_copy(update={"quality_tag": dft.quality_tag})
        if phonon is not None and phonon.quality_tag != dft.quality_tag:
            phonon = phonon.model_copy(update={"quality_tag": dft.quality_tag})
        if eph is not None and eph.quality_tag != dft.quality_tag:
            eph = eph.model_copy(update={"quality_tag": dft.quality_tag})
        # Never advertise stability from a failed / incomplete phonon setup
        if phonon is not None and phonon.status not in {"ok", "mock"}:
            phonon = phonon.model_copy(
                update={
                    "dynamically_stable": False,
                    "has_imaginary_modes": False,
                }
            )

        performance = getattr(wf, "performance_score", None)
        if performance is None and eph is not None:
            performance = performance_score_from_epw(eph.best_tc_K())

        err_list: list[str] = []
        if not wf.success:
            err_list.append(
                extract_primary_failure_reason(
                    wf.message or "", step_name=fail_step
                )
            )
            err_list.append(f"work_dir={cand_dir}")
            err_list.append(truncate_for_notes(wf.message, max_chars=600))

        dftu_result = getattr(wf, "_dftu_result", None)
        if dftu_result is not None:
            notes_parts.append(f"dftu={dftu_result.summary_line()}")
        notes_parts.append(f"do_dftu={want_dftu}")

        return CandidateEvaluation(
            candidate=out_candidate,
            scf=scf,
            phonon=phonon,
            electron_phonon=eph,
            dftu=dftu_result,
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
                    "do_dftu": want_dftu,
                },
                parent_ids=[candidate.candidate_id],
                notes=(
                    "QE relax/SCF/phonon"
                    + ("/EPW" if want_epw else "")
                    + ("/DFT+U" if want_dftu else "")
                    + " evaluation"
                ),
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


class QEDftuCalculator(QECalculator):
    """QE calculator focused on DFT+U (Hubbard) SCF for correlated proxies.

    Registration name: ``qe-dftu``. Forces ``do_dftu``; defaults phonon/EPW off
    unless the campaign explicitly re-enables them. Does not require Wannier90
    or TRIQS (those arrive in P3.2 / P3.3).
    """

    name = "qe-dftu"
    force_dftu = True

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        base = dft or DFTConfig(engine="qe-dftu", do_phonon=False, do_epw=False)
        if not base.do_dftu:
            base = base.model_copy(
                update={
                    "do_dftu": True,
                    "dftu": base.dftu.model_copy(update={"enabled": True}),
                    "engine": "qe-dftu",
                }
            )
        super().__init__(dft=base, work_root=work_root, force_dftu=True)


def register_qe_calculators() -> None:
    """Register ``qe``, ``quantum-espresso``, ``qe-epw``, and ``qe-dftu`` aliases."""
    from siscforge.calculators import registry

    calc = QECalculator()
    registry.register(calc, name="qe", overwrite=True)
    registry.register(calc, name="quantum-espresso", overwrite=True)
    registry.register(QEEpwCalculator(), name="qe-epw", overwrite=True)
    registry.register(QEEpwCalculator(), name="epw", overwrite=True)
    registry.register(QEDftuCalculator(), name="qe-dftu", overwrite=True)
    registry.register(QEDftuCalculator(), name="dftu", overwrite=True)
