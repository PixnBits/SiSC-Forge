"""QECalculator — Calculator protocol for Quantum ESPRESSO (+ optional EPW)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from siscforge import __version__
from siscforge.calculators.base import BaseCalculator
from siscforge.calculators.qe.eliashberg import performance_score_from_epw
from siscforge.calculators.qe.env import QENotAvailableError, require_qe
from siscforge.calculators.qe.epw_recipes import (
    EPW_BLOCKED_SOFT_TOKEN,
    run_relax_scf_phonon_epw,
)
from siscforge.calculators.qe.inputs import candidate_to_structure
from siscforge.calculators.qe.recipes import run_dftu_workflow, run_relax_scf_phonon
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility

_LOG = logging.getLogger(__name__)

__all__ = [
    "QECalculator",
    "QENotAvailableError",
    "QEEpwCalculator",
    "QEDftuCalculator",
    "QEWannierCalculator",
    "QEDmftCalculator",
]


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
    Enable standalone Wannier (P3.2 / P3.2.1) with ``dft.do_wannier: true`` /
    ``dft.wannier.enabled: true``, or :class:`QEWannierCalculator`
    (``qe-wannier``). Does not alter EPW-internal Wannier.
    Enable DMFT (P3.3) with ``dft.do_dmft: true`` / ``dft.dmft.enabled: true``,
    or :class:`QEDmftCalculator` (``qe-dmft``). Inert by default.
    """

    name = "qe"
    force_epw: bool = False
    force_dftu: bool = False
    force_wannier: bool = False
    force_dmft: bool = False

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
        *,
        force_epw: bool = False,
        force_dftu: bool = False,
        force_wannier: bool = False,
        force_dmft: bool = False,
    ) -> None:
        self.dft = dft or DFTConfig(engine="qe")
        self.work_root = Path(work_root) if work_root else Path("qe_work")
        self.force_epw = force_epw
        self.force_dftu = force_dftu
        self.force_wannier = force_wannier
        self.force_dmft = force_dmft

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        """Execute the QE (+ optional EPW / DFT+U) workflow for *candidate*."""
        from siscforge.calculators.qe.dftu import dftu_is_enabled
        from siscforge.calculators.qe.dmft import dmft_is_enabled
        from siscforge.calculators.qe.wannier import wannier_is_enabled

        dft = _merge_dft_config(self.dft, kwargs)
        want_epw = self.force_epw or dft.do_epw or dft.epw.enabled
        want_dftu = dftu_is_enabled(dft, force=self.force_dftu)
        want_wannier = wannier_is_enabled(dft, force=self.force_wannier)
        want_dmft = dmft_is_enabled(dft, force=self.force_dmft)
        if want_dftu:
            dft = dft.model_copy(
                update={
                    "do_dftu": True,
                    "dftu": dft.dftu.model_copy(update={"enabled": True}),
                }
            )
        if want_wannier:
            dft = dft.model_copy(
                update={
                    "do_wannier": True,
                    "wannier": dft.wannier.model_copy(update={"enabled": True}),
                }
            )
        if want_dmft:
            dft = dft.model_copy(
                update={
                    "do_dmft": True,
                    "dmft": dft.dmft.model_copy(update={"enabled": True}),
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
                dftu=base.dftu,
                steps=list(base.steps),
                relaxed_structure=base.relaxed_structure,
                success=base.success,
                message=base.message,
            )
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
                # under U when dftu.do_relax_with_u is explicitly enabled — and
                # force do_relax=True in that case so the stage is entered even
                # when the campaign-level do_relax is False.
                if dft.dftu.do_relax_with_u:
                    dft_u = dft.model_copy(update={"do_relax": True})
                else:
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
                wf.dftu = dftu_base.dftu
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
                # under U when dftu.do_relax_with_u is explicitly enabled — and
                # force do_relax=True in that case so the stage is entered even
                # when the campaign-level do_relax is False.
                if dft.dftu.do_relax_with_u:
                    dft_u = dft.model_copy(update={"do_relax": True})
                else:
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
                wf.dftu = dftu_base.dftu
                wf.steps = list(wf.steps) + list(dftu_base.steps)
                # Additive DFT+U should not fail the whole eval if conventional ok
                if not dftu_base.success:
                    step_log.append(
                        f"DFT+U failed (conventional path kept): {dftu_base.message}"
                    )

        # P3.2: additive Wannierization after SCF / DFT+U.
        # Sacred upstream: Wannier failures never delete finished SCF/DFT+U.
        # Soft-phonon EPW block also skips Wannier / DMFT follow-ons (#52).
        epw_soft_blocked = EPW_BLOCKED_SOFT_TOKEN in (wf.message or "")
        if want_wannier and not epw_soft_blocked:
            from siscforge.calculators.qe.recipes import run_wannier_after_scf

            wannier_dir = cand_dir / "wannier"
            struct_for_w = wf.relaxed_structure or structure
            fermi = None
            if wf.scf is not None:
                fermi = getattr(wf.scf, "fermi_energy_eV", None) or (
                    (wf.scf.raw or {}).get("fermi_energy_eV")
                    if getattr(wf.scf, "raw", None)
                    else None
                )
            if fermi is None and getattr(wf, "dftu", None) is not None:
                fermi = getattr(wf.dftu, "fermi_energy_eV", None)
            try:
                wres = run_wannier_after_scf(
                    struct_for_w,
                    dft,
                    wannier_dir,
                    prefix=prefix,
                    fermi_eV=fermi,
                    qe_env=qe_env,
                    scf_work_dir=cand_dir,
                    step_log=step_log,
                )
                wf.wannier = wres
                if not wres.wannier_ok:
                    step_log.append(
                        "Wannier failed (upstream SCF/DFT+U kept): "
                        + wres.summary_line()
                    )
            except Exception as exc:  # noqa: BLE001 — never destroy upstream
                from siscforge.calculators.qe.wannier import (
                    primary_wannier_failure_reason,
                )
                from siscforge.models.results import WannierResult

                _LOG.exception(
                    "Wannier step failed (upstream preserved) work_dir=%s",
                    wannier_dir,
                )
                step_log.append(f"Wannier exception (upstream kept): {exc}")
                wf.wannier = WannierResult(
                    wannier_ok=False,
                    ready_for_dmft=False,
                    dmft_gate_notes=f"not ready for DMFT: {exc}",
                    status="failed",
                    quality_tag=dft.quality_tag,
                    failure_class="other",
                    work_dir=str(wannier_dir),
                    raw={"error": str(exc), "pathway": "wannier"},
                    provenance=Provenance(
                        source="qe_wannier",
                        software={"siscforge": __version__},
                        notes=primary_wannier_failure_reason(str(exc)),
                    ),
                )

        # P3.3: additive DMFT after Wannier (gated on ready_for_dmft).
        # Sacred upstream: DMFT failures never delete finished SCF/DFT+U/Wannier.
        if want_dmft and not epw_soft_blocked:
            from siscforge.calculators.qe.recipes import run_dmft_after_wannier

            dmft_dir = cand_dir / "dmft"
            try:
                dres = run_dmft_after_wannier(
                    dft,
                    dmft_dir,
                    wannier=getattr(wf, "wannier", None),
                    formula=candidate.formula,
                    material_family=candidate.material_family,
                    seed=candidate.candidate_id,
                    step_log=step_log,
                )
                wf.dmft = dres
                if dres.status not in {"ok", "mock"}:
                    step_log.append(
                        "DMFT did not succeed (upstream SCF/DFT+U/Wannier kept): "
                        + dres.summary_line()
                    )
            except Exception as exc:  # noqa: BLE001 — never destroy upstream
                from siscforge.calculators.qe.dmft import classify_dmft_failure
                from siscforge.models.results import DMFTResult

                _LOG.exception(
                    "DMFT step failed (upstream preserved) work_dir=%s",
                    dmft_dir,
                )
                step_log.append(f"DMFT exception (upstream kept): {exc}")
                wf.dmft = DMFTResult(
                    status="failed",
                    quality_tag=dft.quality_tag,
                    converged=False,
                    solver=dft.dmft.solver,
                    failure_class=classify_dmft_failure(str(exc)),
                    gate_notes=f"DMFT exception: {exc}",
                    work_dir=str(dmft_dir),
                    raw={"error": str(exc), "pathway": "dmft"},
                    provenance=Provenance(
                        source="qe_dmft",
                        software={"siscforge": __version__},
                        notes=str(exc),
                    ),
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
                            from siscforge.calculators.qe.qe_checkpoint import (
                                phonon_diagnostic_text,
                            )

                            diag_src = phonon_diagnostic_text(
                                getattr(step, "work_dir", None), Path(sp)
                            ) or Path(sp).read_text(
                                encoding="utf-8", errors="replace"
                            )
                            # Prefer last 8 KiB for classification, not the full multi-MB log
                            # but keep a leading CRASH sidecar if present (usually short).
                            if len(diag_src) > 8192:
                                head = diag_src[:2048]
                                tail = diag_src[-8192:]
                                diag_src = head + "\n" + tail if head not in tail else tail
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
            if (
                "fft" in primary.lower()
                or "phq_setup" in primary.lower()
                or "d_matrix" in primary.lower()
                or "phq_readin" in primary.lower()
                or "niter_ph" in primary.lower()
            ):
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

        dftu_result = getattr(wf, "dftu", None)
        if dftu_result is not None:
            notes_parts.append(f"dftu={dftu_result.summary_line()}")
        notes_parts.append(f"do_dftu={want_dftu}")
        wannier_result = getattr(wf, "wannier", None)
        if wannier_result is not None:
            notes_parts.append(f"wannier={wannier_result.summary_line()}")
        notes_parts.append(f"do_wannier={want_wannier}")
        dmft_result = getattr(wf, "dmft", None)
        if dmft_result is not None:
            notes_parts.append(f"dmft={dmft_result.summary_line()}")
        notes_parts.append(f"do_dmft={want_dmft}")

        ev = CandidateEvaluation(
            candidate=out_candidate,
            scf=scf,
            phonon=phonon,
            electron_phonon=eph,
            dftu=dftu_result,
            wannier=wannier_result,
            dmft=dmft_result,
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
                    "do_wannier": want_wannier,
                    "do_dmft": want_dmft,
                },
                parent_ids=[candidate.candidate_id],
                notes=(
                    "QE relax/SCF/phonon"
                    + ("/EPW" if want_epw else "")
                    + ("/DFT+U" if want_dftu else "")
                    + ("/Wannier" if want_wannier else "")
                    + ("/DMFT" if want_dmft else "")
                    + " evaluation"
                ),
            ),
        )
        # P3.4: default precedence only (scoring knobs). Campaign
        # ranking.performance_precedence is re-applied in CLI finalize / rank.
        from siscforge.scoring.pairing import apply_performance_score

        scoring = getattr(getattr(dft, "dmft", None), "scoring", None)
        return apply_performance_score(ev, scoring=scoring)


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
        # When callers pass DFTConfig() the model default is do_phonon=True.
        # Only force phonon/EPW off when those fields were not explicitly set —
        # same contract as the CLI qe-dftu path (model_fields_set).
        if dft is None:
            base = DFTConfig(engine="qe-dftu", do_phonon=False, do_epw=False)
        else:
            base = dft
            fields = set(getattr(base, "model_fields_set", set()) or set())
            updates: dict = {}
            if "do_phonon" not in fields:
                updates["do_phonon"] = False
            if "do_epw" not in fields and "epw" not in fields:
                updates["do_epw"] = False
                updates["epw"] = base.epw.model_copy(update={"enabled": False})
            if updates:
                base = base.model_copy(update=updates)
        if not base.do_dftu or base.engine != "qe-dftu":
            base = base.model_copy(
                update={
                    "do_dftu": True,
                    "dftu": base.dftu.model_copy(update={"enabled": True}),
                    "engine": "qe-dftu",
                }
            )
        super().__init__(dft=base, work_root=work_root, force_dftu=True)


