from kentoagent.simulation.entities import SurvivorStatus
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def test_seed_replays_domain_state() -> None:
    first = generate_scenario(ScenarioConfig(seed=49281))
    second = generate_scenario(ScenarioConfig(seed=49281))
    first_payload = first.model_dump(exclude={"run_id"})
    second_payload = second.model_dump(exclude={"run_id"})
    assert first_payload == second_payload


def test_scenario_has_all_specialist_roles() -> None:
    world = generate_scenario(ScenarioConfig(seed=7))
    assert {agent.role.value for agent in world.agents.values()} == {
        "locate",
        "coordinate",
        "control",
        "priority",
        "planner",
    }


def test_mapped_survivors_start_in_raw_schema_waiting_status() -> None:
    world = generate_scenario(ScenarioConfig(seed=12, survivor_count=5))

    assert {survivor.status for survivor in world.survivors.values()} == {
        SurvivorStatus.WAITING
    }
