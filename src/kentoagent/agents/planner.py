from __future__ import annotations

from kentoagent.agents.base import SpecialistAgent
from kentoagent.orchestration.messages import RescuePlan
from kentoagent.simulation.entities import AgentRole, WorldState
from kentoagent.simulation.routing import shortest_route


class PlannerAgent(SpecialistAgent[RescuePlan]):
    role = "planner"

    def run(self, world: WorldState) -> list[RescuePlan]:
        plans: list[RescuePlan] = []
        for agent in world.agents.values():
            if agent.role != AgentRole.PLANNER or not agent.current_assignment:
                continue
            survivor = world.survivors.get(agent.current_assignment)
            if survivor is None:
                continue
            route = shortest_route(world, agent.node_id, survivor.node_id)
            if route is None:
                continue
            plans.append(
                RescuePlan(
                    agent_id=agent.id,
                    survivor_id=survivor.id,
                    node_path=route.nodes,
                    road_ids=route.road_ids,
                    estimated_distance_m=route.distance_m,
                )
            )
        return plans
