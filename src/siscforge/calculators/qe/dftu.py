"""DFT+U (Hubbard) helpers for Quantum ESPRESSO — Phase 3.1.

Provides:
- species / U,J resolution from :class:`~siscforge.models.config.DFTUConfig`
- pw.x SYSTEM namelist extras **or** HUBBARD card (exactly one dialect)
- parse of total energy, magnetization, and basic occupancy proxies
- mock :class:`~siscforge.models.results.DFTUResult` for dry-run

**Out of scope (later packages):** Wannierization (P3.2), TRIQS/solid_dmft
(P3.3), pairing eigenvalue (P3.4), oxygen-vacancy enumeration (P3.5).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from siscforge import __version__
from siscforge.models.config import DFTConfig, DFTUConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import DFTUResult

# Correlated metals / rare earths commonly treated with DFT+U in nickelates
# and related oxides. Used only when hubbard_species is empty.
_DEFAULT_HUBBARD_ELEMENTS: frozenset[str] = frozenset(
    {
        "Ti",
        "V",
        "Cr",
        "Mn",
        "Fe",
        "Co",
        "Ni",
        "Cu",
        "Ru",
        "Rh",
        "Pd",
        "Ir",
        "Pt",
        "Ce",
        "Pr",
        "Nd",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "Lu",
    }
)


def dftu_is_enabled(dft: DFTConfig | None, *, force: bool = False) -> bool:
    """Return True when DFT+U should run for this campaign / calculator."""
    if force:
        return True
    if dft is None:
        return False
    if bool(getattr(dft, "do_dftu", False)):
        return True
    dftu = getattr(dft, "dftu", None)
    return bool(dftu is not None and getattr(dftu, "enabled", False))


def resolve_hubbard_species(
    structure: Structure,
    dftu: DFTUConfig,
) -> list[str]:
    """Ordered unique Hubbard species present in *structure*.

    When ``hubbard_species`` is set explicitly, **every** requested element must
    appear in the cell. Silent fallback to other correlated metals is refused
    so a mismatched campaign cannot run a scientifically different calculation.
    """
    present = {str(sp.symbol) for sp in structure.composition.elements}
    if dftu.hubbard_species:
        missing = [s for s in dftu.hubbard_species if s not in present]
        if missing:
            raise ValueError(
                f"hubbard_species includes {missing} not present in structure "
                f"(elements={sorted(present)}). Fix campaign YAML; refusing "
                "silent fallback to other correlated metals."
            )
        seen: set[str] = set()
        ordered: list[str] = []
        for s in dftu.hubbard_species:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
        return ordered
    # Auto: correlated metals present in the structure
    auto = sorted(present & _DEFAULT_HUBBARD_ELEMENTS)
    if auto:
        return auto
    # Last resort: non-light element (oxide/nitride host)
    light = {"H", "He", "C", "N", "O", "F", "Ne", "Cl", "Ar"}
    metals = sorted(el for el in present if el not in light)
    return metals[:1] if metals else sorted(present)[:1]


def resolve_u_j_maps(
    structure: Structure,
    dftu: DFTUConfig,
) -> tuple[list[str], dict[str, float], dict[str, float]]:
    """Return ``(species, U_by_species, J_by_species)`` for the cell."""
    species = resolve_hubbard_species(structure, dftu)
    u_map: dict[str, float] = {}
    j_map: dict[str, float] = {}
    for el in species:
        u_map[el] = float(dftu.U_by_species.get(el, dftu.U_eV))
        j_map[el] = float(dftu.J_by_species.get(el, dftu.J_eV))
    return species, u_map, j_map


def species_type_index(structure: Structure) -> dict[str, int]:
    """Map element symbol → 1-based ATOMIC_SPECIES index (QE convention).

    Order matches pymatgen ``PWInput`` / QE ``ATOMIC_SPECIES``: unique symbols
    sorted by atomic number (N before Nb). Site-appearance order is wrong for
    Hubbard_U(i) / starting_magnetization(i) indices.
    """
    from siscforge.calculators.qe.epw_inputs import qe_atomic_type_symbols

    order = qe_atomic_type_symbols(structure)
    return {sym: i + 1 for i, sym in enumerate(order)}


def _validate_hubbard_j_kind(dftu: DFTUConfig, j_map: dict[str, float]) -> None:
    """Reject full Liechtenstein kind with scalar J we cannot express."""
    if int(dftu.lda_plus_u_kind) != 1:
        return
    if any(float(v) > 0.0 for v in j_map.values()) or float(dftu.J_eV) > 0.0:
        raise ValueError(
            "lda_plus_u_kind=1 (full Liechtenstein) needs anisotropic Hubbard_J "
            "parameters not expressible as scalar J_eV/J_by_species. "
            "Use lda_plus_u_kind=0 (simplified, J0) or set all J values to 0."
        )


def hubbard_system_extras(
    structure: Structure,
    dftu: DFTUConfig,
    *,
    syntax: str | None = None,
) -> dict[str, Any]:
    """Build SYSTEM namelist extras for DFT+U pw.x input.

    *syntax* is ``\"namelist\"`` (QE 6.x classic ``lda_plus_u`` / ``Hubbard_U``)
    or ``\"card\"`` (QE ≥ 7.1 HUBBARD card only — spin/mag fields only here).
    Defaults to ``dftu.hubbard_syntax``. Exactly one Hubbard dialect is used
    per input; never both namelist U and HUBBARD card together.
    """
    dialect = (syntax or getattr(dftu, "hubbard_syntax", "namelist") or "namelist").lower()
    if dialect not in {"namelist", "card"}:
        raise ValueError(f"Unknown hubbard_syntax {dialect!r}; use 'namelist' or 'card'")

    species, u_map, j_map = resolve_u_j_maps(structure, dftu)
    _validate_hubbard_j_kind(dftu, j_map)
    type_idx = species_type_index(structure)
    extras: dict[str, Any] = {
        "nspin": int(dftu.nspin),
    }
    if dftu.nspin >= 2:
        for el in species:
            idx = type_idx.get(el)
            if idx is None:
                continue
            mag = dftu.starting_magnetization.get(
                el, dftu.default_starting_magnetization
            )
            extras[f"starting_magnetization({idx})"] = float(mag)
        for el, mag in dftu.starting_magnetization.items():
            idx = type_idx.get(el)
            if idx is not None and f"starting_magnetization({idx})" not in extras:
                extras[f"starting_magnetization({idx})"] = float(mag)

    if dialect == "card":
        return extras

    extras["lda_plus_u"] = True
    extras["lda_plus_u_kind"] = int(dftu.lda_plus_u_kind)
    # Classic SYSTEM namelist uses U_projection_type (not Hubbard_projectors,
    # which is the parenthesized option of the QE ≥7.1 HUBBARD card only).
    extras["U_projection_type"] = dftu.hubbard_projectors
    for el in species:
        idx = type_idx.get(el)
        if idx is None:
            continue
        extras[f"Hubbard_U({idx})"] = float(u_map[el])
        j_val = float(j_map.get(el, 0.0))
        if j_val > 0.0:
            # Simplified (kind=0) uses Hubbard_J0 only
            extras[f"Hubbard_J0({idx})"] = j_val
    return extras


def append_hubbard_card(
    pw_text: str,
    structure: Structure,
    dftu: DFTUConfig,
) -> str:
    """Append a QE ≥ 7.1 ``HUBBARD`` card (card dialect only).

    Call this **only** when ``dftu.hubbard_syntax == \"card\"``. Do not combine
    with namelist ``Hubbard_U(*)`` / ``lda_plus_u`` — QE 6.x rejects the card,
    and dual syntax is invalid.
    """
    if re.search(r"^\s*HUBBARD\b", pw_text, flags=re.IGNORECASE | re.MULTILINE):
        return pw_text
    species, u_map, j_map = resolve_u_j_maps(structure, dftu)
    _validate_hubbard_j_kind(dftu, j_map)
    if not species:
        return pw_text
    lines = [f"HUBBARD ({dftu.hubbard_projectors})"]
    for el in species:
        manifold = resolve_hubbard_manifold(el, dftu)
        lines.append(f"  U {el}-{manifold} {u_map[el]:.4f}")
        j_val = float(j_map.get(el, 0.0))
        if j_val > 0.0:
            # kind=0 simplified → J0 (scalar Hund). Full J is rejected above.
            lines.append(f"  J0 {el}-{manifold} {j_val:.4f}")
    card = "\n".join(lines) + "\n"
    text = pw_text if pw_text.endswith("\n") else pw_text + "\n"
    return text + card


# Species with a safe built-in orbital heuristic for HUBBARD card lines.
_RE_4F = {
    "Ce",
    "Pr",
    "Nd",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
}
_D4 = {"Ru", "Rh", "Pd", "Ag", "Cd", "Y", "Zr", "Nb", "Mo", "Tc"}
_D5 = {"Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au"}
_D3 = {
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
}


def _default_manifold(element: str) -> str | None:
    """Heuristic correlated manifold, or None when guessing would be unsafe."""
    if element in _RE_4F:
        return "4f"
    if element in _D4:
        return "4d"
    if element in _D5:
        return "5d"
    if element in _D3:
        return "3d"
    return None


def resolve_hubbard_manifold(element: str, dftu: DFTUConfig) -> str:
    """Resolve the HUBBARD-card orbital label for *element*.

    Prefer ``dftu.hubbard_manifolds[el]``. Fall back to a TM/RE heuristic only
    for well-known correlated metals; otherwise raise so oxygen *p* (etc.) is
    never silently emitted as ``3d``.
    """
    maps = getattr(dftu, "hubbard_manifolds", None) or {}
    if element in maps:
        return str(maps[element])
    guessed = _default_manifold(element)
    if guessed is not None:
        return guessed
    raise ValueError(
        f"No Hubbard manifold for element {element!r}. "
        f"Set dft.dftu.hubbard_manifolds, e.g. {{{element}: '2p'}}, "
        "or remove it from hubbard_species. Silent 3d fallback is not allowed "
        "for non-TM/RE species (would mismatch UPF projectors)."
    )

def parse_magnetization(text: str) -> tuple[float | None, float | None]:
    """Extract total and absolute magnetization (μ_B) from pw.x stdout."""
    total = None
    absolute = None
    m_tot = re.findall(
        r"total magnetization\s*=\s*([-\d.Ee+]+)\s*Bohr",
        text,
        flags=re.IGNORECASE,
    )
    if m_tot:
        total = float(m_tot[-1])
    m_abs = re.findall(
        r"absolute magnetization\s*=\s*([-\d.Ee+]+)\s*Bohr",
        text,
        flags=re.IGNORECASE,
    )
    if m_abs:
        absolute = float(m_abs[-1])
    return total, absolute


def parse_atomic_magnetic_moments(text: str) -> dict[str, float]:
    """Parse per-atom magnetic moments when QE prints them.

    Prefer the value after ``magn`` / ``moment`` — never the atomic charge
    that often appears earlier on the same line.
    """
    moments: dict[str, float] = {}
    # e.g. "atom:    1 charge:   8.1234  magn:  1.1000"
    site_pat = re.compile(
        r"atom:\s*(\d+)\s+.*?\bmagn(?:etization|etic)?\b\s*[:=]?\s*([-\d.Ee+]+)",
        re.IGNORECASE,
    )
    for m in site_pat.finditer(text):
        moments[f"atom_{int(m.group(1))}"] = float(m.group(2))
    if not moments:
        site_pat2 = re.compile(
            r"atom:\s*(\d+)\s+.*?\bmoment\b\s*[:=]\s*([-\d.Ee+]+)",
            re.IGNORECASE,
        )
        for m in site_pat2.finditer(text):
            moments[f"atom_{int(m.group(1))}"] = float(m.group(2))
    if moments:
        return moments
    simple = re.findall(
        r"magnetic moment\s*(?:of|on)?\s*([A-Za-z]+)\s*[:=]\s*([-\d.Ee+]+)",
        text,
        flags=re.IGNORECASE,
    )
    for el, val in simple:
        moments[el] = float(val)
    return moments


def parse_occupancy_proxy(text: str) -> dict[str, float]:
    """Best-effort Hubbard occupancy / atomic charge summary from pw.x text."""
    occ: dict[str, float] = {}
    for m in re.finditer(
        r"Hubbard\s+(?:occupation|occupancy)[^\n]*?([A-Za-z]+)\s*[:=]\s*([-\d.Ee+]+)",
        text,
        flags=re.IGNORECASE,
    ):
        occ[f"hubbard_{m.group(1)}"] = float(m.group(2))
    for m in re.finditer(
        r"Tr\[ns\(na\)\]\s*=\s*([-\d.Ee+]+)",
        text,
        flags=re.IGNORECASE,
    ):
        key = f"Tr_ns_{len(occ)}"
        occ[key] = float(m.group(1))
    return occ


def _uniform_scalar(mapping: dict[str, float], default: float) -> float | None:
    """Return a scalar only when all map values agree; else None."""
    if not mapping:
        return float(default)
    vals = [float(v) for v in mapping.values()]
    if all(abs(v - vals[0]) < 1e-12 for v in vals):
        return float(vals[0])
    return None


def parse_dftu_output(
    path_or_text: Path | str,
    *,
    dftu: DFTUConfig,
    structure: Structure | None = None,
    quality_tag: str = "screening",
    extra_raw: dict[str, Any] | None = None,
) -> DFTUResult:
    """Parse a DFT+U pw.x output into :class:`DFTUResult`."""
    from siscforge.calculators.qe.parser import (
        parse_fermi_energy_eV,
        parse_pw_energy_from_text,
        resolve_text_or_path,
    )

    text, source_name = resolve_text_or_path(path_or_text)
    energy = parse_pw_energy_from_text(text)
    job_done = "JOB DONE" in text.upper()
    fermi = parse_fermi_energy_eV(text)
    total_m, abs_m = parse_magnetization(text)
    moments = parse_atomic_magnetic_moments(text)
    occ = parse_occupancy_proxy(text)
    # Do not guess metallicity from Fermi-energy lines or smearing: insulating
    # DFT+U runs commonly use smearing and still print a Fermi level. Leave
    # unknown until a band-gap / DOS parse exists (P3.x+).
    is_metallic: bool | None = None
    # Require both a final energy and JOB DONE. Intermediate total-energy lines
    # from a killed SCF must not be treated as success.
    status = "ok" if energy is not None and job_done else "failed"

    species: list[str] = list(dftu.hubbard_species)
    u_map = dict(dftu.U_by_species)
    j_map = dict(dftu.J_by_species)
    if structure is not None:
        species, u_map, j_map = resolve_u_j_maps(structure, dftu)

    scalar_u = _uniform_scalar(u_map, float(dftu.U_eV))
    scalar_j = _uniform_scalar(j_map, float(dftu.J_eV))

    raw: dict[str, Any] = {
        "source": source_name,
        "job_done": job_done,
        "pathway": "dftu",
        "extension_hooks": {
            "p3_2_wannier": "CandidateEvaluation.wannier (WannierResult) — P3.2 shipped",
            "p3_3_dmft": "parallel CandidateEvaluation.dmft field (not yet)",
            "p3_4_pairing": "map leading eigenvalue → performance_score",
        },
    }
    if extra_raw:
        raw.update(extra_raw)

    qtag = (
        quality_tag
        if quality_tag in {"screening", "production", "mock", "unknown"}
        else "screening"
    )

    return DFTUResult(
        U_eV=scalar_u,
        J_eV=scalar_j,
        U_by_species=u_map,
        J_by_species=j_map,
        hubbard_species=species,
        hubbard_projectors=dftu.hubbard_projectors,
        occupancy_summary=occ,
        magnetic_moments=moments,
        total_magnetization=total_m,
        absolute_magnetization=abs_m,
        total_energy_eV=energy,
        is_metallic=is_metallic,
        fermi_energy_eV=fermi,
        status=status,
        quality_tag=qtag,  # type: ignore[arg-type]
        raw=raw,
        provenance=Provenance(
            source="qe_dftu",
            software={"siscforge": __version__},
            parameters={
                "U_eV": scalar_u,
                "J_eV": scalar_j,
                "hubbard_species": species,
                "hubbard_projectors": dftu.hubbard_projectors,
            },
            notes="DFT+U pw.x parse (P3.1)",
        ),
    )


def mock_dftu_result(
    *,
    seed: str,
    dftu: DFTUConfig | None = None,
    formula: str = "",
    material_family: str = "other",
    quality_tag: str = "mock",
) -> DFTUResult:
    """Deterministic placeholder DFTUResult for dry-run / mock calculator."""
    cfg = dftu or DFTUConfig(enabled=True)
    digest = hashlib.sha256(f"{seed}:dftu".encode()).hexdigest()
    r = int(digest[:8], 16) / 0xFFFFFFFF

    species = list(cfg.hubbard_species) or (
        ["Ni"] if material_family == "nickelate" or "Ni" in formula else ["M"]
    )
    u_map = {el: float(cfg.U_by_species.get(el, cfg.U_eV)) for el in species}
    j_map = {el: float(cfg.J_by_species.get(el, cfg.J_eV)) for el in species}
    base_occ = 8.0 if any(s in {"Ni", "Cu", "Fe", "Co"} for s in species) else 6.0 + r
    occ = {f"hubbard_{species[0]}": round(base_occ - 0.3 * r, 3)}
    mom = round(0.8 + 1.4 * r, 3)
    total_m = round(mom * (1.0 if material_family == "nickelate" else 0.5 + r), 3)
    energy = round(-150.0 - 40.0 * r, 4)
    scalar_u = _uniform_scalar(u_map, float(cfg.U_eV))
    scalar_j = _uniform_scalar(j_map, float(cfg.J_eV))
    qtag = (
        quality_tag
        if quality_tag in {"screening", "production", "mock", "unknown"}
        else "mock"
    )

    return DFTUResult(
        U_eV=scalar_u,
        J_eV=scalar_j,
        U_by_species=u_map,
        J_by_species=j_map,
        hubbard_species=species,
        hubbard_projectors=cfg.hubbard_projectors,
        occupancy_summary=occ,
        magnetic_moments={species[0]: mom},
        total_magnetization=total_m,
        absolute_magnetization=round(abs(total_m) + 0.1 * r, 3),
        total_energy_eV=energy,
        is_metallic=True,
        fermi_energy_eV=round(5.0 + 3.0 * r, 3),
        status="mock",
        quality_tag=qtag,  # type: ignore[arg-type]
        raw={
            "method": "mock_dftu",
            "pathway": "dftu",
            "extension_hooks": {
                "p3_2_wannier": "CandidateEvaluation.wannier (WannierResult) — P3.2 shipped",
                "p3_3_dmft": "parallel CandidateEvaluation.dmft field (not yet)",
                "p3_4_pairing": "map leading eigenvalue → performance_score",
            },
        },
        provenance=Provenance(
            source="mock_calculator",
            software={"siscforge": __version__},
            parameters={"U_eV": cfg.U_eV, "J_eV": cfg.J_eV, "species": species},
            notes="dry-run DFT+U placeholder (P3.1)",
        ),
    )


# ---------------------------------------------------------------------------
# DFT+U checkpoint fingerprint (structure + calc settings + U/J)
# ---------------------------------------------------------------------------

DFTU_CONFIG_SIDECAR = "siscforge_dftu_config.json"
DFTU_RELAX_SIDECAR = "siscforge_dftu_relax.json"


def _structure_geometry_fp(structure: Structure) -> dict[str, Any]:
    """Full cell geometry for resume gates (lattice + fractional sites)."""
    lat = structure.lattice
    sites: list[dict[str, Any]] = []
    for site in structure:
        sites.append(
            {
                "el": str(site.specie.symbol),
                "frac": [round(float(x), 6) for x in site.frac_coords],
            }
        )
    return {
        "formula": structure.composition.reduced_formula,
        "nsites": len(structure),
        "elements": sorted({str(sp.symbol) for sp in structure.composition.elements}),
        "lattice_abc": [round(float(x), 6) for x in (lat.a, lat.b, lat.c)],
        "lattice_angles": [
            round(float(x), 4) for x in (lat.alpha, lat.beta, lat.gamma)
        ],
        "sites": sites,
    }


def _file_sha256(path: Path) -> str | None:
    """Content digest of a UPF (or any file); None if unreadable."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _resolved_pseudo_fp(
    structure: Structure,
    dft: DFTConfig | None,
) -> dict[str, Any]:
    """Resolved per-species UPF names + content digests for resume gates.

    Config-only ``pseudopotentials`` maps miss in-place UPF replacement and
    auto-discovery changes; hashing the resolved files closes that hole.
    """
    if dft is None:
        return {"resolved": {}, "pseudo_dir": None}
    resolved: dict[str, str] = {}
    try:
        from siscforge.calculators.qe.pseudos import resolve_pseudopotentials

        resolved = {
            str(k): str(v) for k, v in resolve_pseudopotentials(structure, dft).items()
        }
    except Exception:
        # Unit tests / dry paths may lack UPFs; fall back to configured map.
        resolved = {
            str(k): str(v) for k, v in sorted((dft.pseudopotentials or {}).items())
        }
    pdir = Path(dft.pseudo_dir).resolve() if dft.pseudo_dir else None
    digests: dict[str, dict[str, Any]] = {}
    for el, fname in sorted(resolved.items()):
        entry: dict[str, Any] = {"file": fname, "sha256": None}
        if pdir is not None:
            fpath = pdir / fname
            if fpath.is_file():
                entry["sha256"] = _file_sha256(fpath)
        digests[el] = entry
    return {
        "pseudo_dir": str(pdir) if pdir is not None else dft.pseudo_dir,
        "resolved": digests,
    }


