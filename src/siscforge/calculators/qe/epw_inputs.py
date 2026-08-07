"""Build minimal EPW input decks for workstation screening (QE ≥ 7.x).

Grid tiers
----------
``quality_tag: screening`` (default) uses coarse DFPT/EPW meshes suitable for
order-of-magnitude λ / Tc on a workstation.  ``quality_tag: production`` is a
label for denser hand-tuned campaigns — raise k/q grids in YAML (see
``recommended_grids`` and docs/examples/nbN_epw.md).

Coarse electronic k (``nkc`` / EPW ``nk1–3``)
--------------------------------------------
Wannier90 needs enough b-vectors on the coarse k-mesh. On ≥8-atom supercells,
``nk=6`` often aborts with ``kmesh_get_bvector: Not enough bvectors found``
after multi-day DFPT. SiSC-Forge therefore:

- Uses **minimum 8³** coarse k for ``workstation_dense`` / ``production`` tiers
  when ``n_atoms ≥ 8`` (see :func:`minimum_coarse_k_dim`).
- Auto-raises undersized ``nkc`` in preflight (unless ``strict_coarse_k``).
- **Never** auto-changes ``nqc`` / DFPT q-mesh after phonons are done.

This module does **not** auto-discover Wannier projections; ``proj=random`` is
intentional for screening. Production runs need material-specific projections.
Auto-raised nk does **not** guarantee physical λ/Tc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from pymatgen.core import Element, Structure

from siscforge.models.config import DFTConfig, EPWConfig

# Default atomic masses (amu) for common species when structure is unavailable.
_DEFAULT_AMASS: dict[str, float] = {
    "H": 1.008,
    "B": 10.811,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "Mg": 24.305,
    "Si": 28.085,
    "Ti": 47.867,
    "V": 50.942,
    "Nb": 92.906,
    "Zr": 91.224,
    "Hf": 178.49,
    "Ta": 180.948,
    "Pb": 207.2,
}

# Coarse-k remediation ladder after kmesh_get_bvector (per dimension, isotropic bump).
# Phase A — denser nkc (re-NSCF + epw). Phase B — search_shells (EPW-only, same nkc).
COARSE_K_REMEDIATION_LADDER: tuple[int, ...] = (6, 8, 12)
MAX_EPW_KMESH_RETRIES: int = 2
# Wannier90 default search_shells is 12; Phase B raises neighbour-shell search.
DEFAULT_W90_SEARCH_SHELLS: int = 12
SEARCH_SHELLS_REMEDIATION_LADDER: tuple[int, ...] = (36, 48)
MAX_EPW_SEARCH_SHELLS_RETRIES: int = 2


def qe_atomic_type_symbols(structure: Structure) -> list[str]:
    """Atomic-type order matching pymatgen ``PWInput`` / QE ``ATOMIC_SPECIES``.

    Types are unique element symbols sorted by atomic number (N before Nb).
    EPW ``amass(i)`` **must** use this order, not site order in the structure.
    """
    symbols = {site.specie.symbol for site in structure}
    return sorted(symbols, key=lambda s: Element(s).Z)


def _amass_lines(
    structure: Structure | None,
    species_order: list[str] | None = None,
) -> list[str]:
    """Return ``amass(i) = …`` lines for EPW namelist (QE type order)."""
    if species_order is not None:
        symbols = list(species_order)
    elif structure is not None:
        symbols = qe_atomic_type_symbols(structure)
    else:
        return []

    lines: list[str] = []
    for i, sym in enumerate(symbols, start=1):
        mass = _DEFAULT_AMASS.get(sym)
        if mass is None:
            try:
                mass = float(Element(sym).atomic_mass)
            except Exception:  # noqa: BLE001
                mass = 1.0
        lines.append(f"  amass({i})    = {mass}")
    return lines


def default_nbndsub_screening(
    *,
    nbnd: int | None,
    structure: Structure | None = None,
    explicit: int | None = None,
    auto: bool = True,
) -> int:
    """Screening default for EPW ``nbndsub`` (target Wannier functions).

    Policy (documented):
    - Auto: ``nbndsub = min(nbnd, max(16, 4 * n_atoms, nbnd // 2))``
    - Floor 16 avoids the classic supercell trap (nbnd=64, nbndsub=10).
    - Cap at ``nbnd`` so we never request more WFs than KS bands.
    - When *explicit* is set and *auto* is True, raise it to the auto floor
      if the explicit value is below the floor (user low values still get a
      safer screening default). Set auto=False to force the explicit value.

    Production Wannier still needs material-specific projections.
    """
    n_at = len(structure) if structure is not None else 2
    n_bands = int(nbnd) if nbnd is not None and int(nbnd) > 0 else max(24, 8 * n_at)
    auto_val = min(n_bands, max(16, 4 * n_at, n_bands // 2))
    auto_val = max(8, auto_val)  # absolute minimum for metals

    if explicit is not None and int(explicit) > 0:
        exp = int(explicit)
        if not auto:
            return min(exp, n_bands)
        # Raise undersized explicit values to the screening floor
        return min(n_bands, max(exp, auto_val))
    return auto_val if auto else 10


def _wannier_window_lines(
    fermi_eV: float | None,
    *,
    screening_tight_froz: bool = True,
) -> list[str]:
    """Disentanglement windows.

    Absolute eigenvalue windows must **bracket the Fermi level**. Hard-coded
    ``dis_win_max = 20`` fails for NbN (E_F ≈ 21 eV) and causes EPW
    ``efermig: cannot bracket Ef`` after Wannier interpolation.

    Screening uses a **tighter frozen window** (``screening_tight_froz``) so
    ``proj=random`` + moderate nbndsub does not trip Wannier90
    ``More states in the frozen window than target WFs``. Outer dis_win
    stays wide for disentanglement.
    """
    if fermi_eV is None:
        # Conservative absolute defaults for mid-gap / low-E_F systems only.
        if screening_tight_froz:
            return [
                "  dis_win_min = -10.0",
                "  dis_win_max = 30.0",
                "  dis_froz_min= -2.0",
                "  dis_froz_max= 2.0",
            ]
        return [
            "  dis_win_min = -10.0",
            "  dis_win_max = 30.0",
            "  dis_froz_min= -5.0",
            "  dis_froz_max= 15.0",
        ]
    # Outer window wide; frozen window tight for screening random projs.
    if screening_tight_froz:
        return [
            f"  dis_win_min = {fermi_eV - 18.0:.4f}",
            f"  dis_win_max = {fermi_eV + 12.0:.4f}",
            f"  dis_froz_min= {fermi_eV - 3.0:.4f}",
            f"  dis_froz_max= {fermi_eV + 1.0:.4f}",
        ]
    return [
        f"  dis_win_min = {fermi_eV - 18.0:.4f}",
        f"  dis_win_max = {fermi_eV + 12.0:.4f}",
        f"  dis_froz_min= {fermi_eV - 12.0:.4f}",
        f"  dis_froz_max= {fermi_eV + 2.0:.4f}",
    ]


# ---------------------------------------------------------------------------
# Coarse electronic k — Wannier safety (Feature A / B)
# ---------------------------------------------------------------------------


def _normalize_grid3(values: Sequence[int] | None, default: list[int]) -> list[int]:
    base = list(values) if values is not None else list(default)
    out = [int(x) for x in (base + list(default))[:3]]
    return [max(1, x) for x in out]


def minimum_coarse_k_dim(
    *,
    quality_tag: str = "screening",
    n_atoms: int = 2,
    tier: str | None = None,
) -> int:
    """Minimum per-dimension coarse electronic k for Wannier safety.

    Policy
    ------
    - **screening**: allow 4³ on small cells; allow 6³ on ≥8-atom cells
      (documented risk of bvector failures on awkward cells).
    - **workstation_dense / production** (or ``quality_tag=production``):
      **minimum 8** per dimension when ``n_atoms ≥ 8``; 6 on smaller cells.

    ``tier`` may be set explicitly by refine (``workstation_dense``) even when
    the YAML still carries ``quality_tag: production``.
    """
    qtag = (quality_tag or "screening").lower()
    t = (tier or "").lower()
    dense = (
        qtag in {"production", "workstation_dense"}
        or t in {"production", "workstation_dense"}
    )
    n_at = max(1, int(n_atoms))
    if dense:
        return 8 if n_at >= 8 else 6
    # screening
    return 6 if n_at >= 8 else 4


def ensure_wannier_safe_nkc(
    nkc: Sequence[int],
    *,
    quality_tag: str = "screening",
    n_atoms: int = 2,
    tier: str | None = None,
    auto_raise: bool = True,
) -> tuple[list[int], str | None]:
    """Return Wannier-safe coarse k and an optional log line when raised.

    Does **not** touch nqc / DFPT q-mesh.
    """
    nkc_list = _normalize_grid3(nkc, [4, 4, 4])
    floor = minimum_coarse_k_dim(
        quality_tag=quality_tag, n_atoms=n_atoms, tier=tier
    )
    if all(x >= floor for x in nkc_list):
        return nkc_list, None
    if not auto_raise:
        return nkc_list, (
            f"EPW coarse k {nkc_list[0]}×{nkc_list[1]}×{nkc_list[2]} is below "
            f"Wannier safety floor {floor}³ for n_atoms={n_atoms} "
            f"(quality_tag={quality_tag}"
            + (f", tier={tier}" if tier else "")
            + "); auto_raise disabled"
        )
    raised = [max(x, floor) for x in nkc_list]
    msg = (
        f"EPW coarse k raised to {raised[0]}×{raised[1]}×{raised[2]} "
        f"(Wannier safety; was {nkc_list[0]}×{nkc_list[1]}×{nkc_list[2]}; "
        f"nq unchanged to match DFPT)"
    )
    return raised, msg


def next_coarse_k_after_bvector_failure(
    nkc: Sequence[int],
    *,
    attempt: int = 0,
) -> list[int] | None:
    """Next isotropic coarse-k after ``kmesh_get_bvector`` (6→8→12).

    *attempt* is 0-based index among k-mesh remediation tries (max 2).
    Returns None when the ladder is exhausted.
    """
    cur = _normalize_grid3(nkc, [6, 6, 6])
    # Use max dimension as current density proxy
    cur_dim = max(cur)
    # Find next ladder step strictly denser than current
    for step in COARSE_K_REMEDIATION_LADDER:
        if step > cur_dim:
            if attempt >= MAX_EPW_KMESH_RETRIES:
                return None
            return [step, step, step]
    # Already at or above top of ladder
    return None


def effective_search_shells(value: int | None) -> int:
    """Resolve configured ``search_shells`` (None → Wannier90 default 12)."""
    if value is None:
        return DEFAULT_W90_SEARCH_SHELLS
    return max(1, int(value))


def next_search_shells_after_bvector_failure(
    search_shells: int | None,
    *,
    attempt: int = 0,
) -> int | None:
    """Next Wannier90 ``search_shells`` after nk ladder exhausted (12→36→48).

    *attempt* is 0-based among Phase-B tries (max
    :data:`MAX_EPW_SEARCH_SHELLS_RETRIES`). Returns None when exhausted.
    Does **not** change nkc / nqc / DFPT.
    """
    cur = effective_search_shells(search_shells)
    for step in SEARCH_SHELLS_REMEDIATION_LADDER:
        if step > cur:
            if attempt >= MAX_EPW_SEARCH_SHELLS_RETRIES:
                return None
            return int(step)
    return None


def apply_search_shells_to_config(
    config: DFTConfig,
    search_shells: int,
    *,
    kmesh_tol: float | None = None,
) -> DFTConfig:
    """Return config with ``epw.search_shells`` set (nkc / nqc unchanged)."""
    updates: dict[str, Any] = {"search_shells": int(search_shells)}
    if kmesh_tol is not None:
        updates["kmesh_tol"] = float(kmesh_tol)
    return config.model_copy(
        update={"epw": config.epw.model_copy(update=updates)}
    )


def recommended_grids(
    family: Literal["tm_nitride", "mgb2_boride", "generic"] = "generic",
    tier: Literal["screening", "workstation_dense", "production"] = "screening",
) -> dict[str, Any]:
    """Suggested DFT/EPW grid knobs by material family and quality tier.

    These are **guidance** for YAML overrides — also applied by
    ``default_refine_dft``. ``workstation_dense`` is a practical next step
    after screening on a high-end workstation; ``production`` still needs
    hand-tuned Wannier.

    **Wannier safety (Slice 25):** dense tiers use coarse k ≥ 8³ for typical
    8-atom nitride supercells — never 6³ (kmesh_get_bvector trap).
    """
    # Common skeleton
    base: dict[str, Any] = {
        "quality_tag": "screening" if tier == "screening" else "production",
        "notes": "",
    }
    if family == "tm_nitride":
        if tier == "screening":
            base.update(
                {
                    "kpoints": [6, 6, 6],
                    "qpoints": [2, 2, 2],
                    "epw": {
                        "nkc": [4, 4, 4],
                        "nqc": [2, 2, 2],
                        "nkf": [6, 6, 6],
                        "nqf": [6, 6, 6],
                        "eps_acustic": 15.0,
                        "fsthick": 0.6,
                    },
                    "notes": (
                        "Order-of-magnitude NbN-like λ/Tc; soft modes may inflate λ. "
                        "Screening nkc=4 is intentional; supercell EPW may need 6³+."
                    ),
                }
            )
        elif tier == "workstation_dense":
            base.update(
                {
                    "kpoints": [8, 8, 8],
                    "qpoints": [4, 4, 4],
                    "epw": {
                        # Was 6³ — Wannier90 kmesh_get_bvector fails on 8-atom cells
                        "nkc": [8, 8, 8],
                        "nqc": [4, 4, 4],
                        "nkf": [12, 12, 12],
                        "nqf": [12, 12, 12],
                        "eps_acustic": 5.0,
                        "fsthick": 0.4,
                        "degaussw": 0.05,
                    },
                    "notes": (
                        "Denser DFPT q + EPW fine mesh; nqc must match DFPT q-grid. "
                        "Coarse k ≥ 8³ for Wannier safety on supercells. "
                        "Expect multi-hour wall-time on 16–32 cores."
                    ),
                }
            )
        else:  # production
            base.update(
                {
                    "kpoints": [12, 12, 12],
                    "qpoints": [6, 6, 6],
                    "epw": {
                        "nkc": [8, 8, 8],
                        "nqc": [6, 6, 6],
                        "nkf": [18, 18, 18],
                        "nqf": [18, 18, 18],
                        "eps_acustic": 2.0,
                        "fsthick": 0.3,
                    },
                    "notes": (
                        "Literature recovery needs tuned Wannier projections "
                        "(not proj=random) and careful soft-mode treatment."
                    ),
                }
            )
    elif family == "mgb2_boride":
        if tier == "screening":
            base.update(
                {
                    "kpoints": [6, 6, 4],
                    "qpoints": [2, 2, 2],
                    "epw": {
                        "nkc": [4, 4, 2],
                        "nqc": [2, 2, 2],
                        "nkf": [8, 8, 6],
                        "nqf": [8, 8, 6],
                    },
                    "notes": "Isotropic average of two-gap MgB2; order-of-magnitude Tc.",
                }
            )
        elif tier == "workstation_dense":
            base.update(
                {
                    "kpoints": [8, 8, 6],
                    "qpoints": [4, 4, 2],
                    "epw": {
                        # denser in-plane coarse k (3-atom cell; anisotropic ok)
                        "nkc": [8, 8, 4],
                        "nqc": [4, 4, 2],
                        "nkf": [16, 16, 12],
                        "nqf": [16, 16, 12],
                    },
                    "notes": (
                        "Denser anisotropic-cell meshes; still isotropic λ/Tc. "
                        "Tune B-p / Mg-s projections for better recovery of ~39 K."
                    ),
                }
            )
        else:
            base.update(
                {
                    "kpoints": [12, 12, 8],
                    "qpoints": [6, 6, 4],
                    "epw": {
                        "nkc": [8, 8, 6],
                        "nqc": [6, 6, 4],
                        "nkf": [24, 24, 16],
                        "nqf": [24, 24, 16],
                    },
                    "notes": (
                        "Production isotropic still underestimates two-gap physics; "
                        "full recovery needs anisotropic Eliashberg (out of scope)."
                    ),
                }
            )
    else:
        base.update(
            {
                "kpoints": [4, 4, 4],
                "qpoints": [2, 2, 2],
                "epw": {
                    "nkc": [4, 4, 4],
                    "nqc": [2, 2, 2],
                    "nkf": [6, 6, 6],
                    "nqf": [6, 6, 6],
                },
                "notes": "Generic screening defaults (EPWConfig).",
            }
        )
    return base


@dataclass
class EPWPreflightResult:
    """Outcome of pre-DFPT EPW grid validation."""

    ok: bool
    config: DFTConfig
    messages: list[str] = field(default_factory=list)
    nkc_raised: bool = False
    nqc_aligned: bool = False
    strict_violations: list[str] = field(default_factory=list)

    @property
    def summary_lines(self) -> list[str]:
        lines = list(self.messages)
        if self.strict_violations:
            lines.extend(f"STRICT: {v}" for v in self.strict_violations)
        return lines


def preflight_epw_grids(
    config: DFTConfig,
    *,
    structure: Structure | None = None,
    n_atoms: int | None = None,
    tier: str | None = None,
    auto_raise: bool | None = None,
) -> EPWPreflightResult:
    """Validate / auto-fix coarse k and nq consistency **before** DFPT+EPW.

    - Coarse k must meet Wannier safety floor for tier + cell size.
    - ``epw.nqc`` should equal ``dft.qpoints`` (DFPT mesh); auto-align nqc →
      qpoints when they differ (never the reverse after DFPT exists).
    - When ``epw.strict_coarse_k`` is True, undersized k is a hard failure
      (``ok=False``) instead of auto-raise.

    Fine grids (nkf/nqf) are not modified.
    """
    n_at = (
        int(n_atoms)
        if n_atoms is not None
        else (len(structure) if structure is not None else 2)
    )
    epw = config.epw
    strict = bool(getattr(epw, "strict_coarse_k", False))
    do_auto = (not strict) if auto_raise is None else (bool(auto_raise) and not strict)

    messages: list[str] = []
    violations: list[str] = []
    nkc_in = _normalize_grid3(epw.nkc, [4, 4, 4])
    nqc_in = _normalize_grid3(epw.nqc, [2, 2, 2])
    qpts = _normalize_grid3(config.qpoints, [2, 2, 2])

    floor = minimum_coarse_k_dim(
        quality_tag=config.quality_tag or "screening",
        n_atoms=n_at,
        tier=tier,
    )
    below = any(x < floor for x in nkc_in)

    nkc_out = list(nkc_in)
    nkc_raised = False
    if below:
        if do_auto:
            nkc_out, raise_msg = ensure_wannier_safe_nkc(
                nkc_in,
                quality_tag=config.quality_tag or "screening",
                n_atoms=n_at,
                tier=tier,
                auto_raise=True,
            )
            nkc_raised = nkc_out != nkc_in
            if raise_msg:
                messages.append(raise_msg)
        else:
            violations.append(
                f"EPW coarse k {nkc_in[0]}×{nkc_in[1]}×{nkc_in[2]} is below "
                f"Wannier safety floor {floor}³ for n_atoms={n_at} "
                f"(quality_tag={config.quality_tag}"
                + (f", tier={tier}" if tier else "")
                + "); set epw.strict_coarse_k=false to auto-raise or raise nkc in YAML"
            )

    nqc_out = list(nqc_in)
    nqc_aligned = False
    if nqc_in != qpts:
        # Align EPW coarse q to DFPT qpoints (required for dvscf/save match)
        nqc_out = list(qpts)
        nqc_aligned = True
        messages.append(
            f"EPW nqc aligned to DFPT qpoints {qpts[0]}×{qpts[1]}×{qpts[2]} "
            f"(was {nqc_in[0]}×{nqc_in[1]}×{nqc_in[2]})"
        )

    cfg = config
    epw_updates: dict[str, Any] = {}
    if nkc_out != nkc_in:
        epw_updates["nkc"] = nkc_out
    if nqc_out != nqc_in:
        epw_updates["nqc"] = nqc_out
    if epw_updates:
        cfg = config.model_copy(
            update={"epw": config.epw.model_copy(update=epw_updates)}
        )

    # Summary always present for CLI once-at-start
    summary = (
        f"EPW preflight: n_atoms={n_at} quality_tag={cfg.quality_tag} "
        f"nkc={list(cfg.epw.nkc)} nqc={list(cfg.epw.nqc)} "
        f"qpoints={list(cfg.qpoints)} nkf={list(cfg.epw.nkf)} "
        f"nproc={cfg.nproc} npool={cfg.epw.npool}"
    )
    messages.insert(0, summary)

    ok = len(violations) == 0
    return EPWPreflightResult(
        ok=ok,
        config=cfg,
        messages=messages,
        nkc_raised=nkc_raised,
        nqc_aligned=nqc_aligned,
        strict_violations=violations,
    )


def apply_coarse_k_to_config(
    config: DFTConfig,
    nkc: Sequence[int],
) -> DFTConfig:
    """Return config with epw.nkc set (NSCF builders read this)."""
    nkc_list = _normalize_grid3(nkc, [8, 8, 8])
    return config.model_copy(
        update={"epw": config.epw.model_copy(update={"nkc": nkc_list})}
    )


def build_epw_input(
    config: DFTConfig,
    *,
    prefix: str = "siscforge",
    outdir: str = "./out",
    dvscf_dir: str = "./save",
    structure: Structure | None = None,
    fermi_eV: float | None = None,
) -> str:
    """Return a QE-7-compatible coarse-grid ``epw.in`` for metals (screening).

    Notes
    -----
    - ``bands_skipped`` is a **character** field in EPW (Wannier exclude string),
      not an integer. Omitting it is safest for screening.
    - Grid keys are one-per-line (EPW namelist is picky about multi-assignment lines).
    - When *fermi_eV* is known (from nscf/scf), windows are set relative to E_F and
      ``efermi_read`` pins the fine-grid Fermi level to the DFT value.
    - MgB₂ (and other multi-band systems) still use **isotropic** λ / Tc here;
      anisotropic Eliashberg is not generated by this template.
    - Full production runs still need careful Wannier projections and denser grids.
    - Generated file includes a header comment recording ``quality_tag`` and grids.
    - Coarse k may be raised for Wannier safety (see :func:`ensure_wannier_safe_nkc`);
      nqc is **not** auto-changed here (must match DFPT).
    """
    epw: EPWConfig = config.epw
    nkf = (list(epw.nkf) + [6, 6, 6])[:3]
    nqf = (list(epw.nqf) + [6, 6, 6])[:3]
    n_at = len(structure) if structure is not None else 2
    nkc_raw = (list(epw.nkc) + [4, 4, 4])[:3]
    nkc, _raise_msg = ensure_wannier_safe_nkc(
        nkc_raw,
        quality_tag=getattr(config, "quality_tag", "screening") or "screening",
        n_atoms=n_at,
        auto_raise=not bool(getattr(epw, "strict_coarse_k", False)),
    )
    nqc = (list(epw.nqc) + [2, 2, 2])[:3]
    qtag = getattr(config, "quality_tag", "screening") or "screening"
    auto_nbnd = bool(getattr(epw, "auto_nbndsub", True))
    # Screening auto policy; production labels still get auto unless disabled
    nbndsub = default_nbndsub_screening(
        nbnd=config.nbnd,
        structure=structure,
        explicit=epw.nbndsub,
        auto=auto_nbnd,
    )
    screening_tight = qtag == "screening"

    ss_hdr = getattr(epw, "search_shells", None)
    ss_note = (
        f"search_shells={int(ss_hdr)}"
        if ss_hdr is not None
        else "search_shells=W90 default (12)"
    )
    header = [
        "!",
        f"! SiSC-Forge EPW input — quality_tag={qtag}",
        f"! Fine k/q (nkf/nqf) = {nkf} / {nqf}",
        f"! Coarse k/q (nkc/nqc) = {nkc} / {nqc}  (nqc should match DFPT q-grid)",
        f"! nbndsub={nbndsub} (auto_nbndsub={auto_nbnd}; dft.nbnd={config.nbnd})",
        f"! {ss_note} (Phase B remediation may raise via wdata)",
        "! Screening: proj=random + tight frozen window; production needs hand projs.",
        "! Coarse k auto-bump for Wannier safety; DFPT nq never redone on k-mesh fail.",
        "! Raise nkf/nqf/qpoints for denser grids (recommended_grids / docs).",
        "!",
    ]

    lines: list[str] = [
        *header,
        "--",
        "&inputepw",
        f"  prefix      = '{prefix}'",
        f"  outdir      = '{outdir}'",
        f"  dvscf_dir   = '{dvscf_dir}'",
    ]
    lines.extend(_amass_lines(structure))
    lines.extend(
        [
            "",
            "  elph        = .true.",
            "  epbwrite    = .true.",
            "  epbread     = .false.",
            "  epwwrite    = .true.",
            "  epwread     = .false.",
            "",
            f"  nbndsub     = {nbndsub}",
            "",
            "  wannierize  = .true.",
            "  num_iter    = 500",
        ]
    )
    lines.extend(
        _wannier_window_lines(fermi_eV, screening_tight_froz=screening_tight)
    )
    lines.extend(
        [
            "  proj(1)     = 'random'",
            "",
            "  iverbosity  = 2",
            # Note for quality layer: screening template uses random projs
            "",
            "  elecselfen  = .false.",
            "  phonselfen  = .true.",
            "  a2f         = .true.",
            "",
            f"  fsthick     = {epw.fsthick}",
            f"  degaussw    = {epw.degaussw}",
            f"  degaussq    = {epw.degaussq}",
            f"  eps_acustic = {epw.eps_acustic}",
        ]
    )
    # Wannier90 knobs via EPW wdata (search_shells / kmesh_tol). Omitted when
    # unset so W90 defaults apply; Phase-B remediation sets search_shells.
    wdata_i = 1
    ss = getattr(epw, "search_shells", None)
    if ss is not None and int(ss) > 0:
        lines.append(f"  wdata({wdata_i})  = 'search_shells = {int(ss)}'")
        wdata_i += 1
    kt = getattr(epw, "kmesh_tol", None)
    if kt is not None and float(kt) > 0.0:
        # Scientific format is accepted by Wannier90
        lines.append(f"  wdata({wdata_i})  = 'kmesh_tol = {float(kt):.3e}'")
        wdata_i += 1
    if fermi_eV is not None:
        # Avoid efermig failure on a poorly interpolated fine mesh.
        lines.extend(
            [
                "",
                "  efermi_read = .true.",
                f"  fermi_energy = {fermi_eV:.6f}",
            ]
        )

    lines.extend(
        [
            "",
            f"  nk1         = {int(nkc[0])}",
            f"  nk2         = {int(nkc[1])}",
            f"  nk3         = {int(nkc[2])}",
            "",
            f"  nq1         = {int(nqc[0])}",
            f"  nq2         = {int(nqc[1])}",
            f"  nq3         = {int(nqc[2])}",
            "",
            f"  nkf1        = {int(nkf[0])}",
            f"  nkf2        = {int(nkf[1])}",
            f"  nkf3        = {int(nkf[2])}",
            "",
            f"  nqf1        = {int(nqf[0])}",
            f"  nqf2        = {int(nqf[1])}",
            f"  nqf3        = {int(nqf[2])}",
        ]
    )

    if epw.eliashberg:
        # Isotropic-oriented flags; anisotropic laniso left off for screening
        lines.extend(
            [
                "",
                "  eliashberg  = .true.",
                "  limag       = .true.",
                "  lpade       = .true.",
                f"  muc         = {epw.mu_star}",
                "  nstemp      = 1",
                "  temps       = 10.0",
                "  nsiter      = 200",
                "  wscut       = 1.0",
                "  ephwrite    = .true.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "  eliashberg  = .false.",
            ]
        )

    # Optional Wannier exclude string only if user set a non-empty string in extras
    # (EPWConfig.bands_skipped is int in our schema — do NOT emit it as integer;
    # EPW expects character data like "exclude_bands = 1-5".)

    lines.append("/")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_epw_input(content: str, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def build_nscf_note() -> str:
    """Human-readable reminder of the EPW prerequisite steps."""
    return (
        "EPW requires: (1) SCF, (2) phonons with ldisp + fildvscf on coarse q, "
        "(3) EPW pp.py → save/, (4) NSCF on coarse k, (5) epw.x. "
        "SiSC-Forge automates a screening template; production needs tuned Wannier projs."
    )


def epw_material_notes(structure: Structure | None) -> str:
    """Short material-family notes for logs / metadata (isotropic screening)."""
    if structure is None:
        return ""
    symbols = {site.specie.symbol for site in structure}
    if {"Mg", "B"}.issubset(symbols):
        return (
            "MgB2-like: two-gap superconductor; screening EPW uses isotropic "
            "λ/ω_log average (not anisotropic multi-band Eliashberg). "
            "Production runs need tuned Wannier projections (B p / Mg s)."
        )
    if "N" in symbols and symbols & {"Nb", "Ti", "Zr", "Hf", "V", "Ta"}:
        return (
            "TM nitride: screening EPW template; soft modes and random Wannier "
            "projections can inflate λ — treat Tc as order-of-magnitude only."
        )
    return ""
