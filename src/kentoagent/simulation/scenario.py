from __future__ import annotations

import math
import random
from uuid import uuid4

from pydantic import BaseModel, Field

from kentoagent.simulation.entities import (
    AgentRole,
    AgentState,
    Coordinate,
    Hazard,
    RoadNode,
    RoadSegment,
    Severity,
    Survivor,
    SurvivorStatus,
    WorldState,
)


class ScenarioConfig(BaseModel):
    seed: int = 49281
    survivor_count: int = Field(default=8, ge=1, le=100)
    agent_count: int = Field(default=5, ge=5, le=25)
    hazard_density: float = Field(default=0.18, ge=0, le=0.8)
    blocked_road_probability: float = Field(default=0.16, ge=0, le=0.8)
    region_name: str = "San Francisco — SoMa / Mission"
    center: Coordinate = Coordinate(lon=-122.414, lat=37.774)


def _distance_m(a: Coordinate, b: Coordinate) -> float:
    mean_lat = math.radians((a.lat + b.lat) / 2)
    x = math.radians(b.lon - a.lon) * math.cos(mean_lat)
    y = math.radians(b.lat - a.lat)
    return math.hypot(x, y) * 6_371_000


def _street_graph(center: Coordinate) -> tuple[dict[str, RoadNode], dict[str, RoadSegment]]:
    """Create a compact graph aligned to real WGS84 coordinates around central SF."""
    nodes: dict[str, RoadNode] = {}
    roads: dict[str, RoadSegment] = {}
    lon_step, lat_step = 0.0032, 0.00255
    for row in range(5):
        for col in range(5):
            node_id = f"n-{row}-{col}"
            position = Coordinate(
                lon=center.lon + (col - 2) * lon_step,
                lat=center.lat + (row - 2) * lat_step,
            )
            nodes[node_id] = RoadNode(id=node_id, position=position)
    for row in range(5):
        for col in range(5):
            source = f"n-{row}-{col}"
            for dr, dc in ((0, 1), (1, 0)):
                nr, nc = row + dr, col + dc
                if nr >= 5 or nc >= 5:
                    continue
                target = f"n-{nr}-{nc}"
                segment_id = f"road-{source}-{target}"
                a, b = nodes[source].position, nodes[target].position
                roads[segment_id] = RoadSegment(
                    id=segment_id,
                    source=source,
                    target=target,
                    coordinates=[a, b],
                    distance_m=_distance_m(a, b),
                )
    return nodes, roads


def generate_scenario(config: ScenarioConfig) -> WorldState:
    rng = random.Random(config.seed)
    nodes, roads = _street_graph(config.center)
    node_ids = sorted(nodes)

    road_ids = sorted(roads)
    block_count = min(len(road_ids) - 6, round(len(road_ids) * config.blocked_road_probability))
    for road_id in rng.sample(road_ids, k=max(0, block_count)):
        roads[road_id].blocked = True

    hazard_count = round(len(node_ids) * config.hazard_density)
    hazards: dict[str, Hazard] = {}
    for index, node_id in enumerate(rng.sample(node_ids, k=hazard_count), start=1):
        hazard = Hazard(
            id=f"hazard-{index:03d}",
            node_id=node_id,
            position=nodes[node_id].position,
            kind=rng.choice(["fire", "collapse", "gas_leak"]),
            intensity=round(rng.uniform(0.35, 0.95), 2),
            radius_m=round(rng.uniform(45, 120), 1),
        )
        hazards[hazard.id] = hazard
        for road in roads.values():
            if node_id in (road.source, road.target):
                road.hazard_cost = max(road.hazard_cost, hazard.intensity * 250)

    available_nodes = rng.sample(node_ids, k=min(config.survivor_count, len(node_ids)))
    survivors: dict[str, Survivor] = {}
    severities = [Severity.LOW, Severity.MODERATE, Severity.HIGH, Severity.CRITICAL]
    weights = [0.18, 0.32, 0.32, 0.18]
    for index, node_id in enumerate(available_nodes, start=1):
        survivor = Survivor(
            id=f"survivor-{index:03d}",
            node_id=node_id,
            position=nodes[node_id].position,
            severity=rng.choices(severities, weights=weights, k=1)[0],
            trapped=rng.random() < 0.35,
            # raw_kentoagent.survivors defaults mapped records to waiting.
            # Keep generated state aligned so playback does not demote known
            # map points to unknown before orchestration starts.
            status=SurvivorStatus.WAITING,
        )
        survivors[survivor.id] = survivor

    roles = list(AgentRole)
    agent_nodes = [rng.choice(node_ids) for _ in range(config.agent_count)]
    agents: dict[str, AgentState] = {}
    for index in range(config.agent_count):
        role = roles[index % len(roles)]
        node_id = agent_nodes[index]
        agent = AgentState(
            id=f"agent-{index + 1:02d}",
            name=f"{role.value.title()} {index + 1}",
            role=role,
            node_id=node_id,
            position=nodes[node_id].position,
        )
        agents[agent.id] = agent

    scenario_id = f"scenario-{config.seed}"
    return WorldState(
        run_id=f"run-{uuid4().hex[:10]}",
        scenario_id=scenario_id,
        seed=config.seed,
        region_name=config.region_name,
        center=config.center,
        nodes=nodes,
        roads=roads,
        survivors=survivors,
        hazards=hazards,
        agents=agents,
    )
