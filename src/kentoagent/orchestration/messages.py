from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SurvivorDetected(BaseModel):
    type: Literal["survivor_detected"] = "survivor_detected"
    survivor_id: str
    confidence: float = Field(ge=0, le=1)
    detected_by: str


class RouteBlocked(BaseModel):
    type: Literal["route_blocked"] = "route_blocked"
    road_id: str
    agent_id: str
    reason: str


class TaskAssignment(BaseModel):
    type: Literal["task_assignment"] = "task_assignment"
    agent_id: str
    survivor_id: str
    priority_score: float


class PriorityUpdate(BaseModel):
    type: Literal["priority_update"] = "priority_update"
    survivor_id: str
    score: float
    factors: dict[str, float]


class RescuePlan(BaseModel):
    type: Literal["rescue_plan"] = "rescue_plan"
    agent_id: str
    survivor_id: str
    node_path: list[str]
    road_ids: list[str]
    estimated_distance_m: float


class ReplanRequested(BaseModel):
    type: Literal["replan_requested"] = "replan_requested"
    agent_id: str
    survivor_id: str
    reason: str
    attempt: int


class ActionRejected(BaseModel):
    type: Literal["action_rejected"] = "action_rejected"
    agent_id: str
    action: str
    reasons: list[str]


class RescueCompleted(BaseModel):
    type: Literal["rescue_completed"] = "rescue_completed"
    agent_id: str
    survivor_id: str
    step: int
