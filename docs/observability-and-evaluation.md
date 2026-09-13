# Weave and Marimo production path

Weave and Marimo are required KentoAgent technologies. Weave owns inference and
system-behavior observability; Marimo is the reactive operations and evaluation workspace.
Neither replaces deterministic guardrails, Temporal durability, or the replayable domain event
stream.

## Runtime modes

`KENTO_WEAVE_MODE` controls failure behavior:

- `auto` is the development default. Calls are published when a project is configured and the
  service is reachable; simulation remains available if telemetry is unavailable.
- `local` intentionally disables remote publication for tests and disconnected development.
- `required` is the production setting. API and worker startup fails if Weave cannot initialize.

Set `WANDB_ENTITY`, `WANDB_PROJECT`, `WANDB_API_KEY`, and `KENTO_ENVIRONMENT`. The legacy
`WEAVE_PROJECT` variable remains supported for local setups. Credentials must come from the
deployment secret manager, never from simulation state or trace attributes.

## Trace model

`kentoagent.simulation_run` is the root of a local orchestrator run. It contains
`kentoagent.orchestration_step` calls and `kentoagent.simulation_event` calls. Temporal activity
inferences are recorded as `kentoagent.agent_inference`; their stable `run_id`, `scenario_id`,
role, step, provider, and model fields reconnect activity-level traces even when activities run
on different workers. Supported OpenAI requests made after `weave.init()` are automatically
traced beneath the inference operation, including token use, latency, output, and errors.

Inputs are deliberately compact. Full world snapshots, secrets, survivor personal data, and
hidden chain-of-thought must not be uploaded. Agent outputs contain typed proposals and concise
decision summaries only.

## System evaluations

Run local replay evaluation:

```powershell
uv run python -m kentoagent.eval --runs 50 --seed 42
```

Publish the same seeded dataset as a native Weave Evaluation:

```powershell
uv run --env-file .env python -m kentoagent.eval --runs 50 --seed 42 --weave `
  --policy-version deterministic-v1
```

Every dataset row stores its seed. `SimulationEvaluationModel.predict` emits the complete metric
record, while mission-outcome, deterministic-guardrail, and loop-efficiency scorers make runs
comparable in Weave. Change `policy-version` whenever prompts, tools, orchestration order, or
priority policy changes.

## Production Temporal step evaluations

Production step evaluation is independent of the seed-based system evaluation above and is
disabled by default. A logical step starts before the first v2 agent fan-out and becomes
evaluable only after `kentoagent.v2.apply_round` returns. Validation-driven corrections remain
inside that row; Temporal Activity retries and structured-output repairs are recorded on each
agent invocation as separate counters.

The Workflow builds only a compact `StepEvalRecord`. After commit it schedules
`kentoagent.v2.store_step_evaluation`, which writes the record to the SQLite outbox. At terminal
workflow completion, `kentoagent.v2.publish_step_evaluations` creates one `EvaluationLogger`,
logs one prediction per pending step using the stable evaluation ID as `example_id`, and calls
`log_summary()` once. Neither Activity is executed during Workflow replay. A continued Workflow
retains a single application Workflow ID while each row records both its current Run ID and the
first execution Run ID.

The imperative logger emits the same `Evaluation.evaluate` root used by Weave's Evaluations
page. Each terminal Temporal workflow appears as one evaluation run with:

- a display name prefixed by `kentoagent-production-steps`;
- a named, versioned dataset containing one compact row per logical step;
- a model label containing the provider, agent model, and prompt version;
- `kentoagent_deterministic_step_v1` as one structured scorer result, so numeric fields aggregate
  as means and boolean fields aggregate as true counts/fractions;
- optional `kentoagent_orchestration_judge_v1` results; and
- filterable Workflow ID, status, model, provider, rubric, and prompt attributes.

The worker logs the resulting Weave UI URL after successful publication. Because production
steps have already executed in Temporal, publication records their stored outputs rather than
calling the application model again. The optional judge uses OpenAI structured outputs for its
flat seven-dimension score contract. A malformed or refused judge response is recorded as
`not_evaluable` and does not discard the deterministic step evaluation.

Configuration is resolved by the API or CLI before start and captured in Workflow input:

- `KENTO_STEP_EVAL_ENABLED` (default `false`)
- `KENTO_STEP_EVAL_DETERMINISTIC` (default `true`)
- `KENTO_STEP_EVAL_LLM_JUDGE` (default `false`)
- `KENTO_STEP_EVAL_JUDGE_MODEL` (default `gpt-4.1`; configure a different or stronger model than
  the production agent model)
- `KENTO_STEP_EVAL_RUBRIC_VERSION` and `KENTO_STEP_EVAL_PROMPT_VERSION`
- `KENTO_STEP_EVAL_PASS_THRESHOLD` (default `70`)
- `KENTO_STEP_EVAL_PUBLICATION_MODE` (`terminal_outbox`, the only supported mode)
- `KENTO_STEP_EVAL_FAILURE_POLICY` (`fail_open` by default, or `retry`)
- `KENTO_STEP_EVAL_LEDGER_PATH` (worker-local Activity configuration; default
  `.kentoagent/step-evaluations.sqlite3`)

The only publication mode currently implemented is `terminal_outbox`. The SQLite ledger is
transactional and deduplicates normal Activity re-execution by `eval_id`; partial publication
resumes from rows marked unpublished. Its delivery contract is stable-ID at-least-once: a worker
crash after Weave accepts a row but before the local ledger commit can resend that row. Consumers
must deduplicate by `eval_id`. Multi-worker deployments must place the ledger on shared durable
storage or replace `EvaluationLedger` with a transactional database-backed implementation before
enabling evaluations.

Step rows do not claim automatic ancestry to agent traces created in earlier Temporal
Activities. Correlation uses explicit Temporal Workflow/Run IDs, logical step and round IDs, and
an agent trace ID only when supported instrumentation returns one.

## Marimo operations

`evaluation_lab.py` compares seeded local results and can publish the selected cohort to Weave.
`inference_lab.py` runs one typed specialist inference; its default mock provider makes script
validation credential-free, while the OpenAI option exercises the traced production inference
boundary.

```powershell
uv run marimo run marimo/evaluation_lab.py
uv run marimo run marimo/inference_lab.py
```

## Production checklist

1. Set `KENTO_WEAVE_MODE=required` on every API and Temporal worker process.
2. Use a team-owned `WANDB_ENTITY/WANDB_PROJECT` and a scoped service-account key.
3. Version prompts, policy configuration, models, scenario schema, and evaluation datasets.
4. Alert on inference errors, guardrail rejection rate, correction exhaustion, latency, token
   use, unresolved critical survivors, and trace-export degradation.
5. Apply retention and redaction policies before using non-synthetic data.
6. Keep deterministic validation outside the LLM and Weave layers.
7. Use Temporal workflow IDs and KentoAgent run IDs as correlation fields; never rely on process
   memory for trace linkage.
8. Run a fixed canary seed suite before deployment, then compare its Weave Evaluation against
   the promoted policy version.
9. Deploy Marimo as a read-mostly research surface with authentication; do not expose its eval
   publication controls on the public operations endpoint.
10. Monitor exporter health and W&B quotas. `auto` mode is appropriate for laptops, not for a
    production environment that promises complete auditability.
