import asyncio

import pytest

from kentoagent.config import SimulationConfig
from kentoagent.durable.activities import (
    _mock_decision,
    _normalise_decision,
    apply_round,
    infer_agent_turn,
    initialize_simulation_payload,
    validate_round,
)
from kentoagent.durable.models import (
    AgentProposal,
    AgentTurnDecision,
    AgentTurnRequest,
    ApplyRoundRequest,
    DurableSimulationInput,
    ValidateRoundRequest,
)
from kentoagent.api import durable as durable_api
from temporalio.exceptions import WorkflowAlreadyStartedError
from kentoagent.simulation.entities import AgentRole, SurvivorStatus
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def test_mock_agent_round_produces_guarded_progress() -> None:
    world = generate_scenario(
        ScenarioConfig(seed=14, survivor_count=5, blocked_road_probability=0.05)
    )
    decisions: list[AgentTurnDecision] = []
    for role in (
        AgentRole.LOCATE,
        AgentRole.PRIORITY,
        AgentRole.COORDINATE,
        AgentRole.PLANNER,
    ):
        decisions.append(
            _mock_decision(
                AgentTurnRequest(
                    world=world,
                    role=role,
                    prior_decisions=decisions,
                    provider="mock",
                )
            )
        )
    validation = validate_round(ValidateRoundRequest(world=world, decisions=decisions))
    assert not validation.rejections

    updated = apply_round(
        ApplyRoundRequest(
            world=world,
            decisions=decisions,
            validation=validation,
            simulation_config=SimulationConfig(max_steps=10),
        )
    )
    assert updated.step == 1
    assert any(item.status != SurvivorStatus.UNKNOWN for item in updated.survivors.values())
    event_types = {event.event_type for event in updated.events}
    assert event_types >= {"task_assignment", "rescue_plan"}
    assert "survivor_detected" not in event_types


def test_invalid_llm_route_is_rejected_before_world_mutation() -> None:
    world = generate_scenario(ScenarioConfig(seed=3, blocked_road_probability=0))
    agent = next(item for item in world.agents.values() if item.role == AgentRole.PLANNER)
    survivor = next(iter(world.survivors.values()))
    survivor.status = SurvivorStatus.WAITING
    bad_plan = AgentProposal(
        kind="plan",
        agent_id=agent.id,
        survivor_id=survivor.id,
        node_path=[agent.node_id, survivor.node_id],
        road_ids=["road-does-not-exist"],
    )
    decisions = [
        AgentTurnDecision(
            role=AgentRole.COORDINATE,
            objective="assign",
            proposals=[
                AgentProposal(kind="assign", agent_id=agent.id, survivor_id=survivor.id)
            ],
            summary="assignment",
        ),
        AgentTurnDecision(
            role=AgentRole.PLANNER,
            objective="plan",
            proposals=[bad_plan],
            summary="invalid plan",
        ),
    ]
    validation = validate_round(ValidateRoundRequest(world=world, decisions=decisions))
    assert any(item.idempotency_key == bad_plan.idempotency_key for item in validation.rejections)

    updated = apply_round(
        ApplyRoundRequest(
            world=world,
            decisions=decisions,
            validation=validation,
            simulation_config=SimulationConfig(max_steps=10),
        )
    )
    assert updated.agents[agent.id].node_id == agent.node_id
    assert any(event.event_type == "action_rejected" for event in updated.events)


def test_conflicting_proposals_for_one_survivor_cannot_both_commit() -> None:
    world = generate_scenario(ScenarioConfig(seed=12, blocked_road_probability=0))
    survivor = next(iter(world.survivors.values()))
    survivor.status = SurvivorStatus.WAITING
    decisions = [
        AgentTurnDecision(
            role=AgentRole.PRIORITY,
            objective="prioritize",
            proposals=[
                AgentProposal(kind="prioritize", survivor_id=survivor.id, score=10),
                AgentProposal(kind="prioritize", survivor_id=survivor.id, score=90),
            ],
            summary="conflicting priorities",
        )
    ]

    validation = validate_round(ValidateRoundRequest(world=world, decisions=decisions))

    assert len(validation.accepted_proposal_ids) == 1
    assert len(validation.rejections) == 1
    assert "conflicting proposal" in validation.rejections[0].reasons


