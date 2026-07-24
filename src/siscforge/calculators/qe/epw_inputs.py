"""Build minimal EPW input decks for workstation screening."""

from __future__ import annotations

from pathlib import Path

from siscforge.models.config import DFTConfig, EPWConfig


def build_epw_input(
    config: DFTConfig,
    *,
    prefix: str = "siscforge",
    outdir: str = "./out",
    dvscf_dir: str = "./save",
) -> str:
    """Return a coarse-grid ``epw.in`` suitable for metals (screening).

    Full production EPW workflows also need a prepared Wannier90 ``.win`` /
    NSCF step. This deck documents the SiSC-Forge defaults; users can replace
    it with a hand-tuned input while still using our parsers.
    """
    epw: EPWConfig = config.epw
    nkf = list(epw.nkf) + [6, 6, 6]
    nqf = list(epw.nqf) + [6, 6, 6]
    nkc = list(epw.nkc) + [4, 4, 4]
    nqc = list(epw.nqc) + [2, 2, 2]

    lines = [
        "--",
        "&inputepw",
        f"  prefix      = '{prefix}'",
        f"  outdir      = '{outdir}'",
        f"  dvscf_dir   = '{dvscf_dir}'",
        "  elph        = .true.",
        "  epbwrite    = .true.",
        "  epbread     = .false.",
        "  epwwrite    = .true.",
        "  epwread     = .false.",
        f"  nbndsub     = {epw.nbndsub if epw.nbndsub is not None else 8}",
        f"  bands_skipped = {epw.bands_skipped}",
        "  wannierize  = .true.",
        "  num_iter    = 300",
        "  dis_win_max = 20",
        "  dis_froz_max= 10",
        "  proj(1)     = 'random'",
        "  iverbosity  = 2",
        f"  fsthick     = {epw.fsthick}",
        f"  degaussw    = {epw.degaussw}",
        f"  degaussq    = {epw.degaussq}",
        f"  nkf1 = {int(nkf[0])}, nkf2 = {int(nkf[1])}, nkf3 = {int(nkf[2])}",
        f"  nqf1 = {int(nqf[0])}, nqf2 = {int(nqf[1])}, nqf3 = {int(nqf[2])}",
        f"  nk1  = {int(nkc[0])}, nk2  = {int(nkc[1])}, nk3  = {int(nkc[2])}",
        f"  nq1  = {int(nqc[0])}, nq2  = {int(nqc[1])}, nq3  = {int(nqc[2])}",
    ]
    if epw.eliashberg:
        lines.extend(
            [
                "  eliashberg  = .true.",
                f"  muc         = {epw.mu_star}",
            ]
        )
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
        "EPW requires: (1) SCF, (2) phonons on coarse q, (3) NSCF on coarse k "
        "with wavefunctions, (4) Wannierization, (5) epw.x. "
        "SiSC-Forge writes a screening epw.in; prepare Wannier projections for production."
    )
