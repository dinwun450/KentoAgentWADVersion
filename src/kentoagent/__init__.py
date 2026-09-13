"""KentoAgent disaster-response simulator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kentoagent.orchestration.orchestrator import Orchestrator
    from kentoagent.simulation.scenario import ScenarioConfig

__all__ = ["Orchestrator", "ScenarioConfig", "generate_scenario"]
__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Load convenience exports lazily so Temporal sandbox imports stay side-effect free."""

    if name == "Orchestrator":
        from kentoagent.orchestration.orchestrator import Orchestrator

        return Orchestrator
    if name in {"ScenarioConfig", "generate_scenario"}:
        from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario

        return {"ScenarioConfig": ScenarioConfig, "generate_scenario": generate_scenario}[name]
    raise AttributeError(name)
