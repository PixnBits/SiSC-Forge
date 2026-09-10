"""Pseudopotential discovery helpers (SSSP / common UPF naming)."""

from __future__ import annotations

import re
from pathlib import Path

from pymatgen.core import Structure

from siscforge.models.config import DFTConfig

# Preferred filename tokens (lower score = better) for auto-pick.
_PREFERENCE_TOKENS: list[tuple[str, int]] = [
    ("pbe", 0),
    ("pbesol", 1),
    ("pw91", 5),
    ("pz", 6),
    ("kjpaw", 0),
    ("paw", 1),
    ("uspp", 2),
    ("nc", 3),
    ("oncv", 1),
    ("sssp", 0),
    ("efficiency", 0),
    ("precision", 2),
]

# pslibrary / SSSP config token, e.g. pbe-n-kjpaw, pbe-spn-kjpaw, pbe-dn-rrkjus.
# The bare ``-n-`` would otherwise look like element N to infix matching.
_PSLIBRARY_CONFIG_RE = re.compile(
    r"(?:pbe|pbesol|pz|pw91|blyp)-(?:nlcc|spn|dn|n)-",
    re.IGNORECASE,
)
# GBRV scalar-relativistic flag: b_pbe_v1.4.uspp.F.UPF must not look like fluorine.
_GBRV_F_SUFFIX_RE = re.compile(r"\.f\.upf$", re.IGNORECASE)


class PseudoResolutionError(FileNotFoundError):
    """Raised when required UPFs cannot be resolved — message is user-facing."""


def list_upf_files(pseudo_dir: str | Path) -> list[Path]:
    """List ``*.upf`` / ``*.UPF`` files in *pseudo_dir*."""
    d = Path(pseudo_dir)
    if not d.is_dir():
        raise PseudoResolutionError(
            f"pseudo_dir does not exist or is not a directory: {d}\n"
            "Download SSSP or PseudoDojo UPF files and set dft.pseudo_dir to that folder.\n"
            "See docs/examples/nbN_phonon_qe.md."
        )
    files = sorted(set(d.glob("*.upf")) | set(d.glob("*.UPF")))
    if not files:
        raise PseudoResolutionError(
            f"No *.upf files found in {d}.\n"
            "Expected Quantum ESPRESSO UPF pseudopotentials (e.g. SSSP efficiency).\n"
            "https://www.materialscloud.org/discover/sssp/table/efficiency"
        )
    return files


def _species_haystack(name: str) -> str:
    """Filename with non-species tokens stripped so infix match is safe."""
    s = name.lower()
    s = _PSLIBRARY_CONFIG_RE.sub("", s)
    s = _GBRV_F_SUFFIX_RE.sub(".upf", s)
    return s


def _score_upf(name: str, element: str) -> tuple[int, int, str]:
    """Sort key: lower is better."""
    lower = name.lower()
    el = element.lower()
    # Must look related to element. Require a non-letter boundary after the
    # symbol so short names (e.g. B) do not match longer ones (Ba.*.UPF).
    starts_with_el = lower.startswith(el) and (
        len(lower) == len(el) or not lower[len(el)].isalpha()
    )
    hay = _species_haystack(name)
    if not (
        starts_with_el
        or f"_{el}_" in hay
        or f".{el}." in hay
        or f"-{el}-" in hay
    ):
        return (999, 999, name)

    score = 10
    for token, weight in _PREFERENCE_TOKENS:
        if token in lower:
            score += weight
    # Prefer shorter names slightly
    return (score, len(name), name)


def match_upf_for_element(element: str, upf_files: list[Path]) -> str | None:
    """Return best UPF filename for *element*, or None."""
    ranked = sorted((_score_upf(p.name, element), p.name) for p in upf_files)
    if not ranked or ranked[0][0][0] >= 999:
        return None
    return ranked[0][1]


def resolve_pseudopotentials(
    structure: Structure,
    config: DFTConfig,
) -> dict[str, str]:
    """Map elements → UPF filenames with clear errors for missing species.

    Resolution order:
    1. Explicit ``config.pseudopotentials`` map (must cover all elements).
    2. Auto-scan ``config.pseudo_dir`` with SSSP-friendly name heuristics.
    """
    elements = sorted({site.specie.symbol for site in structure})

    if config.pseudopotentials:
        missing = [el for el in elements if el not in config.pseudopotentials]
        if missing:
            raise PseudoResolutionError(
                f"dft.pseudopotentials is missing entries for: {missing}.\n"
                f"Provided: {sorted(config.pseudopotentials)}.\n"
                "Add the missing element → UPF filename mappings, or clear "
                "pseudopotentials and set pseudo_dir for auto-discovery."
            )
        # Verify files exist only when pseudo_dir is set *and* is a real directory
        # (allows unit tests to pass a map without shipping binary UPF files).
        if config.pseudo_dir:
            pdir = Path(config.pseudo_dir)
            if pdir.is_dir():
                for el in elements:
                    fname = config.pseudopotentials[el]
                    if not (pdir / fname).is_file():
                        raise PseudoResolutionError(
                            f"Pseudopotential file for {el} not found: {pdir / fname}\n"
                            f"Check dft.pseudo_dir and dft.pseudopotentials['{el}']."
                        )
        return {el: config.pseudopotentials[el] for el in elements}

    if not config.pseudo_dir:
        raise PseudoResolutionError(
            "No pseudopotentials configured.\n"
            "Set dft.pseudo_dir to a directory of UPF files (recommended: SSSP PBE),\n"
            "or set dft.pseudopotentials: {Element: filename.upf}.\n"
            "Example:\n"
            "  dft:\n"
            "    pseudo_dir: /path/to/sssp_efficiency\n"
            "Docs: docs/examples/nbN_phonon_qe.md"
        )

    upfs = list_upf_files(config.pseudo_dir)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for el in elements:
        match = match_upf_for_element(el, upfs)
        if match is None:
            missing.append(el)
        else:
            resolved[el] = match

    if missing:
        available = [p.name for p in upfs[:30]]
        raise PseudoResolutionError(
            f"Could not auto-resolve UPF for elements: {missing}\n"
            f"pseudo_dir: {config.pseudo_dir}\n"
            f"Available (first 30): {available}\n"
            "Fix: add explicit dft.pseudopotentials entries for missing elements,\n"
            "or install the corresponding SSSP/PseudoDojo files."
        )
    return resolved


def describe_pseudo_dir(pseudo_dir: str | Path) -> dict[str, object]:
    """Return a summary useful for CLI diagnostics."""
    try:
        files = list_upf_files(pseudo_dir)
        return {
            "path": str(pseudo_dir),
            "n_upf": len(files),
            "files": [p.name for p in files[:50]],
            "ok": True,
        }
    except PseudoResolutionError as exc:
        return {"path": str(pseudo_dir), "ok": False, "error": str(exc)}
