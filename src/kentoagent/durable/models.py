from __future__ import annotations

import json
import os
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from kentoagent.config import SimulationConfig
from kentoagent.simulation.entities import AgentRole, WorldState
from kentoagent.simulation.scenario import ScenarioConfig

from dotenv import load_dotenv

ProposalKind = Literal["detect", "prioritize", "assign", "plan", "approve", "reject", "noop"]
LLMProvider = Literal["openai", "mock"]
TelemetryFailurePolicy = Literal["fail_open", "retry"]
EvaluationPublicationMode = Literal["terminal_outbox"]

load_dotenv()  # Load environment variables from .env file if present

class StepEvaluationSettings(BaseModel):
    """Versioned evaluation policy captured in Workflow input for deterministic replay."""

    enabled: bool = False
    deterministic_scoring_enabled: bool = True
    llm_judge_enabled: bool = False
    judge_model: str = "gpt-4.1"
    rubric_version: str = "orchestration-v1"
    prompt_version: str = "orchestration-v1"
    pass_threshold: float = Field(default=70.0, ge=0, le=100)
    publication_mode: EvaluationPublicationMode = "terminal_outbox"
    telemetry_failure_policy: TelemetryFailurePolicy = "fail_open"

    @classmethod
    def from_env(cls) -> StepEvaluationSettings:
        def enabled(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}

        policy = os.getenv("KENTO_STEP_EVAL_FAILURE_POLICY", "fail_open").lower()
        if policy not in {"fail_open", "retry"}:
            raise ValueError("KENTO_STEP_EVAL_FAILURE_POLICY must be fail_open or retry")
        publication_mode = os.getenv("KENTO_STEP_EVAL_PUBLICATION_MODE", "terminal_outbox").lower()
        if publication_mode != "terminal_outbox":
            raise ValueError("KENTO_STEP_EVAL_PUBLICATION_MODE must be terminal_outbox")
        return cls(
            enabled=enabled("KENTO_STEP_EVAL_ENABLED", False),
            deterministic_scoring_enabled=enabled("KENTO_STEP_EVAL_DETERMINISTIC", True),
            llm_judge_enabled=enabled("KENTO_STEP_EVAL_LLM_JUDGE", False),
            judge_model=os.getenv("KENTO_STEP_EVAL_JUDGE_MODEL", "gpt-4.1"),
            rubric_version=os.getenv("KENTO_STEP_EVAL_RUBRIC_VERSION", "orchestration-v1"),
            prompt_version=os.getenv("KENTO_STEP_EVAL_PROMPT_VERSION", "orchestration-v1"),
            pass_threshold=float(os.getenv("KENTO_STEP_EVAL_PASS_THRESHOLD", "70")),
            publication_mode=cast(EvaluationPublicationMode, publication_mode),
            telemetry_failure_policy=cast(TelemetryFailurePolicy, policy),
        )


class StepAgentInvocation(BaseModel):
    invocation_id: str
    round_id: str
    role: AgentRole
    status: Literal["completed", "failed", "stale"] = "completed"
    temporal_activity_attempt: int = Field(default=1, ge=1)
    schema_repair_attempts: int = Field(default=0, ge=0)
    proposal_ids: list[str] = Field(default_factory=list)
    proposal_kinds: list[ProposalKind] = Field(default_factory=list)
    summary: str = Field(default="", max_length=500)
    handoff_id: str | None = None
    handoff_to: AgentRole | None = None
    agent_trace_id: str | None = None


class StepRoundRecord(BaseModel):
    round_id: str
    round_index: int = Field(ge=0)
    roles_invoked: list[AgentRole] = Field(default_factory=list)
    validation_id: str | None = None
    invocations: list[StepAgentInvocation] = Field(default_factory=list)


class StepProposalRecord(BaseModel):
    proposal_id: str
    originating_role: AgentRole
    kind: ProposalKind


class StepRejectionRecord(BaseModel):
    proposal_id: str
    reason_codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    responsible_role: AgentRole | None = None
    feedback_id: str | None = None


class StepHandoffRecord(BaseModel):
    handoff_id: str
    source_role: AgentRole
    target_role: AgentRole
    round_id: str
    completed: bool


class StepFeedbackRecord(BaseModel):
    feedback_id: str
    proposal_id: str
    responsible_role: AgentRole | None = None
    reasons: list[str] = Field(default_factory=list)
    resolved: bool


class StepActionRecord(BaseModel):
    event_id: str
    event_type: str
    status: str
    agent_id: str | None = None
    survivor_id: str | None = None


class AgentOutputScores(BaseModel):
    call_count: int = Field(ge=0)
    proposal_count: int = Field(ge=0)
    accepted_proposal_count: int = Field(ge=0)
    rejected_proposal_count: int = Field(ge=0)
    valid_proposal_rate: float = Field(ge=0, le=1)
    no_op_call_count: int = Field(ge=0)
    temporal_activity_retry_count: int = Field(ge=0)
    schema_repair_attempt_count: int = Field(ge=0)
    stale_result_count: int = Field(ge=0)


class DeterministicStepScores(BaseModel):
    valid_actions: int = Field(ge=0)
    state_changed: bool
    converged: bool
    orchestration_round_count: int = Field(ge=0)
    agent_call_count: int = Field(ge=0)
    selective_retry_rate: float = Field(ge=0, le=1)
    feedback_resolution_rate: float = Field(ge=0, le=1)
    handoff_completion_rate: float = Field(ge=0, le=1)
    duplicate_proposal_rate: float = Field(ge=0, le=1)
    stale_result_rejection_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    no_op_agent_call_count: int = Field(ge=0)
    temporal_activity_retry_count: int = Field(ge=0)
    schema_repair_attempt_count: int = Field(ge=0)
    duplicate_side_effect_count: int = Field(ge=0)
    valid_step_status: bool
    step_latency_s: float = Field(ge=0)
    agent_output_quality: dict[AgentRole, AgentOutputScores] = Field(default_factory=dict)


