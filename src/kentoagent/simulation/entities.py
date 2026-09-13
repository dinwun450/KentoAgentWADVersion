from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AgentRole(StrEnum):
    LOCATE = "locate"
    COORDINATE = "coordinate"
    CONTROL = "control"
    PRIORITY = "priority"
    PLANNER = "planner"


class AgentStatus(StrEnum):
    IDLE = "idle"
    SEARCHING = "searching"
    ASSIGNED = "assigned"
    MOVING = "moving"
    RESCUING = "rescuing"
    UNAVAILABLE = "unavailable"


class Severity(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class SurvivorStatus(StrEnum):
    UNKNOWN = "unknown"
    WAITING = "waiting"
    ASSIGNED = "assigned"
    RESCUED = "rescued"
    UNREACHABLE = "unreachable"


class Coordinate(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)

    def as_list(self) -> list[float]:
        return [self.lon, self.lat]


class RoadNode(BaseModel):
    id: str
    position: Coordinate


class RoadSegment(BaseModel):
    id: str
    source: str
    target: str
    coordinates: list[Coordinate]
    distance_m: float = Field(gt=0)
    blocked: bool = False
    hazard_cost: float = Field(default=0, ge=0)


class Survivor(BaseModel):
    id: str
    node_id: str
    position: Coordinate
    severity: Severity
    trapped: bool
    status: SurvivorStatus = SurvivorStatus.UNKNOWN
    detection_confidence: float = Field(default=0, ge=0, le=1)
    priority_score: float = 0
    assigned_agent_id: str | None = None
    rescue_step: int | None = None


class Hazard(BaseModel):
    id: str
    node_id: str
    position: Coordinate
    kind: str
    intensity: float = Field(ge=0, le=1)
    radius_m: float = Field(gt=0)


class DecisionExplanation(BaseModel):
    objective: str
    observations: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    action: str
    validation: str
    result: str
    next_step: str


class AgentState(BaseModel):
    id: str
    name: str
    role: AgentRole
    node_id: str
    position: Coordinate
    status: AgentStatus = AgentStatus.IDLE
    current_assignment: str | None = None
    route: list[str] = Field(default_factory=list)
    objective: str = "Await orchestration"
    last_action: str = "initialized"
    confidence: float | None = None
    recent_messages: list[str] = Field(default_factory=list)
    decision: DecisionExplanation | None = None


class SimulationEvent(BaseModel):
    id: str
    run_id: str
    scenario_id: str
    step: int
    simulation_time_s: int
    event_type: str
    agent_id: str | None = None
    survivor_id: str | None = None
    status: str
    detail: dict[str, object] = Field(default_factory=dict)


class WorldState(BaseModel):
    run_id: str
    scenario_id: str
    seed: int
    region_name: str
    center: Coordinate
    nodes: dict[str, RoadNode]
    roads: dict[str, RoadSegment]
    survivors: dict[str, Survivor]
    hazards: dict[str, Hazard]
    agents: dict[str, AgentState]
    step: int = 0
    simulation_time_s: int = 0
    running: bool = False
    stopped_reason: str | None = None
    events: list[SimulationEvent] = Field(default_factory=list)
