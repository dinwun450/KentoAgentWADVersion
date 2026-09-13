from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import weave
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio import activity

from kentoagent.durable.models import StepEvalRecord, StepEvaluationSettings
from kentoagent.evaluation.step_eval import compact_eval_inputs, compact_eval_output
from kentoagent.observability.tracing import initialize_weave

logger = logging.getLogger(__name__)
DEFAULT_LEDGER_PATH = Path(".kentoagent") / "step-evaluations.sqlite3"
EVALUATION_NAME_PREFIX = "kentoagent-production-steps"
EVALUATION_DATASET_PREFIX = "kentoagent-production-step-trajectories"
EVALUATION_MODEL_PREFIX = "kentoagent-temporal-orchestrator"
DETERMINISTIC_SCORER_NAME = "kentoagent_deterministic_step_v1"
ORCHESTRATION_JUDGE_SCORER_NAME = "kentoagent_orchestration_judge_v1"
JUDGE_DIMENSIONS = (
    "role_specialization",
    "information_flow",
    "handoff_quality",
    "feedback_adaptation",
    "coordination_efficiency",
    "decision_coherence",
    "termination_correctness",
)


class JudgeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_id: str
    claim: str


class OrchestrationJudgeResponse(BaseModel):
    """Strict schema produced by the judge before deterministic post-processing."""

    model_config = ConfigDict(extra="forbid")

    role_specialization: int = Field(ge=0, le=4)
    information_flow: int = Field(ge=0, le=4)
    handoff_quality: int = Field(ge=0, le=4)
    feedback_adaptation: int = Field(ge=0, le=4)
    coordination_efficiency: int = Field(ge=0, le=4)
    decision_coherence: int = Field(ge=0, le=4)
    termination_correctness: int = Field(ge=0, le=4)
    not_evaluable: bool
    failure_tags: list[str]
    evidence: list[JudgeEvidence]
    recommendation: str = Field(max_length=500)


class OrchestrationJudgeResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role_specialization: int = Field(ge=0, le=4)
    information_flow: int = Field(ge=0, le=4)
    handoff_quality: int = Field(ge=0, le=4)
    feedback_adaptation: int = Field(ge=0, le=4)
    coordination_efficiency: int = Field(ge=0, le=4)
    decision_coherence: int = Field(ge=0, le=4)
    termination_correctness: int = Field(ge=0, le=4)
    overall_score: float = Field(ge=0, le=100)
    passed: bool = Field(alias="pass")
    not_evaluable: bool
    failure_tags: list[str] = Field(default_factory=list)
    evidence: list[JudgeEvidence] = Field(default_factory=list)
    recommendation: str = Field(max_length=500)
    judge_model: str
    rubric_version: str
    prompt_version: str


class OrchestrationJudgeV1(weave.Scorer):
    """Optional semantic trajectory judge; deterministic checks stay outside the LLM."""

    judge_model: str = "gpt-4.1"
    rubric_version: str = "orchestration-v1"
    prompt_version: str = "orchestration-v1"
    pass_threshold: float = 70.0

    @weave.op(name="kentoagent.orchestration_judge_v1", enable_code_capture=False)
    def score(
        self,
        *,
        output: Any,
        trajectory: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if not isinstance(output, dict) or trajectory is None:
            return self._not_evaluable("missing_trajectory")
        rounds = trajectory.get("rounds", [])
        if not rounds:
            return self._not_evaluable("missing_trajectory")
        from openai import OpenAI

        valid_ids = _collect_ids({"trajectory": trajectory, "output": output})
        prompt = {
            "instruction": (
                "Score the full orchestration trajectory, not just its outcome. Use integers 0-4 "
                "for each dimension. Penalize unnecessary full fan-out, ignored feedback, repeated "
                "unchanged proposals, stale-state decisions, and unjustified handoffs. Reward "
                "selective retry, resolved feedback, causal handoffs, meaningful changes, and "
                "correct early termination. Return every dimension as a flat top-level field. "
                "Evidence must be a JSON list of {reference_id, claim} objects. Each reference_id "
                "must exactly copy one allowed ID; never use a JSON path or invent an event. Set "
                "not_evaluable=true if evidence is insufficient."
            ),
            "rubric_version": self.rubric_version,
            "prompt_version": self.prompt_version,
            "dimensions": list(JUDGE_DIMENSIONS),
            "allowed_evidence_reference_ids": sorted(valid_ids),
            "trajectory": trajectory,
            "result": output,
        }
        try:
            response = OpenAI().chat.completions.parse(
                model=self.judge_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Evaluate the trajectory and return data matching the supplied "
                            "structured-output schema."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
                ],
                response_format=OrchestrationJudgeResponse,
            )
            parsed = response.choices[0].message.parsed
        except ValidationError:
            logger.warning("Orchestration judge returned invalid structured output", exc_info=True)
            return self._not_evaluable("invalid_judge_response")
        if parsed is None:
            return self._not_evaluable("judge_refusal_or_empty_response")
        raw = parsed.model_dump(mode="json")
        raw.update(
            {
                "overall_score": 0,
                "pass": False,
                "judge_model": self.judge_model,
                "rubric_version": self.rubric_version,
                "prompt_version": self.prompt_version,
            }
        )
        result = OrchestrationJudgeResult.model_validate(raw)
        invalid_refs = [
            item.reference_id for item in result.evidence if item.reference_id not in valid_ids
        ]
        if invalid_refs:
            return self._not_evaluable("invalid_evidence_reference")
        if not result.not_evaluable and not result.evidence:
            return self._not_evaluable("missing_evidence")
        overall = sum(getattr(result, name) for name in JUDGE_DIMENSIONS) / 28 * 100
        result.overall_score = round(overall, 2)
        result.passed = not result.not_evaluable and overall >= self.pass_threshold
        return result.model_dump(mode="json", by_alias=True)

    def _not_evaluable(self, tag: str) -> dict[str, Any]:
        payload: dict[str, Any] = {name: 0 for name in JUDGE_DIMENSIONS}
        payload.update(
            {
                "overall_score": 0,
                "pass": False,
                "not_evaluable": True,
                "failure_tags": [tag],
                "evidence": [],
                "recommendation": (
                    "Capture a complete compact trajectory before semantic judging."
                ),
                "judge_model": self.judge_model,
                "rubric_version": self.rubric_version,
                "prompt_version": self.prompt_version,
            }
        )
        return OrchestrationJudgeResult.model_validate(payload).model_dump(
            mode="json", by_alias=True
        )