class StepEvalRecord(BaseModel):
    eval_id: str
    application_workflow_id: str
    temporal_workflow_id: str
    temporal_workflow_run_id: str
    first_execution_run_id: str
    logical_step: int = Field(ge=0)
    state_version_before: int = Field(ge=1)
    state_version_after: int = Field(ge=1)
    orchestration_round_count: int = Field(ge=0)
    rounds: list[StepRoundRecord] = Field(default_factory=list)
    proposals: list[StepProposalRecord] = Field(default_factory=list)
    accepted_proposal_ids: list[str] = Field(default_factory=list)
    rejected_proposal_ids: list[str] = Field(default_factory=list)
    rejections: list[StepRejectionRecord] = Field(default_factory=list)
    handoffs: list[StepHandoffRecord] = Field(default_factory=list)
    feedback: list[StepFeedbackRecord] = Field(default_factory=list)
    stale_results_rejected: int = Field(default=0, ge=0)
    conflicting_proposals_detected: int = Field(default=0, ge=0)
    actions_applied: list[StepActionRecord] = Field(default_factory=list)
    state_summary_before: dict[str, Any] = Field(default_factory=dict)
    state_summary_after: dict[str, Any] = Field(default_factory=dict)
    state_digest_before: str
    state_digest_after: str
    started_at: str
    ended_at: str
    duration_s: float = Field(ge=0)
    final_step_status: str
    termination_reason: str | None = None
    deterministic_scores: DeterministicStepScores | None = None


class AgentProposal(BaseModel):
    proposal_id: str | None = None
    kind: ProposalKind
    agent_id: str | None = None
    survivor_id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    score: float | None = Field(default=None, ge=0, le=100)
    node_path: list[str] = Field(default_factory=list)
    road_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def idempotency_key(self) -> str:
        path = ",".join(self.node_path)
        return f"{self.kind}:{self.agent_id or '-'}:{self.survivor_id or '-'}:{path}"

    @property
    def fingerprint(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude={"proposal_id"}),
            sort_keys=True,
            separators=(",", ":"),
        )


class AgentTurnDecision(BaseModel):
    role: AgentRole
    state_version: int = 1
    orchestration_round: int = 0
    objective: str
    observations: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    proposals: list[AgentProposal] = Field(default_factory=list)
    handoff_to: AgentRole | None = None
    summary: str


class AgentTurnRequest(BaseModel):
    world: WorldState
    role: AgentRole
    workflow_id: str = ""
    business_id: str = ""
    state_version: int = 1
    orchestration_round: int = 0
    prior_decisions: list[AgentTurnDecision] = Field(default_factory=list)
    validation_feedback: list[str] = Field(default_factory=list)
    model: str = "gpt-4.1-mini"
    provider: LLMProvider = "openai"


class ProposalRejection(BaseModel):
    proposal_id: str | None = None
    idempotency_key: str
    reasons: list[str]


class ProposalValidation(BaseModel):
    proposal_id: str
    status: Literal["accepted", "rejected"]
    reason_code: str | None = None
    responsible_role: AgentRole
    affected_dependencies: list[str] = Field(default_factory=list)
    feedback_id: str | None = None
    retryable: bool = False


class RoundValidation(BaseModel):
    accepted_keys: list[str] = Field(default_factory=list)
    accepted_proposal_ids: list[str] = Field(default_factory=list)
    rejections: list[ProposalRejection] = Field(default_factory=list)
    results: list[ProposalValidation] = Field(default_factory=list)
    state_version: int = 1
    orchestration_round: int = 0


class ValidateRoundRequest(BaseModel):
    world: WorldState
    decisions: list[AgentTurnDecision]
    workflow_id: str = ""
    business_id: str = ""
    state_version: int = 1
    orchestration_round: int = 0


class ApplyRoundRequest(BaseModel):
    world: WorldState
    decisions: list[AgentTurnDecision]
    validation: RoundValidation
    simulation_config: SimulationConfig
    workflow_id: str = ""
    business_id: str = ""
    state_version: int = 1
    orchestration_round: int = 0


class DurableSimulationInput(BaseModel):
    business_id: str = ""
    request_fingerprint: str = ""
    workflow_id: str = ""
    state_version: int = 1
    orchestration_round: int = 0
    scenario: ScenarioConfig = Field(default_factory=ScenarioConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    model: str = "gpt-4.1-mini"
    provider: LLMProvider = "openai"
    step_interval_s: float = Field(default=1.0, ge=0, le=60)
    continue_as_new_every: int = Field(default=25, ge=0, le=1_000)
    world: WorldState | None = None
    processed_command_ids: list[str] = Field(default_factory=list)
    evaluation: StepEvaluationSettings = Field(default_factory=StepEvaluationSettings)


class DurableSimulationStatus(BaseModel):
    workflow_id: str = ""
    business_id: str = ""
    request_fingerprint: str = ""
    state_version: int = 1
    orchestration_round: int = 0
    application_step: str = "created"
    phase: str
    paused: bool
    stop_requested: bool
    current_role: AgentRole | None = None
    active_roles: list[AgentRole] = Field(default_factory=list)
    correction_attempt: int = 0
    world: WorldState | None = None
    status: Literal[
        "running",
        "completed",
        "waiting_for_input",
        "no_actionable_work",
        "no_state_change",
        "repeated_rejection",
        "unsatisfiable",
        "max_rounds",
        "cancelled",
        "failed",
    ] = "running"
    termination_reason: str | None = None
    processed_command_ids: list[str] = Field(default_factory=list)
    evaluation_status: str = "disabled"
