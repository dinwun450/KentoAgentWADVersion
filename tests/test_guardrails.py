from kentoagent.evaluation.guardrails import validate_rescue_plan
from kentoagent.orchestration.messages import RescuePlan
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def test_malformed_route_is_rejected_without_index_error() -> None:
    world = generate_scenario(ScenarioConfig(seed=21))
    agent = next(iter(world.agents.values()))
    survivor = next(iter(world.survivors.values()))
    road_id = next(iter(world.roads))
    plan = RescuePlan(
        agent_id=agent.id,
        survivor_id=survivor.id,
        node_path=[agent.node_id],
        road_ids=[road_id],
        estimated_distance_m=world.roads[road_id].distance_m,
    )

    result = validate_rescue_plan(world, plan)

    assert result.valid is False
    assert "route topology is inconsistent" in result.reasons