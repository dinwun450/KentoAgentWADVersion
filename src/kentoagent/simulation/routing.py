from __future__ import annotations

import heapq
from dataclasses import dataclass

from kentoagent.simulation.entities import WorldState


@dataclass(frozen=True)
class Route:
    nodes: list[str]
    road_ids: list[str]
    distance_m: float
    risk_adjusted_cost: float


def shortest_route(
    world: WorldState, source: str, target: str, *, include_hazard_cost: bool = True
) -> Route | None:
    if source not in world.nodes or target not in world.nodes:
        return None
    adjacency: dict[str, list[tuple[str, str, float, float]]] = {key: [] for key in world.nodes}
    for road in world.roads.values():
        if road.blocked:
            continue
        risk = road.hazard_cost if include_hazard_cost else 0
        adjacency[road.source].append((road.target, road.id, road.distance_m, risk))
        adjacency[road.target].append((road.source, road.id, road.distance_m, risk))

    queue: list[tuple[float, str]] = [(0, source)]
    costs = {source: 0.0}
    previous: dict[str, tuple[str, str]] = {}
    while queue:
        cost, node = heapq.heappop(queue)
        if node == target:
            break
        if cost > costs[node]:
            continue
        for neighbor, road_id, distance, risk in adjacency[node]:
            next_cost = cost + distance + risk
            if next_cost < costs.get(neighbor, float("inf")):
                costs[neighbor] = next_cost
                previous[neighbor] = (node, road_id)
                heapq.heappush(queue, (next_cost, neighbor))
    if target not in costs:
        return None

    nodes = [target]
    road_ids: list[str] = []
    cursor = target
    while cursor != source:
        cursor, road_id = previous[cursor]
        nodes.append(cursor)
        road_ids.append(road_id)
    nodes.reverse()
    road_ids.reverse()
    distance_m = sum(world.roads[road_id].distance_m for road_id in road_ids)
    return Route(nodes, road_ids, round(distance_m, 2), round(costs[target], 2))
