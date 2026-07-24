"""Calculator protocol / ABC for pluggable physics engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from siscforge.models.candidate import CandidateEvaluation, StructureCandidate


@runtime_checkable
class Calculator(Protocol):
    """Structural protocol for calculation backends.

    Implementations accept a :class:`~siscforge.models.candidate.StructureCandidate`
    and return a typed result (typically :class:`~siscforge.models.candidate.CandidateEvaluation`
    for end-to-end evaluators, or a single result model for focused engines).
    """

    name: str

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> Any:
        """Execute the calculation for *candidate* and return a result object."""
        ...


class BaseCalculator(ABC):
    """Optional ABC for calculators that prefer explicit inheritance."""

    name: str = "base"

    @abstractmethod
    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        """Execute the calculation for *candidate*."""
        raise NotImplementedError