def _collect_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "eval_id" or key.endswith("_id"):
                if isinstance(child, str):
                    found.add(child)
            elif key.endswith("_ids") and isinstance(child, list):
                found.update(str(item) for item in child)
            found.update(_collect_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_ids(child))
    return found


class EvaluationLedger:
    """SQLite-backed outbox with stable-ID deduplication and row-level resume."""

    def __init__(self, path: str | Path | None = None) -> None:
        raw_path: str | Path = (
            path
            if path is not None
            else os.getenv("KENTO_STEP_EVAL_LEDGER_PATH") or DEFAULT_LEDGER_PATH
        )
        resolved = Path(raw_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.path = resolved
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS step_evaluations (
                    eval_id TEXT PRIMARY KEY,
                    temporal_workflow_id TEXT NOT NULL,
                    logical_step INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_summaries (
                    temporal_workflow_id TEXT PRIMARY KEY,
                    published_at TEXT NOT NULL
                )
                """
            )

    def enqueue(self, record: StepEvalRecord) -> bool:
        payload = record.model_dump_json()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM step_evaluations WHERE eval_id = ?", (record.eval_id,)
            ).fetchone()
            if existing:
                if existing["payload"] != payload:
                    raise ValueError(f"evaluation ID collision for {record.eval_id}")
                return False
            connection.execute(
                """
                INSERT INTO step_evaluations
                    (eval_id, temporal_workflow_id, logical_step, payload)
                VALUES (?, ?, ?, ?)
                """,
                (record.eval_id, record.temporal_workflow_id, record.logical_step, payload),
            )
            return True

    def pending(self, temporal_workflow_id: str) -> list[StepEvalRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM step_evaluations
                WHERE temporal_workflow_id = ? AND published = 0
                ORDER BY logical_step, eval_id
                """,
                (temporal_workflow_id,),
            ).fetchall()
        return [StepEvalRecord.model_validate_json(row["payload"]) for row in rows]

    def counts(self, temporal_workflow_id: str) -> tuple[int, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(published), 0) AS published
                FROM step_evaluations WHERE temporal_workflow_id = ?
                """,
                (temporal_workflow_id,),
            ).fetchone()
        return int(row["total"]), int(row["published"])

    def mark_published(self, eval_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE step_evaluations SET published = 1, published_at = ? WHERE eval_id = ?",
                (datetime.now(UTC).isoformat(), eval_id),
            )

    def summary_published(self, temporal_workflow_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM evaluation_summaries WHERE temporal_workflow_id = ?",
                (temporal_workflow_id,),
            ).fetchone()
        return row is not None

    def mark_summary_published(self, temporal_workflow_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO evaluation_summaries
                    (temporal_workflow_id, published_at) VALUES (?, ?)
                """,
                (temporal_workflow_id, datetime.now(UTC).isoformat()),
            )


@activity.defn(name="kentoagent.v2.store_step_evaluation")
def store_step_evaluation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    record = StepEvalRecord.model_validate(payload)
    inserted = EvaluationLedger().enqueue(record)
    return {"eval_id": record.eval_id, "inserted": inserted}


LoggerFactory = Callable[..., Any]
JudgeFactory = Callable[[StepEvaluationSettings], Any]


def _default_logger_factory(
    *,
    name: str,
    rows: list[dict[str, Any]],
    model: str,
    dataset_name: str,
    scorers: list[str],
    eval_attributes: dict[str, Any],
) -> Any:
    if initialize_weave() is None:
        raise RuntimeError("Weave project is not configured or unavailable")
    dataset = weave.Dataset(name=dataset_name, rows=weave.Table(rows))
    return weave.EvaluationLogger(
        name=name,
        model=model,
        dataset=dataset,
        scorers=scorers,
        eval_attributes=eval_attributes,
    )


