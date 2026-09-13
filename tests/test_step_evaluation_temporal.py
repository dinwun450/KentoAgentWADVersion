from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from kentoagent.config import SimulationConfig
from kentoagent.durable.activities import (
    _mock_decision,
    _normalise_decision,
    apply_round_payload,
    initialize_simulation_payload,
    invoke_agent_payload,
    stop_simulation_payload,
    validate_round_payload,
)
from kentoagent.durable.models import (
    AgentTurnRequest,
    DurableSimulationInput,
    StepEvaluationSettings,
)
from kentoagent.durable.workflow import KentoAgentParallelWorkflow
from kentoagent.evaluation.step_publisher import (
    EvaluationLedger,
    publish_workflow_evaluations_activity,
    store_step_evaluation_activity,
)
from kentoagent.simulation.scenario import ScenarioConfig


@activity.defn(name="kentoagent.v2.invoke_agent")
async def flaky_invoke_agent(payload: dict[str, Any]) -> dict[str, Any]:
    request = AgentTurnRequest.model_validate(payload)
    if request.role.value == "locate" and activity.info().attempt == 1:
        raise ApplicationError("synthetic infrastructure failure", type="SyntheticRetry")
    decision = _normalise_decision(_mock_decision(request), request).model_dump(mode="json")
    decision["_evaluation"] = {
        "temporal_activity_attempt": activity.info().attempt,
        "schema_repair_attempts": 0,
        "agent_trace_id": None,
    }
    return decision


