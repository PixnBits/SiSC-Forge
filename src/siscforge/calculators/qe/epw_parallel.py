"""EPW MPI topology validation (nproc / npool / nimage).

EPW v5.x aborts in ``epw_readin`` when::

    nproc ≠ npool × nimage × (nbgrp)

and image parallelization is only valid on coarse-grid calculations. SiSC-Forge
uses the fine-grid / Eliashberg path → **nimage must be 1**.

Desktop failure mode (observed): ``mpirun -np 8 epw.x`` with ``npool=1`` (or
``-npool`` omitted) wastes multi-hour DFPT and dies only at the last step.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EPWParallelPlan:
    """Resolved EPW parallel topology ready for launch."""

    nproc: int
    npool: int
    nimage: int
    nbgrp: int
    ok: bool
    message: str
    """Human-readable status / error / auto-fix note."""

    auto_fixed: bool = False
    """True when npool was adjusted from the user-supplied value."""

    original_npool: int | None = None
    """npool before auto-fix (if any)."""

    @property
    def product(self) -> int:
        return self.npool * self.nimage * self.nbgrp


def validate_epw_parallel(
    nproc: int,
    npool: int = 1,
    nimage: int = 1,
    *,
    nbgrp: int = 1,
    fine_grid: bool = True,
) -> EPWParallelPlan:
    """Validate EPW parallel topology without mutating config.

    Parameters
    ----------
    nproc:
        MPI ranks (``mpirun -np``).
    npool:
        EPW k-point pools (``epw.x -npool``).
    nimage:
        Image parallelization (must be 1 for fine-grid / Eliashberg path).
    nbgrp:
        Band groups (SiSC-Forge does not expose this; treat as 1).
    fine_grid:
        When True (default SiSC-Forge path), nimage must be 1.

    Returns
    -------
    EPWParallelPlan with ``ok=False`` and an actionable ``message`` on error.
    Does **not** auto-correct — see :func:`resolve_epw_parallel`.
    """
    nproc = int(nproc)
    npool = int(npool)
    nimage = int(nimage)
    nbgrp = int(nbgrp)

    if nproc < 1:
        return EPWParallelPlan(
            nproc=nproc,
            npool=npool,
            nimage=nimage,
            nbgrp=nbgrp,
            ok=False,
            message=(
                f"Invalid nproc={nproc}: must be >= 1. "
                f"Set dft.nproc to the number of MPI ranks for mpirun."
            ),
        )
    if npool < 1:
        return EPWParallelPlan(
            nproc=nproc,
            npool=npool,
            nimage=nimage,
            nbgrp=nbgrp,
            ok=False,
            message=(
                f"Invalid epw.npool={npool}: must be >= 1. "
                f"For desktop fine-grid EPW, set epw.npool equal to dft.nproc "
                f"(e.g. both 8)."
            ),
        )
    if nimage < 1:
        return EPWParallelPlan(
            nproc=nproc,
            npool=npool,
            nimage=nimage,
            nbgrp=nbgrp,
            ok=False,
            message=f"Invalid nimage={nimage}: must be >= 1.",
        )
    if nbgrp < 1:
        return EPWParallelPlan(
            nproc=nproc,
            npool=npool,
            nimage=nimage,
            nbgrp=nbgrp,
            ok=False,
            message=f"Invalid nbgrp={nbgrp}: must be >= 1.",
        )

    if fine_grid and nimage != 1:
        return EPWParallelPlan(
            nproc=nproc,
            npool=npool,
            nimage=nimage,
            nbgrp=nbgrp,
            ok=False,
            message=(
                f"Image parallelization (nimage={nimage}) is not allowed for "
                f"fine-grid / Eliashberg EPW used by SiSC-Forge. "
                f"Set nimage=1 and use npool only. "
                f"Suggested: nproc={nproc}, epw.npool={nproc}, nimage=1."
            ),
        )

    product = npool * nimage * nbgrp
    if product != nproc:
        return EPWParallelPlan(
            nproc=nproc,
            npool=npool,
            nimage=nimage,
            nbgrp=nbgrp,
            ok=False,
            message=(
                f"EPW parallel topology invalid: nproc ({nproc}) must equal "
                f"npool × nimage × nbgrp ({npool} × {nimage} × {nbgrp} = {product}). "
                f"EPW aborts with: "
                f"'Number of processes must be equal to product of number of "
                f"pools and number of images'. "
                f"Desktop fix: set epw.npool={nproc} and nimage=1 "
                f"(or set dft.nproc={product})."
            ),
        )

    return EPWParallelPlan(
        nproc=nproc,
        npool=npool,
        nimage=nimage,
        nbgrp=nbgrp,
        ok=True,
        message=(
            f"EPW parallel ok: nproc={nproc} = npool={npool} × nimage={nimage}"
            + (f" × nbgrp={nbgrp}" if nbgrp != 1 else "")
        ),
    )


def resolve_epw_parallel(
    nproc: int,
    npool: int = 1,
    nimage: int = 1,
    *,
    nbgrp: int = 1,
    fine_grid: bool = True,
    auto_fix: bool = True,
) -> EPWParallelPlan:
    """Validate and optionally auto-correct EPW parallel topology.

    Desktop default (``auto_fix=True``, fine-grid):
    - Force ``nimage=1`` if needed (with note) only when auto_fix and nimage!=1
      would otherwise fail — actually for nimage!=1 we fail even with auto_fix
      unless we can set nimage=1; we set nimage=1 and npool=nproc.
    - When ``nproc > 1`` and ``npool * nimage * nbgrp != nproc``, set
      ``npool = nproc`` (with nimage=1, nbgrp=1).

    When ``auto_fix=False`` (strict), return the raw validation error.
    """
    nproc = max(1, int(nproc))
    npool = int(npool) if int(npool) >= 1 else 1
    nimage = int(nimage) if int(nimage) >= 1 else 1
    nbgrp = int(nbgrp) if int(nbgrp) >= 1 else 1
    original_npool = npool
    original_nimage = nimage

    plan = validate_epw_parallel(
        nproc, npool, nimage, nbgrp=nbgrp, fine_grid=fine_grid
    )
    if plan.ok:
        return plan

    if not auto_fix:
        return plan

    # Auto-fix for fine-grid desktop path: nimage=1, npool=nproc, nbgrp=1
    if fine_grid:
        fixed_npool = nproc
        fixed_nimage = 1
        fixed_nbgrp = 1
        fixed = validate_epw_parallel(
            nproc,
            fixed_npool,
            fixed_nimage,
            nbgrp=fixed_nbgrp,
            fine_grid=True,
        )
        if not fixed.ok:
            return plan  # should not happen

        notes: list[str] = []
        if original_nimage != 1:
            notes.append(f"nimage {original_nimage}→1 (fine-grid)")
        if original_npool != fixed_npool:
            notes.append(f"npool {original_npool}→{fixed_npool}")
        detail = "; ".join(notes) if notes else "topology adjusted"
        return EPWParallelPlan(
            nproc=nproc,
            npool=fixed_npool,
            nimage=1,
            nbgrp=1,
            ok=True,
            message=(
                f"EPW parallel: auto-set npool={fixed_npool} to match "
                f"nproc={nproc} (nimage=1) [{detail}]"
            ),
            auto_fixed=True,
            original_npool=original_npool,
        )

    return plan


def epw_npool_cli_args(npool: int) -> list[str]:
    """Return ``['-npool', N]`` for any npool >= 1 (always pass for EPW)."""
    n = max(1, int(npool))
    return ["-npool", str(n)]
