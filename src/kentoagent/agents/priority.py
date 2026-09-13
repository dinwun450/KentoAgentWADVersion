from __future__ import annotations

from kentoagent.agents.base import SpecialistAgent
from kentoagent.orchestration.messages import PriorityUpdate
from kentoagent.simulation.entities import Severity, SurvivorStatus, WorldState


class PriorityAgent(SpecialistAgent[PriorityUpdate]):
    role = "priority"
    severity_weight = {
        Severity.LOW: 20.0,
        Severity.MODERATE: 45.0,
        Severity.HIGH: 70.0,
        Severity.CRITICAL: 90.0,
    }
    status_weight = {
        SurvivorStatus.WAITING: 2.0,
        SurvivorStatus.ASSIGNED: 0.0,
    }

    def run(self, world: WorldState) -> list[PriorityUpdate]:
        updates: list[PriorityUpdate] = []
        for survivor in world.survivors.values():
            if survivor.status not in (SurvivorStatus.WAITING, SurvivorStatus.ASSIGNED):
                continue
            severity = self.severity_weight[survivor.severity]
            status = self.status_weight[survivor.status]
            trapped = 6.0 if survivor.trapped else 0.0
            local_hazard = max(
                (
                    hazard.intensity
                    for hazard in world.hazards.values()
                    if hazard.node_id == survivor.node_id
                ),
                default=0,
            )
            # Severity bands do not overlap, so route risk or trapped status can
            # refine urgency without allowing a lower-severity case to jump a
            # higher-severity one. Hazard exposure increases urgency here; the
            # router accounts for that risk separately when choosing a path.
            hazard_urgency = round(local_hazard * 2.0, 2)
            score = round(min(100.0, severity + status + trapped + hazard_urgency), 2)
            updates.append(
                PriorityUpdate(
                    survivor_id=survivor.id,
                    score=score,
                    factors={
                        "severity": severity,
                        "status": status,
                        "trapped": trapped,
                        "hazard": local_hazard,
                    },
                )
            )
        return sorted(updates, key=lambda item: (-item.score, item.survivor_id))
