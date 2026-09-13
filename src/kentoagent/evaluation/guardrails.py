from __future__ import annotations

from pydantic import BaseModel, Field

from kentoagent.orchestration.messages import RescuePlan
from kentoagent.simulation.entities import SurvivorStatus, WorldState


class ValidationResult(BaseModel):
    valid: bool
    reasons: list[str] = Field(default_factory=list)


def validate_rescue_plan(world: WorldState, plan: RescuePlan) -> ValidationResult:
    reasons: list[str] = []
    agent = world.agents.get(plan.agent_id)
    survivor = world.survivors.get(plan.survivor_id)
    if agent is None:
        reasons.append("agent does not exist")
    if survivor is None:
        reasons.append("survivor does not exist")
    elif survivor.status == SurvivorStatus.RESCUED:
        reasons.append("survivor is already rescued")
    elif survivor.assigned_agent_id not in (None, plan.agent_id):
        reasons.append("survivor is assigned to another agent")
    if agent is not None and (not plan.node_path or plan.node_path[0] != agent.node_id):
        reasons.append("route does not start at agent position")
    if survivor is not None and (not plan.node_path or plan.node_path[-1] != survivor.node_id):
        reasons.append("route does not end at survivor position")
    if len(plan.node_path) != len(plan.road_ids) + 1:
        reasons.append("route topology is inconsistent")
    for index, road_id in enumerate(plan.road_ids):
        road = world.roads.get(road_id)
        if road is None:
            reasons.append(f"road {road_id} does not exist")
            continue
        if road.blocked:
            reasons.append(f"road {road_id} is blocked")
        if index + 1 >= len(plan.node_path):
            continue
        pair = {plan.node_path[index], plan.node_path[index + 1]}
        if pair != {road.source, road.target}:
            reasons.append(f"road {road_id} does not connect proposed nodes")
    return ValidationResult(valid=not reasons, reasons=reasons)
