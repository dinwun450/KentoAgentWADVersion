from __future__ import annotations

import hashlib
import json
from typing import Any

from kentoagent.durable.models import (
    AgentOutputScores,
    DeterministicStepScores,
    StepActionRecord,
    StepEvalRecord,
    StepFeedbackRecord,
    StepHandoffRecord,
    StepProposalRecord,
    StepRejectionRecord,
    StepRoundRecord,
)
from kentoagent.simulation.entities import AgentRole

VALID_STEP_STATUSES = {"committed", "completed", "no_actionable_work", "no_state_change"}


def compact_world_summary(world: dict[str, Any]) -> dict[str, Any]:
    survivors: dict[str, Any] = dict(world.get("survivors", {}))
    agents: dict[str, Any] = dict(world.get("agents", {}))
    survivor_statuses: dict[str, int] = {}
    for survivor in survivors.values():
        status = str(survivor.get("status", "unknown"))
        survivor_statuses[status] = survivor_statuses.get(status, 0) + 1
    agent_statuses: dict[str, int] = {}
    for agent in agents.values():
        status = str(agent.get("status", "unknown"))
        agent_statuses[status] = agent_statuses.get(status, 0) + 1
    return {
        "survivor_statuses": dict(sorted(survivor_statuses.items())),
        "agent_statuses": dict(sorted(agent_statuses.items())),
        "assigned_survivor_ids": sorted(
            survivor_id
            for survivor_id, survivor in survivors.items()
            if survivor.get("assigned_agent_id")
        ),
        "agent_nodes": {
            agent_id: agent.get("node_id") for agent_id, agent in sorted(agents.items())
        },
        "stopped_reason": world.get("stopped_reason"),
    }


def state_digest(summary: dict[str, Any]) -> str:
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _safe_rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def score_step(record: StepEvalRecord) -> DeterministicStepScores:
    invocations = [invocation for round_ in record.rounds for invocation in round_.invocations]
    initial_roles = set(record.rounds[0].roles_invoked) if record.rounds else set()
    retries = record.rounds[1:]
    selective = sum(bool(set(round_.roles_invoked) < initial_roles) for round_ in retries)
    proposal_ids = [proposal.proposal_id for proposal in record.proposals]
    duplicate_count = len(proposal_ids) - len(set(proposal_ids))
    completed_handoffs = sum(handoff.completed for handoff in record.handoffs)
    resolved_feedback = sum(feedback.resolved for feedback in record.feedback)
    event_ids = [action.event_id for action in record.actions_applied]
    duplicate_side_effect_count = len(event_ids) - len(set(event_ids))
    no_op_calls = sum(
        not invocation.proposal_ids or set(invocation.proposal_kinds) == {"noop"}
        for invocation in invocations
        if invocation.status == "completed"
    )
    accepted_ids = set(record.accepted_proposal_ids)
    rejected_ids = set(record.rejected_proposal_ids)
    agent_output_quality: dict[AgentRole, AgentOutputScores] = {}
    for role in AgentRole:
        role_calls = [invocation for invocation in invocations if invocation.role == role]
        if not role_calls:
            continue
        role_proposals = [
            proposal_id for invocation in role_calls for proposal_id in invocation.proposal_ids
        ]
        accepted = sum(proposal_id in accepted_ids for proposal_id in role_proposals)
        rejected = sum(proposal_id in rejected_ids for proposal_id in role_proposals)
        agent_output_quality[role] = AgentOutputScores(
            call_count=len(role_calls),
            proposal_count=len(role_proposals),
            accepted_proposal_count=accepted,
            rejected_proposal_count=rejected,
            valid_proposal_rate=_safe_rate(accepted, len(role_proposals)),
            no_op_call_count=sum(
                not invocation.proposal_ids or set(invocation.proposal_kinds) == {"noop"}
                for invocation in role_calls
                if invocation.status == "completed"
            ),
            temporal_activity_retry_count=sum(
                max(0, invocation.temporal_activity_attempt - 1) for invocation in role_calls
            ),
            schema_repair_attempt_count=sum(
                invocation.schema_repair_attempts for invocation in role_calls
            ),
            stale_result_count=sum(invocation.status == "stale" for invocation in role_calls),
        )
    return DeterministicStepScores(
        valid_actions=len(record.accepted_proposal_ids),
        state_changed=record.state_digest_before != record.state_digest_after,
        converged=record.final_step_status in VALID_STEP_STATUSES,
        orchestration_round_count=len(record.rounds),
        agent_call_count=len(invocations),
        selective_retry_rate=_safe_rate(selective, len(retries)),
        feedback_resolution_rate=_safe_rate(resolved_feedback, len(record.feedback)),
        handoff_completion_rate=_safe_rate(completed_handoffs, len(record.handoffs)),
        duplicate_proposal_rate=duplicate_count / max(1, len(proposal_ids)),
        stale_result_rejection_count=record.stale_results_rejected,
        conflict_count=record.conflicting_proposals_detected,
        no_op_agent_call_count=no_op_calls,
        temporal_activity_retry_count=sum(
            max(0, invocation.temporal_activity_attempt - 1) for invocation in invocations
        ),
        schema_repair_attempt_count=sum(
            invocation.schema_repair_attempts for invocation in invocations
        ),
        duplicate_side_effect_count=duplicate_side_effect_count,
        valid_step_status=record.final_step_status in VALID_STEP_STATUSES,
        step_latency_s=record.duration_s,
        agent_output_quality=agent_output_quality,
    )


