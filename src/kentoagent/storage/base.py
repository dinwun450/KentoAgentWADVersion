from __future__ import annotations

from typing import Protocol

from kentoagent.simulation.entities import WorldState


class SimulationStore(Protocol):
    """Persistence seam for local JSON now and Snowflake in a future phase."""

    def save(self, state: WorldState) -> None: ...

    def load(self, run_id: str) -> WorldState: ...

    def list_runs(self) -> list[str]: ...
