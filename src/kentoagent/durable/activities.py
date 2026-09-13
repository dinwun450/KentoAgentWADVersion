from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import weave
from dotenv import load_dotenv
from temporalio import activity
from temporalio.exceptions import ApplicationError

from kentoagent.agents.coordinate import CoordinateAgent
from kentoagent.agents.priority import PriorityAgent
from kentoagent.durable.models import (
    AgentProposal,
    AgentTurnDecision,
    AgentTurnRequest,
    ApplyRoundRequest,
    DurableSimulationInput,
    ProposalRejection,
    ProposalValidation,
    RoundValidation,
    ValidateRoundRequest,
)
from kentoagent.evaluation.guardrails import validate_rescue_plan
from kentoagent.observability.tracing import TraceSink, inference_inputs
from kentoagent.orchestration.messages import RescuePlan
from kentoagent.simulation.entities import (
    AgentRole,
    AgentStatus,
    DecisionExplanation,
    SimulationEvent,
    SurvivorStatus,
    WorldState,
)
from kentoagent.simulation.routing import shortest_route
from kentoagent.simulation.scenario import generate_scenario

ROLE_PROMPTS: dict[AgentRole, str] = {
    AgentRole.LOCATE: (
        "Find plausible survivor records still marked unknown. Propose detect actions only. "
        "Do not prioritize, assign, route, or mutate state."
    ),
    AgentRole.PRIORITY: (
        "Rank visible or newly detected survivors. Propose prioritize actions with scores from "
        "0 to 100 using severity, trapped status, hazards, accessibility, and rescue probability."
    ),
    AgentRole.COORDINATE: (
        "Orchestrate up to five mobile responders. Allocate unique waiting survivors by severity "
        "and status, using only the safe recommended dispatch pairs. Propose assign actions only."
    ),
    AgentRole.PLANNER: (
        "Independently plan routes for the safe recommended dispatch pairs and active assignments. "
        "Return exact node_path and road_ids and never use a blocked road."
    ),
    AgentRole.CONTROL: (
        "Review proposals and deterministic validation feedback. Propose approve, reject, or noop. "
        "Never claim that a deterministically invalid action is valid."
    ),
}

ALLOWED_KINDS: dict[AgentRole, set[str]] = {
    AgentRole.LOCATE: {"detect", "noop"},
    AgentRole.PRIORITY: {"prioritize", "noop"},
    AgentRole.COORDINATE: {"assign", "noop"},
    AgentRole.PLANNER: {"plan", "noop"},
    AgentRole.CONTROL: {"approve", "reject", "noop"},
}

load_dotenv()  # Load environment variables from .env file


def _recommended_dispatch(world: WorldState) -> list[dict[str, object]]:
    """Return deterministic safe pairings shared with independent specialist turns."""

    working = world.model_copy(deep=True)
    for update in PriorityAgent().run(working):
        working.survivors[update.survivor_id].priority_score = update.score
    return [item.model_dump(mode="json") for item in CoordinateAgent().run(working)]


def _world_context(request: AgentTurnRequest) -> str:
    return json.dumps(
        {
            "role": request.role.value,
            "execution_mode": "five_independent_parallel_specialists",
            "world": request.world.model_dump(mode="json", exclude={"events"}),
            "recommended_dispatch": _recommended_dispatch(request.world),
            "prior_decisions": [item.model_dump(mode="json") for item in request.prior_decisions],
            "validation_feedback": request.validation_feedback,
            "required_output": AgentTurnDecision.model_json_schema(),
        },
        separators=(",", ":"),
    )


async def _llamaindex_decision(request: AgentTurnRequest) -> AgentTurnDecision:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ApplicationError(
            "OPENAI_API_KEY is required for provider=openai. You probably forgot to load "
            "the dotenv module, haven't you?!",
            type="ConfigurationError",
            non_retryable=True,
        )
    try:
        from llama_index.core.agent.workflow import FunctionAgent
        from llama_index.llms.openai import OpenAI
    except ImportError as exc:
        raise ApplicationError(
            "Install the agents extra: uv sync --extra agents",
            type="ConfigurationError",
            non_retryable=True,
        ) from exc

    llm = OpenAI(model=request.model, api_key=api_key, max_retries=0, timeout=60.0)
    agent = FunctionAgent(
        name=f"{request.role.value.title()}Agent",
        description=ROLE_PROMPTS[request.role].split(".")[0],
        system_prompt=(
            f"You are KentoAgent's {request.role.value} specialist. {ROLE_PROMPTS[request.role]} "
            "Return only grounded typed proposals. The world snapshot is authoritative. Do not "
            "expose hidden chain-of-thought; observations and summary must be concise evidence."
        ),
        tools=[],
        llm=llm,
        output_cls=AgentTurnDecision,
    )
    response: Any = await agent.run(user_msg=_world_context(request))
    if response.structured_response is None:
        raise ApplicationError(
            "Agent did not return AgentTurnDecision",
            type="StructuredOutputError",
        )
    decision = AgentTurnDecision.model_validate(response.structured_response)
    if decision.role != request.role:
        raise ApplicationError(
            f"Agent returned role {decision.role.value}; expected {request.role.value}",
            type="StructuredOutputError",
        )
    return decision