def _evaluation_surface(
    payload: dict[str, Any],
    settings: StepEvaluationSettings,
    workflow_id: str,
) -> dict[str, Any]:
    """Stable labels and attributes surfaced by Weave's Evaluations page."""

    provider = str(payload.get("provider") or "unknown")
    agent_model = str(payload.get("model") or "unknown")
    scorer_names: list[str] = []
    if settings.deterministic_scoring_enabled:
        scorer_names.append(DETERMINISTIC_SCORER_NAME)
    if settings.llm_judge_enabled:
        scorer_names.append(ORCHESTRATION_JUDGE_SCORER_NAME)
    return {
        "name": f"{EVALUATION_NAME_PREFIX}:{workflow_id}",
        "model": (f"{EVALUATION_MODEL_PREFIX}:{provider}:{agent_model}:{settings.prompt_version}"),
        "dataset_name": f"{EVALUATION_DATASET_PREFIX}:{workflow_id}",
        "scorers": scorer_names,
        "eval_attributes": {
            "source": "temporal",
            "evaluation_kind": "production_step_trajectory",
            "temporal_workflow_id": workflow_id,
            "application_workflow_id": payload.get("application_workflow_id"),
            "final_status": payload.get("final_status"),
            "provider": provider,
            "agent_model": agent_model,
            "rubric_version": settings.rubric_version,
            "prompt_version": settings.prompt_version,
        },
    }


def _default_judge_factory(settings: StepEvaluationSettings) -> OrchestrationJudgeV1:
    return OrchestrationJudgeV1(
        judge_model=settings.judge_model,
        rubric_version=settings.rubric_version,
        prompt_version=settings.prompt_version,
        pass_threshold=settings.pass_threshold,
    )


def publish_workflow_evaluations(
    payload: dict[str, Any],
    *,
    ledger: EvaluationLedger | None = None,
    logger_factory: LoggerFactory = _default_logger_factory,
    judge_factory: JudgeFactory = _default_judge_factory,
) -> dict[str, Any]:
    settings = StepEvaluationSettings.model_validate(payload.get("settings", {}))
    workflow_id = str(payload["temporal_workflow_id"])
    resolved_ledger = ledger or EvaluationLedger()
    total, already_published = resolved_ledger.counts(workflow_id)
    if resolved_ledger.summary_published(workflow_id):
        return {
            "status": "already_published",
            "total": total,
            "published": 0,
            "already_published": already_published,
        }
    pending = resolved_ledger.pending(workflow_id)
    surface = _evaluation_surface(payload, settings, workflow_id)
    logger_instance = logger_factory(
        rows=[compact_eval_inputs(record) for record in pending],
        **surface,
    )
    judge = judge_factory(settings) if settings.llm_judge_enabled else None
    published = 0
    for record in pending:
        inputs = compact_eval_inputs(record)
        output = compact_eval_output(record)
        prediction = logger_instance.log_prediction(
            inputs,
            output=output,
            example_id=record.eval_id,
            row_digest=record.eval_id,
            trial_index=0,
            eval_kind="agent",
        )
        if settings.deterministic_scoring_enabled and record.deterministic_scores:
            scores = record.deterministic_scores.model_dump(mode="json")
            prediction.log_score(DETERMINISTIC_SCORER_NAME, scores)
        if judge is not None:
            judge_result = judge.score(output=output, trajectory=inputs)
            prediction.log_score(ORCHESTRATION_JUDGE_SCORER_NAME, judge_result)
        prediction.finish()
        resolved_ledger.mark_published(record.eval_id)
        published += 1
    logger_instance.log_summary(
        {
            "temporal_workflow_id": workflow_id,
            "application_workflow_id": payload.get("application_workflow_id"),
            "final_status": payload.get("final_status"),
            "termination_reason": payload.get("termination_reason"),
            "step_count": total,
            "rubric_version": settings.rubric_version,
            "prompt_version": settings.prompt_version,
            "model": surface["model"],
            "dataset": surface["dataset_name"],
            "scorers": surface["scorers"],
        }
    )
    resolved_ledger.mark_summary_published(workflow_id)
    result = {
        "status": "published",
        "total": total,
        "published": published,
        "already_published": already_published,
    }
    ui_url = getattr(logger_instance, "ui_url", None)
    if ui_url:
        result["ui_url"] = str(ui_url)
        logger.info("Published production step evaluation to %s", ui_url)
    return result


@activity.defn(name="kentoagent.v2.publish_step_evaluations")
def publish_workflow_evaluations_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = StepEvaluationSettings.model_validate(payload.get("settings", {}))
    try:
        return publish_workflow_evaluations(payload)
    except Exception as exc:
        if settings.telemetry_failure_policy == "retry":
            raise
        logger.warning("Step evaluation publication failed open", exc_info=True)
        return {"status": "unavailable", "error_type": type(exc).__name__}
