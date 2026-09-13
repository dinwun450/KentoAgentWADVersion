# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "altair>=5,<7",
#   "kentoagent",
#   "marimo>=0.20,<1",
#   "pandas>=2.2,<3",
#   "weave>=0.52,<1",
# ]
# [tool.uv.sources]
# kentoagent = { path = ".." }
# ///

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="full")


@app.cell
def _():
    import altair as alt
    import pandas as pd

    import marimo as mo
    from kentoagent.evaluation.runner import evaluate_many
    from kentoagent.evaluation.weave_eval import run_weave_evaluation
    from kentoagent.observability.tracing import observability_status

    return alt, evaluate_many, mo, observability_status, pd, run_weave_evaluation


@app.cell
def _(mo):
    base_seed = mo.ui.number(value=42, label="Base seed")
    run_count = mo.ui.slider(2, 50, value=10, label="Seeded runs")
    evaluation_steps = mo.ui.slider(20, 150, step=10, value=100, label="Max steps per run")
    mo.hstack([base_seed, run_count, evaluation_steps])
    return base_seed, evaluation_steps, run_count


@app.cell
def _(mo):
    policy_version = mo.ui.text(value="deterministic-v1", label="Policy version")
    publish_evaluation = mo.ui.run_button(label="Publish this evaluation to Weave")
    mo.hstack([policy_version, publish_evaluation])
    return policy_version, publish_evaluation


@app.cell
def _(base_seed, evaluate_many, evaluation_steps, pd, run_count):
    evaluation_results = evaluate_many(
        run_count.value,
        int(base_seed.value),
        max_steps=evaluation_steps.value,
    )
    evaluation_frame = pd.DataFrame(
        [
            {"seed": result.seed, **result.metrics.model_dump(mode="json")}
            for result in evaluation_results
        ]
    )
    return evaluation_frame, evaluation_results


@app.cell
def _(evaluation_frame, mo):
    aggregates = evaluation_frame[
        [
            "rescue_rate",
            "critical_survivor_rescue_rate",
            "invalid_actions",
            "replans",
            "number_of_steps",
        ]
    ].mean()
    mo.md(
        f"""
        # Systemic evaluation lab

        **Mean rescue rate:** {aggregates["rescue_rate"]:.0%} ·
        **Mean critical rescue rate:** {aggregates["critical_survivor_rescue_rate"]:.0%} ·
        **Mean replans:** {aggregates["replans"]:.1f} ·
        **Mean invalid actions:** {aggregates["invalid_actions"]:.1f}
        """
    )
    return


@app.cell
def _(mo, observability_status):
    weave_state = observability_status()
    project = weave_state["project"] or "not configured"
    mo.md(
        f"**Weave:** `{weave_state['mode']}` mode · **Project:** `{project}` · "
        f"**Environment:** `{weave_state['environment']}`"
    )
    return


@app.cell
async def _(
    base_seed,
    evaluation_steps,
    mo,
    policy_version,
    publish_evaluation,
    run_count,
    run_weave_evaluation,
):
    mo.stop(
        not publish_evaluation.value,
        mo.md("Local results are ready. Use the button to create a native Weave Evaluation."),
    )
    weave_summary = await run_weave_evaluation(
        run_count.value,
        int(base_seed.value),
        max_steps=evaluation_steps.value,
        policy_version=policy_version.value,
    )
    mo.md(f"### Weave evaluation published\n\n```json\n{weave_summary}\n```")
    return


@app.cell
def _(alt, evaluation_frame):
    evaluation_chart = (
        alt.Chart(evaluation_frame)
        .transform_fold(
            ["rescue_rate", "critical_survivor_rescue_rate"],
            as_=["metric", "value"],
        )
        .mark_line(point=True)
        .encode(x="seed:O", y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 1])), color="metric:N")
        .properties(height=380, title="System outcomes across deterministic seeds")
    )
    evaluation_chart
    return


@app.cell
def _(evaluation_frame, mo):
    mo.ui.table(evaluation_frame)
    return


if __name__ == "__main__":
    app.run()
