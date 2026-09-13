from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from temporalio.worker import Worker

from kentoagent.durable.activities import (
    apply_round_activity,
    apply_round_payload,
    initialize_simulation,
    initialize_simulation_payload,
    invoke_agent,
    invoke_agent_payload,
    stop_simulation,
    stop_simulation_payload,
    validate_round_activity,
    validate_round_payload,
)
from kentoagent.durable.client import connect_temporal, task_queue
from kentoagent.durable.workflow import KentoAgentParallelWorkflow, KentoAgentWorkflow
from kentoagent.evaluation.step_publisher import (
    publish_workflow_evaluations_activity,
    store_step_evaluation_activity,
)


async def run_worker() -> None:
    client = await connect_temporal()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as activity_executor:
        worker = Worker(
            client,
            task_queue=task_queue(),
            workflows=[KentoAgentWorkflow, KentoAgentParallelWorkflow],
            activities=[
                initialize_simulation,
                invoke_agent,
                validate_round_activity,
                apply_round_activity,
                stop_simulation,
                initialize_simulation_payload,
                invoke_agent_payload,
                validate_round_payload,
                apply_round_payload,
                stop_simulation_payload,
                store_step_evaluation_activity,
                publish_workflow_evaluations_activity,
            ],
            activity_executor=activity_executor,
        )
        logging.getLogger(__name__).info("KentoAgent worker polling %s", task_queue())
        await worker.run()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
