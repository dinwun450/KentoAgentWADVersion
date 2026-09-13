from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kentoagent.durable.models import StepEvalRecord, StepEvaluationSettings
from kentoagent.evaluation import step_publisher
from kentoagent.evaluation.step_eval import (
    build_step_eval_record,
    compact_eval_inputs,
    compact_eval_output,
    score_step,
)
from kentoagent.evaluation.step_publisher import (
    DETERMINISTIC_SCORER_NAME,
    ORCHESTRATION_JUDGE_SCORER_NAME,
    EvaluationLedger,
    OrchestrationJudgeResponse,
    OrchestrationJudgeV1,
    _default_logger_factory,
    publish_workflow_evaluations,
)
from kentoagent.simulation.entities import AgentRole


def _world(status: str, *, step: int, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "step": step,
        "survivors": {
            "survivor-1": {"status": status, "assigned_agent_id": None},
        },
        "agents": {
            "agent-1": {"status": "idle", "node_id": "node-1"},
        },
        "events": events or [],
        "stopped_reason": None,
    }


def _round(
    index: int,
    roles: list[str],
    *,
    activity_attempt: int = 1,
    schema_repairs: int = 0,
    proposal_id: str | None = None,
    proposal_kind: str = "detect",
) -> dict[str, Any]:
    round_id = f"workflow-1:step:1:round:{index}"
    invocations = []
    for position, role in enumerate(roles):
        role_proposal_id = (
            proposal_id if position == 0 and proposal_id is not None else f"proposal-{index}-{role}"
        )
        invocations.append(
            {
                "invocation_id": f"{round_id}:{role}",
                "round_id": round_id,
                "role": role,
                "temporal_activity_attempt": activity_attempt,
                "schema_repair_attempts": schema_repairs,
                "proposal_ids": [role_proposal_id] if role_proposal_id else [],
                "proposal_kinds": [proposal_kind] if role_proposal_id else [],
                "summary": f"{role} output",
                "handoff_id": f"handoff-{index}-{role}" if role == "locate" else None,
                "handoff_to": "priority" if role == "locate" else None,
            }
        )
    return {
        "round_id": round_id,
        "round_index": index,
        "roles_invoked": roles,
        "validation_id": f"validation-{index}",
        "invocations": invocations,
    }


def _record(
    *,
    logical_step: int = 1,
    rounds: list[dict[str, Any]] | None = None,
    validations: list[dict[str, Any]] | None = None,
    stale: int = 0,
    action: bool = True,
) -> StepEvalRecord:
    before = _world("unknown", step=logical_step - 1)
    events = (
        [
            {
                "id": f"event-{logical_step}",
                "event_type": "survivor_detected",
                "status": "detected",
                "survivor_id": "survivor-1",
            }
        ]
        if action
        else []
    )
    after = _world("waiting" if action else "unknown", step=logical_step, events=events)
    selected_rounds = rounds if rounds is not None else [_round(0, ["locate", "priority"])]
    selected_validations = (
        validations
        if validations is not None
        else [
            {
                "accepted_proposal_ids": ["proposal-0-locate"],
                "rejections": [],
                "results": [
                    {
                        "proposal_id": "proposal-0-locate",
                        "status": "accepted",
                        "responsible_role": "locate",
                    }
                ],
            }
        ]
    )
    return build_step_eval_record(
        application_workflow_id="business-1",
        temporal_workflow_id="workflow-1",
        temporal_workflow_run_id="run-1",
        first_execution_run_id="run-1",
        logical_step=logical_step,
        state_version_before=logical_step,
        state_version_after=logical_step + 1,
        rounds=selected_rounds,
        validations=selected_validations,
        world_before=before,
        world_after=after,
        event_count_before=0,
        stale_results_rejected=stale,
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:02+00:00",
        duration_s=2,
    )


def test_one_logical_step_builds_one_compact_record() -> None:
    record = _record()

    assert record.eval_id == "workflow-1:run-1:1:2"
    assert record.logical_step == 1
    assert record.state_version_before == 1
    assert record.state_version_after == 2
    assert record.deterministic_scores is not None
    serialized = record.model_dump_json()
    assert '"world"' not in serialized
    assert "credentials" not in serialized