class QEWannierCalculator(QECalculator):
    """QE calculator that forces standalone Wannier prep + metrics (P3.2).

    Registration name: ``qe-wannier``. Forces ``do_wannier``. Defaults phonon/EPW
    off unless the campaign re-enables them. Prefer pairing with DFT+U for
    nickelates (``do_dftu`` remains independent). Prep + optional P3.2.1
    nscf/pw2wannier90 + gated wannier90.x. Does not require TRIQS.
    """

    name = "qe-wannier"
    force_wannier = True

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        if dft is None:
            base = DFTConfig(engine="qe-wannier", do_phonon=False, do_epw=False)
        else:
            base = dft
            fields = set(getattr(base, "model_fields_set", set()) or set())
            updates: dict = {}
            if "do_phonon" not in fields:
                updates["do_phonon"] = False
            if "do_epw" not in fields and "epw" not in fields:
                updates["do_epw"] = False
                updates["epw"] = base.epw.model_copy(update={"enabled": False})
            if updates:
                base = base.model_copy(update=updates)
        base = base.model_copy(
            update={
                "do_wannier": True,
                "wannier": base.wannier.model_copy(update={"enabled": True}),
                "engine": "qe-wannier",
            }
        )
        super().__init__(dft=base, work_root=work_root, force_wannier=True)


