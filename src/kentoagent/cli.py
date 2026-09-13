from __future__ import annotations

import argparse
import json

from kentoagent.evaluation.metrics import calculate_metrics
from kentoagent.orchestration.orchestrator import Orchestrator
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario


def main() -> None:
    parser = argparse.ArgumentParser(prog="kentoagent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate = subparsers.add_parser("simulate", help="Run an offline seeded simulation")
    simulate.add_argument("--seed", type=int, default=49281)
    simulate.add_argument("--steps", type=int, default=25)
    args = parser.parse_args()
    world = generate_scenario(ScenarioConfig(seed=args.seed))
    Orchestrator(world).run(args.steps)
    payload = {
        "run_id": world.run_id,
        "scenario_id": world.scenario_id,
        "seed": world.seed,
        "stopped_reason": world.stopped_reason,
        "metrics": calculate_metrics(world).model_dump(mode="json"),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
