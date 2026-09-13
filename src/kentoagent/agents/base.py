from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from kentoagent.simulation.entities import WorldState

T = TypeVar("T", bound=BaseModel)


class SpecialistAgent(ABC, Generic[T]):
    role: str

    @abstractmethod
    def run(self, world: WorldState) -> list[T]:
        """Return typed proposals without directly mutating the world."""
