from __future__ import annotations

from pydantic import BaseModel

from kentoagent.config import SimulationConfig
from kentoagent.evaluation.metrics import SystemMetrics, calculate_metrics
from kentoagent.orchestration.orchestrator import Orchestrator
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


class EvaluationResult(BaseModel):
    seed: int
    run_id: str
    metrics: SystemMetrics


def evaluate_seed(seed: int, *, max_steps: int = 100) -> EvaluationResult:
    world = generate_scenario(ScenarioConfig(seed=seed))
    orchestrator = Orchestrator(world, SimulationConfig(max_steps=max_steps))
    orchestrator.run()
    return EvaluationResult(seed=seed, run_id=world.run_id, metrics=calculate_metrics(world))


def evaluate_many(runs: int, seed: int, *, max_steps: int = 100) -> list[EvaluationResult]:
    return [evaluate_seed(seed + offset, max_steps=max_steps) for offset in range(runs)]
