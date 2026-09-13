from kentoagent.simulation.routing import shortest_route
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def test_route_never_contains_blocked_road() -> None:
    world = generate_scenario(ScenarioConfig(seed=21, blocked_road_probability=0.2))
    route = shortest_route(world, "n-0-0", "n-4-4")
    if route is not None:
        assert all(not world.roads[road_id].blocked for road_id in route.road_ids)
