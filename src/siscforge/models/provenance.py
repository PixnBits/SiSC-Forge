"""Provenance tracking for reproducible calculations and campaign runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Provenance(BaseModel):
    """Lightweight provenance record attached to candidates and evaluations.

    Captures enough metadata for reproducibility without a full AiiDA-style
    graph. Expanded in later phases when jobflow / MongoDB land.
    """

    created_at: datetime = Field(default_factory=_utc_now)
    """UTC timestamp when this record was created."""

    source: str = "siscforge"
    """Originating subsystem (e.g. enumerator name, calculator name)."""

    software: dict[str, str] = Field(default_factory=dict)
    """Mapping of software package → version string."""

    parameters: dict[str, Any] = Field(default_factory=dict)
    """Free-form calculation or generation parameters."""

    input_hashes: dict[str, str] = Field(default_factory=dict)
    """Optional content hashes of inputs (structure, pseudopotentials, YAML)."""

    notes: str = ""
    """Human-readable free-form notes."""

    parent_ids: list[str] = Field(default_factory=list)
    """IDs of upstream records this result depends on."""