def test_validation_retry_stays_one_row_and_is_selective() -> None:
    record = _record(
        rounds=[
            _round(0, ["locate", "priority", "coordinate", "planner", "control"]),
            _round(1, ["planner"], proposal_id="proposal-fixed", proposal_kind="plan"),
        ],
        validations=[
            {
                "accepted_proposal_ids": [],
                "rejections": [
                    {
                        "proposal_id": "proposal-0-planner",
                        "reasons": ["blocked road"],
                    }
                ],
                "results": [
                    {
                        "proposal_id": "proposal-0-planner",
                        "status": "rejected",
                        "responsible_role": "planner",
                        "feedback_id": "feedback-1",
                    }
                ],
            },
            {
                "accepted_proposal_ids": ["proposal-fixed"],
                "rejections": [],
                "results": [],
            },
        ],
    )

    scores = score_step(record)
    assert record.orchestration_round_count == 2
    assert scores.orchestration_round_count == 2
    assert scores.selective_retry_rate == 1
    assert scores.feedback_resolution_rate == 1


def test_full_fanout_retry_is_not_selective() -> None:
    roles = ["locate", "priority", "coordinate", "planner", "control"]
    record = _record(rounds=[_round(0, roles), _round(1, roles)])

    assert score_step(record).selective_retry_rate == 0


def test_temporal_retry_and_schema_repair_do_not_increment_rounds() -> None:
    record = _record(
        rounds=[
            _round(
                0,
                ["locate"],
                activity_attempt=3,
                schema_repairs=1,
                proposal_id="proposal-1",
            )
        ]
    )

    scores = score_step(record)
    assert scores.orchestration_round_count == 1
    assert scores.temporal_activity_retry_count == 2
    assert scores.schema_repair_attempt_count == 1
    assert scores.agent_output_quality[AgentRole.LOCATE].temporal_activity_retry_count == 2
    assert scores.agent_output_quality[AgentRole.LOCATE].schema_repair_attempt_count == 1


def test_repeated_unchanged_proposal_and_duplicate_side_effect_are_counted() -> None:
    rounds = [
        _round(0, ["locate"], proposal_id="proposal-same"),
        _round(1, ["locate"], proposal_id="proposal-same"),
    ]
    record = _record(rounds=rounds)
    duplicate = record.actions_applied[0].model_copy()
    record.actions_applied.append(duplicate)

    scores = score_step(record)
    assert scores.duplicate_proposal_rate == 0.5
    assert scores.duplicate_side_effect_count == 1


def test_handoff_completion_requires_target_role_to_run() -> None:
    complete = _record(rounds=[_round(0, ["locate", "priority"])])
    incomplete = _record(rounds=[_round(0, ["locate"])])

    assert score_step(complete).handoff_completion_rate == 1
    assert score_step(incomplete).handoff_completion_rate == 0


def test_partial_validation_stale_and_conflict_are_preserved() -> None:
    record = _record(
        validations=[
            {
                "accepted_proposal_ids": ["proposal-good"],
                "rejections": [
                    {
                        "proposal_id": "proposal-bad",
                        "reasons": ["conflicting proposal"],
                    }
                ],
                "results": [
                    {
                        "proposal_id": "proposal-bad",
                        "status": "rejected",
                        "reason_code": "conflicting_proposal",
                        "responsible_role": "priority",
                        "feedback_id": "feedback-conflict",
                    }
                ],
            }
        ],
        stale=2,
    )

    scores = score_step(record)
    assert scores.valid_actions == 1
    assert record.rejected_proposal_ids == ["proposal-bad"]
    assert scores.stale_result_rejection_count == 2
    assert scores.conflict_count == 1


def test_no_action_step_needs_no_agent_calls() -> None:
    record = _record(rounds=[], validations=[], action=False)

    scores = score_step(record)
    assert record.final_step_status == "no_actionable_work"
    assert scores.agent_call_count == 0
    assert scores.state_changed is False
    assert scores.converged is True


class _FakePrediction:
    def __init__(self, eval_id: str) -> None:
        self.eval_id = eval_id
        self.scores: dict[str, Any] = {}
        self.finished = False

    def log_score(self, name: str, value: Any) -> None:
        self.scores[name] = value

    def finish(self) -> None:
        self.finished = True


class _FakeLogger:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.predictions: list[_FakePrediction] = []
        self.summary: dict[str, Any] | None = None
        self.ui_url: str | None = None

    def log_prediction(self, inputs: dict[str, Any], **kwargs: Any) -> _FakePrediction:
        call = len(self.predictions) + 1
        if self.fail_on_call == call:
            raise RuntimeError("synthetic logger outage")
        prediction = _FakePrediction(str(kwargs["example_id"]))
        self.predictions.append(prediction)
        return prediction

    def log_summary(self, summary: dict[str, Any]) -> None:
        self.summary = summary


def _publication_payload(**settings: Any) -> dict[str, Any]:
    return {
        "temporal_workflow_id": "workflow-1",
        "application_workflow_id": "business-1",
        "final_status": "completed",
        "termination_reason": "done",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "settings": StepEvaluationSettings(enabled=True, **settings).model_dump(mode="json"),
    }


