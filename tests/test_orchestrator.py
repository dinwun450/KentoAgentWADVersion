from kentoagent.agents.coordinate import CoordinateAgent
from kentoagent.agents.priority import PriorityAgent
from kentoagent.config import SimulationConfig
from kentoagent.evaluation.guardrails import validate_rescue_plan
from kentoagent.orchestration.messages import RescuePlan
from kentoagent.orchestration.orchestrator import Orchestrator
from kentoagent.simulation.entities import Severity, SurvivorStatus
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def test_guardrail_rejects_blocked_road() -> None:
    world = generate_scenario(ScenarioConfig(seed=1, blocked_road_probability=0))
    road = next(iter(world.roads.values()))
    road.blocked = True
    survivor = next(iter(world.survivors.values()))
    survivor.node_id = road.target
    survivor.position = world.nodes[road.target].position
    agent = next(agent for agent in world.agents.values() if agent.role.value == "planner")
    agent.node_id = road.source
    plan = RescuePlan(
        agent_id=agent.id,
        survivor_id=survivor.id,
        node_path=[road.source, road.target],
        road_ids=[road.id],
        estimated_distance_m=road.distance_m,
    )
    result = validate_rescue_plan(world, plan)
    assert not result.valid
    assert any("blocked" in reason for reason in result.reasons)


def test_loop_is_bounded_and_makes_progress() -> None:
    world = generate_scenario(
        ScenarioConfig(seed=14, survivor_count=5, blocked_road_probability=0.05)
    )
    Orchestrator(world, SimulationConfig(max_steps=60, dynamic_change_interval=4)).run()
    assert world.step <= 60
    assert any(item.status == SurvivorStatus.RESCUED for item in world.survivors.values())
    assert world.stopped_reason is not None


def test_coordinator_dispatches_five_agents_by_severity_and_reachability() -> None:
    world = generate_scenario(
        ScenarioConfig(seed=8, survivor_count=8, agent_count=5, blocked_road_probability=0)
    )
    survivors = list(world.survivors.values())
    severity_order = [
        Severity.CRITICAL,
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.HIGH,
        Severity.MODERATE,
        Severity.MODERATE,
        Severity.LOW,
        Severity.LOW,
    ]
    for survivor, severity in zip(survivors, severity_order, strict=True):
        survivor.status = SurvivorStatus.WAITING
        survivor.severity = severity

    for update in PriorityAgent().run(world):
        world.survivors[update.survivor_id].priority_score = update.score
    assignments = CoordinateAgent().run(world)

    assert len(assignments) == 5
    assert len({item.agent_id for item in assignments}) == 5
    assert len({item.survivor_id for item in assignments}) == 5
    assigned = [world.survivors[item.survivor_id] for item in assignments]
    assert all(item.status == SurvivorStatus.WAITING for item in assigned)
    assert {item.severity for item in assigned} == {
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MODERATE,
    }


def test_priority_uses_raw_schema_severity_and_active_status() -> None:
    world = generate_scenario(
        ScenarioConfig(seed=19, survivor_count=3, blocked_road_probability=0)
    )
    critical, high, resolved = world.survivors.values()
    critical.severity = Severity.CRITICAL
    critical.status = SurvivorStatus.ASSIGNED
    high.severity = Severity.HIGH
    high.status = SurvivorStatus.WAITING
    resolved.severity = Severity.CRITICAL
    resolved.status = SurvivorStatus.RESCUED

    updates = PriorityAgent().run(world)

    assert [item.survivor_id for item in updates] == [critical.id, high.id]
    assert updates[0].score > updates[1].score
