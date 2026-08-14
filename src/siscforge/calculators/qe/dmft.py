"""TRIQS / solid_dmft scaffold + mock path — Phase 3.3.

Provides:
- enablement helper from :class:`~siscforge.models.config.DMFTConfig`
- Wannier ``ready_for_dmft`` gate (honoured outside explicit mock bypass)
- mock :class:`~siscforge.models.results.DMFTResult` (success + failure)
- thin optional wrapper: writes a config sidecar and parses a drop-in
  ``observables.json``. **Does not launch** solid_dmft / CTHYB. Skips
  cleanly when TRIQS is not installed (never a hard dependency)
- sequential recipe glue after Wannier (sacred upstream artifacts)

**P3.4** maps ``leading_pairing_eigenvalue`` onto the common
``performance_score`` (see ``siscforge.scoring.pairing``). This module
still does not launch CTHYB.

**Out of scope (later packages):** oxygen-vacancy enumeration (P3.5), mixed
AL pools (P3.6), automated solid_dmft launch (residual ``p3_x_real_launch``).

Conventional nitride / MgB₂ / EPW paths are unchanged when DMFT is off.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from siscforge import __version__
from siscforge.models.config import DFTConfig, DFTUConfig, DMFTConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import DMFTResult, WannierResult

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Failure classes
# ---------------------------------------------------------------------------

DMFT_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "wannier_gate",
        "solver_missing",
        "not_converged",
        "binary_missing",
        "import_error",
        "other",
    }
)

_EXTENSION_HOOKS: dict[str, str] = {
    "p3_4_pairing": (
        "P3.4 maps leading_pairing_eigenvalue onto performance_score via "
        "siscforge.scoring.pairing (pairing_symmetry is metadata only)."
    ),
    "p3_5_ovac": "oxygen-vacancy enumeration (structure generation, not DMFT)",
    "p3_6_al": "mixed conventional/unconventional AL acquisition",
    "p3_x_real_launch": (
        "Minimal launcher that shells out or writes a ready-to-run "
        "solid_dmft config from WannierResult + DMFTConfig remains residual. "
        "P3.3 only writes a sidecar and parses a drop-in observables.json."
    ),
    "limits": (
        "screening defaults (U, J, beta, n_cycles) are thin workstation knobs; "
        "not production CTHYB settings. TRIQS is optional and never a hard dep. "
        "n_loops/n_cycles/n_warmup_cycles are stored for a future launcher."
    ),
}

_MOCK_PHYSICS_LABEL = (
    "illustrative / deterministic placeholder, not literature-validated"
)

_SACRED_NOTE = (
    "DMFT failure must not delete finished DFT+U or Wannier artifacts "
    "(sibling dmft/ workdir only)"
)

_VALID_QUALITY = frozenset({"screening", "production", "mock", "unknown"})


def _quality_tag(value: str, default: str = "screening") -> str:
    return value if value in _VALID_QUALITY else default


def dmft_is_enabled(dft: DFTConfig | None, *, force: bool = False) -> bool:
    """Return True when the DMFT step should run."""
    if force:
        return True
    if dft is None:
        return False
    if bool(getattr(dft, "do_dmft", False)):
        return True
    dmft = getattr(dft, "dmft", None)
    return bool(dmft is not None and getattr(dmft, "enabled", False))


def triqs_available() -> bool:
    """True when a TRIQS / solid_dmft stack is importable."""
    try:
        import triqs  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        import solid_dmft  # noqa: F401

        return True
    except ImportError:
        return False


def solid_dmft_available() -> bool:
    """True when the ``solid_dmft`` package is importable."""
    try:
        import solid_dmft  # noqa: F401

        return True
    except ImportError:
        return False


def classify_dmft_failure(text: str | None) -> str:
    """Best-effort classify a solver / gate failure into :data:`DMFT_FAILURE_CLASSES`.

    v0 string heuristics over logs / import errors — not a structured
    solver API. Labels are diagnostic only.
    """
    if not text or not str(text).strip():
        return "other"
    blob = text.lower()
    if "ready_for_dmft" in blob or "wannier_gate" in blob or "not ready" in blob:
        return "wannier_gate"
    if "no module named" in blob or "importerror" in blob or "import error" in blob:
        return "import_error"
    if "triqs" in blob and ("not" in blob or "missing" in blob):
        return "solver_missing"
    if "solid_dmft" in blob and (
        "not" in blob or "missing" in blob or "no module" in blob
    ):
        return "solver_missing"
    if "not found" in blob or "cannot execute" in blob or "no such file" in blob:
        return "binary_missing"
    if "not converge" in blob or "did not converge" in blob or "unconverged" in blob:
        return "not_converged"
    return "other"


def evaluate_wannier_gate(
    wannier: WannierResult | None,
    cfg: DMFTConfig,
    *,
    solver: str | None = None,
) -> tuple[bool, str, bool]:
    """Return ``(allowed, notes, used_bypass)`` for the P3.2 → P3.3 gate.

    Non-mock solvers require ``wannier.ready_for_dmft`` unless
    ``allow_without_wannier_gate`` is True. Mock solver may bypass when
    ``mock_bypass_gate`` is True (documented dry-run demo path).
    """
    solver_name = (solver or cfg.solver or "mock").lower()
    ready = bool(wannier is not None and getattr(wannier, "ready_for_dmft", False))

    if cfg.allow_without_wannier_gate:
        note = "bypass: allow_without_wannier_gate"
        if wannier is None:
            note += " (no WannierResult attached)"
        elif not ready:
            extra = wannier.dmft_gate_notes or "ready_for_dmft=False"
            note += f" (Wannier not ready: {extra})"
        return True, note, True

    if solver_name == "mock" and bool(cfg.mock_bypass_gate):
        if ready:
            return True, "", False
        note = "bypass: mock_bypass_gate (dry-run demo; Wannier not ready_for_dmft)"
        if wannier is None:
            note = "bypass: mock_bypass_gate (dry-run demo; no WannierResult)"
        return True, note, True

    if not cfg.require_wannier_gate:
        return True, "bypass: require_wannier_gate=False", True

    if wannier is None:
        return False, "refused: no WannierResult (ready_for_dmft gate)", False
    if not ready:
        extra = wannier.dmft_gate_notes or "ready_for_dmft=False"
        return False, f"refused: Wannier not ready_for_dmft ({extra})", False
    return True, "", False


def resolve_interaction(
    cfg: DMFTConfig,
    dftu: DFTUConfig | None = None,
) -> tuple[float, float, dict[str, float], dict[str, float]]:
    """Resolve (U, J, U_by_species, J_by_species).

    DMFT config wins; DFT+U maps fill gaps so the nickelate path can reuse
    P3.1 Hubbard values without duplicating YAML.
    """
    u_map = dict(cfg.U_by_species or {})
    j_map = dict(cfg.J_by_species or {})
    if dftu is not None:
        for k, v in (dftu.U_by_species or {}).items():
            u_map.setdefault(str(k), float(v))
        for k, v in (dftu.J_by_species or {}).items():
            j_map.setdefault(str(k), float(v))
    u = float(cfg.U_eV)
    j = float(cfg.J_eV)
    if dftu is not None:
        if not cfg.U_by_species and not (cfg.model_fields_set & {"U_eV"}):
            if dftu.U_eV is not None:
                u = float(dftu.U_eV)
        if not cfg.J_by_species and not (cfg.model_fields_set & {"J_eV"}):
            if dftu.J_eV is not None:
                j = float(dftu.J_eV)
    return u, j, u_map, j_map


def _wannier_refs(wannier: WannierResult | None) -> dict[str, Any]:
    if wannier is None:
        return {
            "wannier_work_dir": None,
            "wannier_chk_path": None,
            "wannier_ready_for_dmft": None,
        }
    return {
        "wannier_work_dir": wannier.work_dir,
        "wannier_chk_path": wannier.chk_path,
        "wannier_ready_for_dmft": bool(wannier.ready_for_dmft),
    }


def _refused_result(
    *,
    cfg: DMFTConfig,
    wannier: WannierResult | None,
    notes: str,
    quality_tag: str,
    work_dir: str | None,
    extra_raw: dict[str, Any] | None = None,
    u: float | None = None,
    j: float | None = None,
) -> DMFTResult:
    refs = _wannier_refs(wannier)
    raw: dict[str, Any] = {
        "pathway": "dmft",
        "extension_hooks": dict(_EXTENSION_HOOKS),
        "upstream_sacred": _SACRED_NOTE,
        "gate": notes,
    }
    if extra_raw:
        raw.update(extra_raw)
    return DMFTResult(
        status="refused",
        quality_tag=_quality_tag(quality_tag, "unknown"),  # type: ignore[arg-type]
        converged=False,
        U_eV=u if u is not None else float(cfg.U_eV),
        J_eV=j if j is not None else float(cfg.J_eV),
        solver=(cfg.solver or "unknown"),
        beta=float(cfg.beta),
        n_cycles=int(cfg.n_cycles),
        n_warmup_cycles=int(cfg.n_warmup_cycles),
        gate_notes=notes,
        failure_class="wannier_gate",
        work_dir=work_dir,
        raw=raw,
        provenance=Provenance(
            source="qe_dmft",
            software={"siscforge": __version__},
            notes=notes,
        ),
        **refs,
    )


# ---------------------------------------------------------------------------
# Mock path
# ---------------------------------------------------------------------------


def mock_dmft_result(
    *,
    seed: str,
    dmft: DMFTConfig | None = None,
    wannier: WannierResult | None = None,
    dftu: DFTUConfig | None = None,
    formula: str = "",
    material_family: str = "other",
    quality_tag: str = "mock",
    work_dir: str | Path | None = None,
    force_failure: bool | None = None,
    failure_class: str | None = None,
) -> DMFTResult:
    """Deterministic placeholder DMFTResult for dry-run / mock calculator.

    Occupancy and mass enhancement are **illustrative / deterministic
    placeholders, not literature-validated** (nickelate-like filling
    ~8.65–8.95 and m*/m ~2.4–4 are seeded hashes, not a calibrated
    NdNiO₂ fit). See ``raw["physics_label"]``.

    Honours the Wannier gate unless ``solver=mock`` + ``mock_bypass_gate``
    (default) or ``allow_without_wannier_gate``. When ``force_failure``
    (or ``dmft.mock_force_failure``) is True, returns a failed result
    without touching upstream DFT+U / Wannier artifacts.
    """
    cfg = dmft or DMFTConfig(enabled=True, solver="mock")
    allowed, gate_notes, used_bypass = evaluate_wannier_gate(
        wannier, cfg, solver="mock"
    )
    wd = str(work_dir) if work_dir is not None else f"mock://dmft/{seed[:12]}"
    u, j, u_map, j_map = resolve_interaction(cfg, dftu)

    if not allowed:
        return _refused_result(
            cfg=cfg,
            wannier=wannier,
            notes=gate_notes,
            quality_tag=quality_tag,
            work_dir=wd,
            extra_raw={"method": "mock_dmft", "used_bypass": used_bypass},
            u=u,
            j=j,
        )

    fail = (
        bool(force_failure)
        if force_failure is not None
        else bool(cfg.mock_force_failure)
    )
    fcls = failure_class or cfg.mock_failure_class or "not_converged"
    if fcls not in DMFT_FAILURE_CLASSES:
        fcls = "other"

    qtag = _quality_tag(quality_tag, "mock")
    refs = _wannier_refs(wannier)
    digest = hashlib.sha256(f"{seed}:dmft".encode()).hexdigest()
    r = int(digest[:8], 16) / 0xFFFFFFFF

    if work_dir is not None:
        Path(work_dir).mkdir(parents=True, exist_ok=True)

    if fail:
        return DMFTResult(
            status="failed",
            quality_tag=qtag,  # type: ignore[arg-type]
            converged=False,
            U_eV=u,
            J_eV=j,
            U_by_species=u_map,
            J_by_species=j_map,
            solver="mock",
            beta=float(cfg.beta),
            n_cycles=int(cfg.n_cycles),
            n_warmup_cycles=int(cfg.n_warmup_cycles),
            leading_pairing_eigenvalue=None,
            pairing_symmetry=None,
            gate_notes=gate_notes,
            failure_class=fcls,
            work_dir=wd,
            raw={
                "method": "mock_dmft",
                "pathway": "dmft",
                "mock_force_failure": True,
                "used_bypass": used_bypass,
                "physics_label": _MOCK_PHYSICS_LABEL,
                "extension_hooks": dict(_EXTENSION_HOOKS),
                "upstream_sacred": _SACRED_NOTE,
                "formula": formula,
                "material_family": material_family,
            },
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                parameters={"U_eV": u, "J_eV": j, "failure_class": fcls},
                notes="dry-run DMFT failure placeholder (P3.3); "
                "illustrative only, not literature-validated",
            ),
            **refs,
        )

    # Illustrative nickelate-like mock (NOT a literature-calibrated fit):
    # filling ≈ 8.65–8.95, m*/m ≈ 2.4–4 — deterministic from seed hash.
    if material_family == "nickelate" or "Ni" in formula:
        filling = round(8.65 + 0.30 * r, 4)
        occ = {"Ni_d": filling}
        mass = round(2.4 + 1.6 * r, 4)
        mass_orb = {
            "Ni_dxy": round(mass * 0.85, 4),
            "Ni_dx2-y2": round(mass * 1.15, 4),
        }
    else:
        filling = round(7.8 + 0.6 * r, 4)
        occ = {"imp_d": filling}
        mass = round(1.6 + 0.8 * r, 4)
        mass_orb = {"imp_d": mass}

    # Illustrative pairing (P3.4) — NOT literature-validated.
    # Stronger λ only for material_family=nickelate (not a "Ni" substring —
    # alloys / doped formulas must opt in via family). Other families: weaker.
    if material_family == "nickelate":
        pair_eig = round(0.55 + 0.70 * r, 4)
        pair_sym = "d_x2-y2"
    else:
        pair_eig = round(0.20 + 0.45 * r, 4)
        pair_sym = "unknown"

    return DMFTResult(
        status="mock",
        quality_tag=qtag,  # type: ignore[arg-type]
        converged=True,
        U_eV=u,
        J_eV=j,
        U_by_species=u_map,
        J_by_species=j_map,
        occupancy_summary=occ,
        filling=filling,
        mass_enhancement=mass,
        mass_enhancement_by_orbital=mass_orb,
        leading_pairing_eigenvalue=pair_eig,
        pairing_symmetry=pair_sym,
        solver="mock",
        beta=float(cfg.beta),
        n_cycles=int(cfg.n_cycles),
        n_warmup_cycles=int(cfg.n_warmup_cycles),
        gate_notes=gate_notes,
        failure_class=None,
        work_dir=wd,
        raw={
            "method": "mock_dmft",
            "pathway": "dmft",
            "used_bypass": used_bypass,
            "physics_label": _MOCK_PHYSICS_LABEL,
            "pairing_label": (
                "illustrative mock pairing eigenvalue, not literature-validated"
            ),
            "extension_hooks": dict(_EXTENSION_HOOKS),
            "formula": formula,
            "material_family": material_family,
            "p3_4_note": _EXTENSION_HOOKS["p3_4_pairing"],
        },
        provenance=Provenance(
            source="mock_calculator",
            software={"siscforge": __version__},
            parameters={
                "U_eV": u,
                "J_eV": j,
                "beta": float(cfg.beta),
                "n_cycles": int(cfg.n_cycles),
            },
            notes=(
                "dry-run DMFT placeholder (P3.3/P3.4); "
                f"illustrative pairing λ={pair_eig:g} ({pair_sym}); "
                f"{_MOCK_PHYSICS_LABEL}"
            ),
        ),
        **refs,
    )


# ---------------------------------------------------------------------------
# Optional real path (gated on TRIQS / solid_dmft)
# ---------------------------------------------------------------------------


def parse_dmft_observables(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Best-effort extract occupancy / Z from solid_dmft-like JSON or text.

    Accepts a path to a JSON file, a JSON/text body, or a pre-parsed dict.
    Unknown formats return an empty metrics dict (never raise).
    """
    data: dict[str, Any] | None = None
    text = ""
    if isinstance(source, dict):
        data = source
    else:
        path: Path | None = None
        if isinstance(source, Path) or (
            isinstance(source, str)
            and len(source) < 4096
            and "\n" not in source
            and Path(source).is_file()
        ):
            path = Path(source)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return {}
        else:
            text = str(source)
        if text.lstrip().startswith("{") or text.lstrip().startswith("["):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    data = loaded
            except json.JSONDecodeError:
                data = None

    out: dict[str, Any] = {
        "occupancy_summary": {},
        "filling": None,
        "mass_enhancement": None,
        "mass_enhancement_by_orbital": {},
        "converged": False,
        "leading_pairing_eigenvalue": None,
        "pairing_symmetry": None,
    }
    if data is None:
        blob = text.lower()
        out["converged"] = "converged" in blob and "not converge" not in blob
        return out

    # Common solid_dmft / observ. keys (best-effort; keep loose)
    occ = data.get("occupancy") or data.get("occupancies") or data.get("n_imp") or {}
    if isinstance(occ, dict):
        parsed_occ: dict[str, float] = {}
        for k, v in occ.items():
            try:
                parsed_occ[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        out["occupancy_summary"] = parsed_occ
        if parsed_occ:
            out["filling"] = float(sum(parsed_occ.values()))
    elif isinstance(occ, (int, float)):
        out["filling"] = float(occ)
        out["occupancy_summary"] = {"imp": float(occ)}

    filling = data.get("filling") or data.get("n_tot") or data.get("density")
    if filling is not None:
        try:
            out["filling"] = float(filling)
        except (TypeError, ValueError):
            pass

    z = data.get("Z") or data.get("quasi_particle_weight") or data.get("z")
    mass = data.get("mass_enhancement") or data.get("mstar") or data.get("m*/m")
    if mass is not None:
        try:
            out["mass_enhancement"] = float(mass)
        except (TypeError, ValueError):
            pass
    elif isinstance(z, (int, float)) and float(z) != 0.0:
        out["mass_enhancement"] = float(1.0 / float(z))
    elif isinstance(z, dict) and z:
        orb: dict[str, float] = {}
        for k, v in z.items():
            try:
                fv = float(v)
                if fv != 0.0:
                    orb[str(k)] = float(1.0 / fv)
            except (TypeError, ValueError):
                continue
        out["mass_enhancement_by_orbital"] = orb
        if orb:
            out["mass_enhancement"] = float(sum(orb.values()) / len(orb))

    conv = data.get("converged")
    if isinstance(conv, bool):
        out["converged"] = conv
    else:
        out["converged"] = bool(data.get("success") or data.get("job_done"))

    # P3.4 homes — parse if a solver already wrote them, but do not rank.
    eig = data.get("leading_pairing_eigenvalue") or data.get("lambda_pair")
    if eig is not None:
        try:
            out["leading_pairing_eigenvalue"] = float(eig)
        except (TypeError, ValueError):
            pass
    sym = data.get("pairing_symmetry") or data.get("symmetry")
    if isinstance(sym, str) and sym.strip():
        out["pairing_symmetry"] = sym.strip()
    return out


def _write_dmft_config_sidecar(
    work_dir: Path,
    cfg: DMFTConfig,
    *,
    wannier: WannierResult | None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a small JSON sidecar so operators can audit the launch knobs."""
    work_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "solver": cfg.solver,
        "U_eV": cfg.U_eV,
        "J_eV": cfg.J_eV,
        "beta": cfg.beta,
        "n_cycles": cfg.n_cycles,
        "n_warmup_cycles": cfg.n_warmup_cycles,
        "n_loops": cfg.n_loops,
        "n_loops_note": (
            "stored for a future solid_dmft launcher; unused by the thin "
            "P3.3 sidecar + observables parser"
        ),
        "allow_without_wannier_gate": cfg.allow_without_wannier_gate,
        "mock_bypass_gate": cfg.mock_bypass_gate,
        "wannier_work_dir": wannier.work_dir if wannier is not None else None,
        "wannier_chk_path": wannier.chk_path if wannier is not None else None,
        "wannier_ready_for_dmft": (
            bool(wannier.ready_for_dmft) if wannier is not None else None
        ),
        "extension_hooks": dict(_EXTENSION_HOOKS),
        "version": cfg.version,
    }
    if extra:
        payload.update(extra)
    path = work_dir / "siscforge_dmft_config.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_solid_dmft(
    *,
    cfg: DMFTConfig,
    wannier: WannierResult | None,
    work_dir: Path,
    quality_tag: str,
    dftu: DFTUConfig | None = None,
    formula: str = "",
) -> DMFTResult:
    """Thin optional wrapper around solid_dmft / TRIQS (scaffold, not a launcher).

    Writes ``siscforge_dmft_config.json`` and, if present, parses a drop-in
    ``observables.json``. Does **not** start CTHYB / solid_dmft.

    Operator workflow: produce Wannier artifacts → run solid_dmft
    externally → drop ``observables.json`` into *work_dir* → re-invoke
    this function. Residual ``p3_x_real_launch`` would automate the
    middle step.

    Skips cleanly with ``failure_class=solver_missing`` when the stack is
    not importable. Never deletes files outside *work_dir*.
    """
    u, j, u_map, j_map = resolve_interaction(cfg, dftu)
    refs = _wannier_refs(wannier)
    sidecar = _write_dmft_config_sidecar(
        work_dir,
        cfg,
        wannier=wannier,
        extra={"U_resolved": u, "J_resolved": j, "formula": formula},
    )
    qtag = _quality_tag(quality_tag)

    if not triqs_available() and not solid_dmft_available():
        return DMFTResult(
            status="skipped",
            quality_tag=qtag,  # type: ignore[arg-type]
            converged=False,
            U_eV=u,
            J_eV=j,
            U_by_species=u_map,
            J_by_species=j_map,
            solver=cfg.solver,
            beta=float(cfg.beta),
            n_cycles=int(cfg.n_cycles),
            n_warmup_cycles=int(cfg.n_warmup_cycles),
            gate_notes="",
            failure_class="solver_missing",
            work_dir=str(work_dir),
            raw={
                "pathway": "dmft",
                "reason": "TRIQS / solid_dmft not importable — skipped cleanly",
                "sidecar": str(sidecar),
                "extension_hooks": dict(_EXTENSION_HOOKS),
                "upstream_sacred": _SACRED_NOTE,
            },
            provenance=Provenance(
                source="qe_dmft",
                software={"siscforge": __version__},
                notes="solid_dmft/TRIQS not installed; DMFT skipped (optional extra)",
            ),
            **refs,
        )

    # Stack is present: attempt a documented thin invoke. Any exception is
    # captured so upstream Wannier / DFT+U artifacts stay sacred.
    metrics: dict[str, Any] = {}
    err: str | None = None
    try:
        # Look for a previously written observ. JSON (resume / operator drop-in)
        for name in (
            "observables.json",
            "observables_imp0.json",
            "siscforge_dmft_observables.json",
        ):
            cand = work_dir / name
            if cand.is_file():
                metrics = parse_dmft_observables(cand)
                break
        if not metrics:
            # Import is enough to prove the extra is wired; running a full
            # CTHYB job is operator-driven (workstation time + license).
            # TODO(p3_x_real_launch): generate a ready-to-run solid_dmft
            # config from WannierResult + DMFTConfig and optionally shell
            # out. P3.3 stops at sidecar + drop-in parse.
            try:
                import solid_dmft  # noqa: F401
            except ImportError:
                import triqs  # noqa: F401
            err = (
                "TRIQS/solid_dmft importable but no observables JSON in work_dir; "
                "P3.3 does not launch a full CTHYB job automatically "
                "(operator drop-in: write observables.json)"
            )
    except Exception as exc:  # noqa: BLE001 — never destroy upstream
        _LOG.exception(
            "solid_dmft wrapper failed (upstream preserved) work_dir=%s", work_dir
        )
        err = str(exc)

    if err and not metrics.get("occupancy_summary") and metrics.get("filling") is None:
        return DMFTResult(
            status="failed",
            quality_tag=qtag,  # type: ignore[arg-type]
            converged=False,
            U_eV=u,
            J_eV=j,
            U_by_species=u_map,
            J_by_species=j_map,
            solver=cfg.solver,
            beta=float(cfg.beta),
            n_cycles=int(cfg.n_cycles),
            n_warmup_cycles=int(cfg.n_warmup_cycles),
            gate_notes="",
            failure_class=classify_dmft_failure(err),
            work_dir=str(work_dir),
            raw={
                "pathway": "dmft",
                "error": err,
                "sidecar": str(sidecar),
                "extension_hooks": dict(_EXTENSION_HOOKS),
                "upstream_sacred": _SACRED_NOTE,
            },
            provenance=Provenance(
                source="qe_dmft",
                software={"siscforge": __version__},
                notes=err,
            ),
            **refs,
        )

    converged = bool(metrics.get("converged"))
    return DMFTResult(
        status="ok" if converged else "failed",
        quality_tag=qtag,  # type: ignore[arg-type]
        converged=converged,
        U_eV=u,
        J_eV=j,
        U_by_species=u_map,
        J_by_species=j_map,
        occupancy_summary=dict(metrics.get("occupancy_summary") or {}),
        filling=metrics.get("filling"),
        mass_enhancement=metrics.get("mass_enhancement"),
        mass_enhancement_by_orbital=dict(
            metrics.get("mass_enhancement_by_orbital") or {}
        ),
        leading_pairing_eigenvalue=metrics.get("leading_pairing_eigenvalue"),
        pairing_symmetry=metrics.get("pairing_symmetry"),
        solver=cfg.solver,
        beta=float(cfg.beta),
        n_cycles=int(cfg.n_cycles),
        n_warmup_cycles=int(cfg.n_warmup_cycles),
        gate_notes="",
        failure_class=None if converged else "not_converged",
        work_dir=str(work_dir),
        raw={
            "pathway": "dmft",
            "sidecar": str(sidecar),
            "extension_hooks": dict(_EXTENSION_HOOKS),
            "metrics": {
                k: metrics.get(k) for k in ("filling", "mass_enhancement", "converged")
            },
        },
        provenance=Provenance(
            source="qe_dmft",
            software={"siscforge": __version__},
            notes="solid_dmft observables parse (P3.3)",
        ),
        **refs,
    )


def run_dmft_workflow(
    dft: DFTConfig,
    work_dir: Path | str,
    *,
    wannier: WannierResult | None = None,
    formula: str = "",
    material_family: str = "other",
    seed: str = "dmft",
    step_log: list[str] | None = None,
    quality_tag: str | None = None,
) -> DMFTResult:
    """Run (or mock / skip) DMFT under a sibling *work_dir*.

    Sacred-upstream contract: *work_dir* is the only tree this function
    writes. Finished SCF / DFT+U / Wannier directories are never deleted.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    log: list[str] = step_log if step_log is not None else []
    cfg = dft.dmft
    solver = (cfg.solver or "mock").lower()
    qtag = quality_tag or dft.quality_tag
    allowed, gate_notes, used_bypass = evaluate_wannier_gate(
        wannier, cfg, solver=solver
    )
    log.append(f"dmft gate: allowed={allowed} solver={solver} {gate_notes}".strip())

    if not allowed:
        result = _refused_result(
            cfg=cfg,
            wannier=wannier,
            notes=gate_notes,
            quality_tag=qtag,
            work_dir=str(work_dir),
            extra_raw={"used_bypass": used_bypass, "solver": solver},
        )
        log.append(result.summary_line())
        if step_log is not None:
            step_log[:] = log
        return result

    if solver == "mock":
        mock_tag = "mock" if qtag == "screening" and dft.engine == "mock" else qtag
        result = mock_dmft_result(
            seed=seed,
            dmft=cfg,
            wannier=wannier,
            dftu=dft.dftu,
            formula=formula,
            material_family=material_family,
            quality_tag=mock_tag,
            work_dir=work_dir,
        )
        log.append(result.summary_line())
        if step_log is not None:
            step_log[:] = log
        return result

    result = run_solid_dmft(
        cfg=cfg,
        wannier=wannier,
        work_dir=work_dir,
        quality_tag=qtag,
        dftu=dft.dftu,
        formula=formula,
    )
    if gate_notes and not result.gate_notes:
        result = result.model_copy(update={"gate_notes": gate_notes})
    log.append(result.summary_line())
    if step_log is not None:
        step_log[:] = log
    return result