@weave.op(
    name="kentoagent.agent_inference",
    postprocess_inputs=inference_inputs,
    enable_code_capture=False,
)
async def infer_agent_turn(request: AgentTurnRequest) -> AgentTurnDecision:
    """Trace one specialist inference, including auto-traced provider calls."""

    if request.provider == "mock":
        return _mock_decision(request)
    return await _llamaindex_decision(request)


def _mock_decision(request: AgentTurnRequest) -> AgentTurnDecision:
    world = request.world
    proposals: list[AgentProposal] = []
    handoff: AgentRole | None = None
    if request.role == AgentRole.LOCATE:
        unknown = sorted(
            (item for item in world.survivors.values() if item.status == SurvivorStatus.UNKNOWN),
            key=lambda item: item.id,
        )
        if unknown:
            proposals.append(
                AgentProposal(kind="detect", survivor_id=unknown[0].id, confidence=0.85)
            )
        handoff = AgentRole.PRIORITY
    elif request.role == AgentRole.PRIORITY:
        proposals.extend(
            AgentProposal(
                kind="prioritize",
                survivor_id=update.survivor_id,
                score=update.score,
            )
            for update in PriorityAgent().run(world)
        )
        handoff = AgentRole.COORDINATE
    elif request.role == AgentRole.COORDINATE:
        proposals.extend(
            AgentProposal(
                kind="assign",
                agent_id=str(item["agent_id"]),
                survivor_id=str(item["survivor_id"]),
            )
            for item in _recommended_dispatch(world)
        )
        handoff = AgentRole.PLANNER
    elif request.role == AgentRole.PLANNER:
        assignments = [
            (str(item["agent_id"]), str(item["survivor_id"]))
            for item in _recommended_dispatch(world)
        ]
        for agent in world.agents.values():
            if agent.current_assignment:
                assignments.append((agent.id, agent.current_assignment))
        seen: set[tuple[str, str]] = set()
        for agent_id, survivor_id in assignments:
            if (agent_id, survivor_id) in seen:
                continue
            seen.add((agent_id, survivor_id))
            agent = world.agents[agent_id]
            survivor = world.survivors[survivor_id]
            route = shortest_route(world, agent.node_id, survivor.node_id)
            if route:
                proposals.append(
                    AgentProposal(
                        kind="plan",
                        agent_id=agent.id,
                        survivor_id=survivor.id,
                        node_path=route.nodes,
                        road_ids=route.road_ids,
                    )
                )
        handoff = AgentRole.CONTROL
    else:
        proposals.append(
            AgentProposal(
                kind="reject" if request.validation_feedback else "approve",
                reasons=request.validation_feedback,
            )
        )

    return AgentTurnDecision(
        role=request.role,
        state_version=request.state_version,
        orchestration_round=request.orchestration_round,
        objective=ROLE_PROMPTS[request.role].split(".")[0],
        observations=[f"World revision step {world.step}", *request.validation_feedback[:3]],
        constraints=["Typed proposals only", "Deterministic guardrails are authoritative"],
        proposals=proposals,
        handoff_to=handoff,
        summary=f"{request.role.value} produced {len(proposals)} proposal(s)",
    )


def _stable_proposal_id(role: AgentRole, proposal: AgentProposal, index: int) -> str:
    digest = hashlib.sha256(proposal.fingerprint.encode()).hexdigest()[:16]
    return f"proposal-{role.value}-{index}-{digest}"


def _feedback_id(proposal_id: str, reasons: list[str]) -> str:
    payload = json.dumps({"proposal_id": proposal_id, "reasons": reasons}, sort_keys=True)
    return f"feedback-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _activity_attempt() -> int:
    try:
        return activity.info().attempt
    except RuntimeError:
        return 1