def test_complete_publication_logs_rows_once_and_one_summary(tmp_path: Path) -> None:
    ledger = EvaluationLedger(tmp_path / "evals.sqlite3")
    assert ledger.enqueue(_record(logical_step=1))
    assert ledger.enqueue(_record(logical_step=2))
    assert not ledger.enqueue(_record(logical_step=1))
    fake = _FakeLogger()

    first = publish_workflow_evaluations(
        _publication_payload(), ledger=ledger, logger_factory=lambda **_: fake
    )
    second = publish_workflow_evaluations(
        _publication_payload(), ledger=ledger, logger_factory=lambda **_: _FakeLogger()
    )

    assert first == {"status": "published", "total": 2, "published": 2, "already_published": 0}
    assert second["status"] == "already_published"
    assert [item.eval_id for item in fake.predictions] == [
        "workflow-1:run-1:1:2",
        "workflow-1:run-1:2:3",
    ]
    assert all(item.finished for item in fake.predictions)
    assert fake.summary is not None
    assert fake.summary["step_count"] == 2
    assert set(fake.predictions[0].scores) == {DETERMINISTIC_SCORER_NAME}


def test_publication_maps_workflow_to_named_weave_evaluation_surface(tmp_path: Path) -> None:
    ledger = EvaluationLedger(tmp_path / "evals.sqlite3")
    ledger.enqueue(_record())
    fake = _FakeLogger()
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeLogger:
        captured.update(kwargs)
        return fake

    class FakeJudge:
        def score(self, **_: Any) -> dict[str, Any]:
            return {"overall_score": 100, "pass": True}

    publish_workflow_evaluations(
        _publication_payload(llm_judge_enabled=True),
        ledger=ledger,
        logger_factory=factory,
        judge_factory=lambda _: FakeJudge(),
    )

    assert captured["name"] == "kentoagent-production-steps:workflow-1"
    assert captured["model"] == (
        "kentoagent-temporal-orchestrator:openai:gpt-4.1-mini:orchestration-v1"
    )
    assert captured["dataset_name"] == ("kentoagent-production-step-trajectories:workflow-1")
    assert captured["scorers"] == [
        DETERMINISTIC_SCORER_NAME,
        ORCHESTRATION_JUDGE_SCORER_NAME,
    ]
    assert captured["eval_attributes"]["evaluation_kind"] == ("production_step_trajectory")
    assert set(fake.predictions[0].scores) == {
        DETERMINISTIC_SCORER_NAME,
        ORCHESTRATION_JUDGE_SCORER_NAME,
    }