class QEDmftCalculator(QECalculator):
    """QE calculator that forces the DMFT step (P3.3).

    Registration name: ``qe-dmft``. Forces ``do_dmft``. Defaults phonon/EPW
    off unless the campaign re-enables them.

    Does **not** force ``do_wannier`` (independence is intentional).
    Non-mock solvers still expect a ready :class:`WannierResult`
    (``ready_for_dmft``) or an explicit bypass
    (``allow_without_wannier_gate``). Pair with ``do_wannier: true`` /
    ``qe-wannier`` for a real chain; mock + ``mock_bypass_gate`` covers
    dry-run without the gate. TRIQS is never required to import this
    calculator. Real launch writes a run package and invokes when the
    stack is present (``p3_x_real_launch``); production U/J/β remains
    residual.
    """

    name = "qe-dmft"
    force_dmft = True

    def __init__(
        self,
        dft: DFTConfig | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        if dft is None:
            base = DFTConfig(engine="qe-dmft", do_phonon=False, do_epw=False)
        else:
            base = dft
            fields = set(getattr(base, "model_fields_set", set()) or set())
            updates: dict = {}
            if "do_phonon" not in fields:
                updates["do_phonon"] = False
            if "do_epw" not in fields and "epw" not in fields:
                updates["do_epw"] = False
                updates["epw"] = base.epw.model_copy(update={"enabled": False})
            if updates:
                base = base.model_copy(update=updates)
        base = base.model_copy(
            update={
                "do_dmft": True,
                "dmft": base.dmft.model_copy(update={"enabled": True}),
                "engine": "qe-dmft",
            }
        )
        super().__init__(dft=base, work_root=work_root, force_dmft=True)


def register_qe_calculators() -> None:
    """Register ``qe``, ``qe-epw``, ``qe-dftu``, ``qe-wannier``, and ``qe-dmft`` aliases."""
    from siscforge.calculators import registry

    calc = QECalculator()
    registry.register(calc, name="qe", overwrite=True)
    registry.register(calc, name="quantum-espresso", overwrite=True)
    registry.register(QEEpwCalculator(), name="qe-epw", overwrite=True)
    registry.register(QEEpwCalculator(), name="epw", overwrite=True)
    registry.register(QEDftuCalculator(), name="qe-dftu", overwrite=True)
    registry.register(QEDftuCalculator(), name="dftu", overwrite=True)
    registry.register(QEWannierCalculator(), name="qe-wannier", overwrite=True)
    registry.register(QEWannierCalculator(), name="wannier", overwrite=True)
    registry.register(QEDmftCalculator(), name="qe-dmft", overwrite=True)
    registry.register(QEDmftCalculator(), name="dmft", overwrite=True)