@activity.defn(name="kentoagent.v2.validate_round")
def reject_first_validation(payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_round_payload(payload)
    planner_decisions = [
        decision for decision in payload["decisions"] if decision["role"] == "planner"
    ]
    planner = planner_decisions[-1]
    has_feedback = any(
        "synthetic feedback" in observation for observation in planner.get("observations", [])
    )
    if not has_feedback:
        proposal = planner["proposals"][0]
        proposal_id = str(proposal["proposal_id"])
        validation["accepted_proposal_ids"] = [
            item for item in validation["accepted_proposal_ids"] if item != proposal_id
        ]
        validation["rejections"] = [
            {
                "proposal_id": proposal_id,
                "idempotency_key": "synthetic-plan",
                "reasons": ["synthetic feedback"],
            }
        ]
        validation["results"] = [
            {
                "proposal_id": proposal_id,
                "status": "rejected",
                "reason_code": "synthetic_feedback",
                "responsible_role": "planner",
                "feedback_id": "feedback-synthetic",
                "retryable": True,
            }
        ]
    else:
        proposal_ids = [
            str(proposal["proposal_id"])
            for decision in payload["decisions"]
            for proposal in decision["proposals"]
        ]
        validation["accepted_proposal_ids"] = proposal_ids
        validation["rejections"] = []
        validation["results"] = [
            {
                "proposal_id": proposal_id,
                "status": "accepted",
                "responsible_role": "planner",
                "retryable": False,
            }
            for proposal_id in proposal_ids
        ]
    return validation


def test_temporal_commit_creates_one_row_and_replays_without_weave_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_path = tmp_path / "temporal-evals.sqlite3"
    monkeypatch.setenv("KENTO_STEP_EVAL_LEDGER_PATH", str(ledger_path))

    async def run() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                async with Worker(
                    environment.client,
                    task_queue="step-eval-test",
                    workflows=[KentoAgentParallelWorkflow],
                    activities=[
                        initialize_simulation_payload,
                        invoke_agent_payload,
                        validate_round_payload,
                        apply_round_payload,
                        stop_simulation_payload,
                        store_step_evaluation_activity,
                        publish_workflow_evaluations_activity,
                    ],
                    activity_executor=executor,
                ):
                    handle = await environment.client.start_workflow(
                        KentoAgentParallelWorkflow.run,
                        DurableSimulationInput(
                            business_id="temporal-eval-business",
                            workflow_id="temporal-eval-workflow",
                            scenario=ScenarioConfig(seed=23),
                            simulation=SimulationConfig(max_steps=1),
                            provider="mock",
                            step_interval_s=0,
                            continue_as_new_every=0,
                            evaluation=StepEvaluationSettings(enabled=True),
                        ).model_dump(mode="json"),
                        id="temporal-eval-workflow",
                        task_queue="step-eval-test",
                    )
                    result = await handle.result()
                    assert result["step"] == 1
                    history = await handle.fetch_history()
            replay = await Replayer(workflows=[KentoAgentParallelWorkflow]).replay_workflow(history)
            assert replay.replay_failure is None
        finally:
            await environment.shutdown()

    asyncio.run(run())

    ledger = EvaluationLedger(ledger_path)
    assert ledger.counts("temporal-eval-workflow") == (1, 0)
    pending = ledger.pending("temporal-eval-workflow")
    assert len(pending) == 1
    assert pending[0].logical_step == 1
    assert pending[0].orchestration_round_count == 1


def test_temporal_activity_retry_and_validation_retry_remain_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_path = tmp_path / "retry-evals.sqlite3"
    monkeypatch.setenv("KENTO_STEP_EVAL_LEDGER_PATH", str(ledger_path))

    async def run() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                async with Worker(
                    environment.client,
                    task_queue="step-eval-retry-test",
                    workflows=[KentoAgentParallelWorkflow],
                    activities=[
                        initialize_simulation_payload,
                        flaky_invoke_agent,
                        reject_first_validation,
                        apply_round_payload,
                        stop_simulation_payload,
                        store_step_evaluation_activity,
                        publish_workflow_evaluations_activity,
                    ],
                    activity_executor=executor,
                ):
                    await environment.client.execute_workflow(
                        KentoAgentParallelWorkflow.run,
                        DurableSimulationInput(
                            business_id="retry-business",
                            workflow_id="retry-workflow",
                            scenario=ScenarioConfig(seed=29),
                            simulation=SimulationConfig(max_steps=1),
                            provider="mock",
                            step_interval_s=0,
                            continue_as_new_every=0,
                            evaluation=StepEvaluationSettings(enabled=True),
                        ).model_dump(mode="json"),
                        id="retry-workflow",
                        task_queue="step-eval-retry-test",
                    )
        finally:
            await environment.shutdown()

    asyncio.run(run())

    record = EvaluationLedger(ledger_path).pending("retry-workflow")[0]
    scores = record.deterministic_scores
    assert scores is not None
    assert scores.orchestration_round_count == 2
    assert scores.temporal_activity_retry_count == 1
    assert scores.selective_retry_rate == 1


def test_continue_as_new_preserves_first_run_and_distinguishes_current_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_path = tmp_path / "continued-evals.sqlite3"
    monkeypatch.setenv("KENTO_STEP_EVAL_LEDGER_PATH", str(ledger_path))

    async def run() -> None:
        environment = await WorkflowEnvironment.start_time_skipping()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                async with Worker(
                    environment.client,
                    task_queue="step-eval-continue-test",
                    workflows=[KentoAgentParallelWorkflow],
                    activities=[
                        initialize_simulation_payload,
                        invoke_agent_payload,
                        validate_round_payload,
                        apply_round_payload,
                        stop_simulation_payload,
                        store_step_evaluation_activity,
                        publish_workflow_evaluations_activity,
                    ],
                    activity_executor=executor,
                ):
                    result = await environment.client.execute_workflow(
                        KentoAgentParallelWorkflow.run,
                        DurableSimulationInput(
                            business_id="continued-business",
                            workflow_id="continued-workflow",
                            scenario=ScenarioConfig(seed=31),
                            simulation=SimulationConfig(max_steps=2),
                            provider="mock",
                            step_interval_s=0,
                            continue_as_new_every=1,
                            evaluation=StepEvaluationSettings(enabled=True),
                        ).model_dump(mode="json"),
                        id="continued-workflow",
                        task_queue="step-eval-continue-test",
                    )
                    assert result["step"] == 2
        finally:
            await environment.shutdown()

    asyncio.run(run())

    records = EvaluationLedger(ledger_path).pending("continued-workflow")
    assert len(records) == 2
    assert len({record.temporal_workflow_run_id for record in records}) == 2
    assert len({record.first_execution_run_id for record in records}) == 1