def test_default_logger_factory_uses_named_weave_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeDataset:
        def __init__(self, **kwargs: Any) -> None:
            captured["dataset_kwargs"] = kwargs

    class FakeTable:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

    def fake_logger(**kwargs: Any) -> object:
        captured["logger_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(step_publisher, "initialize_weave", lambda: object())
    monkeypatch.setattr(step_publisher.weave, "Dataset", FakeDataset)
    monkeypatch.setattr(step_publisher.weave, "Table", FakeTable)
    monkeypatch.setattr(step_publisher.weave, "EvaluationLogger", fake_logger)

    _default_logger_factory(
        name="eval-name",
        rows=[{"eval_id": "step-1"}],
        model="model-name",
        dataset_name="dataset-name",
        scorers=[DETERMINISTIC_SCORER_NAME],
        eval_attributes={"source": "temporal"},
    )

    assert captured["dataset_kwargs"]["name"] == "dataset-name"
    assert captured["dataset_kwargs"]["rows"].rows == [{"eval_id": "step-1"}]
    assert captured["logger_kwargs"]["name"] == "eval-name"
    assert captured["logger_kwargs"]["model"] == "model-name"
    assert captured["logger_kwargs"]["scorers"] == [DETERMINISTIC_SCORER_NAME]


def test_partial_publication_resumes_unpublished_rows(tmp_path: Path) -> None:
    ledger = EvaluationLedger(tmp_path / "evals.sqlite3")
    ledger.enqueue(_record(logical_step=1))
    ledger.enqueue(_record(logical_step=2))
    failing = _FakeLogger(fail_on_call=2)

    with pytest.raises(RuntimeError, match="synthetic logger outage"):
        publish_workflow_evaluations(
            _publication_payload(), ledger=ledger, logger_factory=lambda **_: failing
        )
    resumed = _FakeLogger()
    result = publish_workflow_evaluations(
        _publication_payload(), ledger=ledger, logger_factory=lambda **_: resumed
    )

    assert ledger.counts("workflow-1") == (2, 2)
    assert result["published"] == 1
    assert [item.eval_id for item in resumed.predictions] == ["workflow-1:run-1:2:3"]
    assert resumed.summary is not None


def test_missing_trajectory_is_not_evaluable_without_calling_an_llm() -> None:
    judge = OrchestrationJudgeV1()

    result = judge.score(output={}, trajectory={"rounds": []})

    assert result["not_evaluable"] is True
    assert result["pass"] is False
    assert result["failure_tags"] == ["missing_trajectory"]


def test_judge_uses_strict_structured_output_and_valid_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    trajectory = compact_eval_inputs(record)
    output = compact_eval_output(record)
    round_id = record.rounds[0].round_id
    captured: dict[str, Any] = {}
    parsed = OrchestrationJudgeResponse(
        role_specialization=3,
        information_flow=3,
        handoff_quality=3,
        feedback_adaptation=3,
        coordination_efficiency=3,
        decision_coherence=3,
        termination_correctness=3,
        not_evaluable=False,
        failure_tags=[],
        evidence=[{"reference_id": round_id, "claim": "The round coordinated roles."}],
        recommendation="Keep using selective retries.",
    )

    class FakeCompletions:
        def parse(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
            )

    class FakeOpenAI:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    result = OrchestrationJudgeV1().score(output=output, trajectory=trajectory)

    assert captured["response_format"] is OrchestrationJudgeResponse
    assert round_id in captured["messages"][1]["content"]
    assert result["overall_score"] == 75
    assert result["pass"] is True
    assert result["evidence"][0]["reference_id"] == round_id


def test_invalid_judge_shape_becomes_not_evaluable(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record()

    class FakeCompletions:
        def parse(self, **_: Any) -> Any:
            return OrchestrationJudgeResponse.model_validate(
                {"not_evaluable": False, "evidence": {"round": ["round-0"]}}
            )

    class FakeOpenAI:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    result = OrchestrationJudgeV1().score(
        output=compact_eval_output(record),
        trajectory=compact_eval_inputs(record),
    )

    assert result["not_evaluable"] is True
    assert result["pass"] is False
    assert result["failure_tags"] == ["invalid_judge_response"]


def test_fake_judge_cites_a_real_trajectory_id(tmp_path: Path) -> None:
    ledger = EvaluationLedger(tmp_path / "evals.sqlite3")
    record = _record()
    ledger.enqueue(record)
    fake_logger = _FakeLogger()

    class FakeJudge:
        def score(self, *, output: dict[str, Any], trajectory: dict[str, Any]) -> dict[str, Any]:
            round_id = trajectory["rounds"][0]["round_id"]
            return {
                "overall_score": 90,
                "pass": True,
                "not_evaluable": False,
                "evidence": [{"reference_id": round_id, "claim": "roles coordinated"}],
            }

    publish_workflow_evaluations(
        _publication_payload(llm_judge_enabled=True),
        ledger=ledger,
        logger_factory=lambda **_: fake_logger,
        judge_factory=lambda _: FakeJudge(),
    )

    judge_score = fake_logger.predictions[0].scores[ORCHESTRATION_JUDGE_SCORER_NAME]
    assert judge_score["evidence"][0]["reference_id"] == record.rounds[0].round_id


def test_fail_open_publication_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Weave unavailable")

    monkeypatch.setattr(step_publisher, "publish_workflow_evaluations", unavailable)

    result = step_publisher.publish_workflow_evaluations_activity(_publication_payload())

    assert result == {"status": "unavailable", "error_type": "RuntimeError"}


def test_eval_payloads_remain_compact() -> None:
    record = _record()
    inputs = compact_eval_inputs(record)
    output = compact_eval_output(record)

    assert "state_summary_after" not in inputs
    assert "rounds" in inputs
    assert "actions_applied" in output
    assert "OPENAI_API_KEY" not in str({"inputs": inputs, "output": output})


def test_evaluation_configuration_is_resolved_before_workflow_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KENTO_STEP_EVAL_ENABLED", "true")
    monkeypatch.setenv("KENTO_STEP_EVAL_LLM_JUDGE", "true")
    monkeypatch.setenv("KENTO_STEP_EVAL_JUDGE_MODEL", "gpt-4.1")
    monkeypatch.setenv("KENTO_STEP_EVAL_PASS_THRESHOLD", "80")
    monkeypatch.setenv("KENTO_STEP_EVAL_FAILURE_POLICY", "retry")

    settings = StepEvaluationSettings.from_env()

    assert settings.enabled is True
    assert settings.llm_judge_enabled is True
    assert settings.judge_model == "gpt-4.1"
    assert settings.pass_threshold == 80
    assert settings.telemetry_failure_policy == "retry"