def _normalise_decision(
    decision: AgentTurnDecision,
    request: AgentTurnRequest,
) -> AgentTurnDecision:
    decision.state_version = request.state_version
    decision.orchestration_round = request.orchestration_round
    for index, proposal in enumerate(decision.proposals):
        proposal.proposal_id = proposal.proposal_id or _stable_proposal_id(
            decision.role, proposal, index
        )
    return decision


@activity.defn(name="kentoagent.initialize_simulation")
def initialize_simulation(input: DurableSimulationInput) -> WorldState:
    world = input.world.model_copy(deep=True) if input.world else generate_scenario(input.scenario)
    world.running = True
    world.stopped_reason = None
    return world


@activity.defn(name="kentoagent.invoke_agent")
async def invoke_agent(request: AgentTurnRequest) -> AgentTurnDecision:
    activity.heartbeat({"role": request.role.value, "step": request.world.step})
    trace = TraceSink()
    decision = _normalise_decision(await infer_agent_turn(request), request)
    trace.emit(
        "llm_agent_turn",
        run_id=request.world.run_id,
        scenario_id=request.world.scenario_id,
        agent_role=request.role.value,
        step=request.world.step,
        model=request.model,
        provider=request.provider,
        workflow_id=request.workflow_id,
        state_version=request.state_version,
        orchestration_round=request.orchestration_round,
        proposal_count=len(decision.proposals),
        status="completed",
    )
    return decision


def validate_round(request: ValidateRoundRequest) -> RoundValidation:
    world = request.world
    accepted: list[str] = []
    rejected: list[ProposalRejection] = []
    results: list[ProposalValidation] = []
    accepted_proposal_ids: list[str] = []
    seen_fingerprints: set[str] = set()
    seen_targets: dict[tuple[str, str | None, str | None], str] = {}
    visible = {
        item.id
        for item in world.survivors.values()
        if item.status in (SurvivorStatus.WAITING, SurvivorStatus.ASSIGNED)
    }
    assignments = {
        item.id: item.assigned_agent_id
        for item in world.survivors.values()
        if item.assigned_agent_id
    }

    for decision in sorted(request.decisions, key=lambda item: item.role.value):
        for index, proposal in enumerate(decision.proposals):
            proposal_id = proposal.proposal_id or _stable_proposal_id(
                decision.role, proposal, index
            )
            reasons: list[str] = []
            fingerprint = proposal.fingerprint
            target_agent = proposal.agent_id if proposal.kind == "plan" else None
            target = (proposal.kind, target_agent, proposal.survivor_id)
            if fingerprint in seen_fingerprints:
                reasons.append("duplicate proposal")
            elif target in seen_targets and seen_targets[target] != fingerprint:
                reasons.append("conflicting proposal")
            seen_fingerprints.add(fingerprint)
            seen_targets[target] = fingerprint
            if proposal.kind not in ALLOWED_KINDS[decision.role]:
                reasons.append(f"{decision.role.value} cannot propose {proposal.kind}")
            survivor = world.survivors.get(proposal.survivor_id or "")
            agent = world.agents.get(proposal.agent_id or "")
            if proposal.kind == "detect":
                if survivor is None:
                    reasons.append("survivor does not exist")
                elif survivor.status != SurvivorStatus.UNKNOWN:
                    reasons.append("survivor is not unknown")
                if proposal.confidence is None:
                    reasons.append("confidence is required")
                if not reasons and survivor:
                    visible.add(survivor.id)
            elif proposal.kind == "prioritize":
                if survivor is None or survivor.id not in visible:
                    reasons.append("survivor is not visible")
                if proposal.score is None:
                    reasons.append("score is required")
            elif proposal.kind == "assign":
                if agent is None:
                    reasons.append("assignment agent does not exist")
                elif agent.current_assignment is not None or agent.status not in (
                    AgentStatus.IDLE,
                    AgentStatus.SEARCHING,
                ):
                    reasons.append("assignment agent is not an available responder")
                if survivor is None or survivor.id not in visible:
                    reasons.append("assignment survivor is not waiting")
                elif survivor.id in assignments:
                    reasons.append("survivor already has an assignment")
                if not reasons and survivor and agent:
                    assignments[survivor.id] = agent.id
            elif proposal.kind == "plan":
                if agent is None or survivor is None:
                    reasons.append("plan agent or survivor does not exist")
                elif assignments.get(survivor.id, survivor.assigned_agent_id) != agent.id:
                    reasons.append("plan does not match an accepted assignment")
                if not reasons and agent and survivor:
                    plan = RescuePlan(
                        agent_id=agent.id,
                        survivor_id=survivor.id,
                        node_path=proposal.node_path,
                        road_ids=proposal.road_ids,
                        estimated_distance_m=sum(
                            world.roads[road_id].distance_m
                            for road_id in proposal.road_ids
                            if road_id in world.roads
                        ),
                    )
                    result = validate_rescue_plan(world, plan)
                    reasons.extend(result.reasons)
            if reasons:
                reason_code = "domain_validation"
                if "duplicate proposal" in reasons:
                    reason_code = "duplicate_proposal"
                elif "conflicting proposal" in reasons:
                    reason_code = "conflicting_proposal"
                feedback_id = _feedback_id(proposal_id, reasons)
                rejected.append(
                    ProposalRejection(
                        proposal_id=proposal_id,
                        idempotency_key=proposal.idempotency_key,
                        reasons=reasons,
                    )
                )
                results.append(
                    ProposalValidation(
                        proposal_id=proposal_id,
                        status="rejected",
                        reason_code=reason_code,
                        responsible_role=decision.role,
                        feedback_id=feedback_id,
                        retryable=True,
                    )
                )
            else:
                accepted.append(proposal.idempotency_key)
                accepted_proposal_ids.append(proposal_id)
                results.append(
                    ProposalValidation(
                        proposal_id=proposal_id,
                        status="accepted",
                        responsible_role=decision.role,
                    )
                )
    return RoundValidation(
        accepted_keys=accepted,
        accepted_proposal_ids=accepted_proposal_ids,
        rejections=rejected,
        results=results,
        state_version=request.state_version,
        orchestration_round=request.orchestration_round,
    )


