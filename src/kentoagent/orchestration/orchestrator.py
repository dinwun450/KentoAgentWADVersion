from __future__ import annotations

from uuid import uuid4

import weave

from kentoagent.agents import (
    ControlAgent,
    CoordinateAgent,
    LocateAgent,
    PlannerAgent,
    PriorityAgent,
)
from kentoagent.config import SimulationConfig
from kentoagent.observability.tracing import TraceSink, orchestration_inputs
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


class Orchestrator:
    """Bounded deterministic OBSERVE → PLAN → VALIDATE → ACT loop."""

    def __init__(
        self,
        world: WorldState,
        config: SimulationConfig | None = None,
        trace: TraceSink | None = None,
    ) -> None:
        self.world = world
        self.config = config or SimulationConfig()
        self.trace = trace or TraceSink()
        self.locate = LocateAgent()
        self.priority = PriorityAgent()
        self.coordinate = CoordinateAgent()
        self.planner = PlannerAgent()
        self.control = ControlAgent()

    def _event(
        self,
        event_type: str,
        status: str,
        *,
        agent_id: str | None = None,
        survivor_id: str | None = None,
        **detail: object,
    ) -> None:
        event = SimulationEvent(
            id=f"event-{uuid4().hex[:12]}",
            run_id=self.world.run_id,
            scenario_id=self.world.scenario_id,
            step=self.world.step,
            simulation_time_s=self.world.simulation_time_s,
            event_type=event_type,
            agent_id=agent_id,
            survivor_id=survivor_id,
            status=status,
            detail=detail,
        )
        self.world.events.append(event)
        self.trace.emit(event_type, **event.model_dump(exclude={"event_type"}))

    def _observe(self) -> None:
        for detection in self.locate.run(self.world):
            survivor = self.world.survivors[detection.survivor_id]
            survivor.status = SurvivorStatus.WAITING
            survivor.detection_confidence = detection.confidence
            detector = self.world.agents[detection.detected_by]
            detector.status = AgentStatus.SEARCHING
            detector.confidence = detection.confidence
            detector.last_action = f"Detected {survivor.id}"
            message = f"Candidate {survivor.id} confidence {detection.confidence:.2f}"
            detector.recent_messages = (detector.recent_messages + [message])[-5:]
            detector.decision = DecisionExplanation(
                objective="Search unexplored zones for survivors",
                observations=[f"Candidate at {survivor.node_id}"],
                constraints=["Report typed detections only", "Do not assign rescue resources"],
                action=f"Publish SurvivorDetected for {survivor.id}",
                validation="Candidate exists in scenario state",
                result=f"Confidence {detection.confidence:.2f}",
                next_step="Continue deterministic sweep",
            )
            self._event(
                detection.type,
                "detected",
                agent_id=detection.detected_by,
                survivor_id=detection.survivor_id,
                confidence=detection.confidence,
            )

    def _prioritize(self) -> None:
        for update in self.priority.run(self.world):
            self.world.survivors[update.survivor_id].priority_score = update.score
            self._event(
                update.type,
                "ranked",
                survivor_id=update.survivor_id,
                score=update.score,
                factors=update.factors,
            )
        priorities = [
            agent for agent in self.world.agents.values() if agent.role == AgentRole.PRIORITY
        ]
        if priorities:
            best = max(
                (s for s in self.world.survivors.values() if s.status == SurvivorStatus.WAITING),
                key=lambda item: item.priority_score,
                default=None,
            )
            agent = priorities[0]
            agent.last_action = f"Ranked {best.id}" if best else "No visible candidates"
            agent.decision = DecisionExplanation(
                objective="Rank visible rescue candidates",
                observations=[
                    f"{best.id} score {best.priority_score}" if best else "No waiting survivors"
                ],
                constraints=["Severity cannot bypass reachability guardrails"],
                action=agent.last_action,
                validation="Deterministic weighted score",
                result="Priority queue updated",
                next_step="Coordinate assignments",
            )

    def _assign(self) -> None:
        for assignment in self.coordinate.run(self.world):
            agent = self.world.agents[assignment.agent_id]
            survivor = self.world.survivors[assignment.survivor_id]
            if survivor.assigned_agent_id is not None:
                self._event(
                    "duplicate_assignment",
                    "prevented",
                    agent_id=agent.id,
                    survivor_id=survivor.id,
                )
                continue
            agent.current_assignment = survivor.id
            agent.status = AgentStatus.ASSIGNED
            agent.objective = f"Rescue {survivor.id}"
            survivor.assigned_agent_id = agent.id
            survivor.status = SurvivorStatus.ASSIGNED
            self._event(
                assignment.type,
                "accepted",
                agent_id=agent.id,
                survivor_id=survivor.id,
                priority_score=assignment.priority_score,
            )

    def _road_between(self, a: str, b: str) -> str | None:
        for road in self.world.roads.values():
            if {road.source, road.target} == {a, b}:
                return road.id
        return None

    def _inject_environment_change(self) -> None:
        interval = self.config.dynamic_change_interval
        if not interval or self.world.step == 0 or self.world.step % interval:
            return
        for agent in sorted(self.world.agents.values(), key=lambda item: item.id):
            if not agent.route:
                continue
            road_id = self._road_between(agent.node_id, agent.route[0])
            if road_id and not self.world.roads[road_id].blocked:
                self.world.roads[road_id].blocked = True
                self._event(
                    "environment_changed",
                    "road_blocked",
                    agent_id=agent.id,
                    road_id=road_id,
                )
                return

    def _cached_plan(self, agent_id: str) -> RescuePlan | None:
        agent = self.world.agents[agent_id]
        if not agent.current_assignment or not agent.route:
            return None
        node_path = [agent.node_id, *agent.route]
        roads = [self._road_between(a, b) for a, b in zip(node_path, node_path[1:], strict=False)]
        if any(road is None for road in roads):
            return None
        return RescuePlan(
            agent_id=agent.id,
            survivor_id=agent.current_assignment,
            node_path=node_path,
            road_ids=[road for road in roads if road is not None],
            estimated_distance_m=sum(
                self.world.roads[road].distance_m for road in roads if road is not None
            ),
        )

    def _plan_for(self, agent_id: str) -> RescuePlan | None:
        agent = self.world.agents[agent_id]
        if not agent.current_assignment:
            return None
        survivor = self.world.survivors[agent.current_assignment]
        route = shortest_route(self.world, agent.node_id, survivor.node_id)
        if route is None:
            survivor.status = SurvivorStatus.UNREACHABLE
            survivor.assigned_agent_id = None
            agent.current_assignment = None
            agent.status = AgentStatus.IDLE
            agent.route = []
            self._event(
                "route_unavailable",
                "unreachable",
                agent_id=agent.id,
                survivor_id=survivor.id,
            )
            return None
        return RescuePlan(
            agent_id=agent.id,
            survivor_id=survivor.id,
            node_path=route.nodes,
            road_ids=route.road_ids,
            estimated_distance_m=route.distance_m,
        )

    def _plan_validate_act(self) -> None:
        responders = [
            agent
            for agent in self.world.agents.values()
            if agent.current_assignment
        ]
        for agent in responders:
            plan = self._cached_plan(agent.id) or self._plan_for(agent.id)
            correction_attempt = 0
            while plan is not None:
                rejection = self.control.inspect(self.world, plan)
                if rejection is None:
                    break
                self._event(
                    rejection.type,
                    "rejected",
                    agent_id=agent.id,
                    survivor_id=plan.survivor_id,
                    reasons=rejection.reasons,
                )
                correction_attempt += 1
                agent.route = []
                if correction_attempt > self.config.max_correction_attempts:
                    agent.status = AgentStatus.UNAVAILABLE
                    self._event(
                        "correction_exhausted",
                        "escalated",
                        agent_id=agent.id,
                        survivor_id=plan.survivor_id,
                    )
                    plan = None
                    break
                self._event(
                    "replan_requested",
                    "retrying",
                    agent_id=agent.id,
                    survivor_id=plan.survivor_id,
                    attempt=correction_attempt,
                    reason="; ".join(rejection.reasons),
                )
                plan = self._plan_for(agent.id)
                if plan is not None and self.control.inspect(self.world, plan) is None:
                    self._event(
                        "correction_succeeded",
                        "accepted",
                        agent_id=agent.id,
                        survivor_id=plan.survivor_id,
                        attempt=correction_attempt,
                    )
            if plan is None:
                continue
            agent.route = plan.node_path[1:]
            agent.status = AgentStatus.MOVING
            agent.last_action = f"Validated route to {plan.survivor_id}"
            self._event(
                plan.type,
                "validated",
                agent_id=agent.id,
                survivor_id=plan.survivor_id,
                node_path=plan.node_path,
                road_ids=plan.road_ids,
                estimated_distance_m=plan.estimated_distance_m,
            )
            if agent.route:
                next_node = agent.route.pop(0)
                agent.node_id = next_node
                agent.position = self.world.nodes[next_node].position
                agent.last_action = f"Moved to {next_node}"
                self._event(
                    "movement",
                    "completed",
                    agent_id=agent.id,
                    survivor_id=plan.survivor_id,
                    node_id=next_node,
                )
            survivor = self.world.survivors[plan.survivor_id]
            if agent.node_id == survivor.node_id:
                survivor.status = SurvivorStatus.RESCUED
                survivor.rescue_step = self.world.step
                survivor.assigned_agent_id = None
                agent.current_assignment = None
                agent.status = AgentStatus.IDLE
                agent.route = []
                agent.last_action = f"Rescued {survivor.id}"
                self._event(
                    "rescue_completed",
                    "rescued",
                    agent_id=agent.id,
                    survivor_id=survivor.id,
                )
            agent.decision = DecisionExplanation(
                objective=f"Rescue {plan.survivor_id}",
                observations=[f"Route length {len(plan.road_ids)} roads"],
                constraints=[
                    "Existing traversable roads",
                    "Avoid blocked road points",
                    "No duplicate assignment",
                ],
                action=agent.last_action,
                validation="PASS — deterministic route guardrails",
                result=agent.status.value,
                next_step="Continue route" if agent.current_assignment else "Await assignment",
            )

    def _termination_reason(self) -> str | None:
        unresolved = [
            item
            for item in self.world.survivors.values()
            if item.status not in (SurvivorStatus.RESCUED, SurvivorStatus.UNREACHABLE)
        ]
        if not unresolved:
            return "all reachable survivors resolved"
        if self.world.step >= self.config.max_steps:
            return "configured max steps reached"
        responders = list(self.world.agents.values())
        if responders and all(a.status == AgentStatus.UNAVAILABLE for a in responders):
            return "all responder agents unavailable"
        return None

    @weave.op(
        name="kentoagent.orchestration_step",
        postprocess_inputs=orchestration_inputs,
        enable_code_capture=False,
    )
    def step(self) -> WorldState:
        if self.world.stopped_reason:
            return self.world
        self.world.step += 1
        self.world.simulation_time_s += self.config.seconds_per_step
        with self.trace.span(
            "orchestration_step",
            run_id=self.world.run_id,
            scenario_id=self.world.scenario_id,
            step=self.world.step,
        ):
            self._observe()
            self._prioritize()
            self._assign()
            self._inject_environment_change()
            self._plan_validate_act()
        reason = self._termination_reason()
        if reason:
            self.world.running = False
            self.world.stopped_reason = reason
            self._event("simulation_stopped", "complete", reason=reason)
        return self.world

    @weave.op(
        name="kentoagent.simulation_run",
        postprocess_inputs=orchestration_inputs,
        enable_code_capture=False,
    )
    def run(self, steps: int | None = None) -> WorldState:
        self.world.running = True
        budget = steps if steps is not None else self.config.max_steps
        for _ in range(budget):
            if self.world.stopped_reason:
                break
            self.step()
        return self.world
