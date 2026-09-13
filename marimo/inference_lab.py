# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo>=0.20,<1", "weave>=0.52,<1"]
# ///

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    from kentoagent.durable.activities import infer_agent_turn
    from kentoagent.durable.models import AgentTurnRequest
    from kentoagent.observability.tracing import TraceSink, observability_status
    from kentoagent.simulation.entities import AgentRole
    from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario

    return (
        AgentRole,
        AgentTurnRequest,
        ScenarioConfig,
        TraceSink,
        generate_scenario,
        infer_agent_turn,
        mo,
        observability_status,
    )


@app.cell
def _(AgentRole, mo):
    inference_seed = mo.ui.number(value=49281, label="Replay seed")
    role = mo.ui.dropdown(
        options={item.value.title(): item for item in AgentRole},
        value="Locate",
        label="Specialist",
    )
    provider = mo.ui.dropdown(options=["mock", "openai"], value="mock", label="Provider")
    model = mo.ui.text(value="gpt-4.1-mini", label="Model")
    run_inference = mo.ui.run_button(label="Run traced inference")
    mo.hstack([inference_seed, role, provider, model, run_inference])
    return inference_seed, model, provider, role, run_inference


@app.cell
def _(mo):
    script_mode = mo.app_meta().mode == "script"
    return (script_mode,)


@app.cell
async def _(
    AgentTurnRequest,
    ScenarioConfig,
    TraceSink,
    generate_scenario,
    inference_seed,
    infer_agent_turn,
    mo,
    model,
    observability_status,
    provider,
    role,
    run_inference,
    script_mode,
):
    mo.stop(not script_mode and not run_inference.value)
    selected_provider = "mock" if script_mode else provider.value
    world = generate_scenario(ScenarioConfig(seed=int(inference_seed.value)))
    trace = TraceSink()
    decision = await infer_agent_turn(
        AgentTurnRequest(
            world=world,
            role=role.value,
            provider=selected_provider,
            model=model.value,
        )
    )
    state = observability_status()
    proposal_rows = [proposal.model_dump(mode="json") for proposal in decision.proposals]
    mo.vstack(
        [
            mo.md(
                f"# Traced specialist inference\n\n"
                f"**Role:** `{decision.role.value}` · **Provider:** `{selected_provider}` · "
                f"**Model:** `{model.value}` · **Weave project:** "
                f"`{state['project'] or 'local-only'}`\n\n"
                f"**Objective:** {decision.objective}\n\n"
                f"**Summary:** {decision.summary}"
            ),
            mo.ui.table(proposal_rows, label="Typed proposals"),
            mo.md(f"Local trace records: `{len(trace.records)}`"),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