@activity.defn(name="kentoagent.validate_round")
def validate_round_activity(request: ValidateRoundRequest) -> RoundValidation:
    return validate_round(request)


def _record_event(
    world: WorldState,
    event_type: str,
    status: str,
    *,
    agent_id: str | None = None,
    survivor_id: str | None = None,
    **detail: Any,
) -> None:
    world.events.append(
        SimulationEvent(
            id=f"event-{world.step:05d}-{len(world.events) + 1:06d}",
            run_id=world.run_id,
            scenario_id=world.scenario_id,
            step=world.step,
            simulation_time_s=world.simulation_time_s,
            event_type=event_type,
            agent_id=agent_id,
            survivor_id=survivor_id,
            status=status,
            detail=detail,
        )
    )


def apply_round(request: ApplyRoundRequest) -> WorldState:
    world = request.world.model_copy(deep=True)
    accepted = set(request.validation.accepted_keys)
    accepted_proposal_ids = set(request.validation.accepted_proposal_ids)
    world.step += 1
    world.simulation_time_s += request.simulation_config.seconds_per_step
    moved_agents: set[str] = set()

    for rejection in request.validation.rejections:
        _record_event(
            world,
            "action_rejected",
            "rejected",
            idempotency_key=rejection.idempotency_key,
            reasons=rejection.reasons,
        )
    for decision in request.decisions:
        role_agents = [item for item in world.agents.values() if item.role == decision.role]
        if role_agents:
            role_agents[0].decision = DecisionExplanation(
                objective=decision.objective,
                observations=decision.observations,
                constraints=decision.constraints,
                action=decision.summary,
                validation="PASS" if not request.validation.rejections else "Guardrails applied",
                result=f"{len(decision.proposals)} proposal(s)",
                next_step=(
                    f"Handoff to {decision.handoff_to.value}"
                    if decision.handoff_to
                    else "Await next durable round"
                ),
            )
            role_agents[0].last_action = decision.summary

        for index, proposal in enumerate(decision.proposals):
            proposal_id = proposal.proposal_id or _stable_proposal_id(
                decision.role, proposal, index
            )
            if accepted_proposal_ids:
                accepted_proposal = proposal_id in accepted_proposal_ids
            else:
                accepted_proposal = proposal.idempotency_key in accepted
            if not accepted_proposal:
                continue
            survivor = world.survivors.get(proposal.survivor_id or "")
            agent = world.agents.get(proposal.agent_id or "")
            if proposal.kind == "detect" and survivor:
                survivor.status = SurvivorStatus.WAITING
                survivor.detection_confidence = proposal.confidence or 0
                _record_event(
                    world,
                    "survivor_detected",
                    "detected",
                    survivor_id=survivor.id,
                    confidence=survivor.detection_confidence,
                )
            elif proposal.kind == "prioritize" and survivor:
                survivor.priority_score = proposal.score or 0
                _record_event(
                    world,
                    "priority_update",
                    "ranked",
                    survivor_id=survivor.id,
                    score=survivor.priority_score,
                )
            elif proposal.kind == "assign" and survivor and agent:
                survivor.assigned_agent_id = agent.id
                survivor.status = SurvivorStatus.ASSIGNED
                agent.current_assignment = survivor.id
                agent.status = AgentStatus.ASSIGNED
                agent.objective = f"Rescue {survivor.id}"
                _record_event(
                    world,
                    "task_assignment",
                    "accepted",
                    agent_id=agent.id,
                    survivor_id=survivor.id,
                )
            elif proposal.kind == "plan" and survivor and agent and agent.id not in moved_agents:
                agent.route = proposal.node_path[1:]
                agent.status = AgentStatus.MOVING
                _record_event(
                    world,
                    "rescue_plan",
                    "validated",
                    agent_id=agent.id,
                    survivor_id=survivor.id,
                    node_path=proposal.node_path,
                    road_ids=proposal.road_ids,
                )
                if agent.route:
                    next_node = agent.route.pop(0)
                    agent.node_id = next_node
                    agent.position = world.nodes[next_node].position
                    agent.last_action = f"Moved to {next_node}"
                    _record_event(
                        world,
                        "movement",
                        "completed",
                        agent_id=agent.id,
                        survivor_id=survivor.id,
                        node_id=next_node,
                    )
                if agent.node_id == survivor.node_id:
                    survivor.status = SurvivorStatus.RESCUED
                    survivor.rescue_step = world.step
                    survivor.assigned_agent_id = None
                    agent.current_assignment = None
                    agent.status = AgentStatus.IDLE
                    agent.route = []
                    _record_event(
                        world,
                        "rescue_completed",
                        "rescued",
                        agent_id=agent.id,
                        survivor_id=survivor.id,
                    )
                moved_agents.add(agent.id)

    unresolved = [
        item
        for item in world.survivors.values()
        if item.status not in (SurvivorStatus.RESCUED, SurvivorStatus.UNREACHABLE)
    ]
    if not unresolved:
        world.stopped_reason = "all reachable survivors resolved"
    elif world.step >= request.simulation_config.max_steps:
        world.stopped_reason = "configured max steps reached"
    if world.stopped_reason:
        world.running = False
        _record_event(world, "simulation_stopped", "complete", reason=world.stopped_reason)
    return world


