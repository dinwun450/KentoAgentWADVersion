import pytest

from kentoagent.evaluation.weave_eval import (
    SimulationEvaluationModel,
    guardrail_scorer,
    loop_efficiency_scorer,
    mission_outcome_scorer,
)
from kentoagent.observability.tracing import (
    ObservabilityConfigurationError,
    TraceSink,
    WeaveSettings,
    initialize_weave,
)


def test_local_trace_sink_is_offline_and_retains_dimensions() -> None:
    sink = TraceSink(WeaveSettings(mode="local", environment="test"))
    sink.emit("agent_turn", run_id="run-1", step=2)

    assert not sink.weave_enabled
    assert sink.records == [
        {"event": "agent_turn", "environment": "test", "run_id": "run-1", "step": 2}
    ]


def test_required_weave_mode_rejects_missing_project() -> None:
    with pytest.raises(ObservabilityConfigurationError):
        initialize_weave(WeaveSettings(mode="required"))


def test_system_evaluation_model_and_scorers_are_replayable() -> None:
    output = SimulationEvaluationModel(max_steps=12).predict(seed=7)

    mission = mission_outcome_scorer(output)
    guardrails = guardrail_scorer(output)
    efficiency = loop_efficiency_scorer(output)

    assert output["seed"] == 7
    assert 0 <= mission["rescue_rate"] <= 1
    assert isinstance(guardrails["guardrails_held"], bool)
    assert efficiency["within_step_budget"] is True
