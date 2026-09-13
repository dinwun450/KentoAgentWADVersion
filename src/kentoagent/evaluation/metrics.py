from __future__ import annotations

from statistics import mean

from pydantic import BaseModel

from kentoagent.simulation.entities import Severity, SurvivorStatus, WorldState


class SystemMetrics(BaseModel):
    rescue_rate: float
    critical_survivor_rescue_rate: float
    mean_rescue_time_s: float | None
    duplicated_assignments: int
    invalid_actions: int
    replans: int
    correction_rate: float
    correction_success_rate: float
    unresolved_survivors: int
    number_of_steps: int


def calculate_metrics(world: WorldState) -> SystemMetrics:
    survivors = list(world.survivors.values())
    rescued = [item for item in survivors if item.status == SurvivorStatus.RESCUED]
    critical = [item for item in survivors if item.severity == Severity.CRITICAL]
    rescued_critical = [item for item in critical if item.status == SurvivorStatus.RESCUED]
    invalid = [event for event in world.events if event.event_type == "action_rejected"]
    replans = [event for event in world.events if event.event_type == "replan_requested"]
    successful = [event for event in world.events if event.event_type == "correction_succeeded"]
    duplicated = [event for event in world.events if event.event_type == "duplicate_assignment"]
    rescue_times = [item.rescue_step for item in rescued if item.rescue_step is not None]
    return SystemMetrics(
        rescue_rate=len(rescued) / len(survivors) if survivors else 1,
        critical_survivor_rescue_rate=(len(rescued_critical) / len(critical) if critical else 1),
        mean_rescue_time_s=(mean(rescue_times) * 15 if rescue_times else None),
        duplicated_assignments=len(duplicated),
        invalid_actions=len(invalid),
        replans=len(replans),
        correction_rate=len(replans) / max(1, world.step),
        correction_success_rate=len(successful) / len(replans) if replans else 1,
        unresolved_survivors=len(survivors) - len(rescued),
        number_of_steps=world.step,
    )