@activity.defn(name="kentoagent.apply_round")
def apply_round_activity(request: ApplyRoundRequest) -> WorldState:
    return apply_round(request)


@activity.defn(name="kentoagent.stop_simulation")
def stop_simulation(world: WorldState) -> WorldState:
    result = world.model_copy(deep=True)
    result.running = False
    result.stopped_reason = "operator stopped the simulation"
    _record_event(result, "simulation_stopped", "operator", reason=result.stopped_reason)
    return result


# V2 activities deliberately exchange JSON-safe dictionaries with the workflow.
# This avoids Python workflow-sandbox class-identity failures while keeping typed
# Pydantic validation inside the non-deterministic Activity boundary.
@activity.defn(name="kentoagent.v2.initialize_simulation")
def initialize_simulation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    input = DurableSimulationInput.model_validate(payload)
    return initialize_simulation(input).model_dump(mode="json")


@activity.defn(name="kentoagent.v2.invoke_agent")
async def invoke_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request = AgentTurnRequest.model_validate(payload)
    schema_repairs = 0
    while True:
        try:
            decision = await invoke_agent(request)
            result = decision.model_dump(mode="json")
            result["_evaluation"] = {
                "temporal_activity_attempt": _activity_attempt(),
                "schema_repair_attempts": schema_repairs,
                "agent_trace_id": None,
            }
            return result
        except ApplicationError as exc:
            if getattr(exc, "type", None) != "StructuredOutputError" or schema_repairs >= 1:
                raise
            schema_repairs += 1
            request = request.model_copy(
                update={
                    "validation_feedback": [
                        *request.validation_feedback,
                        "Schema repair: return exactly one valid AgentTurnDecision object.",
                    ]
                }
            )


@activity.defn(name="kentoagent.v2.validate_round")
def validate_round_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request = ValidateRoundRequest.model_validate(payload)
    return validate_round(request).model_dump(mode="json")


@activity.defn(name="kentoagent.v2.apply_round")
def apply_round_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request = ApplyRoundRequest.model_validate(payload)
    return apply_round(request).model_dump(mode="json")


@activity.defn(name="kentoagent.v2.stop_simulation")
def stop_simulation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    world = WorldState.model_validate(payload)
    return stop_simulation(world).model_dump(mode="json")