def _dft_calc_fp(
    structure: Structure,
    dft: DFTConfig | None,
) -> dict[str, Any]:
    """Calculation-affecting DFTConfig fields (k-mesh, cutoffs, pseudos, …)."""
    if dft is None:
        return {}
    return {
        "ecutwfc": float(dft.ecutwfc),
        "ecutrho": float(dft.ecutrho),
        "kpoints": [int(x) for x in (dft.kpoints or [])],
        "conv_thr": float(dft.conv_thr),
        "forc_conv_thr": float(dft.forc_conv_thr),
        "press_conv_thr": float(dft.press_conv_thr),
        "occupations": str(dft.occupations),
        "smearing": str(dft.smearing),
        "degauss": float(dft.degauss),
        "nbnd": dft.nbnd,
        "pseudo_dir": dft.pseudo_dir,
        # Configured map (may be empty when auto-discovery is used).
        "pseudopotentials": {
            str(k): str(v) for k, v in sorted((dft.pseudopotentials or {}).items())
        },
        # Resolved filenames + content digests (v3).
        "pseudos_resolved": _resolved_pseudo_fp(structure, dft),
        "do_relax": bool(dft.do_relax),
        "quality_tag": str(dft.quality_tag),
    }


def dftu_config_fingerprint(
    structure: Structure,
    dftu: DFTUConfig,
    *,
    dft: DFTConfig | None = None,
    quality_tag: str | None = None,
    stage: str = "scf",
    hubbard_on_relax: bool | None = None,
) -> dict[str, Any]:
    """Fingerprint of structure + Hubbard + DFT settings for resume gates.

    Version 3 includes fractional coordinates, calculation-affecting
    ``DFTConfig`` fields, and **resolved UPF names + content digests** so
    in-place pseudo replacement or auto-discovery changes invalidate
    checkpoints. ``stage`` is ``\"scf\"`` or ``\"relax\"``.
    """
    species, u_map, j_map = resolve_u_j_maps(structure, dftu)
    qtag = quality_tag if quality_tag is not None else (
        str(dft.quality_tag) if dft is not None else "screening"
    )
    # Card dialect always resolves manifolds (raises on unknown non-TM/RE).
    # Namelist records explicit overrides only (indices, not U el-manifold lines).
    if getattr(dftu, "hubbard_syntax", "namelist") == "card":
        manifolds = {el: resolve_hubbard_manifold(el, dftu) for el in species}
    else:
        explicit = getattr(dftu, "hubbard_manifolds", None) or {}
        manifolds = {
            el: resolve_hubbard_manifold(el, dftu)
            for el in species
            if el in explicit
        }
    payload: dict[str, Any] = {
        "version": 3,
        "stage": stage,
        **_structure_geometry_fp(structure),
        "hubbard_species": list(species),
        "U_by_species": {k: round(float(v), 6) for k, v in sorted(u_map.items())},
        "J_by_species": {k: round(float(v), 6) for k, v in sorted(j_map.items())},
        "hubbard_projectors": dftu.hubbard_projectors,
        "hubbard_manifolds": {
            str(k): str(v)
            for k, v in sorted((getattr(dftu, "hubbard_manifolds", None) or {}).items())
        },
        "resolved_manifolds": {str(k): str(v) for k, v in sorted(manifolds.items())},
        "lda_plus_u_kind": int(dftu.lda_plus_u_kind),
        "nspin": int(dftu.nspin),
        "hubbard_syntax": getattr(dftu, "hubbard_syntax", "namelist"),
        "starting_magnetization": {
            str(k): round(float(v), 6)
            for k, v in sorted((dftu.starting_magnetization or {}).items())
        },
        "default_starting_magnetization": round(
            float(dftu.default_starting_magnetization), 6
        ),
        "do_relax_with_u": bool(dftu.do_relax_with_u),
        "quality_tag": qtag,
        "dft": _dft_calc_fp(structure, dft),
    }
    if stage == "relax":
        payload["hubbard_on_relax"] = (
            bool(hubbard_on_relax)
            if hubbard_on_relax is not None
            else bool(dftu.do_relax_with_u)
        )
    return payload


