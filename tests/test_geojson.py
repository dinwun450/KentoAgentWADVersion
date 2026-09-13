from kentoagent.simulation.entities import SurvivorStatus
from kentoagent.simulation.geojson import world_to_geojson
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def _feature_ids(layer: dict[str, object]) -> set[str]:
    features = layer["features"]
    assert isinstance(features, list)
    return {str(feature["id"]) for feature in features}


def test_operational_points_persist_until_their_domain_state_resolves() -> None:
    world = generate_scenario(
        ScenarioConfig(seed=42, survivor_count=5, blocked_road_probability=0.2)
    )
    initial = world_to_geojson(world)

    assert len(initial["survivor-layer"]["features"]) == 5
    assert len(initial["blockage-layer"]["features"]) == sum(
        road.blocked for road in world.roads.values()
    )

    survivor = next(iter(world.survivors.values()))
    survivor.status = SurvivorStatus.RESCUED
    updated = world_to_geojson(world)

    assert survivor.id not in _feature_ids(updated["survivor-layer"])
    assert _feature_ids(updated["hazard-layer"]) == _feature_ids(initial["hazard-layer"])
    assert _feature_ids(updated["blockage-layer"]) == _feature_ids(initial["blockage-layer"])