def build_step_eval_record(
    *,
    application_workflow_id: str,
    temporal_workflow_id: str,
    temporal_workflow_run_id: str,
    first_execution_run_id: str,
    logical_step: int,
    state_version_before: int,
    state_version_after: int,
    rounds: list[dict[str, Any]],
    validations: list[dict[str, Any]],
    world_before: dict[str, Any],
    world_after: dict[str, Any],
    event_count_before: int,
    stale_results_rejected: int,
    started_at: str,
    ended_at: str,
    duration_s: float,
    deterministic_scoring_enabled: bool = True,
) -> StepEvalRecord:
    typed_rounds = [StepRoundRecord.model_validate(item) for item in rounds]
    proposal_records: list[StepProposalRecord] = []
    handoffs: list[StepHandoffRecord] = []
    invoked_by_round = [set(round_.roles_invoked) for round_ in typed_rounds]
    for round_position, round_ in enumerate(typed_rounds):
        for invocation in round_.invocations:
            for proposal_id, kind in zip(
                invocation.proposal_ids, invocation.proposal_kinds, strict=True
            ):
                proposal_records.append(
                    StepProposalRecord(
                        proposal_id=proposal_id,
                        originating_role=invocation.role,
                        kind=kind,
                    )
                )
            if invocation.handoff_id and invocation.handoff_to:
                completed = any(
                    invocation.handoff_to in role_set
                    for role_set in invoked_by_round[round_position:]
                )
                handoffs.append(
                    StepHandoffRecord(
                        handoff_id=invocation.handoff_id,
                        source_role=invocation.role,
                        target_role=invocation.handoff_to,
                        round_id=round_.round_id,
                        completed=completed,
                    )
                )

    final_validation = validations[-1] if validations else {}
    accepted_ids = [str(item) for item in final_validation.get("accepted_proposal_ids", [])]
    rejection_records: list[StepRejectionRecord] = []
    feedback_records: list[StepFeedbackRecord] = []
    final_rejected_ids = {
        str(item.get("proposal_id"))
        for item in final_validation.get("results", [])
        if item.get("status") == "rejected"
    }
    conflict_count = 0
    seen_feedback: set[str] = set()
    for validation in validations:
        results_by_id = {
            str(item.get("proposal_id")): item for item in validation.get("results", [])
        }
        for rejection in validation.get("rejections", []):
            proposal_id = str(rejection.get("proposal_id") or rejection.get("idempotency_key"))
            result = results_by_id.get(proposal_id, {})
            reasons = [str(reason) for reason in rejection.get("reasons", [])]
            reason_codes = [str(result.get("reason_code") or "domain_validation")]
            if any("conflicting proposal" in reason for reason in reasons):
                conflict_count += 1
            feedback_id = str(result.get("feedback_id") or f"feedback:{proposal_id}")
            role = result.get("responsible_role")
            rejection_records.append(
                StepRejectionRecord(
                    proposal_id=proposal_id,
                    reason_codes=reason_codes,
                    reasons=reasons,
                    responsible_role=AgentRole(str(role)) if role else None,
                    feedback_id=feedback_id,
                )
            )
            if feedback_id not in seen_feedback:
                seen_feedback.add(feedback_id)
                feedback_records.append(
                    StepFeedbackRecord(
                        feedback_id=feedback_id,
                        proposal_id=proposal_id,
                        responsible_role=AgentRole(str(role)) if role else None,
                        reasons=reasons,
                        resolved=proposal_id not in final_rejected_ids,
                    )
                )

    before_summary = compact_world_summary(world_before)
    after_summary = compact_world_summary(world_after)
    new_events = list(world_after.get("events", []))[event_count_before:]
    actions = [
        StepActionRecord(
            event_id=str(event.get("id", "")),
            event_type=str(event.get("event_type", "unknown")),
            status=str(event.get("status", "unknown")),
            agent_id=event.get("agent_id"),
            survivor_id=event.get("survivor_id"),
        )
        for event in new_events
        if event.get("event_type") != "action_rejected"
    ]
    final_status = "completed" if world_after.get("stopped_reason") else "committed"
    if not accepted_ids and not actions:
        final_status = "no_actionable_work"
    eval_id = (
        f"{temporal_workflow_id}:{temporal_workflow_run_id}:{logical_step}:{state_version_after}"
    )
    record = StepEvalRecord(
        eval_id=eval_id,
        application_workflow_id=application_workflow_id,
        temporal_workflow_id=temporal_workflow_id,
        temporal_workflow_run_id=temporal_workflow_run_id,
        first_execution_run_id=first_execution_run_id,
        logical_step=logical_step,
        state_version_before=state_version_before,
        state_version_after=state_version_after,
        orchestration_round_count=len(typed_rounds),
        rounds=typed_rounds,
        proposals=proposal_records,
        accepted_proposal_ids=accepted_ids,
        rejected_proposal_ids=sorted({item.proposal_id for item in rejection_records}),
        rejections=rejection_records,
        handoffs=handoffs,
        feedback=feedback_records,
        stale_results_rejected=stale_results_rejected,
        conflicting_proposals_detected=conflict_count,
        actions_applied=actions,
        state_summary_before=before_summary,
        state_summary_after=after_summary,
        state_digest_before=state_digest(before_summary),
        state_digest_after=state_digest(after_summary),
        started_at=started_at,
        ended_at=ended_at,
        duration_s=max(0.0, duration_s),
        final_step_status=final_status,
        termination_reason=world_after.get("stopped_reason"),
    )
    if deterministic_scoring_enabled:
        record.deterministic_scores = score_step(record)
    return record


def compact_eval_inputs(record: StepEvalRecord) -> dict[str, Any]:
    return record.model_dump(
        mode="json",
        exclude={
            "deterministic_scores",
            "state_summary_after",
            "state_digest_after",
            "final_step_status",
            "termination_reason",
            "actions_applied",
        },
    )


def compact_eval_output(record: StepEvalRecord) -> dict[str, Any]:
    return {
        "eval_id": record.eval_id,
        "state_summary_after": record.state_summary_after,
        "state_digest_after": record.state_digest_after,
        "actions_applied": [item.model_dump(mode="json") for item in record.actions_applied],
        "final_step_status": record.final_step_status,
        "termination_reason": record.termination_reason,
    }
