from __future__ import annotations

from kentoagent.agents.base import SpecialistAgent
from kentoagent.orchestration.messages import SurvivorDetected
from kentoagent.simulation.entities import AgentRole, SurvivorStatus, WorldState


class LocateAgent(SpecialistAgent[SurvivorDetected]):
    role = "locate"

    def run(self, world: WorldState) -> list[SurvivorDetected]:
        locators = [agent for agent in world.agents.values() if agent.role == AgentRole.LOCATE]
        unknown = sorted(
            (item for item in world.survivors.values() if item.status == SurvivorStatus.UNKNOWN),
            key=lambda item: item.id,
        )
        if not locators or not unknown:
            return []
        # A deterministic sweep reveals one candidate per locate agent and step.
        return [
            SurvivorDetected(
                survivor_id=survivor.id,
                confidence=min(0.99, 0.72 + world.step * 0.015),
                detected_by=locators[index % len(locators)].id,
            )
            for index, survivor in enumerate(unknown[: len(locators)])
        ]
