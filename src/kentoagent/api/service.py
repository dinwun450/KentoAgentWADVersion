from __future__ import annotations

from threading import RLock

from pydantic import BaseModel, Field

from kentoagent.evaluation.metrics import SystemMetrics, calculate_metrics
from kentoagent.orchestration.orchestrator import Orchestrator
from kentoagent.simulation.entities import AgentStatus, SurvivorStatus, WorldState
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


class GenerateRequest(BaseModel):
    seed: int = 49281
    survivor_count: int = Field(default=8, ge=1, le=25)
    agent_count: int = Field(default=5, ge=5, le=25)
    hazard_density: float = Field(default=0.18, ge=0, le=0.8)
    blocked_road_probability: float = Field(default=0.16, ge=0, le=0.8)


class OperatorCommand(BaseModel):
    command: str
    agent_id: str | None = None
    seed: int | None = None


class OperatorResponse(BaseModel):
    answer: str
    state_changed: bool = False
    selected_agent_id: str | None = None


class SimulationService:
    def __init__(self) -> None:
        self.lock = RLock()
        self.request = GenerateRequest()
        self.world = generate_scenario(ScenarioConfig(**self.request.model_dump()))
        self.orchestrator = Orchestrator(self.world)
        self.previous_metrics: SystemMetrics | None = None

    def generate(self, request: GenerateRequest) -> WorldState:
        with self.lock:
            self.previous_metrics = calculate_metrics(self.world)
            self.request = request
            self.world = generate_scenario(ScenarioConfig(**request.model_dump()))
            self.orchestrator = Orchestrator(self.world)
            return self.world

    def step(self) -> WorldState:
        with self.lock:
            return self.orchestrator.step()

    def reset(self) -> WorldState:
        return self.generate(self.request)

    def ask(self, request: OperatorCommand) -> OperatorResponse:
        text = request.command.strip().lower()
        if "pause" in text:
            self.world.running = False
            return OperatorResponse(answer="Simulation paused.", state_changed=True)
        if "step" in text:
            self.step()
            return OperatorResponse(
                answer=f"Advanced to step {self.world.step}.", state_changed=True
            )
        if "new" in text and "scenario" in text or "generate" in text:
            seed = request.seed if request.seed is not None else self.world.seed + 1
            self.generate(self.request.model_copy(update={"seed": seed}))
            return OperatorResponse(
                answer=f"Generated replayable scenario with seed {seed}.", state_changed=True
            )
        if "idle" in text:
            idle = [a.name for a in self.world.agents.values() if a.status == AgentStatus.IDLE]
            return OperatorResponse(answer="Idle agents: " + (", ".join(idle) or "none"))
        if "highest" in text and "priority" in text:
            candidates = [
                survivor
                for survivor in self.world.survivors.values()
                if survivor.status in (SurvivorStatus.WAITING, SurvivorStatus.ASSIGNED)
            ]
            target = max(candidates, key=lambda item: item.priority_score, default=None)
            answer = (
                f"{target.id} is highest at {target.priority_score:.1f}."
                if target
                else "No visible unresolved survivor is currently ranked."
            )
            return OperatorResponse(answer=answer)
        if "rejected" in text or "replan" in text:
            rejected = [e for e in self.world.events if e.event_type == "action_rejected"]
            if not rejected:
                return OperatorResponse(answer="No plan has been rejected in this run.")
            last = rejected[-1]
            return OperatorResponse(
                answer=f"{last.agent_id}'s plan was rejected: {last.detail.get('reasons', [])}.",
                selected_agent_id=last.agent_id,
            )
        if "compare" in text:
            current = calculate_metrics(self.world)
            if self.previous_metrics is None:
                return OperatorResponse(answer="No previous run is available for comparison.")
            delta = current.rescue_rate - self.previous_metrics.rescue_rate
            return OperatorResponse(
                answer=f"Rescue-rate delta versus the previous run is {delta:+.1%}."
            )
        if request.agent_id or "agent" in text:
            agent = self.world.agents.get(request.agent_id or "")
            if agent is None:
                for candidate in self.world.agents.values():
                    if candidate.id in text or candidate.name.lower() in text:
                        agent = candidate
                        break
            if agent is None:
                return OperatorResponse(answer="Select an agent or include its exact name.")
            detail = agent.decision
            answer = (
                f"{agent.name}: {detail.action}. {detail.validation}. Next: {detail.next_step}."
                if detail
                else f"{agent.name} is {agent.status.value} and has not made a decision yet."
            )
            return OperatorResponse(answer=answer, selected_agent_id=agent.id)
        return OperatorResponse(
            answer=(
                "Try asking about the highest priority survivor, idle agents, a rejected plan, "
                "an agent, run comparison, or issue pause/step/generate commands."
            )
        )
