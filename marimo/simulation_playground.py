# /// script
# requires-python = ">=3.11"
# dependencies = ["altair>=5,<7", "kentoagent", "marimo>=0.20,<1", "pandas>=2.2,<3"]
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
    from kentoagent.config import SimulationConfig
    from kentoagent.evaluation.metrics import calculate_metrics
    from kentoagent.orchestration.orchestrator import Orchestrator
    from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario

    return (
        Orchestrator,
        ScenarioConfig,
        SimulationConfig,
        alt,
        calculate_metrics,
        generate_scenario,
        mo,
        pd,
    )


@app.cell
def _(mo):
    seed = mo.ui.number(value=49281, label="Seed")
    agents = mo.ui.slider(5, 20, value=5, label="Agents")
    survivors = mo.ui.slider(2, 25, value=8, label="Survivors")
    hazards = mo.ui.slider(0, 0.6, step=0.02, value=0.18, label="Hazard density")
    blocked = mo.ui.slider(0, 0.5, step=0.02, value=0.16, label="Blocked-road probability")
    max_steps = mo.ui.slider(10, 150, step=5, value=60, label="Maximum loop steps")
    correction_attempts = mo.ui.slider(0, 5, value=3, label="Correction attempts")
    controls = mo.hstack(
        [seed, agents, survivors, hazards, blocked, max_steps, correction_attempts],
        widths="equal",
    )
    controls
    return agents, blocked, correction_attempts, hazards, max_steps, seed, survivors


@app.cell
def _(
    Orchestrator,
    ScenarioConfig,
    SimulationConfig,
    agents,
    blocked,
    calculate_metrics,
    correction_attempts,
    generate_scenario,
    hazards,
    max_steps,
    seed,
    survivors,
):
    scenario_config = ScenarioConfig(
        seed=int(seed.value),
        agent_count=agents.value,
        survivor_count=survivors.value,
        hazard_density=hazards.value,
        blocked_road_probability=blocked.value,
    )
    playground_world = generate_scenario(scenario_config)
    loop_config = SimulationConfig(
        max_steps=max_steps.value,
        max_correction_attempts=correction_attempts.value,
    )
    Orchestrator(playground_world, loop_config).run()
    playground_metrics = calculate_metrics(playground_world)
    return playground_metrics, playground_world, scenario_config


@app.cell
def _(mo, playground_metrics, playground_world, scenario_config):
    rescue_rate = f"{playground_metrics.rescue_rate:.0%}"
    critical_rate = f"{playground_metrics.critical_survivor_rescue_rate:.0%}"
    replans = playground_metrics.replans
    invalid_actions = playground_metrics.invalid_actions
    step_count = playground_metrics.number_of_steps
    summary = mo.md(
        f"""
        # KentoAgent simulation playground

        **Seed:** `{scenario_config.seed}` · **Run:** `{playground_world.run_id}` ·
        **Stop:** `{playground_world.stopped_reason or "step budget used"}`

        | Rescue rate | Critical rescue rate | Replans | Invalid actions | Steps |
        |---:|---:|---:|---:|---:|
        | {rescue_rate} | {critical_rate} | {replans} | {invalid_actions} | {step_count} |

        _Seeded simulation over real WGS84 coordinates; not live disaster data._
        """
    )
    summary
    return


@app.cell
def _(alt, pd, playground_world):
    map_rows = [
        {
            "id": survivor.id,
            "longitude": survivor.position.lon,
            "latitude": survivor.position.lat,
            "kind": "trapped" if survivor.trapped else "survivor",
            "status": survivor.status.value,
        }
        for survivor in playground_world.survivors.values()
    ] + [
        {
            "id": agent.id,
            "longitude": agent.position.lon,
            "latitude": agent.position.lat,
            "kind": agent.role.value,
            "status": agent.status.value,
        }
        for agent in playground_world.agents.values()
    ]
    positions = pd.DataFrame(map_rows)
    position_chart = (
        alt.Chart(positions)
        .mark_circle(size=130, opacity=0.85)
        .encode(
            x=alt.X("longitude:Q", scale=alt.Scale(zero=False)),
            y=alt.Y("latitude:Q", scale=alt.Scale(zero=False)),
            color="kind:N",
            shape="status:N",
            tooltip=["id", "kind", "status", "longitude", "latitude"],
        )
        .properties(height=460, title="Final agent and survivor positions")
        .interactive()
    )
    position_chart
    return


@app.cell
def _(mo, playground_world):
    event_rows = [event.model_dump(mode="json") for event in playground_world.events[-100:]]
    mo.ui.table(event_rows, label="Recent structured events")
    return


if __name__ == "__main__":
    app.run()
