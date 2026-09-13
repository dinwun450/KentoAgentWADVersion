# Architecture decisions

## Runtime boundary

The domain package owns all truth. The browser receives read models and GeoJSON; it never
decides whether an action is physically valid. FastAPI serializes operator intents and
invokes the same orchestrator used by CLI, tests, and Marimo.

```text
MapLibre / CopilotKit ──typed HTTP intent──> FastAPI
                                             │
World state -> specialists -> coordinator -> guardrails -> action
     │              │              │              │
     └──────── structured events / Weave spans ───┘
                    │
            SimulationStore protocol
                    │
              local JSON today
```

## Framework responsibilities

- MapLibre renders a public real-world basemap and stable-ID GeoJSON overlays. It contains
  no rescue policy.
- LlamaIndex is an optional policy/orchestration adapter for model-backed experiments.
  The deterministic orchestrator remains the execution authority.
- CopilotKit v2 exposes UI tools for control and inspection. Tool handlers call typed API
  endpoints; they do not mutate React state as world truth.
- Weave mirrors structured sessions, decisions, tool calls, retries, and evaluations.
  Local events remain replayable if Weave is unavailable.
- Marimo is the reactive engineering workspace for scenarios, traces, and evaluations.

## Deferred systems

Snowflake and dbt are deferred. A future `SnowflakeSimulationStore` should implement the
existing `SimulationStore` protocol and map entities/events to run, scenario, agent,
survivor, hazard, assignment, plan, judge, rescue, and metric tables.

Temporal is the durable execution layer for model-backed runs. The V2 workflow fans out five
independent LlamaIndex agent Activities concurrently, stores their results in workflow history,
validates typed proposals, and commits one deterministic state transition per round. Activity
boundaries use JSON-safe payloads to avoid Python sandbox class-identity problems. The original
workflow type remains registered for history compatibility, while the local orchestrator remains
the offline fallback. Redis is unnecessary until multiple API processes need pub/sub or shared
ephemeral state. MCP belongs at the tool boundary if external sensors or dispatch systems are
introduced.
