"""Minimal buffer-layer library for Silicon Integration (Phase 2 start).

Not a full multi-layer process model — lattice constants + short compatibility
notes so Si-feasibility can recommend a buffer and improve effective mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BufferEntry:
    """One buffer material with cubic (or effective cubic) lattice constant."""

    name: str
    lattice_a_ang: float
    """Conventional cubic *a* (Å), or effective in-plane spacing for matching."""

    notes: str = ""
    families: tuple[str, ...] = ("tm_nitride",)
    """Material families that commonly use this buffer."""


# Minimal library (extend later)
BUFFER_LIBRARY: dict[str, BufferEntry] = {
    "TiN": BufferEntry(
        name="TiN",
        lattice_a_ang=4.242,
        notes="Rock-salt seed; common nitride template on Si",
        families=("tm_nitride", "mgb2_boride"),
    ),
    "AlN": BufferEntry(
        name="AlN",
        lattice_a_ang=3.112,  # wurtzite a; used as effective in-plane proxy
        notes="Wide-gap nitride seed; thermal expansion buffer",
        families=("tm_nitride",),
    ),
    "ZrN": BufferEntry(
        name="ZrN",
        lattice_a_ang=4.577,
        notes="Rock-salt nitride; larger lattice than TiN/NbN",
        families=("tm_nitride",),
    ),
    "MgO": BufferEntry(
        name="MgO",
        lattice_a_ang=4.212,
        notes="Rock-salt oxide; used for oxides and some nitrides",
        families=("tm_nitride", "cuprate", "nickelate"),
    ),
    "direct_Si": BufferEntry(
        name="direct_Si",
        lattice_a_ang=5.4307,
        notes="No buffer — direct epitaxy on Si",
        families=("tm_nitride", "b_doped_si", "mgb2_boride", "other"),
    ),
}


def list_buffers_for_family(family: str) -> list[BufferEntry]:
    """Return buffers tagged for *family* (always includes direct_Si)."""
    out = [
        b
        for b in BUFFER_LIBRARY.values()
        if family in b.families or b.name == "direct_Si"
    ]
    # de-dupe by name preserving order
    seen: set[str] = set()
    unique: list[BufferEntry] = []
    for b in out:
        if b.name not in seen:
            seen.add(b.name)
            unique.append(b)
    if "direct_Si" not in seen:
        unique.insert(0, BUFFER_LIBRARY["direct_Si"])
    return unique


def buffer_as_dict(entry: BufferEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "lattice_a_ang": entry.lattice_a_ang,
        "notes": entry.notes,
    }
