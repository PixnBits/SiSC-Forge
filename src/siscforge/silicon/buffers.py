"""Buffer-layer library for Silicon Integration (Phase 2 / P2.2).

Data-driven tables of **single layers** and common **multi-layer stacks** with
lattice constants plus short process / chemical / thermal-window notes.

This is a **heuristic suggestor**, not CALPHAD or full process simulation.
Lattice matching uses cubic (or effective cubic) *a* values; chemical and
thermal flags are rule-based labels for synthesis cards and the Si-feasibility
scorer.
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

    max_process_temp_c: float | None = None
    """Typical process-temperature ceiling (°C) for this layer (heuristic)."""

    chemical_flags: tuple[str, ...] = ()
    """Short machine-readable flags (e.g. nitrogen_window, oxygen_window)."""

    thermal_window_note: str = ""
    nitrogen_window_note: str = ""
    oxygen_window_note: str = ""


@dataclass(frozen=True)
class BufferStack:
    """Ordered buffer stack (substrate-side first → film-side last).

    Single-layer paths may also be represented as one-layer stacks for a
    uniform recommendation API. Multi-layer stacks (e.g. AlN/TiN) carry a short
    process note and aggregated chemical / thermal flags.
    """

    name: str
    """Display / recommendation name (e.g. ``AlN/TiN``)."""

    layers: tuple[str, ...]
    """Layer names (keys into :data:`BUFFER_LIBRARY`), substrate → film."""

    notes: str = ""
    process_note: str = ""
    """One-line growth sequence note for experimentalists."""

    families: tuple[str, ...] = ("tm_nitride",)
    max_process_temp_c: float | None = None
    chemical_flags: tuple[str, ...] = ()
    thermal_window_note: str = ""

    @property
    def is_multilayer(self) -> bool:
        return len(self.layers) > 1

    @property
    def bottom_layer(self) -> str:
        return self.layers[0]

    @property
    def top_layer(self) -> str:
        return self.layers[-1]


# ---------------------------------------------------------------------------
# Single-layer library (kept for back-compat; enriched with window metadata)
# ---------------------------------------------------------------------------

BUFFER_LIBRARY: dict[str, BufferEntry] = {
    "TiN": BufferEntry(
        name="TiN",
        lattice_a_ang=4.242,
        notes="Rock-salt seed; common nitride template on Si",
        families=("tm_nitride", "mgb2_boride"),
        max_process_temp_c=550.0,
        chemical_flags=("nitrogen_window",),
        thermal_window_note="TiN PVD/ALD typically ~200–550 °C",
        nitrogen_window_note=(
            "Prefer stoichiometric / slightly N-rich TiN; limit O exposure before film growth"
        ),
    ),
    "AlN": BufferEntry(
        name="AlN",
        lattice_a_ang=3.112,  # wurtzite a; used as effective in-plane proxy
        notes="Wide-gap nitride seed; thermal expansion buffer",
        families=("tm_nitride",),
        max_process_temp_c=900.0,
        chemical_flags=("nitrogen_window", "high_thermal_budget"),
        thermal_window_note="AlN MOCVD/sputter often ~600–1000 °C (hot for BEOL CMOS)",
        nitrogen_window_note="N-rich AlN nucleation preferred; sensitive to residual O",
    ),
    "ZrN": BufferEntry(
        name="ZrN",
        lattice_a_ang=4.577,
        notes="Rock-salt nitride; larger lattice than TiN/NbN",
        families=("tm_nitride",),
        max_process_temp_c=600.0,
        chemical_flags=("nitrogen_window",),
        thermal_window_note="ZrN reactive sputter typically ~300–600 °C",
        nitrogen_window_note="N2/Ar process window similar to TiN; avoid oxide formation",
    ),
    "MgO": BufferEntry(
        name="MgO",
        lattice_a_ang=4.212,
        notes="Rock-salt oxide; used for oxides and some nitrides",
        families=("tm_nitride", "cuprate", "nickelate"),
        max_process_temp_c=500.0,
        chemical_flags=("oxygen_window", "oxide_on_si"),
        thermal_window_note="MgO PLD/sputter often ~200–500 °C",
        oxygen_window_note=(
            "Controlled O2 partial pressure required; residual O harms subsequent nitrides"
        ),
    ),
    "direct_Si": BufferEntry(
        name="direct_Si",
        lattice_a_ang=5.4307,
        notes="No buffer — direct epitaxy on Si",
        families=("tm_nitride", "b_doped_si", "mgb2_boride", "other"),
        max_process_temp_c=None,
        chemical_flags=("direct_on_si",),
        thermal_window_note="Thermal budget set by film growth only (no buffer step)",
    ),
}


# ---------------------------------------------------------------------------
# Multi-layer stacks (heuristic recipes — not thermodynamic equilibrium)
# ---------------------------------------------------------------------------

STACK_LIBRARY: dict[str, BufferStack] = {
    # --- tm_nitride priority stacks ---
    "AlN/TiN": BufferStack(
        name="AlN/TiN",
        layers=("AlN", "TiN"),
        notes="AlN nucleation on Si + TiN rocksalt template for nitride films",
        process_note=(
            "Thin AlN seed on Si, then TiN template; deposit film on TiN. "
            "Heuristic stack — not CALPHAD."
        ),
        families=("tm_nitride", "mgb2_boride"),
        max_process_temp_c=900.0,
        chemical_flags=(
            "nitrogen_window",
            "interdiffusion_caution",
            "high_thermal_budget",
        ),
        thermal_window_note=(
            "Ceiling set by AlN seed (~600–900 °C); TiN step can run cooler"
        ),
    ),
    "TiN/AlN": BufferStack(
        name="TiN/AlN",
        layers=("TiN", "AlN"),
        notes="TiN adhesion/seed on Si with AlN wide-gap interlayer",
        process_note=(
            "TiN on Si for adhesion/conductivity, AlN spacer, then film. "
            "Heuristic stack — not CALPHAD."
        ),
        families=("tm_nitride",),
        max_process_temp_c=900.0,
        chemical_flags=(
            "nitrogen_window",
            "interdiffusion_caution",
            "high_thermal_budget",
        ),
        thermal_window_note="AlN interlayer often hottest step; watch BEOL budget",
    ),
    "MgO/TiN": BufferStack(
        name="MgO/TiN",
        layers=("MgO", "TiN"),
        notes="MgO oxide seed + TiN nitride template",
        process_note=(
            "Grow MgO seed under O window, purge O, then TiN under N window; film on TiN. "
            "Heuristic stack — not CALPHAD."
        ),
        families=("tm_nitride", "cuprate", "nickelate"),
        max_process_temp_c=550.0,
        chemical_flags=(
            "oxygen_window",
            "nitrogen_window",
            "interdiffusion_caution",
            "oxide_nitride_interface",
        ),
        thermal_window_note="Both steps usually ≤550 °C; O→N purge is critical",
    ),
    "AlN/ZrN": BufferStack(
        name="AlN/ZrN",
        layers=("AlN", "ZrN"),
        notes="AlN seed + larger-lattice ZrN template (for expanded nitrides)",
        process_note=(
            "AlN nucleation then ZrN template for films with a ≳ TiN. "
            "Heuristic stack — not CALPHAD."
        ),
        families=("tm_nitride",),
        max_process_temp_c=900.0,
        chemical_flags=(
            "nitrogen_window",
            "interdiffusion_caution",
            "high_thermal_budget",
        ),
        thermal_window_note="AlN seed sets thermal ceiling; ZrN typically cooler",
    ),
    # --- modest mgb2 / other coverage ---
    "TiN/MgO": BufferStack(
        name="TiN/MgO",
        layers=("TiN", "MgO"),
        notes="TiN seed on Si then MgO for oxide-facing films (e.g. MgB₂ pathways)",
        process_note=(
            "TiN barrier/seed on Si, MgO under O window for oxide-side films. "
            "Heuristic stack — not CALPHAD."
        ),
        families=("mgb2_boride", "cuprate", "nickelate"),
        max_process_temp_c=550.0,
        chemical_flags=(
            "nitrogen_window",
            "oxygen_window",
            "interdiffusion_caution",
            "oxide_nitride_interface",
        ),
        thermal_window_note="N then O ambient switch; keep both steps moderate-T",
    ),
}


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def list_buffers_for_family(family: str) -> list[BufferEntry]:
    """Return single-layer buffers tagged for *family* (always includes direct_Si)."""
    out = [
        b
        for b in BUFFER_LIBRARY.values()
        if family in b.families or b.name == "direct_Si"
    ]
    unique: list[BufferEntry] = []
    seen: set[str] = set()
    for b in out:
        if b.name not in seen:
            seen.add(b.name)
            unique.append(b)
    if "direct_Si" not in seen:
        unique.insert(0, BUFFER_LIBRARY["direct_Si"])
    return unique


def list_stacks_for_family(family: str, *, multilayer_only: bool = False) -> list[BufferStack]:
    """Return multi-layer stacks tagged for *family*."""
    stacks = [s for s in STACK_LIBRARY.values() if family in s.families]
    if multilayer_only:
        stacks = [s for s in stacks if s.is_multilayer]
    return list(stacks)


def stack_from_single(entry: BufferEntry) -> BufferStack:
    """Wrap a single-layer :class:`BufferEntry` as a one-layer :class:`BufferStack`."""
    return BufferStack(
        name=entry.name,
        layers=(entry.name,),
        notes=entry.notes,
        process_note=f"Single-layer {entry.name} buffer/seed on Si.",
        families=entry.families,
        max_process_temp_c=entry.max_process_temp_c,
        chemical_flags=entry.chemical_flags,
        thermal_window_note=entry.thermal_window_note,
    )


def resolve_stack_layers(stack: BufferStack) -> list[BufferEntry]:
    """Map stack layer names to :class:`BufferEntry` objects (skips unknown names)."""
    out: list[BufferEntry] = []
    for name in stack.layers:
        if name in BUFFER_LIBRARY:
            out.append(BUFFER_LIBRARY[name])
    return out


# Flags that only make sense when the layer sits on Si (substrate-side).
_SUBSTRATE_SIDE_ONLY_FLAGS: frozenset[str] = frozenset({"oxide_on_si", "direct_on_si"})


def aggregate_stack_flags(stack: BufferStack) -> tuple[str, ...]:
    """Union stack-level and per-layer chemical flags (stable order).

    Position-aware: substrate-side-only flags (e.g. ``oxide_on_si``) are taken
    from the bottom layer only. Stack-level flags are always included so a
    recipe can declare them explicitly. Other layer flags (N/O windows,
    high_thermal_budget) apply regardless of position.
    """
    flags: list[str] = list(stack.chemical_flags)
    layers = resolve_stack_layers(stack)
    for i, layer in enumerate(layers):
        for flag in layer.chemical_flags:
            if flag in _SUBSTRATE_SIDE_ONLY_FLAGS and i != 0:
                continue
            flags.append(flag)
    return tuple(_dedupe_preserve(flags))


def stack_process_temp_ceiling_c(stack: BufferStack) -> float | None:
    """Max of stack ceiling and known layer ceilings."""
    temps: list[float] = []
    if stack.max_process_temp_c is not None:
        temps.append(float(stack.max_process_temp_c))
    for layer in resolve_stack_layers(stack):
        if layer.max_process_temp_c is not None:
            temps.append(float(layer.max_process_temp_c))
    if not temps:
        return None
    return max(temps)


def stack_window_notes(stack: BufferStack) -> list[str]:
    """Collect short chemical/thermal window notes for cards and score notes."""
    notes: list[str] = []
    if stack.thermal_window_note:
        notes.append(stack.thermal_window_note)
    elif stack.max_process_temp_c is not None:
        notes.append(f"process temp ceiling ~{stack.max_process_temp_c:.0f} °C (heuristic)")
    for layer in resolve_stack_layers(stack):
        if layer.nitrogen_window_note:
            notes.append(f"{layer.name} N-window: {layer.nitrogen_window_note}")
        if layer.oxygen_window_note:
            notes.append(f"{layer.name} O-window: {layer.oxygen_window_note}")
        if layer.thermal_window_note and layer.thermal_window_note not in notes:
            if layer.thermal_window_note != stack.thermal_window_note:
                notes.append(f"{layer.name}: {layer.thermal_window_note}")
    if stack.process_note:
        notes.append(stack.process_note)
    return notes


def buffer_as_dict(entry: BufferEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "lattice_a_ang": entry.lattice_a_ang,
        "notes": entry.notes,
        "max_process_temp_c": entry.max_process_temp_c,
        "chemical_flags": list(entry.chemical_flags),
        "thermal_window_note": entry.thermal_window_note,
    }


def stack_as_dict(stack: BufferStack) -> dict[str, Any]:
    return {
        "name": stack.name,
        "layers": list(stack.layers),
        "notes": stack.notes,
        "process_note": stack.process_note,
        "families": list(stack.families),
        "max_process_temp_c": stack.max_process_temp_c,
        "chemical_flags": list(aggregate_stack_flags(stack)),
        "thermal_window_note": stack.thermal_window_note,
        "is_multilayer": stack.is_multilayer,
    }
