from __future__ import annotations

from kentoagent.agents.base import SpecialistAgent
from kentoagent.evaluation.guardrails import validate_rescue_plan
from kentoagent.orchestration.messages import ActionRejected, RescuePlan
from kentoagent.simulation.entities import WorldState


class ControlAgent(SpecialistAgent[ActionRejected]):
    role = "control"

    def inspect(self, world: WorldState, plan: RescuePlan) -> ActionRejected | None:
        result = validate_rescue_plan(world, plan)
        if result.valid:
            return None
        return ActionRejected(agent_id=plan.agent_id, action="rescue_plan", reasons=result.reasons)

    def run(self, world: WorldState) -> list[ActionRejected]:
        return []
