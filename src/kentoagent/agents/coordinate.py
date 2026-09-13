from __future__ import annotations

from kentoagent.agents.base import SpecialistAgent
from kentoagent.orchestration.messages import TaskAssignment
from kentoagent.simulation.entities import AgentStatus, SurvivorStatus, WorldState
from kentoagent.simulation.routing import shortest_route


class CoordinateAgent(SpecialistAgent[TaskAssignment]):
    role = "coordinate"

    def run(self, world: WorldState) -> list[TaskAssignment]:
        # The five specialist policies make decisions; the coordinator may
        # dispatch every idle mobile agent as a responder. Previously only the
        # single planner-role agent could move, leaving four responders unused.
        available = [
            agent
            for agent in sorted(world.agents.values(), key=lambda item: item.id)
            if agent.current_assignment is None
            and agent.status in (AgentStatus.IDLE, AgentStatus.SEARCHING)
        ]
        targets = sorted(
            (
                survivor
                for survivor in world.survivors.values()
                if survivor.status == SurvivorStatus.WAITING and survivor.assigned_agent_id is None
            ),
            key=lambda survivor: (
                -survivor.priority_score,
                survivor.id,
            ),
        )
        assignments: list[TaskAssignment] = []
        for survivor in targets:
            reachable = []
            for agent in available:
                route = shortest_route(world, agent.node_id, survivor.node_id)
                if route is not None:
                    reachable.append((route.risk_adjusted_cost, agent.id, agent))
            if not reachable:
                continue
            _, _, agent = min(reachable, key=lambda candidate: candidate[:2])
            assignments.append(
                TaskAssignment(
                    agent_id=agent.id,
                    survivor_id=survivor.id,
                    priority_score=survivor.priority_score,
                )
            )
            available.remove(agent)
            if not available:
                break
        return assignments
