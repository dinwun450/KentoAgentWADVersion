from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import RetryState

with workflow.unsafe.imports_passed_through():
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
    from kentoagent.durable.models import (
        AgentTurnDecision,
        AgentTurnRequest,
        ApplyRoundRequest,
        DurableSimulationInput,
        DurableSimulationStatus,
        ValidateRoundRequest,
    )
    from kentoagent.evaluation.step_eval import build_step_eval_record
    from kentoagent.evaluation.step_publisher import (
        publish_workflow_evaluations_activity,
        store_step_evaluation_activity,
    )
    from kentoagent.simulation.entities import AgentRole, WorldState

AGENT_SEQUENCE = (
    AgentRole.LOCATE,
    AgentRole.PRIORITY,
    AgentRole.COORDINATE,
    AgentRole.PLANNER,
)
PARALLEL_AGENT_ROLES = (*AGENT_SEQUENCE, AgentRole.CONTROL)

LLM_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
    non_retryable_error_types=["ConfigurationError"],
)
CORE_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


@workflow.defn
class KentoAgentWorkflow:
    """Durable multi-agent loop; all non-deterministic work runs in Activities."""

    def __init__(self) -> None:
        self._paused = False
        self._stop_requested = False
        self._phase = "created"
        self._current_role: AgentRole | None = None
        self._correction_attempt = 0
        self._world: WorldState | None = None

    @workflow.signal
    def pause(self) -> None:
        self._paused = True
        self._phase = "paused"

    @workflow.signal
    def resume(self) -> None:
        self._paused = False
        self._phase = "running"

    @workflow.signal
    def stop(self) -> None:
        self._stop_requested = True
        self._paused = False
        self._phase = "stopping"

    @workflow.query
    def status(self) -> DurableSimulationStatus:
        return DurableSimulationStatus(
            phase=self._phase,
            paused=self._paused,
            stop_requested=self._stop_requested,
            current_role=self._current_role,
            correction_attempt=self._correction_attempt,
            world=self._world,
        )

    async def _agent_turn(
        self,
        input: DurableSimulationInput,
        role: AgentRole,
        decisions: list[AgentTurnDecision],
        feedback: list[str] | None = None,
    ) -> AgentTurnDecision:
        if self._world is None:
            raise RuntimeError("workflow world is not initialized")
        self._current_role = role
        self._phase = f"agent:{role.value}"
        return await workflow.execute_activity(
            invoke_agent,
            AgentTurnRequest(
                world=self._world,
                role=role,
                prior_decisions=decisions,
                validation_feedback=feedback or [],
                model=input.model,
                provider=input.provider,
            ),
            start_to_close_timeout=timedelta(seconds=90),
            schedule_to_close_timeout=timedelta(minutes=5),
            retry_policy=LLM_RETRY_POLICY,
        )

    @workflow.run
    async def run(self, input: DurableSimulationInput) -> WorldState:
        self._phase = "initializing"
        self._world = await workflow.execute_activity(
            initialize_simulation,
            input,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=CORE_RETRY_POLICY,
        )

        while not self._world.stopped_reason and not self._stop_requested:
            await workflow.wait_condition(lambda: not self._paused or self._stop_requested)
            if self._stop_requested:
                break

            decisions: list[AgentTurnDecision] = []
            for role in AGENT_SEQUENCE:
                decisions.append(await self._agent_turn(input, role, decisions))

            self._phase = "validating"
            validation = await workflow.execute_activity(
                validate_round_activity,
                ValidateRoundRequest(world=self._world, decisions=decisions),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=CORE_RETRY_POLICY,
            )

            self._correction_attempt = 0
            while (
                validation.rejections
                and self._correction_attempt < input.simulation.max_correction_attempts
            ):
                self._correction_attempt += 1
                feedback = [
                    f"{item.idempotency_key}: {'; '.join(item.reasons)}"
                    for item in validation.rejections
                ]
                control = await self._agent_turn(
                    input,
                    AgentRole.CONTROL,
                    decisions,
                    feedback,
                )
                base_decisions = [
                    item
                    for item in decisions
                    if item.role not in (AgentRole.PLANNER, AgentRole.CONTROL)
                ]
                replanned = await self._agent_turn(
                    input,
                    AgentRole.PLANNER,
                    [*base_decisions, control],
                    feedback,
                )
                decisions = [*base_decisions, replanned]
                validation = await workflow.execute_activity(
                    validate_round_activity,
                    ValidateRoundRequest(world=self._world, decisions=decisions),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=CORE_RETRY_POLICY,
                )

            final_feedback = [
                f"{item.idempotency_key}: {'; '.join(item.reasons)}"
                for item in validation.rejections
            ]
            decisions.append(
                await self._agent_turn(
                    input,
                    AgentRole.CONTROL,
                    decisions,
                    final_feedback,
                )
            )

            self._phase = "committing"
            if int(validation.get("state_version", -1)) != self._state_version:
                self._status = "failed"
                self._termination_reason = "stale_validation_result"
                break
            self._world = await workflow.execute_activity(
                apply_round_activity,
                ApplyRoundRequest(
                    world=self._world,
                    decisions=decisions,
                    validation=validation,
                    simulation_config=input.simulation,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=CORE_RETRY_POLICY,
            )
            self._current_role = None
            self._correction_attempt = 0

            if (
                input.continue_as_new_every
                and self._world.step % input.continue_as_new_every == 0
                and not self._world.stopped_reason
            ):
                workflow.continue_as_new(args=[input.model_copy(update={"world": self._world})])
            if input.step_interval_s and not self._world.stopped_reason:
                self._phase = "waiting"
                await workflow.sleep(timedelta(seconds=input.step_interval_s))

        if self._stop_requested and self._world and not self._world.stopped_reason:
            self._world = await workflow.execute_activity(
                stop_simulation,
                self._world,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=CORE_RETRY_POLICY,
            )
        self._phase = "completed"
        self._current_role = None
        return self._world


@workflow.defn(name="KentoAgentParallelWorkflow")
class KentoAgentParallelWorkflow:
    """V2 durable loop: five independent LLM Activities fan out, validate, and commit."""

    def __init__(self) -> None:
        self._paused = False
        self._stop_requested = False
        self._phase = "created"
        self._active_roles: list[str] = []
        self._correction_attempt = 0
        self._world: dict[str, Any] | None = None
        self._workflow_id = ""
        self._business_id = ""
        self._state_version = 1
        self._orchestration_round = 0
        self._status = "running"
        self._termination_reason: str | None = None
        self._processed_command_ids: list[str] = []
        self._last_retry_reason: str | None = None
        self._failed_roles: list[str] = []
        self._request_fingerprint = ""
        self._temporal_run_id = ""
        self._first_execution_run_id = ""
        self._step_rounds: list[dict[str, Any]] = []
        self._step_stale_results = 0
        self._evaluation_status = "disabled"

    def _accept_command(self, command_id: str, expected_state_version: int | None) -> bool:
        if command_id in self._processed_command_ids:
            return False
        if expected_state_version is not None and expected_state_version != self._state_version:
            return False
        self._processed_command_ids.append(command_id)
        return True

    @workflow.signal
    def pause(self, command_id: str = "", expected_state_version: int | None = None) -> None:
        if not self._accept_command(command_id or "pause", expected_state_version):
            return
        self._paused = True
        self._phase = "paused"

    @workflow.signal
    def resume(self, command_id: str = "", expected_state_version: int | None = None) -> None:
        if not self._accept_command(command_id or "resume", expected_state_version):
            return
        self._paused = False
        self._phase = "running"

    @workflow.signal
    def stop(self, command_id: str = "", expected_state_version: int | None = None) -> None:
        if not self._accept_command(command_id or "stop", expected_state_version):
            return
        self._stop_requested = True
        self._paused = False
        self._phase = "stopping"

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "workflow_id": self._workflow_id,
            "business_id": self._business_id,
            "request_fingerprint": self._request_fingerprint,
            "state_version": self._state_version,
            "orchestration_round": self._orchestration_round,
            "application_step": self._phase,
            "phase": self._phase,
            "paused": self._paused,
            "stop_requested": self._stop_requested,
            "current_role": None,
            "active_roles": self._active_roles,
            "correction_attempt": self._correction_attempt,
            "world": self._world,
            "status": self._status,
            "termination_reason": self._termination_reason,
            "processed_command_ids": self._processed_command_ids,
            "evaluation_status": self._evaluation_status,
        }

    async def _parallel_agent_turns(
        self,
        input: dict[str, Any],
        feedback: list[str] | None = None,
        roles: tuple[AgentRole, ...] = PARALLEL_AGENT_ROLES,
        *,
        round_index: int = 0,
        batch_index: int = 0,
    ) -> list[dict[str, Any]]:
        if self._world is None:
            raise RuntimeError("workflow world is not initialized")
        self._phase = "agents:parallel"
        self._active_roles = [role.value for role in roles]
        model = str(input.get("model", "gpt-4.1-mini"))
        provider = str(input.get("provider", "openai"))
        logical_step = int(self._world.get("step", 0)) + 1
        round_id = f"{self._workflow_id}:step:{logical_step}:round:{round_index}"
        round_record = next(
            (item for item in self._step_rounds if item["round_id"] == round_id),
            None,
        )
        if round_record is None:
            round_record = {
                "round_id": round_id,
                "round_index": round_index,
                "roles_invoked": [],
                "validation_id": None,
                "invocations": [],
            }
            self._step_rounds.append(round_record)
        for role in roles:
            if role.value not in round_record["roles_invoked"]:
                round_record["roles_invoked"].append(role.value)
        requests = [
            {
                "world": self._world,
                "role": role.value,
                "prior_decisions": [],
                "validation_feedback": feedback or [],
                "model": model,
                "provider": provider,
                "workflow_id": self._workflow_id,
                "business_id": self._business_id,
                "state_version": self._state_version,
                "orchestration_round": self._orchestration_round,
            }
            for role in roles
        ]
        tasks = [
            workflow.execute_activity(
                invoke_agent_payload,
                request,
                start_to_close_timeout=timedelta(seconds=90),
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=LLM_RETRY_POLICY,
            )
            for role, request in zip(roles, requests, strict=True)
        ]
        try:
            # Temporal's workflow-aware event loop records these five Activity
            # schedules durably while allowing all LLM calls to run concurrently.
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            decisions: list[dict[str, Any]] = []
            failed_roles: list[str] = []
            for role, outcome in zip(roles, outcomes, strict=True):
                invocation_id = f"{round_id}:role:{role.value}:batch:{batch_index}"
                if isinstance(outcome, BaseException):
                    failed_roles.append(role.value)
                    retry_state = getattr(outcome, "retry_state", None)
                    exhausted = retry_state in {
                        RetryState.MAXIMUM_ATTEMPTS_REACHED,
                        RetryState.TIMEOUT,
                    }
                    round_record["invocations"].append(
                        {
                            "invocation_id": invocation_id,
                            "round_id": round_id,
                            "role": role.value,
                            "status": "failed",
                            "temporal_activity_attempt": (
                                LLM_RETRY_POLICY.maximum_attempts if exhausted else 1
                            ),
                            "schema_repair_attempts": 0,
                            "proposal_ids": [],
                            "proposal_kinds": [],
                            "summary": type(outcome).__name__,
                        }
                    )
                    continue
                evaluation = dict(outcome.pop("_evaluation", {}))
                status = "completed"
                if int(outcome.get("state_version", -1)) != self._state_version:
                    status = "stale"
                    self._step_stale_results += 1
                proposal_ids = [
                    str(proposal.get("proposal_id"))
                    for proposal in outcome.get("proposals", [])
                    if proposal.get("proposal_id")
                ]
                proposal_kinds = [
                    str(proposal.get("kind")) for proposal in outcome.get("proposals", [])
                ]
                handoff_to = outcome.get("handoff_to")
                round_record["invocations"].append(
                    {
                        "invocation_id": invocation_id,
                        "round_id": round_id,
                        "role": role.value,
                        "status": status,
                        "temporal_activity_attempt": int(
                            evaluation.get("temporal_activity_attempt", 1)
                        ),
                        "schema_repair_attempts": int(evaluation.get("schema_repair_attempts", 0)),
                        "proposal_ids": proposal_ids,
                        "proposal_kinds": proposal_kinds,
                        "summary": str(outcome.get("summary", ""))[:500],
                        "handoff_id": (
                            f"{invocation_id}:handoff:{handoff_to}" if handoff_to else None
                        ),
                        "handoff_to": handoff_to,
                        "agent_trace_id": evaluation.get("agent_trace_id"),
                    }
                )
                if status == "stale":
                    continue
                decisions.append(outcome)
            if failed_roles:
                self._last_retry_reason = f"activity_failed:{','.join(failed_roles)}"
            self._failed_roles = failed_roles
            return decisions
        finally:
            self._active_roles = []

    @workflow.run
    async def run(self, input: dict[str, Any]) -> dict[str, Any]:
        info = workflow.info()
        self._workflow_id = str(input.get("workflow_id", info.workflow_id))
        self._temporal_run_id = info.run_id
        self._first_execution_run_id = info.first_execution_run_id
        self._business_id = str(input.get("business_id", ""))
        self._request_fingerprint = str(input.get("request_fingerprint", ""))
        self._state_version = int(input.get("state_version", 1))
        self._orchestration_round = int(input.get("orchestration_round", 0))
        self._processed_command_ids = list(input.get("processed_command_ids", []))
        self._status = "running"
        evaluation_settings = dict(input.get("evaluation", {}))
        evaluation_enabled = bool(evaluation_settings.get("enabled", False))
        self._evaluation_status = "pending" if evaluation_enabled else "disabled"
        self._phase = "initializing"
        self._world = await workflow.execute_activity(
            initialize_simulation_payload,
            input,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=CORE_RETRY_POLICY,
        )
        simulation = dict(input.get("simulation", {}))
        max_corrections = int(simulation.get("max_correction_attempts", 2))

        while not self._world.get("stopped_reason") and not self._stop_requested:
            await workflow.wait_condition(lambda: not self._paused or self._stop_requested)
            if self._stop_requested:
                break

            step_started = workflow.now()
            world_before = self._world
            event_count_before = len(self._world.get("events", []))
            state_version_before = self._state_version
            self._step_rounds = []
            self._step_stale_results = 0
            validations: list[dict[str, Any]] = []

            decisions = await self._parallel_agent_turns(input, round_index=0, batch_index=0)
            if self._stop_requested:
                break
            if self._failed_roles:
                failed_roles = tuple(
                    role for role in PARALLEL_AGENT_ROLES if role.value in self._failed_roles
                )
                retry_decisions = await self._parallel_agent_turns(
                    input,
                    roles=failed_roles,
                    round_index=0,
                    batch_index=1,
                )
                decisions = [
                    decision
                    for decision in decisions
                    if decision.get("role") not in {role.value for role in failed_roles}
                ]
                decisions.extend(retry_decisions)
            required_roles = {role.value for role in PARALLEL_AGENT_ROLES}
            if {str(decision.get("role")) for decision in decisions} != required_roles:
                self._status = "failed"
                self._termination_reason = self._last_retry_reason or "incomplete agent round"
                break
            if not decisions:
                self._status = "failed"
                self._termination_reason = self._last_retry_reason or "all agent Activities failed"
                break
            self._phase = "validating"
            try:
                validation = await workflow.execute_activity(
                    validate_round_payload,
                    {
                        "world": self._world,
                        "decisions": decisions,
                        "workflow_id": self._workflow_id,
                        "business_id": self._business_id,
                        "state_version": self._state_version,
                        "orchestration_round": self._orchestration_round,
                    },
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=CORE_RETRY_POLICY,
                )
            except Exception as exc:
                self._status = "failed"
                self._termination_reason = f"validator_failed:{type(exc).__name__}"
                break
            validations.append(validation)
            self._step_rounds[-1]["validation_id"] = (
                f"{self._workflow_id}:step:{int(self._world.get('step', 0)) + 1}:validation:0"
            )

            self._correction_attempt = 0
            while validation.get("rejections") and self._correction_attempt < max_corrections:
                self._correction_attempt += 1
                feedback = [
                    f"{item['idempotency_key']}: {'; '.join(item['reasons'])}"
                    for item in validation["rejections"]
                ]
                affected_roles = tuple(
                    role
                    for role in PARALLEL_AGENT_ROLES
                    if any(
                        result.get("responsible_role") == role.value and result.get("retryable")
                        for result in validation.get("results", [])
                    )
                )
                retry_decisions = await self._parallel_agent_turns(
                    input,
                    feedback,
                    roles=affected_roles or PARALLEL_AGENT_ROLES,
                    round_index=self._correction_attempt,
                    batch_index=0,
                )
                retry_roles = affected_roles or PARALLEL_AGENT_ROLES
                retry_role_names = {role.value for role in retry_roles}
                rejected_ids = {
                    str(result.get("proposal_id"))
                    for result in validation.get("results", [])
                    if result.get("status") == "rejected" and result.get("proposal_id")
                }
                retained_decisions: list[dict[str, Any]] = []
                for decision in decisions:
                    if decision.get("role") not in retry_role_names:
                        retained_decisions.append(decision)
                        continue
                    retained_proposals = [
                        proposal
                        for proposal in decision.get("proposals", [])
                        if proposal.get("proposal_id") not in rejected_ids
                    ]
                    if not rejected_ids:
                        retained_proposals = []
                    if retained_proposals:
                        retained_decisions.append({**decision, "proposals": retained_proposals})
                decisions = retained_decisions
                decisions.extend(retry_decisions)
                decisions.sort(
                    key=lambda decision: PARALLEL_AGENT_ROLES.index(
                        AgentRole(str(decision["role"]))
                    )
                )
                self._phase = "validating"
                try:
                    validation = await workflow.execute_activity(
                        validate_round_payload,
                        {
                            "world": self._world,
                            "decisions": decisions,
                            "workflow_id": self._workflow_id,
                            "business_id": self._business_id,
                            "state_version": self._state_version,
                            "orchestration_round": self._orchestration_round,
                        },
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=CORE_RETRY_POLICY,
                    )
                except Exception as exc:
                    self._status = "failed"
                    self._termination_reason = f"validator_failed:{type(exc).__name__}"
                    break
                validations.append(validation)
                self._step_rounds[-1]["validation_id"] = (
                    f"{self._workflow_id}:step:{int(self._world.get('step', 0)) + 1}:"
                    f"validation:{self._correction_attempt}"
                )

            if self._status == "failed":
                break
            self._phase = "committing"
            validation_version = int(validation.get("state_version", self._state_version))
            validation_round = int(validation.get("orchestration_round", self._orchestration_round))
            if (
                validation_version != self._state_version
                or validation_round != self._orchestration_round
            ):
                self._status = "failed"
                self._termination_reason = "stale_validation_result"
                break
            self._world = await workflow.execute_activity(
                apply_round_payload,
                {
                    "world": self._world,
                    "decisions": decisions,
                    "validation": validation,
                    "simulation_config": simulation,
                    "workflow_id": self._workflow_id,
                    "business_id": self._business_id,
                    "state_version": self._state_version,
                    "orchestration_round": self._orchestration_round,
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=CORE_RETRY_POLICY,
            )
            self._state_version += 1
            self._orchestration_round += 1
            self._correction_attempt = 0

            if evaluation_enabled:
                step_ended = workflow.now()
                record = build_step_eval_record(
                    application_workflow_id=self._business_id,
                    temporal_workflow_id=self._workflow_id,
                    temporal_workflow_run_id=self._temporal_run_id,
                    first_execution_run_id=self._first_execution_run_id,
                    logical_step=int(self._world["step"]),
                    state_version_before=state_version_before,
                    state_version_after=self._state_version,
                    rounds=self._step_rounds,
                    validations=validations,
                    world_before=world_before,
                    world_after=self._world,
                    event_count_before=event_count_before,
                    stale_results_rejected=self._step_stale_results,
                    started_at=step_started.isoformat(),
                    ended_at=step_ended.isoformat(),
                    duration_s=(step_ended - step_started).total_seconds(),
                    deterministic_scoring_enabled=bool(
                        evaluation_settings.get("deterministic_scoring_enabled", True)
                    ),
                )
                try:
                    await workflow.execute_activity(
                        store_step_evaluation_activity,
                        record.model_dump(mode="json"),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=CORE_RETRY_POLICY,
                        activity_id=f"{record.eval_id}:store",
                    )
                    self._evaluation_status = "stored"
                except Exception:
                    self._evaluation_status = "store_unavailable"

            continue_every = int(input.get("continue_as_new_every", 25))
            if (
                continue_every
                and int(self._world["step"]) % continue_every == 0
                and not self._world.get("stopped_reason")
            ):
                workflow.continue_as_new(
                    args=[
                        {
                            **input,
                            "world": self._world,
                            "state_version": self._state_version,
                            "orchestration_round": self._orchestration_round,
                            "request_fingerprint": self._request_fingerprint,
                            "processed_command_ids": self._processed_command_ids,
                        }
                    ]
                )
            step_interval = float(input.get("step_interval_s", 1.0))
            if step_interval and not self._world.get("stopped_reason"):
                self._phase = "waiting"
                await workflow.sleep(timedelta(seconds=step_interval))

        if self._stop_requested and self._world and not self._world.get("stopped_reason"):
            self._world = await workflow.execute_activity(
                stop_simulation_payload,
                self._world,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=CORE_RETRY_POLICY,
                activity_id=f"{self._workflow_id}:apply:{self._orchestration_round}",
            )
        if self._status != "failed":
            self._phase = "completed"
            self._status = "cancelled" if self._stop_requested else "completed"
            self._termination_reason = (
                "operator stopped the simulation"
                if self._stop_requested
                else self._world.get("stopped_reason")
            )
        elif self._world is not None:
            self._world["running"] = False
            self._world["stopped_reason"] = self._termination_reason or "workflow failed"
        if evaluation_enabled:
            try:
                publication = await workflow.execute_activity(
                    publish_workflow_evaluations_activity,
                    {
                        "temporal_workflow_id": self._workflow_id,
                        "application_workflow_id": self._business_id,
                        "final_status": self._status,
                        "termination_reason": self._termination_reason,
                        "provider": input.get("provider"),
                        "model": input.get("model"),
                        "settings": evaluation_settings,
                    },
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=CORE_RETRY_POLICY,
                    activity_id=f"{self._workflow_id}:publish-step-evaluations",
                )
                self._evaluation_status = str(publication.get("status", "unknown"))
            except Exception:
                self._evaluation_status = "publication_unavailable"
        self._active_roles = []
        return self._world
