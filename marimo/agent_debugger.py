# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo>=0.20,<1", "pandas>=2.2,<3"]
# ///

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="full")


@app.cell
def _():
    import pandas as pd

    import marimo as mo
    from kentoagent.orchestration.orchestrator import Orchestrator
    from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario

    return Orchestrator, ScenarioConfig, generate_scenario, mo, pd


@app.cell
def _(mo):
    debug_seed = mo.ui.number(value=49281, label="Replay seed")
    debug_steps = mo.ui.slider(1, 60, value=12, label="Steps to replay")
    mo.hstack([debug_seed, debug_steps])
    return debug_seed, debug_steps


@app.cell
def _(Orchestrator, ScenarioConfig, debug_seed, debug_steps, generate_scenario):
    debug_world = generate_scenario(ScenarioConfig(seed=int(debug_seed.value)))
    Orchestrator(debug_world).run(debug_steps.value)
    agent_options = {
        f"{agent.name} ({agent.id})": agent.id for agent in debug_world.agents.values()
    }
    return agent_options, debug_world


@app.cell
def _(agent_options, mo):
    selected_agent = mo.ui.dropdown(
        options=agent_options,
        value=next(iter(agent_options)),
        label="Inspect agent",
    )
    selected_agent
    return (selected_agent,)


@app.cell
def _(debug_world, mo, selected_agent):
    debug_agent = debug_world.agents[selected_agent.value]
    decision = debug_agent.decision
    decision_view = mo.md(
        f"""
        # {debug_agent.name}

        **OBJECTIVE**  
        {decision.objective if decision else debug_agent.objective}

        **OBSERVATIONS**  
        {" · ".join(decision.observations) if decision else "No decision yet"}

        **CONSTRAINTS**  
        {" · ".join(decision.constraints) if decision else "World-state guardrails"}

        **ACTION** {decision.action if decision else debug_agent.last_action}  
        **VALIDATION** {decision.validation if decision else "Pending"}  
        **RESULT** {decision.result if decision else debug_agent.status.value}  
        **NEXT STEP** {decision.next_step if decision else "Await orchestration"}

        This is a structured decision summary, not hidden chain-of-thought.
        """
    )
    decision_view
    return (debug_agent,)


@app.cell
def _(debug_agent, debug_world, mo, pd):
    agent_events = pd.DataFrame(
        [
            event.model_dump(mode="json")
            for event in debug_world.events
            if event.agent_id == debug_agent.id
        ]
    )
    mo.ui.table(agent_events)
    return


if __name__ == "__main__":
    app.run()
