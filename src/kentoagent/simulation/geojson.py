from __future__ import annotations

from typing import Any

from kentoagent.simulation.entities import SurvivorStatus, WorldState


def _collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def world_to_geojson(world: WorldState) -> dict[str, dict[str, Any]]:
    agents = [
        {
            "type": "Feature",
            "id": agent.id,
            "properties": {
                "id": agent.id,
                "type": "agent",
                "role": agent.role.value,
                "status": agent.status.value,
                "label": agent.name,
            },
            "geometry": {"type": "Point", "coordinates": agent.position.as_list()},
        }
        for agent in world.agents.values()
    ]
    survivors: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "id": survivor.id,
            "properties": {
                "id": survivor.id,
                "type": "survivor",
                "severity": survivor.severity.value,
                "status": survivor.status.value,
                "trapped": survivor.trapped,
                "priority": survivor.priority_score,
            },
            "geometry": {"type": "Point", "coordinates": survivor.position.as_list()},
        }
        for survivor in world.survivors.values()
        # Scenario/Snowflake entities are already mapped before playback starts.
        # Keep every unresolved point stable across ticks and remove it only when
        # the authoritative simulation marks the survivor rescued.
        if survivor.status != SurvivorStatus.RESCUED
    ]
    hazards = [
        {
            "type": "Feature",
            "id": hazard.id,
            "properties": {
                "id": hazard.id,
                "type": "hazard",
                "kind": hazard.kind,
                "intensity": hazard.intensity,
                "radius_m": hazard.radius_m,
            },
            "geometry": {"type": "Point", "coordinates": hazard.position.as_list()},
        }
        for hazard in world.hazards.values()
    ]
    blocked_roads = [
        {
            "type": "Feature",
            "id": road.id,
            "properties": {"id": road.id, "type": "blocked_road"},
            "geometry": {
                "type": "LineString",
                "coordinates": [coordinate.as_list() for coordinate in road.coordinates],
            },
        }
        for road in world.roads.values()
        if road.blocked
    ]
    blockages = [
        {
            "type": "Feature",
            "id": f"blockage-{road.id}",
            "properties": {
                "id": f"blockage-{road.id}",
                "type": "blockage",
                "road_segment_id": road.id,
                "active": True,
                "severity": "major" if road.hazard_cost > 100 else "moderate",
            },
            "geometry": {
                "type": "Point",
                "coordinates": [
                    sum(point.lon for point in road.coordinates) / len(road.coordinates),
                    sum(point.lat for point in road.coordinates) / len(road.coordinates),
                ],
            },
        }
        for road in world.roads.values()
        if road.blocked
    ]
    routes: list[dict[str, Any]] = []
    for agent in world.agents.values():
        if not agent.route:
            continue
        positions = [agent.position, *(world.nodes[node].position for node in agent.route)]
        routes.append(
            {
                "type": "Feature",
                "id": f"route-{agent.id}",
                "properties": {"id": f"route-{agent.id}", "agent_id": agent.id},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [position.as_list() for position in positions],
                },
            }
        )
    targets = [
        feature
        for feature in survivors
        if feature["properties"]["status"] == SurvivorStatus.ASSIGNED.value
    ]
    return {
        "agent-layer": _collection(agents),
        "survivor-layer": _collection(survivors),
        "hazard-layer": _collection(hazards),
        "blocked-road-layer": _collection(blocked_roads),
        "blockage-layer": _collection(blockages),
        "rescue-target-layer": _collection(targets),
        "route-layer": _collection(routes),
        "explored-area-layer": _collection([]),
    }