def write_dftu_config_sidecar(
    work_dir: Path | str,
    structure: Structure,
    dftu: DFTUConfig,
    *,
    dft: DFTConfig | None = None,
    quality_tag: str | None = None,
    stage: str = "scf",
    hubbard_on_relax: bool | None = None,
    sidecar_name: str | None = None,
) -> Path:
    """Persist fingerprint next to dftu.out / vc-relax.out after a successful step."""
    import json

    work_dir = Path(work_dir)
    name = sidecar_name or (
        DFTU_RELAX_SIDECAR if stage == "relax" else DFTU_CONFIG_SIDECAR
    )
    path = work_dir / name
    payload = dftu_config_fingerprint(
        structure,
        dftu,
        dft=dft,
        quality_tag=quality_tag,
        stage=stage,
        hubbard_on_relax=hubbard_on_relax,
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def dftu_checkpoint_matches(
    work_dir: Path | str,
    structure: Structure,
    dftu: DFTUConfig,
    *,
    dft: DFTConfig | None = None,
    quality_tag: str | None = None,
    out_name: str = "dftu.out",
    stage: str = "scf",
    hubbard_on_relax: bool | None = None,
    sidecar_name: str | None = None,
) -> bool:
    """True when output is JOB DONE and sidecar matches current config/structure."""
    import json

    work_dir = Path(work_dir)
    out = work_dir / out_name
    name = sidecar_name or (
        DFTU_RELAX_SIDECAR if stage == "relax" else DFTU_CONFIG_SIDECAR
    )
    side = work_dir / name
    if not out.is_file() or not side.is_file():
        return False
    try:
        body = out.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "JOB DONE" not in body.upper():
        return False
    try:
        saved = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = dftu_config_fingerprint(
        structure,
        dftu,
        dft=dft,
        quality_tag=quality_tag,
        stage=stage,
        hubbard_on_relax=hubbard_on_relax,
    )
    return saved == expected
