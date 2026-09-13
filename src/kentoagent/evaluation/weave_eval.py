from __future__ import annotations

from typing import Any

import weave

from kentoagent.evaluation.runner import evaluate_seed
from kentoagent.observability.tracing import WeaveSettings, initialize_weave


class SimulationEvaluationModel(weave.Model):
    """Replayable whole-system model used by Weave Evaluation."""

    max_steps: int = 100
    policy_version: str = "deterministic-v1"

    @weave.op(name="kentoagent.evaluate_seed", enable_code_capture=False)
    def predict(self, seed: int) -> dict[str, Any]:
        result = evaluate_seed(seed, max_steps=self.max_steps)
        return {
            "seed": seed,
            "run_id": result.run_id,
            "policy_version": self.policy_version,
            "max_steps": self.max_steps,
            "metrics": result.metrics.model_dump(mode="json"),
        }


@weave.op(name="kentoagent.mission_outcome_scorer", enable_code_capture=False)
def mission_outcome_scorer(output: dict[str, Any]) -> dict[str, float | bool]:
    metrics = output["metrics"]
    rescue_rate = float(metrics["rescue_rate"])
    critical_rate = float(metrics["critical_survivor_rescue_rate"])
    return {
        "rescue_rate": rescue_rate,
        "critical_rescue_rate": critical_rate,
        "all_reachable_resolved": int(metrics["unresolved_survivors"]) == 0,
    }


@weave.op(name="kentoagent.guardrail_scorer", enable_code_capture=False)
def guardrail_scorer(output: dict[str, Any]) -> dict[str, int | float | bool]:
    metrics = output["metrics"]
    invalid = int(metrics["invalid_actions"])
    duplicated = int(metrics["duplicated_assignments"])
    return {
        "invalid_actions": invalid,
        "duplicated_assignments": duplicated,
        "guardrails_held": invalid == 0 and duplicated == 0,
        "correction_success_rate": float(metrics["correction_success_rate"]),
    }


@weave.op(name="kentoagent.loop_efficiency_scorer", enable_code_capture=False)
def loop_efficiency_scorer(output: dict[str, Any]) -> dict[str, int | float | bool]:
    metrics = output["metrics"]
    steps = int(metrics["number_of_steps"])
    max_steps = int(output["max_steps"])
    return {
        "steps": steps,
        "replans": int(metrics["replans"]),
        "within_step_budget": steps <= max_steps,
        "step_budget_utilization": steps / max(1, max_steps),
    }


async def run_weave_evaluation(
    runs: int,
    seed: int,
    *,
    max_steps: int = 100,
    policy_version: str = "deterministic-v1",
    settings: WeaveSettings | None = None,
) -> dict[str, Any]:
    """Publish a seeded systemic evaluation and its scorer call tree to Weave."""

    resolved = settings or WeaveSettings.from_env()
    if not resolved.project:
        raise ValueError("Configure WANDB_PROJECT or WEAVE_PROJECT before publishing an evaluation")
    client = initialize_weave(
        WeaveSettings(
            mode="required",
            project=resolved.project,
            environment=resolved.environment,
        )
    )
    if client is None:  # pragma: no cover - required mode always returns or raises
        raise RuntimeError("Weave initialization did not return a client")
    dataset_rows: Any = [{"seed": seed + offset} for offset in range(runs)]
    dataset = weave.Dataset(
        name=f"kentoagent-seeds-{seed}-{runs}",
        rows=dataset_rows,
    )
    evaluation = weave.Evaluation(
        name=f"kentoagent-system-eval-{policy_version}",
        dataset=dataset,
        scorers=[mission_outcome_scorer, guardrail_scorer, loop_efficiency_scorer],
        metadata={
            "base_seed": seed,
            "runs": runs,
            "max_steps": max_steps,
            "policy_version": policy_version,
            "environment": resolved.environment,
        },
    )
    result: dict[str, Any] = await evaluation.evaluate(
        SimulationEvaluationModel(max_steps=max_steps, policy_version=policy_version)
    )
    return result