def test_five_independent_agent_turns_fan_out_and_reconcile() -> None:
    world = generate_scenario(
        ScenarioConfig(seed=31, survivor_count=8, agent_count=5, blocked_road_probability=0)
    )
    roles = (
        AgentRole.LOCATE,
        AgentRole.PRIORITY,
        AgentRole.COORDINATE,
        AgentRole.PLANNER,
        AgentRole.CONTROL,
    )

    async def run_parallel() -> list[AgentTurnDecision]:
        return list(
            await asyncio.gather(
                *(
                    infer_agent_turn(
                        AgentTurnRequest(world=world, role=role, provider="mock")
                    )
                    for role in roles
                )
            )
        )

    decisions = asyncio.run(run_parallel())
    validation = validate_round(ValidateRoundRequest(world=world, decisions=decisions))

    assert [decision.role for decision in decisions] == list(roles)
    assert not validation.rejections
    coordinate = next(item for item in decisions if item.role == AgentRole.COORDINATE)
    planner = next(item for item in decisions if item.role == AgentRole.PLANNER)
    assert len(coordinate.proposals) == 5
    assert len(planner.proposals) == 5


def test_v2_activity_boundary_uses_json_safe_payloads() -> None:
    payload = DurableSimulationInput(
        scenario=ScenarioConfig(seed=5),
        provider="mock",
    ).model_dump(mode="json")

    world = initialize_simulation_payload(payload)

    assert isinstance(world, dict)
    assert world["seed"] == 5
    assert world["running"] is True


def test_llm_decision_metadata_uses_activity_request_version() -> None:
    world = generate_scenario(ScenarioConfig(seed=6))
    request = AgentTurnRequest(
        world=world,
        role=AgentRole.LOCATE,
        state_version=2,
        orchestration_round=1,
    )
    decision = AgentTurnDecision(role=AgentRole.LOCATE, objective="locate", summary="locate")

    normalized = _normalise_decision(decision, request)

    assert normalized.state_version == 2
    assert normalized.orchestration_round == 1


def test_request_identity_is_stable_without_client_storage() -> None:
    request = durable_api.DurableStartRequest(seed=77, provider="mock")
    first = durable_api._business_id(request)
    second = durable_api._business_id(request.model_copy())

    assert first == second
    assert durable_api._workflow_id(first) == durable_api._workflow_id(second)


def test_duplicate_start_attaches_to_existing_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    class Handle:
        first_execution_run_id = "run-1"

        async def query(self, _query: object) -> dict[str, str]:
            return {"request_fingerprint": self.request_fingerprint}

    class FakeClient:
        starts = 0
        request_fingerprint = ""

        async def start_workflow(self, _workflow: object, _input: object, **kwargs: object) -> Handle:
            self.starts += 1
            if self.starts == 1:
                self.request_fingerprint = str(_input.get("request_fingerprint", ""))
            if self.starts > 1:
                raise WorkflowAlreadyStartedError(kwargs["id"], "KentoAgentParallelWorkflow", run_id="run-1")
            handle = Handle()
            handle.request_fingerprint = self.request_fingerprint
            return handle

        def get_workflow_handle(self, _workflow_id: str) -> Handle:
            handle = Handle()
            handle.request_fingerprint = self.request_fingerprint
            return handle

    client = FakeClient()
    monkeypatch.setattr(durable_api, "temporal_client", lambda: _resolved(client))

    async def run() -> tuple[dict[str, str], dict[str, str]]:
        first = await durable_api.start_run(
            durable_api.DurableStartRequest(seed=91, provider="mock")
        )
        second = await durable_api.start_run(
            durable_api.DurableStartRequest(seed=91, provider="mock")
        )
        return first, second

    first, second = asyncio.run(run())

    assert first["workflow_id"] == second["workflow_id"]
    assert first["attached"] == "false"
    assert second["attached"] == "true"
    assert client.starts == 2


async def _resolved(value: object) -> object:
    return value


def test_duplicate_and_stale_commands_are_ignored() -> None:
    workflow = __import__(
        "kentoagent.durable.workflow", fromlist=["KentoAgentParallelWorkflow"]
    ).KentoAgentParallelWorkflow()

    assert workflow._accept_command("command-1", 1)
    assert not workflow._accept_command("command-1", 1)
    assert not workflow._accept_command("command-2", 2)


def test_idempotency_key_cannot_bind_two_request_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    class Handle:
        first_execution_run_id = "run-1"

        async def query(self, _query: object) -> dict[str, str]:
            return {"request_fingerprint": "original"}

    class FakeClient:
        async def start_workflow(self, _workflow: object, _input: object, **kwargs: object) -> Handle:
            raise WorkflowAlreadyStartedError(kwargs["id"], "KentoAgentParallelWorkflow", run_id="run-1")

        def get_workflow_handle(self, _workflow_id: str) -> Handle:
            return Handle()

    monkeypatch.setattr(durable_api, "temporal_client", lambda: _resolved(FakeClient()))

    with pytest.raises(durable_api.HTTPException) as error:
        asyncio.run(
            durable_api.start_run(
                durable_api.DurableStartRequest(
                    idempotency_key="case-7", seed=91, provider="mock"
                )
            )
        )

    assert error.value.status_code == 409
