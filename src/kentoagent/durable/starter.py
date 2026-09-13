from __future__ import annotations

import argparse
import asyncio
import hashlib
import json

from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from kentoagent.config import SimulationConfig
from kentoagent.durable.client import connect_temporal, task_queue
from kentoagent.durable.models import DurableSimulationInput, StepEvaluationSettings
from kentoagent.durable.workflow import KentoAgentParallelWorkflow
from kentoagent.simulation.scenario import ScenarioConfig


async def start(
    seed: int,
    model: str,
    provider: str,
    wait: bool,
    max_steps: int,
    step_interval_s: float,
) -> None:
    client = await connect_temporal()
    business_payload = {
        "seed": seed,
        "model": model,
        "provider": provider,
        "max_steps": max_steps,
        "step_interval_s": step_interval_s,
    }
    business_id = (
        "cli-"
        + hashlib.sha256(
            json.dumps(business_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    workflow_id = f"kentoagent-v2-{business_id[4:36]}"
    handle = await client.start_workflow(
        KentoAgentParallelWorkflow.run,
        DurableSimulationInput(
            business_id=business_id,
            workflow_id=workflow_id,
            scenario=ScenarioConfig(seed=seed),
            simulation=SimulationConfig(max_steps=max_steps),
            model=model,
            provider=provider,  # type: ignore[arg-type]
            step_interval_s=step_interval_s,
            evaluation=StepEvaluationSettings.from_env(),
        ).model_dump(mode="json"),
        id=workflow_id,
        task_queue=task_queue(),
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )
    print(json.dumps({"workflow_id": workflow_id, "run_id": handle.first_execution_run_id}))
    if wait:
        result = await handle.result()
        print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Start a durable KentoAgent workflow")
    parser.add_argument("--seed", type=int, default=49281)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--provider", choices=("openai", "mock"), default="openai")
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--step-interval", type=float, default=1.0)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        start(
            args.seed,
            args.model,
            args.provider,
            args.wait,
            args.max_steps,
            args.step_interval,
        )
    )


if __name__ == "__main__":
    main()
